"""Wait registry and evaluation per DESIGN §8.1 / §19.4."""
from __future__ import annotations

import json

from .errors import HiveError
from .ids import new_id
from .timeutil import format_rfc3339, now_utc, parse_duration, parse_rfc3339


def _dep_status(svc, dependency: str) -> dict | None:
    """Result of a task: or operation: dependency; None when not yet returned."""
    kind, _, ident = dependency.partition(":")
    if kind == "task":
        r = svc.store.conn.execute(
            "SELECT state, result, ended_at FROM tasks WHERE task_id=? AND "
            "state IN ('done','fail','trash')", (ident,)).fetchone()
        if not r:
            return None
        return {"kind": "task", "id": ident, "state": r["state"],
                "result": json.loads(r["result"]) if r["result"] else None,
                "at": r["ended_at"]}
    if kind == "operation":
        r = svc.store.conn.execute(
            "SELECT state, result, ended_at FROM operations WHERE operation_id=? "
            "AND state IN ('succeeded','failed','cancelled')",
            (ident,)).fetchone()
        if not r:
            return None
        return {"kind": "operation", "id": ident, "state": r["state"],
                "result": json.loads(r["result"]) if r["result"] else None,
                "at": r["ended_at"]}
    raise HiveError("INVALID_DEPENDENCY", f"依赖引用不合法: {dependency}")


def create_wait(svc, subject, task_id: str, deps: list[str], mode: str,
                wait_for=None, until=None, checkpoint_refs=None, replace=False,
                claim_id=None):
    from .tasks import get_task, task_card
    row = get_task(svc, task_id)
    if row["state"] in ("done", "fail", "trash"):
        raise HiveError("TASK_TERMINAL", "终态任务不能登记等待")
    if row["owner_subject"] != subject.subject_id and \
            subject.subject_type not in ("owner", "system"):
        raise HiveError("FORBIDDEN", "只能为当前持有的责任任务登记等待")
    if claim_id:
        # DESIGN CMD-006: --claim 是"当前实例领取缓存"，提供时必须是本任
        # 务上调用者的有效领取凭据（RDEF-06: 无效凭据曾被静默接受）
        c = svc.store.conn.execute(
            "SELECT 1 FROM task_claims WHERE claim_id=? AND task_id=? AND "
            "subject_id=? AND invalidated_at IS NULL",
            (claim_id, task_id, subject.subject_id)).fetchone()
        if not c or row["state"] != "claimed":
            raise HiveError("CLAIM_FENCED", "领取凭据无效或已不属于当前领取")
    if len(deps) < 1 or len(deps) > 100:
        raise HiveError("INVALID_ARGUMENT", "依赖数量须为 1..100")
    if mode not in ("all", "any"):
        raise HiveError("INVALID_ARGUMENT", "mode 只能是 all/any")
    # dependency visibility + cycle check
    for d in deps:
        if not d.startswith(("task:", "operation:")):
            raise HiveError("INVALID_DEPENDENCY",
                            f"依赖必须为 task:ID 或 operation:ID: {d}")
        _, _, ident = d.partition(":")
        if d == f"task:{task_id}":
            raise HiveError("WAIT_CYCLE", "不能等待任务自身")
        if d.startswith("task:"):
            from .tasks import require_task_visible
            require_task_visible(svc, subject, ident)
    _check_wait_cycle(svc, task_id, deps)
    # deadline
    now = svc.now()
    deadline = None
    if wait_for is not None and wait_for != "none":
        deadline = format_rfc3339(
            parse_rfc3339(now) + __import__("datetime").timedelta(
                seconds=parse_duration(wait_for)))
    elif until:
        deadline = format_rfc3339(parse_rfc3339(until))
    wait_id = new_id("wait")
    with svc.store.write() as cur:
        existing = cur.execute(
            "SELECT * FROM waits WHERE owner_task_id=? AND state='pending'",
            (task_id,)).fetchone()
        if existing and not replace:
            raise HiveError("WAIT_EXISTS",
                            "已有pending等待；用 --replace 替换",
                            {"wait_id": existing["wait_id"]})
        if existing and replace:
            cur.execute("UPDATE waits SET state='cancelled', resolved_at=? "
                        "WHERE wait_id=? AND state='pending'",
                        (now, existing["wait_id"]))
        cur.execute(
            "INSERT INTO waits(wait_id,owner_task_id,mode,state,deadline,"
            "checkpoint_refs,created_by,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (wait_id, task_id, mode, "pending", deadline,
             json.dumps(checkpoint_refs or []), subject.ref, now))
        for d in deps:
            cur.execute(
                "INSERT OR IGNORE INTO wait_dependencies(wait_id,dependency) "
                "VALUES (?,?)", (wait_id, d))
        svc.store.add_event(cur, entity_type="wait", entity_id=wait_id,
                            kind="wait.created", actor=subject.ref,
                            payload={"task": task_id, "deps": deps, "mode": mode})
        # evaluate immediately: results may have arrived already
        ready = _evaluate_wait(svc, cur, wait_id, now)
    dep_results = [_dep_status(svc, d) or {"kind": d.split(":")[0],
                                           "id": d.split(":", 1)[1],
                                           "state": "pending"} for d in deps]
    wrow = svc.store.conn.execute("SELECT * FROM waits WHERE wait_id=?",
                                  (wait_id,)).fetchone()
    return {"wait": _wait_card(wrow), "dependency_results": dep_results,
            "ready": bool(wrow["state"] == "ready")}


