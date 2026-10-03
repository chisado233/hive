"""Scheduler: deadline settlement, merged notifications, outbox, triggers,
cold-start rotation. No LLM judgments (DESIGN §5.3/§10)."""
from __future__ import annotations

import datetime
import json
import threading

from .auth import SYSTEM_SUBJECT
from .ids import new_id
from .timeutil import format_rfc3339, parse_rfc3339


def _task_candidates(cur, svc, task_id: str, target: str,
                     task_type: str) -> list[str]:
    """Candidate agent ids for waking about task_id, honoring the same
    gates as the first notify: enabled lifecycle+subscription (ports),
    work-capacity, decline suppression on the current spec_version.
    Shared by notify_new_task and reconcile rounds (RDEF-04/05)."""
    if not target.startswith("port:"):
        agent_id = target[6:] if target.startswith("agent:") else target
        row = cur.execute("SELECT lifecycle FROM agents WHERE agent_id=?",
                          (agent_id,)).fetchone()
        return [agent_id] if row and row["lifecycle"] == "enabled" else []
    port_id = target[5:]
    prow = cur.execute("SELECT * FROM ports WHERE port_id=?",
                       (port_id,)).fetchone()
    if not prow or prow["handler"] != "agents" or prow["state"] != "enabled":
        return []
    candidates = cur.execute(
        "SELECT s.agent_id FROM subscriptions s JOIN agents a ON "
        "a.agent_id=s.agent_id WHERE s.port_id=? AND s.enabled=1 AND "
        "a.lifecycle='enabled'", (port_id,)).fetchall()
    out = []
    for c in candidates:
        agent_id = c["agent_id"]
        if task_type == "work":
            inflight = cur.execute(
                "SELECT COUNT(*) c FROM tasks WHERE owner_subject=? AND "
                "state='claimed' AND type='work'", (agent_id,)).fetchone()["c"]
            cfg_limit = 3
            vrow = cur.execute(
                "SELECT file_path FROM agent_config_versions WHERE agent_id=? AND "
                "published=1 ORDER BY revision DESC LIMIT 1", (agent_id,)).fetchone()
            if vrow:
                try:
                    with open(vrow["file_path"], "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                    cfg_limit = int(cfg.get("limits", {}).get(
                        "max_inflight_work", 3))
                except (OSError, ValueError, TypeError):
                    pass
            if inflight >= cfg_limit:
                continue
        sv = cur.execute("SELECT spec_version FROM tasks WHERE task_id=?",
                         (task_id,)).fetchone()
        sv = sv["spec_version"] if sv else None
        declined = cur.execute(
            "SELECT 1 FROM task_declines WHERE task_id=? AND subject_id=? AND "
            "spec_version=?", (task_id, agent_id, sv)).fetchone()
        if declined:
            continue
        out.append(agent_id)
    return out


def notify_new_task(svc, cur, task_id: str, target: str, task_type: str,
                    dedupe_prefix: str | None = None):
    """Deduplicate one task/routing epoch, never a port's entire lifetime.

    Separate task signals remain durable; run_notifications coalesces the
    signals captured in one send batch, without swallowing later arrivals.
    """
    task = cur.execute("SELECT spec_version, claim_epoch FROM tasks WHERE "
                       "task_id=?", (task_id,)).fetchone()
    spec = task["spec_version"] if task else 1
    epoch = task["claim_epoch"] if task else 0
    for agent_id in _task_candidates(cur, svc, task_id, target, task_type):
        scope = (f"port:{target[5:]}" if target.startswith("port:")
                 else f"personal:{agent_id}")
        _queue_notif(cur, agent_id,
                     dedupe_prefix or
                     f"{scope}:task:{task_id}:v{spec}:e{epoch}",
                     "端口有新任务待领取，请查看 inbox" if target.startswith("port:")
                     else "新的个人投递待处理，请查看 inbox", svc)



