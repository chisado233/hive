"""Scheduler: deadline settlement, merged notifications, outbox, triggers,
cold-start rotation. No LLM judgments (DESIGN §5.3/§10)."""
from __future__ import annotations

import datetime
import json
import threading

from .auth import SYSTEM_SUBJECT
from .ids import new_id
from .timeutil import format_rfc3339, parse_rfc3339


def notify_new_task(svc, cur, task_id: str, target: str, task_type: str):
    """Queue candidate notifications inside the creation transaction."""
    if not target.startswith("port:"):
        # personal target: wake the agent if enabled+auto_start (no instances)
        agent_id = target[6:]
        row = cur.execute("SELECT lifecycle FROM agents WHERE agent_id=?",
                          (agent_id,)).fetchone()
        if row and row["lifecycle"] == "enabled":
            cur.execute(
                "INSERT OR IGNORE INTO notifications(notify_id,agent_id,dedupe_key,"
                "message,state,created_at) VALUES (?,?,?,'新的个人投递待处理',"
                "'pending',?)",
                (new_id("view"), agent_id, f"personal:{agent_id}", svc.now()))
        return
    port_id = target[5:]
    prow = cur.execute("SELECT * FROM ports WHERE port_id=?", (port_id,)).fetchone()
    if not prow or prow["handler"] != "agents":
        return
    candidates = cur.execute(
        "SELECT s.agent_id FROM subscriptions s JOIN agents a ON "
        "a.agent_id=s.agent_id WHERE s.port_id=? AND s.enabled=1 AND "
        "a.lifecycle='enabled'", (port_id,)).fetchall()
    for c in candidates:
        agent_id = c["agent_id"]
        # work capacity gate for notification candidacy
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
        # decline suppression on current spec_version
        sv = cur.execute("SELECT spec_version FROM tasks WHERE task_id=?",
                         (task_id,)).fetchone()["spec_version"]
        declined = cur.execute(
            "SELECT 1 FROM task_declines WHERE task_id=? AND subject_id=? AND "
            "spec_version=?", (task_id, agent_id, sv)).fetchone()
        if declined:
            continue
        cur.execute(
            "INSERT OR IGNORE INTO notifications(notify_id,agent_id,dedupe_key,"
            "message,state,created_at) VALUES (?,?,?,'端口有新任务待领取',"
            "'pending',?)",
            (new_id("view"), agent_id, f"port:{port_id}", svc.now()))


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
            cur.execute(
                "INSERT OR IGNORE INTO notifications(notify_id,agent_id,dedupe_key,"
                "message,state,created_at) SELECT ?, agent_id, ?, '端口已恢复领取，"
                "有待领任务', 'pending', ? FROM subscriptions WHERE port_id=? AND "
                "enabled=1", (new_id("view"), f"portresumed:{port_id}",
                              self.svc.now(), port_id))

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
        self.run_cold_start()
        self.run_outbox()
        self.run_triggers()

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

    def run_notifications(self):
        svc = self.svc
        rows = svc.store.conn.execute(
            "SELECT * FROM notifications WHERE state='pending'").fetchall()
        for n in rows:
            inst = svc.store.conn.execute(
                "SELECT * FROM instances WHERE agent_id=? AND phase IN "
                "('starting','live') ORDER BY started_at DESC LIMIT 1",
                (n["agent_id"],)).fetchone()
            if not inst:
                continue  # cold start handles bringing one up
            try:
                result = svc.harnesses.send(svc, n["agent_id"], inst["harness"],
                                            inst["handle"], n["message"],
                                            n["notify_id"])
            except Exception:  # noqa: BLE001
                result = "failed"
            with svc.store.write() as cur:
                if result in ("accepted",):
                    cur.execute("UPDATE notifications SET state='sent', sent_at=? "
                                "WHERE notify_id=?", (svc.now(), n["notify_id"]))
                else:
                    # wake signals are droppable+resendable; back off then drop
                    attempts = n["attempts"] + 1
                    if attempts >= 3:
                        cur.execute("DELETE FROM notifications WHERE notify_id=?",
                                    (n["notify_id"],))
                    else:
                        cur.execute("UPDATE notifications SET attempts=? WHERE "
                                    "notify_id=?", (attempts, n["notify_id"]))

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
