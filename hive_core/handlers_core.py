"""Handlers: control system / agent / port / subscription / permission /
operation / event / audit / delivery / harness."""
from __future__ import annotations

import json
import os

from .errors import HiveError
from .ids import new_id
from .timeutil import format_rfc3339, now_utc


def register(svc):
    svc.register("control.system.stop", h_system_stop)
    svc.register("control.system.status", h_system_status)
    svc.register("control.system.config", h_system_config)
    svc.register("control.system.log", h_system_log)
    svc.register("control.system.backup", h_system_backup)
    for cmd in ("control.system.restore",):
        svc.register(cmd, _not_implemented(cmd))
    # agents
    svc.register("control.agent.list", h_agent_list)
    svc.register("control.agent.show", h_agent_show)
    svc.register("control.agent.create", h_agent_create)
    svc.register("control.agent.clone", h_agent_clone)
    svc.register("control.agent.update", h_agent_update)
    svc.register("control.agent.start", h_agent_start)
    svc.register("control.agent.wake", h_agent_wake)
    svc.register("control.agent.send", h_agent_send)
    svc.register("control.agent.stop", h_agent_stop)
    svc.register("control.agent.restart", h_agent_restart)
    svc.register("control.agent.model", h_agent_model)
    svc.register("control.agent.retire", h_agent_retire)
    # ports
    svc.register("control.port.list", h_port_list)
    svc.register("control.port.show", h_port_show)
    svc.register("control.port.create", h_port_create)
    svc.register("control.port.update", h_port_update)
    svc.register("control.port.pause", h_port_pause)
    svc.register("control.port.resume", h_port_resume)
    svc.register("control.port.retire", h_port_retire)
    # subscriptions
    svc.register("control.subscription.list", h_subscription_list)
    svc.register("control.subscription.add", h_subscription_add)
    svc.register("control.subscription.pause", h_subscription_pause)
    svc.register("control.subscription.resume", h_subscription_resume)
    svc.register("control.subscription.remove", h_subscription_remove)
    # tasks (control view)
    svc.register("control.task.list", h_task_list)
    svc.register("control.task.show", h_task_show)
    svc.register("control.task.amend", h_task_amend)
    svc.register("control.task.reclaim", h_task_reclaim)
    svc.register("control.task.forward", h_task_forward)
    svc.register("control.task.trash", h_task_trash)
    # permission
    svc.register("control.permission.list", h_permission_list)
    svc.register("control.permission.grant", h_permission_grant)
    svc.register("control.permission.revoke", h_permission_revoke)
    # operation/event/audit/delivery
    svc.register("control.operation.list", h_operation_list)
    svc.register("control.operation.show", h_operation_show)
    svc.register("control.operation.cancel", h_operation_cancel)
    svc.register("control.event.list", h_event_list)
    svc.register("control.audit.list", h_audit_list)
    svc.register("control.delivery.list", h_delivery_list)
    svc.register("control.delivery.retry", h_delivery_retry)
    # harness
    svc.register("control.harness.list", h_harness_list)
    svc.register("control.harness.show", h_harness_show)
    svc.register("control.harness.register", h_harness_register)
    svc.register("control.harness.validate", h_harness_validate)
    svc.register("control.harness.unregister", h_harness_unregister)


def _not_implemented(cmd):
    def h(svc, subject, args, rid):
        raise HiveError("NOT_IMPLEMENTED", f"命令尚未实现: {cmd}")
    return h


# ---------------- system ----------------
def h_system_stop(svc, subject, args, rid):
    svc.authz.require(subject, "system.admin")
    with svc.store.write() as cur:
        svc.store.audit(cur, subject.ref, "system.stop", "system", args["reason"],
                        rid)
        cur.execute("UPDATE outbox SET state='pending' WHERE state='sending'")
    svc.logger.info("system.stop.requested", actor=subject.ref)
    data = {"stopping": True, "note": "先提交当前事务并停止接受变更；受管Agent实例默认不停"}
    svc.shutdown.set()
    return data


def h_system_status(svc, subject, args, rid):
    svc.authz.require(subject, "system.observe")
    return svc.status()


def h_system_config(svc, subject, args, rid):
    if "input" not in args or args["input"] is None:
        svc.authz.require(subject, "system.observe")
        return {"effective_config": svc.policy(), "revision": svc.policy_revision(),
                "pending_fields": []}
    svc.authz.require(subject, "system.admin")
    if not (args.get("reason") or "").strip():
        raise HiveError("REQUIRED_ARGUMENT", "更新时 --reason 必填")
    patch = args["input"]
    if not isinstance(patch, dict):
        raise HiveError("INVALID_ARGUMENT", "配置补丁必须是对象")
    forbidden = {"home", "database", "db_path", "credentials", "listen", "auth"}
    hit = forbidden & set(patch)
    if hit:
        raise HiveError("INVALID_ARGUMENT",
                        f"不允许在线修改: {sorted(hit)}")
    pol = svc.policy()
    for k, v in patch.items():
        if k not in svc.policy():
            raise HiveError("SCHEMA_UNSUPPORTED", f"未知策略键 {k}")
        pol[k] = v
    rev = svc.policy_revision() + 1
    with svc.store.write() as cur:
        svc.store.kv_set("policy", pol)
        svc.store.kv_set("policy_revision", rev)
        svc.store.audit(cur, subject.ref, "system.config", "system", args["reason"],
                        rid, {"revision": rev, "patch": patch})
    pending = []
    for k in ("reconcile_interval", "outbox_retry"):
        if k in patch:
            pending.append(k)
    return {"effective_config": pol, "revision": rev, "pending_fields": pending}


def h_system_log(svc, subject, args, rid):
    svc.authz.require(subject, "system.observe")
    entries = svc.logger.tail(n=args["tail"], level=args.get("level"),
                              since=args.get("since"))
    return {"entries": entries, "truncated": len(entries) >= args["tail"]}