def _check_wait_cycle(svc, owner_task: str, new_deps: list[str]):
    """waiter→dependency graph cycle detection (independent of blood tree)."""
    graph: dict[str, list[str]] = {}
    for r in svc.store.conn.execute(
            "SELECT w.owner_task_id, wd.dependency FROM waits w JOIN "
            "wait_dependencies wd ON wd.wait_id=w.wait_id WHERE w.state='pending'"):
        graph.setdefault(r["owner_task_id"], []).append(r["dependency"])
    graph.setdefault(owner_task, []).extend(
        d for d in new_deps if d not in graph.get(owner_task, []))
    # DFS from owner_task following task: deps
    seen: set[str] = set()
    stack = [owner_task]
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        for dep in graph.get(node, []):
            if not dep.startswith("task:"):
                continue
            dep_task = dep[5:]
            if dep_task == owner_task:
                raise HiveError("WAIT_CYCLE", "等待关系将构成环")
            stack.append(dep_task)


def _wait_card(r) -> dict:
    return {"wait_id": r["wait_id"], "owner_task_id": r["owner_task_id"],
            "mode": r["mode"], "state": r["state"], "deadline": r["deadline"],
            "created_at": r["created_at"], "resolved_at": r["resolved_at"]}


def _evaluate_wait(svc, cur, wait_id: str, now: str) -> bool:
    """Recompute one wait from the ledger; create resume notice when resolved."""
    w = cur.execute("SELECT * FROM waits WHERE wait_id=?", (wait_id,)).fetchone()
    if not w or w["state"] != "pending":
        return False
    deps = [r["dependency"] for r in cur.execute(
        "SELECT dependency FROM wait_dependencies WHERE wait_id=?", (wait_id,))]
    results = {}
    for d in deps:
        kind, _, ident = d.partition(":")
        if kind == "task":
            r = cur.execute("SELECT state, result, ended_at FROM tasks WHERE "
                            "task_id=? AND state IN ('done','fail','trash')",
                            (ident,)).fetchone()
        else:
            r = cur.execute("SELECT state AS state, result, ended_at FROM operations "
                            "WHERE operation_id=? AND state IN "
                            "('succeeded','failed','cancelled')", (ident,)).fetchone()
        results[d] = r
        cur.execute("UPDATE wait_dependencies SET result=? WHERE wait_id=? AND "
                    "dependency=?",
                    (json.dumps({"state": r["state"],
                                 "result": json.loads(r["result"])
                                 if r and r["result"] else None},
                                ensure_ascii=False), wait_id, d)) if r else None
    satisfied = [d for d, r in results.items() if r]
    ready = len(satisfied) == len(deps) if w["mode"] == "all" else bool(satisfied)
    timed_out = w["deadline"] and now >= w["deadline"]
    if not ready and not timed_out:
        return False
    new_state = "ready" if ready else "timed_out"
    cur.execute("UPDATE waits SET state=?, resolved_at=? WHERE wait_id=?",
                (new_state, now, wait_id))
    svc.store.add_event(cur, entity_type="wait", entity_id=wait_id,
                        kind=f"wait.{new_state}",
                        payload={"task": w["owner_task_id"]})
    # wait-resume notice to the CURRENT owner of the owner task
    trow = cur.execute("SELECT owner_subject FROM tasks WHERE task_id=?",
                       (w["owner_task_id"],)).fetchone()
    if trow and trow["owner_subject"] and not svc.consume_fault("notification_drop"):
        agent_subject = trow["owner_subject"]
        notice_id = new_id("task")
        from .tasks import insert_task
        insert_task(cur, task_id=notice_id, parent_id=w["owner_task_id"],
                    root_id=w["owner_task_id"], type="notice",
                    title=f"等待已{('就绪' if new_state=='ready' else '超时')}",
                    summary=f"wait {wait_id} -> {new_state}", body="",
                    data={"wait_id": wait_id, "wait_state": new_state},
                    source={"subject": "system:wait", "type": "system"},
                    target=f"agent:{agent_subject}", reply_to=wait_id,
                    refs=[f"wtt:{wait_id}"], created_at=now, updated_at=now)
        svc.store.add_event(cur, entity_type="task", entity_id=notice_id,
                            kind="task.created",
                            payload={"target": f"agent:{agent_subject}",
                                     "type": "notice", "reason": "wait.resume"})
    return new_state == "ready"


