"""Agent retire plan per DESIGN §13.6/§15.1: blockers, dispositions, callback
handover. Not deleting workspaces; unfinished responsibilities keep retiring."""
from __future__ import annotations

import json

from .errors import HiveError
from .ids import new_id
from .timeutil import format_rfc3339, now_utc


def _collect_blockers(svc, agent_id: str) -> list[dict]:
    blockers = []
    for r in svc.store.conn.execute(
            "SELECT task_id, type, state, title FROM tasks WHERE "
            "(owner_subject=? AND state='claimed') OR (target=? AND state IN "
            "('queued','claimed')) AND type != 'receipt'",
            (agent_id, f"agent:{agent_id}")).fetchall():
        blockers.append({"task_id": r["task_id"], "state": r["state"],
                         "type": r["type"], "title": r["title"],
                         "why": "在途责任/个人待办"})
    for r in svc.store.conn.execute(
            "SELECT DISTINCT t.task_id, t.state, t.title FROM tasks t WHERE "
            "json_extract(t.callback,'$.target')=? AND t.state IN "
            "('queued','claimed')", (f"agent:{agent_id}",)).fetchall():
        blockers.append({"task_id": r["task_id"], "state": r["state"],
                         "title": r["title"], "why": "该Agent为已登记回调目标"})
    return blockers


def retire_agent(svc, subject, args, rid) -> dict:
    from .agents import get_agent
    svc.authz.require(subject, "agent.retire", f"agent:{args['agent']}")
    row = get_agent(svc, args["agent"])
    plan = args.get("plan") or {}
    if not isinstance(plan, dict) or not plan.get("reason"):
        raise HiveError("REQUIRED_ARGUMENT", "RetirePlan 需要 reason")
    allowed = {"reason", "stop", "work", "personal", "callbacks"}
    unknown = set(plan) - allowed
    if unknown:
        raise HiveError("SCHEMA_UNSUPPORTED", f"RetirePlan 未知字段: {sorted(unknown)}")
    blockers_before = _collect_blockers(svc, args["agent"])
    covered = {i.get("task_id") for i in plan.get("work", []) if isinstance(i, dict)}
    covered |= {i.get("task_id") for i in plan.get("personal", [])
                if isinstance(i, dict)}
    uncovered = [b for b in blockers_before if b["task_id"] not in covered]
    has_callback_plan = bool(plan.get("callbacks"))
    cb_blockers = [b for b in blockers_before if "回调目标" in b["why"]]
    if not has_callback_plan and cb_blockers:
        uncovered.extend(cb_blockers)
    if args.get("dry_run"):
        return {"operation_id": None, "blockers": uncovered,
                "dispositions": plan.get("work", []) + plan.get("personal", []),
                "dry_run": True}
    # stop instance if requested
    stop_result = None
    if plan.get("stop", True):
        from .runtime_ops import op_stop_agent
        try:
            stop_result = op_stop_agent(svc, subject, args["agent"],
                                        plan.get("reason", ""))
        except HiveError as e:
            stop_result = {"error": e.message}
    dispositions = []
    with svc.store.write() as cur:
        now = svc.now()
        for item in plan.get("work", []) + plan.get("personal", []):
            tid = item.get("task_id")
            action = item.get("action")
            trow = cur.execute("SELECT * FROM tasks WHERE task_id=?",
                               (tid,)).fetchone()
            if not trow or trow["state"] in ("done", "fail", "trash"):
                dispositions.append({"task_id": tid, "action": action,
                                     "result": "already-terminal/missing"})
                continue
            if action == "forward":
                cur.execute(
                    "UPDATE tasks SET state='queued', owner_subject=NULL, "
                    "claim_id=NULL, claim_epoch=claim_epoch+1, target=?, "
                    "version=version+1, updated_at=? WHERE task_id=?",
                    (item.get("target", trow["target"]), now, tid))
                if trow["claim_id"]:
                    cur.execute(
                        "UPDATE task_claims SET invalidated_at=?, "
                        "invalidated_reason='retire-forward' WHERE claim_id=? AND "
                        "invalidated_at IS NULL", (now, trow["claim_id"]))
                dispositions.append({"task_id": tid, "action": "forward",
                                     "target": item.get("target")})
            elif action in ("fail", "trash"):
                cur.execute(
                    "UPDATE tasks SET state=?, ended_at=?, result=?, version="
                    "version+1, updated_at=? WHERE task_id=?",
                    (action, now, json.dumps({
                        "reason": item.get("reason", "retire"),
                        "reason_code": "RETIRE_" + action.upper(),
                        "ended_by": subject.ref, "ended_at": now,
                    }, ensure_ascii=False), now, tid))
                if trow["claim_id"]:
                    cur.execute(
                        "UPDATE task_claims SET invalidated_at=?, "
                        "invalidated_reason=? WHERE claim_id=? AND invalidated_at "
                        "IS NULL", (now, "retire-" + action, trow["claim_id"]))
                dispositions.append({"task_id": tid, "action": action})
            else:
                dispositions.append({"task_id": tid, "action": action,
                                     "result": "unsupported"})
        # callback handover
        cb = plan.get("callbacks")
        if isinstance(cb, dict) and cb.get("action") == "redirect" and cb.get("target"):
            new_target = cb["target"]
            for r in cur.execute(
                    "SELECT task_id, callback FROM tasks WHERE "
                    "json_extract(callback,'$.target')=? AND state IN "
                    "('queued','claimed')",
                    (f"agent:{args['agent']}",)).fetchall():
                try:
                    cdata = json.loads(r["callback"])
                except ValueError:
                    continue
                if cdata and cdata.get("target") == f"agent:{args['agent']}":
                    cdata = {"target": new_target,
                             "pending_handover": {
                                 "from": f"agent:{args['agent']}",
                                 "reason": plan.get("reason")}}
                    cur.execute("UPDATE tasks SET callback=? WHERE task_id=?",
                                (json.dumps(cdata), r["task_id"]))
                    cur.execute(
                        "INSERT OR IGNORE INTO audit_log(audit_id,actor,action,"
                        "target,request_id,reason,details,created_at) VALUES ("
                        "?,?,?,?,?,?,?,?)",
                        (new_id("audit"), subject.ref, "callback.redirect",
                         r["task_id"], rid, plan.get("reason"),
                         json.dumps({"to": new_target}), now))
        svc.store.audit(cur, subject.ref, "agent.retire", f"agent:{args['agent']}",
                        args["reason"], rid, {"dispositions": dispositions})
    remaining = _collect_blockers(svc, args["agent"])
    new_state = "retired" if not remaining else "retiring"
    with svc.store.write() as cur:
        cur.execute("UPDATE agents SET lifecycle=?, retired_at=CASE WHEN ?="
                    "'retired' THEN ? ELSE retired_at END, updated_at=? WHERE "
                    "agent_id=?", (new_state, new_state, svc.now(), svc.now(),
                                   args["agent"]))
        svc.store.add_event(cur, entity_type="agent", entity_id=args["agent"],
                            kind=f"agent.{new_state}", actor=subject.ref)
    return {"operation_id": None, "blockers": remaining,
            "dispositions": dispositions, "state": new_state,
            "stop_result": stop_result}