def h_system_backup(svc, subject, args, rid):
    svc.authz.require(subject, "system.admin")
    from .backup import create_backup
    return create_backup(svc, out=args["out"],
                         include_workspaces=bool(args.get("include_workspaces")),
                         include_credentials=bool(args.get("include_credentials")),
                         actor=subject.ref, reason="", request_id=rid)


# ---------------- agents ----------------
def h_agent_list(svc, subject, args, rid):
    svc.authz.require(subject, "agent.discover")
    rows = svc.store.conn.execute(
        "SELECT * FROM agents ORDER BY agent_id").fetchall()
    items = []
    q = (args.get("query") or "").lower()
    for r in rows:
        cfg = _agent_config_dict(svc, r["agent_id"], r["current_revision"])
        if args.get("state") and r["lifecycle"] != args["state"]:
            continue
        if args.get("harness") and (cfg.get("runtime", {}).get("harness") or "") \
                != args["harness"]:
            continue
        if q and q not in (r["name"] or "").lower() and q not in (
                cfg.get("description") or "").lower():
            continue
        if args.get("port"):
            sub = svc.store.conn.execute(
                "SELECT 1 FROM subscriptions WHERE agent_id=? AND port_id=?",
                (r["agent_id"], _resolve_port_id(svc, args["port"]))).fetchone()
            if not sub:
                continue
        items.append({"agent_id": r["agent_id"], "name": r["name"],
                      "lifecycle": r["lifecycle"],
                      "description": cfg.get("description", ""),
                      "harness": cfg.get("runtime", {}).get("harness")})
    return _paginate(items, args)


def _agent_config_dict(svc, agent_id: str, revision: int | None = None) -> dict:
    row = svc.store.conn.execute(
        "SELECT a.file_path, a.revision FROM agent_config_versions a "
        "WHERE a.agent_id=? AND a.published=1 "
        "ORDER BY (a.revision = ?) DESC, a.revision DESC LIMIT 1",
        (agent_id, revision or 0)).fetchone() if revision else \
        svc.store.conn.execute(
            "SELECT file_path, revision FROM agent_config_versions "
            "WHERE agent_id=? AND published=1 ORDER BY revision DESC LIMIT 1",
            (agent_id,)).fetchone()
    if not row:
        return {}
    with open(row["file_path"], "r", encoding="utf-8") as f:
        return json.load(f)


def _resolve_port_id(svc, ref: str) -> str:
    row = svc.store.conn.execute(
        "SELECT port_id FROM ports WHERE port_id=? OR route=?",
        (ref, ref.lstrip("port:"))).fetchone()
    if not row:
        raise HiveError("NOT_FOUND", f"端口不存在: {ref}")
    return row["port_id"]


def h_agent_show(svc, subject, args, rid):
    from .agents import agent_runtime_summary, agent_card
    row = svc.store.conn.execute("SELECT * FROM agents WHERE agent_id=?",
                                 (args["agent"],)).fetchone()
    if not row:
        raise HiveError("NOT_FOUND", f"Agent不存在: {args['agent']}")
    svc.authz.require(subject, "agent.read", f"agent:{row['agent_id']}")
    data = {"agent": agent_card(row)}
    if args.get("config"):
        svc.authz.require(subject, "agent.read", f"agent:{row['agent_id']}")
        cfg = _agent_config_dict(svc, row["agent_id"])
        cfg["revision"] = row["current_revision"]
        data["config"] = cfg
        from .runtime_ops import ensure_workspace
        ensure_workspace(svc, row["agent_id"], cfg)
    if args.get("runtime"):
        data["runtime"] = agent_runtime_summary(svc, row["agent_id"])
    return data


def h_agent_create(svc, subject, args, rid):
    from .agents import create_agent
    return create_agent(svc, subject, args, rid)


def h_agent_clone(svc, subject, args, rid):
    from .agents import clone_agent
    return clone_agent(svc, subject, args, rid)


def h_agent_update(svc, subject, args, rid):
    from .agents import update_agent
    return update_agent(svc, subject, args, rid)


def h_agent_start(svc, subject, args, rid):
    from .agents import get_agent
    get_agent(svc, args["agent"])
    svc.authz.require(subject, "agent.start", f"agent:{args['agent']}")
    from .runtime_ops import op_start_agent
    return op_start_agent(svc, subject, args["agent"], args.get("callback"),
                          rid=rid)


def h_agent_wake(svc, subject, args, rid):
    from .agents import get_agent
    get_agent(svc, args["agent"])
    svc.authz.require(subject, "agent.start", f"agent:{args['agent']}")
    from .runtime_ops import op_wake_agent
    return op_wake_agent(svc, subject, args["agent"], args.get("message"))


def h_agent_send(svc, subject, args, rid):
    from .runtime_ops import op_send_agent
    return op_send_agent(svc, subject, args["agent"], args, rid)


def h_agent_stop(svc, subject, args, rid):
    from .agents import get_agent
    get_agent(svc, args["agent"])
    svc.authz.require(subject, "agent.stop", f"agent:{args['agent']}")
    from .runtime_ops import op_stop_agent
    return op_stop_agent(svc, subject, args["agent"], args["reason"],
                         grace=args.get("grace"), force=bool(args.get("force")),
                         instance_expected=args.get("instance"), rid=rid)


def h_agent_restart(svc, subject, args, rid):
    from .agents import get_agent
    get_agent(svc, args["agent"])
    svc.authz.require(subject, "agent.restart", f"agent:{args['agent']}")
    from .runtime_ops import op_restart_agent
    return op_restart_agent(svc, subject, args["agent"], args["reason"],
                            grace=args.get("grace"), force=bool(args.get("force")),
                            instance_expected=args.get("instance"), rid=rid)