def _queue_notif(cur, agent_id: str, dedupe_key: str, message: str, svc):
    """Queue a wake with notify_jitter: send_after = now + rand(0, jitter)
    so simultaneous candidate wakes are spread out (DESIGN §10.1)."""
    import datetime
    import random
    from .timeutil import parse_rfc3339
    jitter = float(svc.policy().get("notify_jitter", 3) or 0)
    send_after = None
    if jitter > 0:
        send_after = format_rfc3339(
            parse_rfc3339(svc.now())
            + datetime.timedelta(seconds=random.uniform(0, jitter)))
    cur.execute(
        "INSERT OR IGNORE INTO notifications(notify_id,agent_id,dedupe_key,"
        "message,state,attempts,send_after,created_at) VALUES (?,?,?,?,"
        "'pending',0,?,?)",
        (new_id("view"), agent_id, dedupe_key, message, send_after,
         svc.now()))


class Scheduler:
    def __init__(self, svc):
        self.svc = svc
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._outbox_kick = threading.Event()

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="hive-scheduler")
        self._thread.start()
        self.svc.logger.info("scheduler.started")

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def kick_outbox(self):
        self._outbox_kick.set()

    def notify_port_resumed(self, port_id: str):
        with self.svc.store.write() as cur:
            # Each resume is a new wake. Each subscriber also needs its own
            # notify_id (one INSERT SELECT id silently dropped all but one).
            wake = f"portresumed:{port_id}:{new_id('view')}"
            subscribers = cur.execute(
                "SELECT agent_id FROM subscriptions WHERE port_id=? AND "
                "enabled=1", (port_id,)).fetchall()
            for sub in subscribers:
                _queue_notif(cur, sub["agent_id"], wake,
                             "端口已恢复领取，有待领任务，请查看 inbox", self.svc)

    def _reconcile_page(self, cutoff: str):
        """Persist a fair keyset cursor, including skipped/exhausted rows.

        The next tick proceeds past this page even when none of its tasks
        can be woken. Wrap after the last page so changed candidates/caps
        are reevaluated. Historical queued rows are retained unchanged.
        """
        cursor = self.svc.store.kv_get("wake_scan_cursor", {}) or {}
        after = (cursor.get("created_at", ""), cursor.get("task_id", "")) \
            if isinstance(cursor, dict) else ("", "")
        sql = (
            "SELECT t.task_id, t.target, t.type, t.spec_version, t.created_at "
            "FROM tasks t WHERE t.state='queued' AND t.type IN "
            "('work','notice','message','receipt') AND t.created_at<=? "
            "AND (t.created_at,t.task_id)>(?,?) "
            "ORDER BY t.created_at,t.task_id LIMIT 50")
        rows = self.svc.store.conn.execute(sql, (cutoff, *after)).fetchall()
        if not rows and after != ("", ""):
            rows = self.svc.store.conn.execute(sql, (cutoff, "", "")).fetchall()
        last = rows[-1] if rows else None
        self.svc.store.kv_set("wake_scan_cursor", {
            "created_at": last["created_at"] if last else "",
            "task_id": last["task_id"] if last else ""})
        return rows

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as e:  # noqa: BLE001
                self.svc.logger.error("scheduler.tick", error=repr(e))
            self._outbox_kick.wait(timeout=2.0)
            self._outbox_kick.clear()

    # ---------------- one pass ----------------
    def tick(self):
        self.run_deadline_scan()
        self.run_notifications()
        self.run_due_restarts()
        self.run_grace_stops()
        self.run_reconcile_wakes()
        self.run_cold_start()
        self.run_outbox()
        self.run_triggers()
        self.run_conversation_dispatch()

    def run_conversation_dispatch(self):
        """会话输入派发（非阻塞）：实际 I/O 在桥线程，tick 只触发。"""
        cs = getattr(self.svc, "conversation", None)
        if cs is None:
            return
        try:
            cs.tick()
        except Exception as e:  # noqa: BLE001
            self.svc.logger.error("conv.tick_failed", error=repr(e))

    def run_grace_stops(self):
        """RDEF-09: terminate instances whose accepted grace window has
        elapsed (hint already sent at acceptance). Persistent kv plans
        survive daemon restarts; termination revokes credentials (fencing)
        and honors then_start (graceful restart replacement)."""
        import json as _json
        svc = self.svc
        now = svc.now()
        rows = svc.store.conn.execute(
            "SELECT k, v FROM kv WHERE k LIKE 'gracestop:%'").fetchall()
        for r in rows:
            try:
                st = _json.loads(r["v"])
            except (ValueError, TypeError):
                continue
            if not isinstance(st, dict):
                continue
            if st.get("deadline") and st["deadline"] > now:
                continue
            op_id = st.get("op_id")
            agent_id = st.get("agent_id")
            inst = svc.store.conn.execute(
                "SELECT * FROM instances WHERE instance_id=?",
                (st.get("instance_id"),)).fetchone()
            with svc.store.write() as cur:
                cur.execute("DELETE FROM kv WHERE k=?", (r["k"],))
            if not inst or inst["phase"] not in ("starting", "live",
                                                 "stopping"):
                with svc.store.write() as cur:
                    cur.execute(
                        "UPDATE operations SET state='succeeded', ended_at=?, "
                        "result=COALESCE(result,'{}') WHERE operation_id=?",
                        (now, op_id))
                continue
            try:
                stop_result = svc.harnesses.stop(
                    svc, agent_id, st.get("harness") or inst["harness"],
                    st.get("handle") or inst["handle"], op_id)
            except Exception as e:  # noqa: BLE001
                with svc.store.write() as cur:
                    cur.execute(
                        "UPDATE instances SET phase='failed', ended_at=?, "
                        "error=? WHERE instance_id=?",
                        (now, f"grace stop failed: {e!r:.120}",
                         inst["instance_id"]))
                    cur.execute(
                        "UPDATE operations SET state='failed', ended_at=?, "
                        "error=? WHERE operation_id=?",
                        (now, _json.dumps({"code": "HARNESS_UNAVAILABLE",
                                           "message": "grace stop failed"},
                                          ensure_ascii=False), op_id))
                svc.logger.warning("grace.stop_failed", agent=agent_id,
                                   error=repr(e)[:120])
                continue
            if stop_result == "stopped":
                with svc.store.write() as cur:
                    cur.execute(
                        "UPDATE instances SET phase='stopped', ended_at=?, "
                        "observation='idle' WHERE instance_id=?",
                        (now, inst["instance_id"]))
                    svc.authn.revoke_instance_credentials(
                        cur, inst["instance_id"])
                    cur.execute(
                        "UPDATE agents SET lifecycle='paused', updated_at=? "
                        "WHERE agent_id=?", (now, agent_id))
                    cur.execute(
                        "UPDATE operations SET state='succeeded', ended_at=?,"
                        " result=COALESCE(result,'{}') WHERE operation_id=?",
                        (now, op_id))
                    svc.store.add_event(
                        cur, entity_type="agent", entity_id=agent_id,
                        entity_version=inst["generation"],
                        kind="agent.stopped", actor=st.get("actor")
                        or "system:scheduler",
                        payload={"instance_id": inst["instance_id"],
                                 "grace": st.get("grace"),
                                 "grace_completed": True})
                svc.logger.info("grace.stop_completed", agent=agent_id)
            else:  # unknown outcome: slot retained (existing semantics)
                with svc.store.write() as cur:
                    cur.execute(
                        "UPDATE operations SET state='running', "
                        "outcome_unknown=1, result=COALESCE(result,'{}') "
                        "WHERE operation_id=?", (op_id,))
            if st.get("then_start"):
                try:
                    from .runtime_ops import op_start_agent
                    from .auth import SYSTEM_SUBJECT
                    op_start_agent(svc, SYSTEM_SUBJECT, agent_id, None)
                except Exception as e:  # noqa: BLE001
                    svc.logger.warning("grace.then_start_failed",
                                       agent=agent_id, error=repr(e)[:120])

    def run_deadline_scan(self):
        svc = self.svc
        now = svc.now()
        # business expiry first (both due at same instant -> trash wins)
        expired = svc.store.conn.execute(
            "SELECT task_id FROM tasks WHERE state IN ('queued','claimed') AND "
            "expires_at IS NOT NULL AND expires_at<=?", (now,)).fetchall()
        for r in expired:
            with svc.store.write() as cur:
                row = cur.execute("SELECT * FROM tasks WHERE task_id=? AND state IN "
                                  "('queued','claimed')", (r["task_id"],)).fetchone()
                if not row:
                    continue
                updated = cur.execute(
                    "UPDATE tasks SET state='trash', ended_at=?, result=?, "
                    "updated_at=?, version=version+1 WHERE task_id=? AND state IN "
                    "('queued','claimed')",
                    (now, json.dumps({"reason": "业务有效期已过", "reason_code":
                                      "EXPIRED", "ended_by": "system:deadline",
                                      "ended_at": now}), now, r["task_id"]))
                if updated.rowcount != 1:
                    continue
                if row["claim_id"]:
                    cur.execute("UPDATE task_claims SET invalidated_at=?, "
                                "invalidated_reason='expired' WHERE claim_id=? AND "
                                "invalidated_at IS NULL", (now, row["claim_id"]))
                    # notify the executor to stop this item
                    if row["owner_subject"]:
                        cur.execute(
                            "INSERT OR IGNORE INTO notifications(notify_id,agent_id,"
                            "dedupe_key,message,state,created_at) VALUES (?,?,?,"
                            "'任务已过期，请停止该项工作','pending',?)",
                            (new_id("view"), row["owner_subject"],
                             f"expired:{r['task_id']}", now))
                evt = svc.store.add_event(cur, entity_type="task",
                                          entity_id=r["task_id"], kind="task.terminal",
                                          actor="system:deadline",
                                          payload={"state": "trash",
                                                   "reason_code": "EXPIRED"})
                from .waits import advance_waits, cancel_waits_for_task
                advance_waits(svc, cur, r["task_id"])
                from .delivery import create_receipt
                cb = json.loads(row["callback"]) if row["callback"] else None
                if cb and row["type"] != "receipt":
                    create_receipt(svc, cur, source_row=row,
                                   terminal_event_id=evt, state="trash",
                                   result={"reason": "EXPIRED",
                                           "ended_by": "system:deadline"}, callback=cb)
        # claim deadline -> fail
        timedout = svc.store.conn.execute(
            "SELECT task_id FROM tasks WHERE state='queued' AND claim_deadline IS "
            "NOT NULL AND claim_deadline<=?", (now,)).fetchall()
        for r in timedout:
            with svc.store.write() as cur:
                row = cur.execute("SELECT * FROM tasks WHERE task_id=? AND "
                                  "state='queued'", (r["task_id"],)).fetchone()
                if not row:
                    continue
                updated = cur.execute(
                    "UPDATE tasks SET state='fail', ended_at=?, result=?, "
                    "updated_at=?, version=version+1 WHERE task_id=? AND "
                    "state='queued'",
                    (now, json.dumps({"reason": "领取期限已过无人领取",
                                      "reason_code": "CLAIM_TIMEOUT",
                                      "ended_by": "system:deadline",
                                      "ended_at": now}), now, r["task_id"]))
                if updated.rowcount != 1:
                    continue
                evt = svc.store.add_event(cur, entity_type="task",
                                          entity_id=r["task_id"], kind="task.terminal",
                                          actor="system:deadline",
                                          payload={"state": "fail",
                                                   "reason_code": "CLAIM_TIMEOUT"})
                from .waits import advance_waits
                advance_waits(svc, cur, r["task_id"])
                from .delivery import create_receipt
                cb = json.loads(row["callback"]) if row["callback"] else None
                if cb and row["type"] != "receipt":
                    create_receipt(svc, cur, source_row=row,
                                   terminal_event_id=evt, state="fail",
                                   result={"reason": "CLAIM_TIMEOUT",
                                           "ended_by": "system:deadline"},
                                   callback=cb)
        # wait deadlines -> timed_out
        from .waits import scan_wait_deadlines
        due = svc.store.conn.execute(
            "SELECT wait_id FROM waits WHERE state='pending' AND deadline IS "
            "NOT NULL AND deadline<=?", (now,)).fetchall()
        if due:
            with svc.store.write() as cur:
                scan_wait_deadlines(svc, cur, now)
        # unclaimed alerts
        pol = svc.policy()
        alert_after = pol.get("unclaimed_alert_after", 900)
        cutoff = format_rfc3339(parse_rfc3339(now)
                                - datetime.timedelta(seconds=alert_after))
        stuck = svc.store.conn.execute(
            "SELECT task_id FROM tasks WHERE state='queued' AND created_at<=? AND "
            "task_id NOT IN (SELECT entity_id FROM events WHERE kind="
            "'task.unclaimed' AND entity_type='task')", (cutoff,)).fetchall()
        for r in stuck:
            with svc.store.write() as cur:
                svc.store.add_event(cur, entity_type="task", entity_id=r["task_id"],
                                    kind="task.unclaimed", actor="system:scheduler",
                                    payload={"after": alert_after})

    def run_reconcile_wakes(self):
        """RDEF-04/05: tasks queued past notify_retry_after get their
        candidates re-notified. Round counting is PERSISTENT per task
        (kv wakeround:<task>, scoped to spec_version + candidate set) —
        deleting notification rows can never reset it. In-flight is judged
        PER CANDIDATE by pending-wake freshness, so one bad subscriber's
        stuck pending cannot suppress the whole port. Rounds are paced by
        notify_retry_after (no per-tick re-send after the task ages past a
        threshold). Creates NO tasks/receipts/@notices; on cap an explicit
        task.notify_exhausted event fires once (observable stop, not a
        silent forget)."""
        import datetime
        import json as _json
        from .ids import new_id
        from .timeutil import parse_rfc3339
        svc = self.svc
        pol = svc.policy()
        retry_after = int(pol.get("notify_retry_after", 300) or 300)
        max_rounds = int(pol.get("notify_max_rounds", 12) or 12)
        now = svc.now()
        cutoff = format_rfc3339(parse_rfc3339(now)
                                - datetime.timedelta(seconds=retry_after))
        # sweep round-state rows whose task left 'queued'
        with svc.store.write() as cur:
            cur.execute(
                "DELETE FROM kv WHERE k LIKE 'wakeround:%' AND substr(k, 11) "
                "NOT IN (SELECT task_id FROM tasks WHERE state='queued')")
        stuck = self._reconcile_page(cutoff)
        for t in stuck:
            task_row = svc.store.conn.execute(
                "SELECT * FROM tasks WHERE task_id=? AND state='queued'",
                (t["task_id"],)).fetchone()
            if not task_row:
                continue
            # candidate list honoring the same gates as first notify
            # (enabled/lifecycle/capacity/decline)
            with svc.store.write() as cur:
                candidates = _task_candidates(cur, svc, t["task_id"],
                                              task_row["target"],
                                              task_row["type"])
            if not candidates:
                continue
            st = svc.store.kv_get(f"wakeround:{t['task_id']}", {}) or {}
            if not isinstance(st, dict):
                st = {}
            st.setdefault("total", 0)     # lifetime rounds (dedupe suffix)
            st.setdefault("offset", 0)    # rounds at last condition reset
            if (st.get("spec") != task_row["spec_version"]
                    or st.get("candidates") != candidates):
                # condition change (spec version or candidate set) resets the
                # CAP counter; the lifetime total keeps dedupe keys unique
                st.update({"spec": task_row["spec_version"],
                           "candidates": candidates, "offset": st["total"],
                           "exhausted": False})
            cap_rounds = int(st["total"]) - int(st["offset"])
            if cap_rounds >= max_rounds:
                if not st.get("exhausted"):
                    st["exhausted"] = True
                    st["capped_at"] = now
                    svc.store.kv_set(f"wakeround:{t['task_id']}", st)
                    with svc.store.write() as cur:
                        svc.store.add_event(
                            cur, entity_type="task", entity_id=t["task_id"],
                            kind="task.notify_exhausted",
                            actor="system:scheduler",
                            payload={"rounds": cap_rounds, "max_rounds": max_rounds,
                                     "candidates": candidates,
                                     "note": "对账重唤醒轮次已达上限；任务仍在"
                                             "队列。spec变更/候选变化/调大"
                                             "notify_max_rounds后自动重置"})
                continue
            if st.get("exhausted"):
                st["exhausted"] = False  # cap raised via config -> resume
            # pace rounds by notify_retry_after since the last round fired
            last_at = st.get("last_at")
            if last_at:
                try:
                    if parse_rfc3339(now) - parse_rfc3339(last_at) < \
                            datetime.timedelta(seconds=retry_after):
                        continue
                except (ValueError, TypeError):
                    pass
            # per-candidate in-flight: a FRESH pending wake (created within
            # the retry window) means this candidate is still being served;
            # a STALE one (dead subscriber) blocks nobody else
            fresh_cutoff = format_rfc3339(
                parse_rfc3339(now)
                - datetime.timedelta(seconds=retry_after))
            fresh = [r["agent_id"] for r in svc.store.conn.execute(
                "SELECT DISTINCT agent_id FROM notifications WHERE "
                "state='pending' AND created_at>?", (fresh_cutoff,)).fetchall()]
            targets = [a for a in candidates if a not in fresh]
            if not targets:
                continue
            round_no = int(st["total"]) + 1
            inserted = 0
            with svc.store.write() as cur:
                for ag in targets:
                    cur.execute(
                        "INSERT OR IGNORE INTO notifications(notify_id,agent_id,"
                        "dedupe_key,message,state,attempts,created_at) VALUES "
                        "(?,?,?, '对账：仍有待办任务待处理，请查看 inbox','pending',0,?)",
                        (new_id("view"), ag,
                         f"reconcile:{t['task_id']}:{round_no}", now))
                    inserted += cur.execute("SELECT changes()").fetchone()[0]
            if inserted:
                st["total"] = round_no
                st["last_at"] = now
                svc.store.kv_set(f"wakeround:{t['task_id']}", st)

    def run_notifications(self):
        svc = self.svc
        rows = svc.store.conn.execute(
            "SELECT * FROM notifications WHERE state='pending' AND "
            "(send_after IS NULL OR send_after<=?)",
            (svc.now(),)).fetchall()
        # Coalesce only the captured batch. A signal created while this I/O
        # is in flight is not in the batch and remains pending for next tick.
        batches = {}
        for n in rows:
            coalescible = n["dedupe_key"].startswith(
                ("port:", "personal:", "reconcile:", "receipt:",
                 "portresumed:", "wake:"))
            key = (n["agent_id"], n["message"],
                   None if coalescible else n["notify_id"])
            batches.setdefault(key, []).append(n)
        for batch in batches.values():
            n = batch[0]
            inst = svc.store.conn.execute(
                "SELECT * FROM instances WHERE agent_id=? AND phase IN "
                "('starting','live') ORDER BY started_at DESC LIMIT 1",
                (n["agent_id"],)).fetchone()
            if not inst:
                continue  # cold start handles bringing one up
            try:
                result = svc.harnesses.send(svc, n["agent_id"], inst["harness"],
                                            inst["handle"], n["message"],
                                            n["notify_id"], wait_idle=False)
            except Exception:  # noqa: BLE001
                result = "failed"
            with svc.store.write() as cur:
                if result in ("accepted",):
                    cur.executemany(
                        "UPDATE notifications SET state='sent', sent_at=? "
                        "WHERE notify_id=? AND state='pending'",
                        [(svc.now(), item["notify_id"]) for item in batch])
                else:
                    # wake signals are droppable+resendable; retry paced by
                    # send_unknown_retry_after (busy/unknown merges), then drop
                    import datetime
                    from .timeutil import parse_rfc3339
                    wait = svc.policy().get("send_unknown_retry_after", 300)
                    for item in batch:
                        attempts = item["attempts"] + 1
                        if attempts >= 3:
                            cur.execute(
                                "DELETE FROM notifications WHERE notify_id=?",
                                (item["notify_id"],))
                        else:
                            cur.execute(
                                "UPDATE notifications SET attempts=?, send_after=? "
                                "WHERE notify_id=?",
                                (attempts, format_rfc3339(
                                    parse_rfc3339(svc.now())
                                    + datetime.timedelta(seconds=wait)),
                                 item["notify_id"]))

    def run_due_restarts(self):
        """RDEF-03: crash-restart plans are a recoverable state machine
        (kv restart:<agent>): scheduled -> starting -> (success: plan done |
        failure: state=failed, plan retained and re-queued at the same
        backoff step; 3 consecutive start failures or the per-hour crash
        cap -> awaiting_intervention, queryable, never silently dropped).
        A daemon crash between the consume-transition and the start call
        recovers: the next tick sees state=starting with a stale started_at
        and no live instance and treats it as a failed attempt."""
        import datetime
        import json as _json
        from .timeutil import parse_rfc3339
        svc = self.svc
        now = svc.now()
        rows = svc.store.conn.execute(
            "SELECT k, v FROM kv WHERE k LIKE 'restart:%'").fetchall()
        for r in rows:
            agent_id = r["k"].split(":", 1)[1]
            try:
                st = _json.loads(r["v"])
            except (ValueError, TypeError):
                continue
            if not isinstance(st, dict):
                continue
            state = st.get("state") or "scheduled"
            if state == "awaiting_intervention":
                continue  # explicit pending-intervention; manual start clears
            nxt = st.get("next_allowed_at")
            if not nxt:
                continue
            # stable for an hour since the last scheduled attempt -> reset
            try:
                stable_gap = parse_rfc3339(now) - parse_rfc3339(nxt)
            except (ValueError, TypeError):
                continue
            if stable_gap > datetime.timedelta(hours=1):
                svc.store.kv_set(f"restart:{agent_id}", {})
                continue
            if state == "starting":
                # recover an attempt whose daemon died mid-start: if the
                # instance is actually up the plan is done; otherwise the
                # attempt failed (stale started_at, no live slot)
                started_at = st.get("started_at") or nxt
                try:
                    age = (parse_rfc3339(now)
                           - parse_rfc3339(started_at)).total_seconds()
                except (ValueError, TypeError):
                    age = 1e9
                if age < 15:
                    continue  # give a real in-flight start time to finish
                live = svc.store.conn.execute(
                    "SELECT 1 FROM instances WHERE agent_id=? AND phase IN "
                    "('starting','live','stopping')", (agent_id,)).fetchone()
                if live:
                    svc.store.kv_set(f"restart:{agent_id}",
                                     {"attempts": st.get("attempts", 1)})
                    continue
                self._restart_attempt_failed(agent_id, st,
                                             "启动尝试期间daemon重启，结果未知")
                continue
            if nxt > now:
                continue
            arow = svc.store.conn.execute(
                "SELECT lifecycle FROM agents WHERE agent_id=?",
                (agent_id,)).fetchone()
            if not arow or arow["lifecycle"] != "enabled":
                svc.store.kv_set(f"restart:{agent_id}", {})
                continue
            vrow = svc.store.conn.execute(
                "SELECT file_path FROM agent_config_versions WHERE agent_id=? "
                "AND published=1 ORDER BY revision DESC LIMIT 1",
                (agent_id,)).fetchone()
            if not vrow:
                continue
            try:
                with open(vrow["file_path"], encoding="utf-8") as f:
                    cfg = _json.load(f)
            except (OSError, ValueError):
                continue
            if not cfg.get("runtime", {}).get("restart_on_exit", True):
                svc.store.kv_set(f"restart:{agent_id}", {})
                continue
            if svc.store.conn.execute(
                    "SELECT 1 FROM instances WHERE agent_id=? AND phase IN "
                    "('starting','live','stopping')", (agent_id,)).fetchone():
                # slot taken over by an external start: an old plan must
                # never override a newer instance
                svc.store.kv_set(f"restart:{agent_id}", {})
                continue
            # consume = atomic state transition (not a delete): a crash
            # after this leaves state=starting, recovered above
            st2 = dict(st)
            st2.update({"state": "starting", "started_at": now,
                        "last_error": None, "op_id": None})
            svc.store.kv_set(f"restart:{agent_id}", st2)
            try:
                from .runtime_ops import op_start_agent
                from .auth import SYSTEM_SUBJECT
                op_start_agent(svc, SYSTEM_SUBJECT, agent_id, None)
                svc.logger.info("restart.backoff_fired", agent=agent_id)
                # success: plan complete; keep attempts for future crashes
                svc.store.kv_set(f"restart:{agent_id}",
                                 {"attempts": st.get("attempts", 1)})
            except Exception as e:  # noqa: BLE001
                self._restart_attempt_failed(agent_id, st, repr(e)[:200])

    def _restart_attempt_failed(self, agent_id: str, st: dict, error: str):
        """A start attempt failed: keep the plan and re-queue at the SAME
        backoff step (defer, don't escalate); after 3 consecutive failures
        enter the explicit awaiting_intervention state."""
        import datetime
        from .timeutil import parse_rfc3339
        svc = self.svc
        pol = svc.policy()
        backoff = pol.get("restart_backoff") or [5, 15, 60]
        attempts = int(st.get("attempts", 1) or 1)
        step = backoff[min(attempts - 1, len(backoff) - 1)]
        fail_count = int(st.get("fail_count", 0) or 0) + 1
        now = svc.now()
        next_allowed = format_rfc3339(
            parse_rfc3339(now) + datetime.timedelta(seconds=step))
        new_state = ("awaiting_intervention"
                     if fail_count >= 3 else "failed")
        svc.store.kv_set(f"restart:{agent_id}", {
            "attempts": attempts,
            "next_allowed_at": (None if new_state == "awaiting_intervention"
                                else next_allowed),
            "state": new_state, "fail_count": fail_count,
            "last_error": error, "started_at": st.get("started_at"),
            "op_id": st.get("op_id")})
        with svc.store.write() as cur:
            svc.store.add_event(cur, entity_type="agent", entity_id=agent_id,
                                kind=("agent.restart_stalled"
                                      if new_state == "awaiting_intervention"
                                      else "agent.restart_failed"),
                                payload={"attempts": attempts, "step_s": step,
                                         "fail_count": fail_count,
                                         "error": error[:200]})
        svc.logger.warning("restart.attempt_failed", agent=agent_id,
                           error=error[:120], fail_count=fail_count)

    @staticmethod
    def _restart_blocked(svc, agent_id: str, now: str) -> bool:
        """Automatic start paths (cold start) must not bypass backoff."""
        st = svc.store.kv_get(f"restart:{agent_id}", {}) or {}
        if not isinstance(st, dict) or not st:
            return False
        if st.get("state") == "awaiting_intervention":
            return True
        nxt = st.get("next_allowed_at")
        return bool(nxt and nxt > now)

    def run_cold_start(self):
        """No live candidates: start at most cold_start_batch agents per round,
        rotating. Also covers personal-target autostart."""
        svc = self.svc
        pol = svc.policy()
        batch = pol.get("cold_start_batch", 1)
        started = 0
        # agents with pending notifications and no live instance
        pend = svc.store.conn.execute(
            "SELECT DISTINCT agent_id FROM notifications WHERE state='pending' "
            "AND agent_id NOT IN (SELECT agent_id FROM instances WHERE phase IN "
            "('starting','live','stopping'))").fetchall()
        # rotate: order by fewest recent starts
        for p in pend:
            if started >= batch:
                break
            agent_id = p["agent_id"]
            row = svc.store.conn.execute(
                "SELECT lifecycle FROM agents WHERE agent_id=?",
                (agent_id,)).fetchone()
            if not row or row["lifecycle"] != "enabled":
                continue
            vrow = svc.store.conn.execute(
                "SELECT file_path FROM agent_config_versions WHERE agent_id=? AND "
                "published=1 ORDER BY revision DESC LIMIT 1",
                (agent_id,)).fetchone()
            if not vrow:
                continue
            try:
                with open(vrow["file_path"], "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            except (OSError, ValueError):
                continue
            if not cfg.get("runtime", {}).get("auto_start", True):
                continue
            # RDEF-03: automatic cold start must not bypass crash-restart
            # backoff (scheduled/failed with a future next_allowed_at, or
            # awaiting_intervention)
            if self._restart_blocked(svc, agent_id, svc.now()):
                continue
            try:
                from .runtime_ops import op_start_agent
                op_start_agent(svc, SYSTEM_SUBJECT, agent_id, None)
                started += 1
            except Exception as e:  # noqa: BLE001
                svc.logger.warning("coldstart.failed", agent=agent_id,
                                   error=repr(e))

    def run_outbox(self):
        if self.svc.policy().get("recovery_hold"):
            return
        from .delivery import process_outbox
        process_outbox(self.svc)

    def run_triggers(self):
        from .triggers import run_due_triggers
        if self.svc.policy().get("recovery_hold"):
            return
        run_due_triggers(self.svc)
