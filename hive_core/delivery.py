"""Receipts (terminal callbacks) and delivery payloads per DESIGN §7.3/§15.4."""
from __future__ import annotations

import json

from .errors import HiveError
from .ids import new_id
from .timeutil import format_rfc3339


def create_receipt(svc, cur, *, source_row, terminal_event_id: str, state: str,
                   result: dict, callback: dict) -> str | None:
    """Create receipt Task + outbox delivery inside the caller's transaction.
    Semantic unique key: (terminal_event_id, callback_target)."""
    target = callback["target"]
    semantic_key = f"receipt:{terminal_event_id}:{target}"
    existing = cur.execute("SELECT delivery_id FROM outbox WHERE semantic_key=?",
                           (semantic_key,)).fetchone()
    if existing:
        return None
    receipt_id = new_id("task")
    now = svc.now()
    payload = {
        "receipt_task_id": receipt_id, "source_task_id": source_row["task_id"],
        "terminal_event_id": terminal_event_id, "state": state,
        "result": result, "occurred_at": now,
    }
    from .tasks import insert_task
    insert_task(cur, task_id=receipt_id, parent_id=source_row["task_id"],
                root_id=source_row["root_id"], type="receipt",
                title=f"回执: {source_row['title'][:80]} → {state}",
                summary=f"源任务 {source_row['task_id']} 以 {state} 结束",
                body="", data=payload,
                source={"subject": "system:callback", "type": "system"},
                target=target, reply_to=source_row["task_id"],
                refs=[f"task:{source_row['task_id']}",
                      f"evt:{terminal_event_id}"],
                created_at=now, updated_at=now)
    svc.store.add_event(cur, entity_type="task", entity_id=receipt_id,
                        kind="task.created", actor="system:callback",
                        payload={"target": target, "type": "receipt",
                                 "source_task": source_row["task_id"]})
    kind = "receipt"
    if target.startswith("port:"):
        prow = cur.execute("SELECT * FROM ports WHERE port_id=?",
                           (target[5:],)).fetchone()
        if prow and prow["handler"] == "external":
            kind = "webhook"
    delivery_id = svc.store.add_outbox(
        cur, semantic_key=semantic_key, kind=kind, target=target,
        task_id=receipt_id, payload=payload)
    return receipt_id


def send_webhook(svc, delivery_row) -> tuple[bool, str | None]:
    """Perform one webhook attempt. Returns (success, error)."""
    target = delivery_row["target"]
    prow = svc.store.conn.execute("SELECT * FROM ports WHERE port_id=?",
                                  (target[5:],)).fetchone() \
        if target.startswith("port:") else None
    if not prow or prow["handler"] != "external":
        return False, "目标不是external端口"
    chans = json.loads(prow["channel_ids"])
    if not chans:
        return False, "端口未绑定通道"
    ch = svc.store.conn.execute("SELECT * FROM channels WHERE channel_id=? AND "
                                "enabled=1", (chans[0],)).fetchone()
    if not ch:
        return False, "通道停用或不存在"
    endpoint = ch["endpoint"]
    import urllib.request
    import urllib.error
    body = json.dumps(dict(delivery_row["payload_json"],
                           delivery_id=delivery_row["delivery_id"]),
                      ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(endpoint, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if 200 <= resp.status < 300:
                # receipt delivery task done
                with svc.store.write() as cur:
                    cur.execute("UPDATE tasks SET state='done', ended_at=?, "
                                "result=?, updated_at=? WHERE task_id=? AND "
                                "state='queued'",
                                (svc.now(), json.dumps(
                                    {"text": "webhook投递成功", "ended_by": "system",
                                     "ended_at": svc.now()}), svc.now(),
                                 delivery_row["task_id"]))
                return True, None
            return False, f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        return False, str(e)[:200]


def process_outbox(svc) -> int:
    """One outbox pass: pending/retry due entries. Returns processed count."""
    now = svc.now()
    rows = svc.store.conn.execute(
        "SELECT * FROM outbox WHERE state IN ('pending','retry') AND "
        "(next_attempt_at IS NULL OR next_attempt_at<=?) ORDER BY created_at "
        "LIMIT 50", (now,)).fetchall()
    processed = 0
    pol = svc.policy()
    for row in rows:
        payload = json.loads(row["payload"])
        drow = {"delivery_id": row["delivery_id"], "task_id": row["task_id"],
                "payload_json": payload, "target": row["target"]}
        success, error = send_webhook(svc, drow) if row["kind"] == "webhook" \
            else (True, None)
        attempts = row["attempts"] + 1
        with svc.store.write() as cur:
            if success:
                cur.execute("UPDATE outbox SET state='sent', sent_at=?, attempts=? "
                            "WHERE delivery_id=?", (now, attempts,
                                                    row["delivery_id"]))
            else:
                retries = pol["outbox_retry"]
                max_attempts = pol["outbox_max_attempts"]
                if attempts >= max_attempts:
                    cur.execute("UPDATE outbox SET state='dead', attempts=?, "
                                "last_error=? WHERE delivery_id=?",
                                (attempts, error, row["delivery_id"]))
                else:
                    delay = retries[min(attempts - 1, len(retries) - 1)]
                    import datetime
                    from .timeutil import parse_rfc3339
                    nxt = format_rfc3339(parse_rfc3339(now)
                                         + datetime.timedelta(seconds=delay))
                    cur.execute("UPDATE outbox SET state='retry', attempts=?, "
                                "last_error=?, next_attempt_at=? WHERE delivery_id=?",
                                (attempts, error, nxt, row["delivery_id"]))
            svc.store.add_event(cur, entity_type="outbox",
                                entity_id=row["delivery_id"],
                                kind="delivery.attempted",
                                payload={"success": success, "error": error,
                                         "attempts": attempts})
        processed += 1
    return processed