def h_agent_model(svc, subject, args, rid):
    from .runtime_ops import op_model_agent
    return op_model_agent(svc, subject, args["agent"], args, rid)


def h_agent_retire(svc, subject, args, rid):
    from .retire import retire_agent
    return retire_agent(svc, subject, args, rid)


# ---------------- subscriptions ----------------
def _subscription_change(svc, subject, args, action: str):
    agent_id = args["agent"]
    port_id = _resolve_port_id(svc, args["port"])
    svc.authz.require(subject, "subscription.manage",
                      f"agent:{agent_id}")
    from .agents import get_agent_config, get_agent, _publish_config
    row = get_agent(svc, agent_id)
    prow = svc.store.conn.execute("SELECT handler FROM ports WHERE port_id=?",
                                  (port_id,)).fetchone()
    if not prow or prow["handler"] != "agents":
        raise HiveError("INVALID_ARGUMENT", "仅 handler=agents 端口可订阅")
    cfg = get_agent_config(svc, agent_id)
    subs = [dict(s) for s in cfg.get("subscriptions", [])]
    found = False
    for s in subs:
        pid = s.get("port_id") or s.get("port")
        prow2 = svc.store.conn.execute(
            "SELECT port_id FROM ports WHERE port_id=? OR route=?",
            (pid, str(pid).lstrip("port:"))).fetchone()
        if prow2 and prow2["port_id"] == port_id:
            found = True
            if action == "remove":
                subs.remove(s)
            else:
                s["enabled"] = action != "pause"
    if not found and action in ("add",):
        subs.append({"port_id": port_id, "enabled": True})
    if not found and action in ("pause", "resume", "remove"):
        raise HiveError("NOT_FOUND", "订阅连接不存在")
    cfg["subscriptions"] = subs
    with svc.store.write() as cur:
        revision = _publish_config(svc, cur, agent_id, cfg,
                                   row["current_revision"], subject.ref,
                                   args["reason"])
    enabled = True
    for s in subs:
        pid = s.get("port_id")
        prow2 = svc.store.conn.execute(
            "SELECT port_id FROM ports WHERE port_id=? OR route=?",
            (pid, str(pid).lstrip("port:"))).fetchone()
        if prow2 and prow2["port_id"] == port_id:
            enabled = bool(s.get("enabled", True))
    return {"subscription": {"agent_id": agent_id, "port_id": port_id,
                             "enabled": enabled if action != "remove" else False},
            "agent_revision": revision}


def h_subscription_add(svc, subject, args, rid):
    return _subscription_change(svc, subject, args, "add")


def h_subscription_pause(svc, subject, args, rid):
    return _subscription_change(svc, subject, args, "pause")


def h_subscription_resume(svc, subject, args, rid):
    return _subscription_change(svc, subject, args, "resume")


def h_subscription_remove(svc, subject, args, rid):
    return _subscription_change(svc, subject, args, "remove")


# ---------------- task admin ----------------
def h_task_amend(svc, subject, args, rid):
    from .tasks import get_task, task_card, can_read_task
    svc.authz.require(subject, "task.manage")
    row = get_task(svc, args["task"])
    if row["state"] in ("done", "fail", "trash"):
        raise HiveError("TASK_TERMINAL", "终态任务不能修改要求")
    if not can_read_task(svc, subject, row):
        raise HiveError("NOT_VISIBLE", "任务不可见")
    if args.get("if_version") is not None and args["if_version"] != row["version"]:
        raise HiveError("VERSION_CONFLICT", "任务版本冲突")
    patch = args.get("input")
    allowed = {"title", "summary", "body", "data", "attachments", "urgency",
               "claim_deadline", "expires_at", "callback"}
    unknown = set(patch) - allowed
    if unknown:
        raise HiveError("SCHEMA_UNSUPPORTED", f"TaskAmend 未知字段: {sorted(unknown)}")
    changes = {}
    spec_bump = False
    spec_fields = ("title", "summary", "body", "data", "attachments", "urgency")
    for k, v in patch.items():
        if k == "callback":
            from .tasks import resolve_callback
            cb = resolve_callback(svc, subject, v) if v not in (None, "none") \
                else None
            changes["callback"] = json.dumps(cb) if cb else None
            continue
        if k in ("claim_deadline", "expires_at") and v is not None:
            from .timeutil import parse_rfc3339, format_rfc3339 as fmt
            v = fmt(parse_rfc3339(v))
        if k in spec_fields and v is not None:
            spec_bump = True
        if isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False)
        changes[k] = v
    now = svc.now()
    with svc.store.write() as cur:
        sets = ", ".join(f"{k}=?" for k in changes)
        params = list(changes.values())
        new_version = row["version"] + 1
        new_spec = row["spec_version"] + (1 if spec_bump else 0)
        cur.execute(
            f"UPDATE tasks SET {sets}, version=?, spec_version=?, updated_at=? "
            f"WHERE task_id=?", (*params, new_version, new_spec, now,
                                 args["task"]))
        # spec change clears old decline suppression
        if spec_bump:
            cur.execute("DELETE FROM task_declines WHERE task_id=?",
                        (args["task"],))
        svc.store.add_event(cur, entity_type="task", entity_id=args["task"],
                            entity_version=new_version, kind="task.amended",
                            actor=subject.ref,
                            payload={"spec_version": new_spec})
        svc.store.audit(cur, subject.ref, "task.amend", args["task"],
                        args["reason"], rid)
        if spec_bump and row["owner_subject"]:
            cur.execute(
                "INSERT OR IGNORE INTO notifications(notify_id,agent_id,dedupe_key,"
                "message,state,created_at) VALUES (?,?,?,'任务要求已变化',"
                "'pending',?)",
                (new_id("view"), row["owner_subject"],
                 f"amend:{args['task']}:{new_spec}", now))
    return {"task": task_card(get_task(svc, args["task"])), "version": new_version,
            "spec_version": new_spec}