def advance_waits(svc, cur, changed_task_id: str):
    """After a task terminal event: recompute every pending wait that depends
    on this task. Runs inside the caller's transaction."""
    wait_ids = [r["wait_id"] for r in cur.execute(
        "SELECT DISTINCT wait_id FROM wait_dependencies WHERE dependency=?",
        (f"task:{changed_task_id}",))]
    now = svc.now()
    for wid in wait_ids:
        _evaluate_wait(svc, cur, wid, now)


def advance_operation_waits(svc, cur, operation_id: str):
    wait_ids = [r["wait_id"] for r in cur.execute(
        "SELECT DISTINCT wait_id FROM wait_dependencies WHERE dependency=?",
        (f"operation:{operation_id}",))]
    now = svc.now()
    for wid in wait_ids:
        _evaluate_wait(svc, cur, wid, now)


def cancel_waits_for_task(svc, cur, task_id: str, now: str, note: str = ""):
    rows = cur.execute("SELECT wait_id FROM waits WHERE owner_task_id=? AND "
                       "state='pending'", (task_id,)).fetchall()
    for r in rows:
        cur.execute("UPDATE waits SET state='cancelled', resolved_at=? "
                    "WHERE wait_id=?", (now, r["wait_id"]))
        svc.store.add_event(cur, entity_type="wait", entity_id=r["wait_id"],
                            kind="wait.cancelled", payload={"note": note})


def scan_wait_deadlines(svc, cur, now: str) -> int:
    """Deadline scan: pending waits past deadline -> timed_out."""
    rows = cur.execute("SELECT wait_id FROM waits WHERE state='pending' AND "
                       "deadline IS NOT NULL AND deadline<=?", (now,)).fetchall()
    n = 0
    for r in rows:
        if _evaluate_wait(svc, cur, r["wait_id"], now):
            n += 1
    return n


def waits_summary(svc, task_id: str) -> list[dict]:
    rows = svc.store.conn.execute(
        "SELECT * FROM waits WHERE owner_task_id=? ORDER BY created_at",
        (task_id,)).fetchall()
    return [_wait_card(r) for r in rows]