def h_task_reclaim(svc, subject, args, rid):
    from .tasks import get_task, task_card, resolve_target
    svc.authz.require(subject, "task.manage")
    row = get_task(svc, args["task"])
    if row["state"] in ("done", "fail", "trash"):
        raise HiveError("TASK_TERMINAL", "终态任务不能回收")
    if row["state"] != "claimed":
        raise HiveError("INVALID_ARGUMENT", "只有claimed任务需要reclaim")
    if args.get("if_version") is not None and args["if_version"] != row["version"]:
        raise HiveError("VERSION_CONFLICT", "任务版本冲突")
    norm_target, info = resolve_target(svc, args["to"])
    now = svc.now()
    port_defaults = json.loads(info["port"]["defaults"]) \
        if info["kind"] == "port" else {}
    deadline = None
    dw = port_defaults.get("claim_within")
    if dw:
        from .timeutil import parse_duration, parse_rfc3339
        import datetime
        deadline = format_rfc3339(
            parse_rfc3339(now) + datetime.timedelta(seconds=parse_duration(dw)))
    with svc.store.write() as cur:
        cur.execute("UPDATE tasks SET state='queued', owner_subject=NULL, "
                    "claim_id=NULL, claim_epoch=claim_epoch+1, target=?, "
                    "claim_deadline=?, version=version+1, updated_at=? WHERE "
                    "task_id=?", (norm_target, deadline, now, args["task"]))
        cur.execute("UPDATE task_claims SET invalidated_at=?, invalidated_reason="
                    "'reclaimed' WHERE claim_id=? AND invalidated_at IS NULL",
                    (now, row["claim_id"]))
        svc.store.audit(cur, subject.ref, "task.reclaim", args["task"],
                        args["reason"], rid,
                        {"stop_owner": bool(args.get("stop_owner"))})
        svc.store.add_event(cur, entity_type="task", entity_id=args["task"],
                            kind="task.reclaimed", actor=subject.ref,
                            payload={"to": norm_target})
        if not args.get("stop_owner") and row["owner_subject"]:
            cur.execute(
                "INSERT OR IGNORE INTO notifications(notify_id,agent_id,dedupe_key,"
                "message,state,created_at) VALUES (?,?,?,'任务已被行政回收，请停"
                "止该项工作','pending',?)",
                (new_id("view"), row["owner_subject"],
                 f"reclaim:{args['task']}", now))
    if args.get("stop_owner"):
        from .runtime_ops import op_stop_agent
        try:
            op_stop_agent(svc, subject, row["owner_subject"], args["reason"])
        except HiveError:
            pass  # owner agent may have no instance; audit already notes scope
    return {"operation_id": None, "task": task_card(get_task(svc, args["task"])),
            "invalidated_claim": row["claim_id"]}


def h_task_forward(svc, subject, args, rid):
    from .tasks import forward_task
    svc.authz.require(subject, "task.manage")
    return forward_task(svc, subject, args["task"], args["to"], args["reason"],
                        if_version=args.get("if_version"), admin=True)


def h_task_trash(svc, subject, args, rid):
    from .tasks import get_task, settle_task, task_card, can_read_task
    svc.authz.require(subject, "task.manage")
    row = get_task(svc, args["task"])
    if not can_read_task(svc, subject, row):
        raise HiveError("NOT_VISIBLE", "任务不可见")
    if args.get("if_version") is not None and args["if_version"] != row["version"]:
        raise HiveError("VERSION_CONFLICT", "任务版本冲突")
    if row["state"] in ("done", "fail", "trash"):
        raise HiveError("TASK_TERMINAL", "终态不可改写")
    cascade = bool(args.get("cascade"))
    if args.get("dry_run"):
        affected = [args["task"]]
        if cascade:
            for r in svc.store.conn.execute(
                    "SELECT task_id FROM tasks WHERE root_id=? AND state IN "
                    "('queued','claimed')", (row["root_id"],)):
                affected.append(r["task_id"])
        return {"task": task_card(row), "affected": affected, "blockers": []}
    result = settle_task(svc, subject, args["task"], "trash", claim_id=None,
                         result={}, reason=args["reason"], admin=True)
    cascaded = []
    if cascade:
        for r in svc.store.conn.execute(
                "SELECT task_id FROM tasks WHERE root_id=? AND parent_id IS NOT "
                "NULL AND state IN ('queued','claimed')",
                (row["root_id"],)).fetchall():
            try:
                settle_task(svc, subject, r["task_id"], "trash", claim_id=None,
                            result={}, reason=args["reason"] + " (cascade)",
                            admin=True)
                cascaded.append(r["task_id"])
            except HiveError:
                cascaded.append(r["task_id"] + " (failed)")
    result["affected"] = [args["task"]] + cascaded
    return result


# ---------------- ports ----------------
def h_port_list(svc, subject, args, rid):
    svc.authz.require(subject, "port.discover")
    q = (args.get("query") or "").lower()
    rows = svc.store.conn.execute("SELECT * FROM ports ORDER BY route").fetchall()
    items = []
    for r in rows:
        if args.get("handler") and r["handler"] != args["handler"]:
            continue
        if args.get("state") and r["state"] != args["state"]:
            continue
        if q and q not in (r["route"] or "").lower() and q not in (
                r["name"] or "").lower() and q not in (r["description"] or "").lower():
            continue
        items.append(_port_card(r))
    return _paginate(items, args)


def _port_card(r) -> dict:
    return {"port_id": r["port_id"], "route": r["route"], "name": r["name"],
            "description": r["description"], "handler": r["handler"],
            "state": r["state"],
            "accepted_types": json.loads(r["accepted_types"]),
            "defaults": json.loads(r["defaults"]),
            "visibility": r["visibility"], "revision": r["revision"]}


def h_port_show(svc, subject, args, rid):
    port_id = _resolve_port_id(svc, args["port"])
    svc.authz.require(subject, "port.read", f"port:{port_id}")
    r = svc.store.conn.execute("SELECT * FROM ports WHERE port_id=?",
                               (port_id,)).fetchone()
    card = _port_card(r)
    counts: dict[str, int] = {}
    for row in svc.store.conn.execute(
            "SELECT state, COUNT(*) c FROM tasks WHERE target=? GROUP BY state",
            (f"port:{port_id}",)):
        counts[row["state"]] = row["c"]
    card["queue_counts"] = counts
    card["input_schema"] = json.loads(r["input_schema"])
    card["channel_ids"] = json.loads(r["channel_ids"])
    if args.get("subscribers"):
        subs = svc.store.conn.execute(
            "SELECT s.agent_id, s.enabled FROM subscriptions s WHERE s.port_id=?",
            (port_id,)).fetchall()
        card["subscribers"] = [
            {"agent_id": s["agent_id"], "enabled": bool(s["enabled"])} for s in subs]
    return {"port": card}


def h_port_create(svc, subject, args, rid):
    from .ports import create_port
    return create_port(svc, subject, args, rid)


def h_port_update(svc, subject, args, rid):
    from .ports import update_port
    return update_port(svc, subject, args, rid)


def h_port_pause(svc, subject, args, rid):
    return _port_state(svc, subject, args, "paused")


def h_port_resume(svc, subject, args, rid):
    return _port_state(svc, subject, args, "enabled")


def _port_state(svc, subject, args, state):
    port_id = _resolve_port_id(svc, args["port"])
    svc.authz.require(subject, "port.update", f"port:{port_id}")
    cur_state = svc.store.conn.execute(
        "SELECT state FROM ports WHERE port_id=?", (port_id,)).fetchone()["state"]
    if state == "paused" and cur_state != "enabled":
        raise HiveError("PORT_PAUSED", f"端口当前状态 {cur_state} 不可暂停")
    if state == "enabled" and cur_state != "paused":
        raise HiveError("INVALID_ARGUMENT", f"端口当前状态 {cur_state} 不可恢复")
    with svc.store.write() as cur:
        cur.execute("UPDATE ports SET state=?, updated_at=? WHERE port_id=?",
                    (state, format_rfc3339(now_utc()), port_id))
        svc.store.audit(cur, subject.ref, "port.state", f"port:{port_id}",
                        args["reason"])
        svc.store.add_event(cur, entity_type="port", entity_id=port_id,
                            kind=f"port.{state}", actor=subject.ref)
    if state == "enabled" and svc.scheduler:
        svc.scheduler.notify_port_resumed(port_id)
    r = svc.store.conn.execute("SELECT * FROM ports WHERE port_id=?",
                               (port_id,)).fetchone()
    return {"port": _port_card(r)}


def h_port_retire(svc, subject, args, rid):
    from .ports import retire_port
    return retire_port(svc, subject, args, rid)


# ---------------- subscriptions ----------------
def h_subscription_list(svc, subject, args, rid):
    if not args.get("agent") and not args.get("port"):
        raise HiveError("REQUIRED_ARGUMENT", "至少限定 --agent 或 --port 之一")
    sql = "SELECT * FROM subscriptions WHERE 1=1"
    params: list = []
    if args.get("agent"):
        sql += " AND agent_id=?"
        params.append(args["agent"])
    if args.get("port"):
        sql += " AND port_id=?"
        params.append(_resolve_port_id(svc, args["port"]))
    if args.get("enabled") is not None:
        sql += " AND enabled=?"
        params.append(1 if args["enabled"] else 0)
    rows = svc.store.conn.execute(sql + " ORDER BY agent_id, port_id",
                                  params).fetchall()
    items = [{"agent_id": r["agent_id"], "port_id": r["port_id"],
              "enabled": bool(r["enabled"]), "created_at": r["created_at"]}
             for r in rows]
    return _paginate(items, args)


# ---------------- tasks (control view) ----------------
def h_task_list(svc, subject, args, rid):
    from .tasks import task_card, visible_tasks
    rows = visible_tasks(svc, subject)
    items = []
    for r in rows:
        if args.get("state") and r["state"] != args["state"]:
            continue
        if args.get("type") and r["type"] != args["type"]:
            continue
        if args.get("since") and r["created_at"] < args["since"]:
            continue
        if args.get("agent"):
            agent_id = args["agent"]
            if r["owner_subject"] != agent_id and r["target"] != f"agent:{agent_id}":
                continue
        if args.get("port"):
            if r["target"] != f"port:{_resolve_port_id(svc, args['port'])}":
                continue
        if args.get("root"):
            root = svc.store.conn.execute("SELECT root_id FROM tasks WHERE task_id=?",
                                          (args["root"],)).fetchone()
            if not root or r["root_id"] != root["root_id"]:
                continue
        if args.get("source"):
            src = json.loads(r["source"])
            if args["source"] not in (src.get("subject"), src.get("producer"),
                                      src.get("trigger")):
                continue
        items.append(task_card(r))
    return _paginate(items, args)


def h_task_show(svc, subject, args, rid):
    from .tasks import task_card, require_task_visible
    r = require_task_visible(svc, subject, args["task"])
    data = {"task": task_card(r)}
    if args.get("history"):
        data["history"] = _task_history(svc, r["task_id"])
    return data


def _task_history(svc, task_id: str) -> list[dict]:
    hist = []
    for r in svc.store.conn.execute(
            "SELECT * FROM task_transfers WHERE task_id=? ORDER BY created_at, transfer_id",
            (task_id,)):
        hist.append({"kind": "transfer", "transfer_id": r["transfer_id"],
                     "from": r["from_target"], "to": r["to_target"],
                     "epoch": r["epoch"], "actor": r["actor"],
                     "reason": r["reason"], "at": r["created_at"]})
    for r in svc.store.conn.execute(
            "SELECT * FROM task_claims WHERE task_id=? ORDER BY created_at, claim_id",
            (task_id,)):
        hist.append({"kind": "claim", "claim_id": r["claim_id"],
                     "subject": r["subject_id"], "epoch": r["epoch"],
                     "invalidated": r["invalidated_at"], "at": r["created_at"]})
    for r in svc.store.conn.execute(
            "SELECT * FROM events WHERE entity_type='task' AND entity_id=? "
            "ORDER BY recorded_at, event_id", (task_id,)):
        hist.append({"kind": "event", "event_id": r["event_id"],
                     "event_kind": r["kind"], "at": r["recorded_at"],
                     "actor": r["actor"]})
    hist.sort(key=lambda h: (h["at"], h.get("event_id") or h.get("claim_id") or
                             h.get("transfer_id") or ""))
    return hist


# ---------------- permission ----------------
def h_permission_list(svc, subject, args, rid):
    target_ref = args["subject"]
    target_subject = _resolve_subject(svc, target_ref)
    if subject.subject_id != target_subject and subject.subject_type not in (
            "owner", "system"):
        svc.authz.require(subject, "permission.list", target_ref)
    rows = svc.store.conn.execute(
        "SELECT * FROM grants WHERE subject_id=? AND revoked_at IS NULL "
        "ORDER BY created_at", (target_subject,)).fetchall()
    grants = [{"grant_id": r["grant_id"],
               "actions": json.loads(r["actions"]),
               "resources": json.loads(r["resources"]),
               "delegable": bool(r["delegable"]),
               "expires_at": r["expires_at"],
               "created_at": r["created_at"]} for r in rows]
    return {"grants": grants, "effective_scope": {"base": "见DESIGN §9.1 基础授权"}}


def _resolve_subject(svc, ref: str) -> str:
    """agent:agt_x / producer:prd_x / subj_x -> subject_id.
    Agent and producer subject ids mirror their object ids."""
    prefix, _, ident = ref.partition(":")
    if prefix == "agent":
        if svc.store.conn.execute("SELECT 1 FROM agents WHERE agent_id=?",
                                  (ident,)).fetchone():
            return ident
        raise HiveError("NOT_FOUND", f"主体不存在: {ref}")
    if prefix == "producer":
        if svc.store.conn.execute("SELECT 1 FROM producers WHERE producer_id=?",
                                  (ident,)).fetchone():
            return ident
        raise HiveError("NOT_FOUND", f"主体不存在: {ref}")
    row = svc.store.conn.execute("SELECT subject_id FROM subjects WHERE subject_id=?",
                                 (ref,)).fetchone()
    if row:
        return row["subject_id"]
    raise HiveError("NOT_FOUND", f"主体不存在: {ref}")


def h_permission_grant(svc, subject, args, rid):
    from .auth import can_grant
    spec = args["input"]
    if not isinstance(spec, dict):
        raise HiveError("INVALID_ARGUMENT", "GrantSpec 必须是对象")
    allowed_keys = {"subject", "actions", "resources", "delegable"}
    unknown = set(spec) - allowed_keys
    if unknown:
        raise HiveError("SCHEMA_UNSUPPORTED", f"未知字段: {sorted(unknown)}")
    actions = spec.get("actions")
    resources = spec.get("resources")
    delegable = bool(spec.get("delegable", False))
    if not isinstance(actions, list) or not actions:
        raise HiveError("INVALID_ARGUMENT", "actions 必须为非空数组")
    if not isinstance(resources, list) or not resources:
        raise HiveError("INVALID_ARGUMENT", "resources 必须为非空数组")
    target_subject = _resolve_subject(svc, spec["subject"])
    if not can_grant(subject, svc.store, actions, resources, delegable):
        raise HiveError("GRANT_SCOPE_EXCEEDED",
                        "授权范围超出调用者可转授范围")
    grant_id = new_id("grant")
    with svc.store.write() as cur:
        cur.execute(
            "INSERT INTO grants(grant_id,subject_id,actions,resources,delegable,"
            "created_by,created_at,expires_at) VALUES (?,?,?,?,?,?,?,?)",
            (grant_id, target_subject, json.dumps(actions),
             json.dumps(resources), 1 if delegable else 0, subject.ref,
             format_rfc3339(now_utc()), args.get("expires_at")))
        svc.store.audit(cur, subject.ref, "permission.grant",
                        f"subject:{target_subject}", args["reason"], rid,
                        {"grant_id": grant_id})
    return {"grant_id": grant_id,
            "effective_revision": svc.store.conn.execute(
                "SELECT COUNT(*) c FROM grants WHERE subject_id=? AND revoked_at IS NULL",
                (target_subject,)).fetchone()["c"]}


def h_permission_revoke(svc, subject, args, rid):
    row = svc.store.conn.execute("SELECT * FROM grants WHERE grant_id=?",
                                 (args["grant"],)).fetchone()
    if not row:
        raise HiveError("NOT_FOUND", f"授权不存在: {args['grant']}")
    if row["revoked_at"]:
        return {"grant": {"grant_id": row["grant_id"],
                          "revoked_at": row["revoked_at"]},
                "affected_subject": row["subject_id"]}
    if subject.subject_type not in ("owner", "system") and \
            row["created_by"] != subject.ref:
        raise HiveError("FORBIDDEN", "只能撤销自己授予的授权（或owner）")
    with svc.store.write() as cur:
        cur.execute("UPDATE grants SET revoked_at=? WHERE grant_id=?",
                    (format_rfc3339(now_utc()), args["grant"]))
        svc.store.audit(cur, subject.ref, "permission.revoke",
                        f"grant:{args['grant']}", args["reason"], rid)
    return {"grant": {"grant_id": row["grant_id"],
                      "revoked_at": format_rfc3339(now_utc())},
            "affected_subject": row["subject_id"]}


# ---------------- operation/event/audit/delivery ----------------
def h_operation_list(svc, subject, args, rid):
    sql = "SELECT * FROM operations WHERE 1=1"
    params: list = []
    if args.get("state"):
        sql += " AND state=?"
        params.append(args["state"])
    if args.get("target"):
        sql += " AND target=?"
        params.append(args["target"])
    if args.get("request"):
        sql += " AND request_id=?"
        params.append(args["request"])
    rows = svc.store.conn.execute(sql + " ORDER BY created_at DESC, operation_id",
                                  params).fetchall()
    items = [_op_card(r) for r in rows if _op_visible(svc, subject, r)]
    return _paginate(items, args)


def _op_card(r) -> dict:
    return {"operation_id": r["operation_id"], "request_id": r["request_id"],
            "actor": r["actor"], "action": r["action"], "target": r["target"],
            "state": r["state"], "result": json.loads(r["result"]) if r["result"] else None,
            "error": json.loads(r["error"]) if r["error"] else None,
            "outcome_unknown": bool(r["outcome_unknown"]),
            "created_at": r["created_at"], "started_at": r["started_at"],
            "ended_at": r["ended_at"]}


def _op_visible(svc, subject, r) -> bool:
    if subject.subject_type in ("owner", "system"):
        return True
    return r["actor"] == subject.ref


def h_operation_show(svc, subject, args, rid):
    import time as _time
    deadline = _time.time() + (args.get("wait_seconds") or 0)
    while True:
        r = svc.store.conn.execute("SELECT * FROM operations WHERE operation_id=?",
                                   (args["op"],)).fetchone()
        if not r:
            raise HiveError("NOT_FOUND", f"操作不存在: {args['op']}")
        if not _op_visible(svc, subject, r):
            raise HiveError("NOT_VISIBLE", "操作不可见")
        if r["state"] in ("succeeded", "failed", "cancelled") or \
                _time.time() >= deadline:
            return {"operation": _op_card(r)}
        _time.sleep(0.3)


def h_operation_cancel(svc, subject, args, rid):
    r = svc.store.conn.execute("SELECT * FROM operations WHERE operation_id=?",
                               (args["op"],)).fetchone()
    if not r:
        raise HiveError("NOT_FOUND", f"操作不存在: {args['op']}")
    if r["actor"] != subject.ref and subject.subject_type not in ("owner", "system"):
        raise HiveError("FORBIDDEN", "只能取消自己的操作")
    if r["state"] in ("succeeded", "failed", "cancelled"):
        raise HiveError("OP_NOT_CANCELLABLE",
                        f"操作已终态 {r['state']}，不可取消")
    if r["state"] == "running" and r["action"] in ("agent.start",):
        raise HiveError("OP_NOT_CANCELLABLE",
                        "已启动实例的启动操作必须通过 agent stop 停止")
    with svc.store.write() as cur:
        cur.execute("UPDATE operations SET state='cancelled', ended_at=? "
                    "WHERE operation_id=?",
                    (format_rfc3339(now_utc()), args["op"]))
        svc.store.audit(cur, subject.ref, "operation.cancel", args["op"],
                        args["reason"])
    r = svc.store.conn.execute("SELECT * FROM operations WHERE operation_id=?",
                               (args["op"],)).fetchone()
    return {"operation": _op_card(r), "completed_steps": [],
            "remaining_steps": []}


def h_event_list(svc, subject, args, rid):
    svc.authz.require(subject, "system.observe")
    sql = "SELECT * FROM events WHERE 1=1"
    params: list = []
    if args.get("entity"):
        etype, _, eid = args["entity"].partition(":")
        sql += " AND entity_type=? AND entity_id=?"
        params.extend([etype, eid])
    if args.get("kind"):
        sql += " AND kind=?"
        params.append(args["kind"])
    if args.get("since"):
        sql += " AND recorded_at>=?"
        params.append(args["since"])
    rows = svc.store.conn.execute(sql + " ORDER BY recorded_at, event_id",
                                  params).fetchall()
    items = [{"event_id": r["event_id"], "entity": f"{r['entity_type']}:{r['entity_id']}",
              "kind": r["kind"], "occurred_at": r["occurred_at"],
              "recorded_at": r["recorded_at"], "actor": r["actor"],
              "payload": json.loads(r["payload"])} for r in rows]
    return _paginate(items, args)


def h_audit_list(svc, subject, args, rid):
    svc.authz.require(subject, "system.observe")
    sql = "SELECT * FROM audit_log WHERE 1=1"
    params: list = []
    if args.get("actor"):
        sql += " AND actor=?"
        params.append(args["actor"])
    if args.get("target"):
        sql += " AND target=?"
        params.append(args["target"])
    if args.get("request"):
        sql += " AND request_id=?"
        params.append(args["request"])
    if args.get("since"):
        sql += " AND created_at>=?"
        params.append(args["since"])
    rows = svc.store.conn.execute(sql + " ORDER BY created_at, audit_id",
                                  params).fetchall()
    items = [{"audit_id": r["audit_id"], "actor": r["actor"],
              "action": r["action"], "target": r["target"],
              "request_id": r["request_id"], "reason": r["reason"],
              "at": r["created_at"]} for r in rows]
    return _paginate(items, args)


def h_delivery_list(svc, subject, args, rid):
    svc.authz.require(subject, "system.observe")
    sql = "SELECT * FROM outbox WHERE 1=1"
    params: list = []
    if args.get("state"):
        sql += " AND state=?"
        params.append(args["state"])
    if args.get("task"):
        sql += " AND (task_id=? OR payload LIKE ?)"
        params.extend([args["task"], f'%{args["task"]}%'])
    if args.get("target"):
        sql += " AND target LIKE ?"
        params.append(f"%{args['target']}%")
    rows = svc.store.conn.execute(sql + " ORDER BY created_at, delivery_id",
                                  params).fetchall()
    items = [{"delivery_id": r["delivery_id"], "semantic_key": r["semantic_key"],
              "kind": r["kind"], "target": r["target"], "task_id": r["task_id"],
              "state": r["state"], "attempts": r["attempts"],
              "next_attempt_at": r["next_attempt_at"], "last_error": r["last_error"],
              "created_at": r["created_at"]} for r in rows]
    return _paginate(items, args)


def h_delivery_retry(svc, subject, args, rid):
    svc.authz.require(subject, "system.observe")
    r = svc.store.conn.execute("SELECT * FROM outbox WHERE delivery_id=?",
                               (args["delivery"],)).fetchone()
    if not r:
        raise HiveError("NOT_FOUND", f"投递不存在: {args['delivery']}")
    if r["state"] == "sent":
        return {"delivery": {"delivery_id": r["delivery_id"],
                             "state": "sent"},
                "next_attempt_at": None, "already_sent": True}
    if r["state"] not in ("retry", "dead", "pending"):
        raise HiveError("INVALID_ARGUMENT", f"投递状态 {r['state']} 不可重试")
    with svc.store.write() as cur:
        cur.execute("UPDATE outbox SET state='pending', attempts=0, "
                    "next_attempt_at=?, last_error=NULL WHERE delivery_id=?",
                    (format_rfc3339(now_utc()), args["delivery"]))
        svc.store.audit(cur, subject.ref, "delivery.retry", args["delivery"],
                        args["reason"])
    if svc.scheduler:
        svc.scheduler.kick_outbox()
    r = svc.store.conn.execute("SELECT * FROM outbox WHERE delivery_id=?",
                               (args["delivery"],)).fetchone()
    return {"delivery": {"delivery_id": r["delivery_id"], "state": r["state"],
                         "attempts": r["attempts"]},
            "next_attempt_at": r["next_attempt_at"]}


# ---------------- harness ----------------
def h_harness_list(svc, subject, args, rid):
    svc.authz.require(subject, "harness.read") if False else None
    rows = svc.store.conn.execute("SELECT * FROM harnesses ORDER BY name").fetchall()
    items = []
    for r in rows:
        spec = json.loads(r["spec"])
        items.append({"name": r["name"], "adapter": spec.get("adapter"),
                      "validation_level": r["validation_level"],
                      "capabilities": spec.get("capabilities"),
                      "optional_capabilities": spec.get("optional_capabilities", {})})
    return {"items": items}


def h_harness_show(svc, subject, args, rid):
    r = svc.store.conn.execute("SELECT * FROM harnesses WHERE name=?",
                               (args["name"],)).fetchone()
    if not r:
        raise HiveError("NOT_FOUND", f"harness不存在: {args['name']}")
    spec = json.loads(r["spec"])
    spec.pop("options", None)
    return {"harness": spec, "validation_level": r["validation_level"],
            "revision": r["revision"]}


def h_harness_register(svc, subject, args, rid):
    from .harness import register_harness
    return register_harness(svc, subject, args, rid)


def h_harness_validate(svc, subject, args, rid):
    from .harness import validate_harness
    return validate_harness(svc, subject, args, rid)


def h_harness_unregister(svc, subject, args, rid):
    svc.authz.require(subject, "system.admin")
    r = svc.store.conn.execute("SELECT * FROM harnesses WHERE name=?",
                               (args["name"],)).fetchone()
    if not r:
        raise HiveError("NOT_FOUND", f"harness不存在: {args['name']}")
    live = svc.store.conn.execute(
        "SELECT i.instance_id, a.name FROM instances i JOIN agents a "
        "ON a.agent_id=i.agent_id WHERE i.phase IN ('starting','live','stopping') "
        "AND i.harness=?", (args["name"],)).fetchall()
    refs = svc.store.conn.execute(
        "SELECT agent_id, name FROM agents WHERE current_revision IN ("
        "SELECT agent_id FROM agent_config_versions) AND agent_id IN ("
        "SELECT agent_id FROM agent_config_versions WHERE published=1)").fetchall()
    blockers = [f"活实例 {r2['instance_id']} (agent {r2['name']})" for r2 in live]
    if blockers:
        raise HiveError("RESPONSIBILITY_BLOCKED", "存在活实例引用",
                        {"blockers": blockers})
    with svc.store.write() as cur:
        cur.execute("DELETE FROM harnesses WHERE name=?", (args["name"],))
        svc.store.audit(cur, subject.ref, "harness.unregister", args["name"],
                        args["reason"])
    return {"unregistered": True, "blockers": []}


# ---------------- shared ----------------
def _paginate(items: list, args: dict) -> dict:
    limit = args.get("limit") or 50
    cursor = args.get("cursor")
    start = 0
    if cursor:
        try:
            import base64
            start = int(base64.urlsafe_b64decode(cursor.encode()).decode())
        except Exception:  # noqa: BLE001
            raise HiveError("INVALID_ARGUMENT", "游标不合法")
    window = items[start:start + limit]
    next_cursor = None
    if start + limit < len(items):
        import base64
        next_cursor = base64.urlsafe_b64encode(
            str(start + limit).encode()).decode()
    return {"items": window, "next_cursor": next_cursor,
            "counts": {"total": len(items)}}
