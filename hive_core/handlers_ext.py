"""Handlers for producer / trigger / channel / human commands."""
from __future__ import annotations

import json

from .errors import HiveError
from .ids import new_id
from .timeutil import format_rfc3339, now_utc


def register(svc):
    svc.register("control.producer.list", h_producer_list)
    svc.register("control.producer.show", h_producer_show)
    svc.register("control.producer.create", h_producer_create)
    svc.register("control.producer.update", h_producer_update)
    svc.register("control.producer.credential", h_producer_credential)
    svc.register("control.producer.revoke", h_producer_revoke)
    svc.register("control.trigger.list", h_trigger_list)
    svc.register("control.trigger.show", h_trigger_show)
    svc.register("control.trigger.create", h_trigger_create)
    svc.register("control.trigger.update", h_trigger_update)
    svc.register("control.trigger.fire", h_trigger_fire)
    svc.register("control.trigger.enable", h_trigger_enable)
    svc.register("control.trigger.disable", h_trigger_disable)
    svc.register("control.channel.list", h_channel_list)
    svc.register("control.channel.show", h_channel_show)
    svc.register("control.channel.register", h_channel_register)
    svc.register("control.channel.update", h_channel_update)
    svc.register("control.channel.disable", h_channel_disable)
    svc.register("control.human.list", h_human_list)
    svc.register("control.human.answer", h_human_answer)
    svc.register("control.human.fail", h_human_fail)
    svc.register("control.human.trash", h_human_trash)


def _producer_card(r) -> dict:
    return {"producer_id": r["producer_id"], "name": r["name"],
            "kind": r["kind"],
            "allowed_targets": json.loads(r["allowed_targets"]),
            "enabled": bool(r["enabled"]), "revision": r["revision"]}


def h_producer_list(svc, subject, args, rid):
    svc.authz.require(subject, "producer.manage") if False else None
    rows = svc.store.conn.execute("SELECT * FROM producers ORDER BY created_at, "
                                  "producer_id").fetchall()
    items = [_producer_card(r) for r in rows]
    limit = args.get("limit") or 50
    return {"items": items[:limit], "next_cursor": None}


def h_producer_show(svc, subject, args, rid):
    r = svc.store.conn.execute("SELECT * FROM producers WHERE producer_id=?",
                               (args["producer"],)).fetchone()
    if not r:
        raise HiveError("NOT_FOUND", f"producer不存在: {args['producer']}")
    card = _producer_card(r)
    n = svc.store.conn.execute("SELECT COUNT(*) c FROM producer_events WHERE "
                               "producer_id=?", (r["producer_id"],)).fetchone()["c"]
    card["events_deduplicated"] = n
    return {"producer": card,
            "allowed_targets": json.loads(r["allowed_targets"])}


def h_producer_create(svc, subject, args, rid):
    svc.authz.require(subject, "producer.manage")
    targets = args.get("allow_target") or []
    if isinstance(targets, str):
        targets = [targets]
    if not targets:
        raise HiveError("REQUIRED_ARGUMENT", "至少一个 --allow-target")
    kind = args.get("kind")
    if kind not in ("builtin", "external"):
        raise HiveError("INVALID_ARGUMENT", "kind 只能是 builtin/external")
    if kind == "external" and not args.get("credential_out"):
        raise HiveError("REQUIRED_ARGUMENT", "external 需要 --credential-out")
    producer_id = new_id("producer")
    now = format_rfc3339(now_utc())
    from .tasks import resolve_target
    normalized = []
    for t in targets:
        norm, info = resolve_target(svc, t)
        normalized.append(norm)
    with svc.store.write() as cur:
        cur.execute("INSERT INTO producers(producer_id,name,kind,allowed_targets,"
                    "enabled,revision,metadata,created_at) VALUES (?,?,?,?,1,1,"
                    "'{}',?)",
                    (producer_id, args["name"], kind,
                     json.dumps(normalized), now))
        # subject mirrors producer id
        cur.execute("INSERT INTO subjects(subject_id,subject_type,name,created_at,"
                    "metadata) VALUES (?,?,?,?,?)",
                    (producer_id, "producer", producer_id, now,
                     json.dumps({"producer_id": producer_id})))
        # grant: create on allowed targets + read own created
        cur.execute(
            "INSERT INTO grants(grant_id,subject_id,actions,resources,delegable,"
            "created_by,created_at) VALUES (?,?,?,?,0,'system:bootstrap',?)",
            (new_id("grant"), producer_id,
             json.dumps(["task.create", "task.read"]),
             json.dumps(normalized + ["created_tasks"]), now))
        svc.store.audit(cur, subject.ref, "producer.create", producer_id,
                        args.get("reason") or "create producer", rid)
    data = {"producer": _producer_card(
        svc.store.conn.execute("SELECT * FROM producers WHERE producer_id=?",
                               (producer_id,)).fetchone())}
    if kind == "external":
        from .auth import issue_credential
        with svc.store.write() as cur:
            issue_credential(cur, producer_id, kind="producer",
                             out_path=args["credential_out"])
        data["credential_file"] = args["credential_out"]
    return data


def h_producer_update(svc, subject, args, rid):
    svc.authz.require(subject, "producer.manage")
    r = svc.store.conn.execute("SELECT * FROM producers WHERE producer_id=?",
                               (args["producer"],)).fetchone()
    if not r:
        raise HiveError("NOT_FOUND", f"producer不存在: {args['producer']}")
    if args.get("if_version") is not None and \
            args["if_version"] != r["revision"]:
        raise HiveError("VERSION_CONFLICT", "producer版本冲突")
    patch = args.get("input")
    allowed = {"name", "allowed_targets", "enabled", "metadata"}
    unknown = set(patch) - allowed
    if unknown:
        raise HiveError("SCHEMA_UNSUPPORTED", f"ProducerConfig 未知字段: {sorted(unknown)}")
    name = patch.get("name", r["name"])
    targets = patch.get("allowed_targets") or json.loads(r["allowed_targets"])
    enabled = 1 if patch.get("enabled", bool(r["enabled"])) else 0
    revision = r["revision"] + 1
    with svc.store.write() as cur:
        cur.execute("UPDATE producers SET name=?,allowed_targets=?,enabled=?,"
                    "revision=? WHERE producer_id=?",
                    (name, json.dumps(targets), enabled, revision,
                     args["producer"]))
        # permission shrink applies to new deliveries immediately
        if "allowed_targets" in patch:
            cur.execute("DELETE FROM grants WHERE subject_id=? AND created_by="
                        "'system:bootstrap'",
                        (args["producer"],))
            cur.execute(
                "INSERT INTO grants(grant_id,subject_id,actions,resources,delegable,"
                "created_by,created_at) VALUES (?,?,?,?,0,'system:bootstrap',?)",
                (new_id("grant"), args["producer"],
                 json.dumps(["task.create", "task.read"]),
                 json.dumps(list(targets) + ["created_tasks"]),
                 format_rfc3339(now_utc())))
        svc.store.audit(cur, subject.ref, "producer.update", args["producer"],
                        args["reason"], rid, {"revision": revision})
    return {"producer": _producer_card(
        svc.store.conn.execute("SELECT * FROM producers WHERE producer_id=?",
                               (args["producer"],)).fetchone()),
        "revision": revision}


def h_producer_credential(svc, subject, args, rid):
    svc.authz.require(subject, "producer.manage")
    r = svc.store.conn.execute("SELECT * FROM producers WHERE producer_id=?",
                               (args["producer"],)).fetchone()
    if not r:
        raise HiveError("NOT_FOUND", f"producer不存在: {args['producer']}")
    from .auth import issue_credential, revoke_subject_credentials
    with svc.store.write() as cur:
        if args.get("revoke_previous"):
            revoke_subject_credentials(cur, args["producer"])
        cred_id, path = issue_credential(cur, args["producer"], kind="producer",
                                         out_path=args["out"])
        svc.store.audit(cur, subject.ref, "producer.credential",
                        args["producer"], args["reason"], rid)
    return {"credential_file": path, "credential_id": cred_id}


def h_producer_revoke(svc, subject, args, rid):
    svc.authz.require(subject, "producer.manage")
    r = svc.store.conn.execute("SELECT * FROM producers WHERE producer_id=?",
                               (args["producer"],)).fetchone()
    if not r:
        raise HiveError("NOT_FOUND", f"producer不存在: {args['producer']}")
    from .auth import revoke_subject_credentials
    with svc.store.write() as cur:
        cur.execute("UPDATE producers SET enabled=0 WHERE producer_id=?",
                    (args["producer"],))
        revoke_subject_credentials(cur, args["producer"])
        svc.store.audit(cur, subject.ref, "producer.revoke", args["producer"],
                        args["reason"], rid)
    return {"producer": {"producer_id": args["producer"], "enabled": False}}


# ---------------- trigger ----------------
def h_trigger_list(svc, subject, args, rid):
    svc.authz.require(subject, "trigger.manage") if False else None
    sql = "SELECT * FROM triggers WHERE 1=1"
    params: list = []
    if args.get("enabled") is not None:
        sql += " AND enabled=?"
        params.append(1 if args["enabled"] else 0)
    rows = svc.store.conn.execute(sql + " ORDER BY created_at, trigger_id",
                                  params).fetchall()
    items = [{"trigger_id": r["trigger_id"], "name": r["name"], "kind": r["kind"],
              "enabled": bool(r["enabled"]), "next_run_at": r["next_run_at"]}
             for r in rows]
    limit = args.get("limit") or 50
    return {"items": items[:limit], "next_cursor": None}


def h_trigger_show(svc, subject, args, rid):
    from .triggers import _trigger_card
    r = svc.store.conn.execute("SELECT * FROM triggers WHERE trigger_id=?",
                               (args["trigger"],)).fetchone()
    if not r:
        raise HiveError("NOT_FOUND", f"触发器不存在: {args['trigger']}")
    recent = svc.store.conn.execute(
        "SELECT event_id, task_id, created_at FROM producer_events WHERE "
        "producer_id=? ORDER BY created_at DESC LIMIT 30",
        (r["producer_id"],)).fetchall()
    return {"trigger": _trigger_card(svc, args["trigger"]),
            "status": {"last_cursor": r["last_cursor"],
                       "next_run_at": r["next_run_at"],
                       "recent_fires": [dict(x) for x in recent]}}


def h_trigger_create(svc, subject, args, rid):
    from .triggers import create_trigger
    return create_trigger(svc, subject, args, rid)


def h_trigger_update(svc, subject, args, rid):
    svc.authz.require(subject, "trigger.manage")
    r = svc.store.conn.execute("SELECT * FROM triggers WHERE trigger_id=?",
                               (args["trigger"],)).fetchone()
    if not r:
        raise HiveError("NOT_FOUND", f"触发器不存在: {args['trigger']}")
    if args.get("if_version") is not None and \
            args["if_version"] != r["revision"]:
        raise HiveError("VERSION_CONFLICT", "trigger版本冲突")
    patch = args.get("input")
    allowed = {"name", "task", "every", "cron", "timezone", "enabled", "missed",
               "metadata"}
    unknown = set(patch) - allowed
    if unknown:
        raise HiveError("SCHEMA_UNSUPPORTED", f"TriggerConfig 未知字段: {sorted(unknown)}")
    import datetime
    from .timeutil import parse_rfc3339, parse_duration
    from .triggers import (compile_cron, next_cron_time, tz_offset_minutes,
                           validate_task_spec, validate_timezone)
    task_spec = patch.get("task", json.loads(r["task_spec"]))
    validate_task_spec(task_spec)
    every = patch.get("every", r["every_seconds"] and
                      f"{r['every_seconds']}s")
    every_seconds = parse_duration(every) if every else None
    cron = patch.get("cron", r["cron"])
    compiled = compile_cron(cron) if cron else None
    tz = validate_timezone(patch.get("timezone", r["timezone"]))
    enabled = 1 if patch.get("enabled", bool(r["enabled"])) else 0
    revision = r["revision"] + 1
    next_run = None
    if enabled:
        if every_seconds:
            next_run = format_rfc3339(
                parse_rfc3339(svc.now()) +
                datetime.timedelta(seconds=every_seconds))
        elif compiled:
            next_run = format_rfc3339(
                next_cron_time(compiled, parse_rfc3339(svc.now()),
                               tz_offset_minutes(tz)))
    with svc.store.write() as cur:
        cur.execute(
            "UPDATE triggers SET name=?,task_spec=?,every_seconds=?,cron=?,"
            "timezone=?,enabled=?,missed=?,revision=?,next_run_at=? WHERE "
            "trigger_id=?",
            (patch.get("name", r["name"]), json.dumps(task_spec,
                                                      ensure_ascii=False),
             every_seconds, cron, tz, enabled,
             patch.get("missed", r["missed"]), revision, next_run,
             args["trigger"]))
        svc.store.audit(cur, subject.ref, "trigger.update", args["trigger"],
                        args["reason"], rid, {"revision": revision})
    from .triggers import _trigger_card
    return {"trigger": _trigger_card(svc, args["trigger"]), "revision": revision}


def h_trigger_fire(svc, subject, args, rid):
    svc.authz.require(subject, "trigger.manage")
    from .triggers import fire_trigger
    t = svc.store.conn.execute("SELECT * FROM triggers WHERE trigger_id=?",
                               (args["trigger"],)).fetchone()
    if not t:
        raise HiveError("NOT_FOUND", f"触发器不存在: {args['trigger']}")
    if t["kind"] != "manual" and not t["enabled"]:
        raise HiveError("INVALID_ARGUMENT", "定时规则未启用（manual可随时fire）")
    return fire_trigger(svc, subject, args["trigger"], args["event_id"],
                        args.get("data") or {})


def _set_trigger_enabled(svc, subject, args, enabled: bool):
    svc.authz.require(subject, "trigger.manage")
    import datetime
    from .timeutil import parse_rfc3339
    r = svc.store.conn.execute("SELECT * FROM triggers WHERE trigger_id=?",
                               (args["trigger"],)).fetchone()
    if not r:
        raise HiveError("NOT_FOUND", f"触发器不存在: {args['trigger']}")
    next_run = None
    if enabled:
        if r["every_seconds"]:
            next_run = format_rfc3339(
                parse_rfc3339(svc.now()) +
                datetime.timedelta(seconds=r["every_seconds"]))
        elif r["cron"]:
            from .triggers import compile_cron, next_cron_time, \
                tz_offset_minutes
            next_run = format_rfc3339(
                next_cron_time(compile_cron(r["cron"]),
                               parse_rfc3339(svc.now()),
                               tz_offset_minutes(r["timezone"])))
    with svc.store.write() as cur:
        cur.execute("UPDATE triggers SET enabled=?, next_run_at=? WHERE "
                    "trigger_id=?", (1 if enabled else 0, next_run,
                                     args["trigger"]))
        svc.store.audit(cur, subject.ref,
                        "trigger.enable" if enabled else "trigger.disable",
                        args["trigger"], args["reason"])
    return {"trigger": {"trigger_id": args["trigger"], "enabled": enabled}}


def h_trigger_enable(svc, subject, args, rid):
    return _set_trigger_enabled(svc, subject, args, True)


def h_trigger_disable(svc, subject, args, rid):
    return _set_trigger_enabled(svc, subject, args, False)


# ---------------- channel ----------------
CHANNEL_FIELDS = {"name", "kind", "endpoint", "credential_ref", "enabled",
                  "retry_policy", "metadata"}


def _channel_card(r) -> dict:
    return {"channel_id": r["channel_id"], "name": r["name"], "kind": r["kind"],
            "endpoint": r["endpoint"], "enabled": bool(r["enabled"]),
            "revision": r["revision"]}


def h_channel_list(svc, subject, args, rid):
    rows = svc.store.conn.execute("SELECT * FROM channels ORDER BY name").fetchall()
    items = [_channel_card(r) for r in rows]
    limit = args.get("limit") or 50
    return {"items": items[:limit], "next_cursor": None}


def h_channel_show(svc, subject, args, rid):
    r = svc.store.conn.execute("SELECT * FROM channels WHERE channel_id=? OR "
                               "name=?", (args["channel"], args["channel"])).fetchone()
    if not r:
        raise HiveError("NOT_FOUND", f"通道不存在: {args['channel']}")
    counts = {}
    for row in svc.store.conn.execute(
            "SELECT state, COUNT(*) c FROM channel_deliveries WHERE channel_id=? "
            "GROUP BY state", (r["channel_id"],)):
        counts[row["state"]] = row["c"]
    return {"channel": _channel_card(r), "delivery_counts": counts}


def _validate_channel_spec(spec: dict):
    unknown = set(spec) - CHANNEL_FIELDS
    if unknown:
        raise HiveError("SCHEMA_UNSUPPORTED", f"ChannelSpec 未知字段: {sorted(unknown)}")
    if spec.get("kind") not in ("human-web", "webhook"):
        raise HiveError("INVALID_ARGUMENT", "kind 只能是 human-web/webhook")
    if spec.get("kind") == "webhook" and not spec.get("endpoint"):
        raise HiveError("REQUIRED_ARGUMENT", "webhook 通道需要 endpoint")
    if spec.get("endpoint") and not str(spec["endpoint"]).startswith("http"):
        raise HiveError("INVALID_ARGUMENT", "endpoint 必须是 http(s) URL")


def h_channel_register(svc, subject, args, rid):
    svc.authz.require(subject, "channel.manage")
    spec = args.get("input")
    if not isinstance(spec, dict):
        raise HiveError("INVALID_ARGUMENT", "ChannelSpec 必须是对象")
    _validate_channel_spec(spec)
    existing = svc.store.conn.execute("SELECT channel_id FROM channels WHERE name=?",
                                      (spec["name"],)).fetchone()
    if existing and not args.get("replace"):
        raise HiveError("INVALID_ARGUMENT",
                        f"通道已存在: {spec['name']}（用 --replace）")
    channel_id = existing["channel_id"] if existing else new_id("channel")
    now = format_rfc3339(now_utc())
    with svc.store.write() as cur:
        cur.execute(
            "INSERT OR REPLACE INTO channels(channel_id,name,kind,endpoint,"
            "credential_ref,enabled,retry_policy,revision,metadata,created_at) "
            "VALUES (?,?,?,?,?,?,?,1,?,?)",
            (channel_id, spec["name"], spec["kind"], spec.get("endpoint"),
             spec.get("credential_ref"),
             1 if spec.get("enabled", True) else 0,
             json.dumps(spec.get("retry_policy")) if spec.get("retry_policy")
             else None,
             json.dumps(spec.get("metadata", {})), now))
        svc.store.audit(cur, subject.ref, "channel.register", channel_id,
                        args["reason"], rid)
    return {"channel": _channel_card(
        svc.store.conn.execute("SELECT * FROM channels WHERE channel_id=?",
                               (channel_id,)).fetchone())}


def h_channel_update(svc, subject, args, rid):
    svc.authz.require(subject, "channel.manage")
    r = svc.store.conn.execute("SELECT * FROM channels WHERE channel_id=? OR "
                               "name=?", (args["channel"], args["channel"])).fetchone()
    if not r:
        raise HiveError("NOT_FOUND", f"通道不存在: {args['channel']}")
    if args.get("if_version") is not None and \
            args["if_version"] != r["revision"]:
        raise HiveError("VERSION_CONFLICT", "channel版本冲突")
    patch = args.get("input")
    _validate_channel_spec({**{"kind": r["kind"]}, **patch})
    revision = r["revision"] + 1
    with svc.store.write() as cur:
        cur.execute(
            "UPDATE channels SET name=COALESCE(?,name),endpoint=COALESCE(?,endpoint),"
            "enabled=COALESCE(?,enabled),credential_ref=COALESCE(?,credential_ref),"
            "revision=? WHERE channel_id=?",
            (patch.get("name"), patch.get("endpoint"),
             1 if patch.get("enabled", True) else 0 if "enabled" in patch else None,
             patch.get("credential_ref"), revision, r["channel_id"]))
        svc.store.audit(cur, subject.ref, "channel.update", r["channel_id"],
                        args["reason"], rid,
                        {"endpoint_changed": "endpoint" in patch})
    return {"channel": _channel_card(
        svc.store.conn.execute("SELECT * FROM channels WHERE channel_id=?",
                               (r["channel_id"],)).fetchone()),
        "revision": revision}


def h_channel_disable(svc, subject, args, rid):
    svc.authz.require(subject, "channel.manage")
    r = svc.store.conn.execute("SELECT * FROM channels WHERE channel_id=? OR "
                               "name=?", (args["channel"], args["channel"])).fetchone()
    if not r:
        raise HiveError("NOT_FOUND", f"通道不存在: {args['channel']}")
    with svc.store.write() as cur:
        cur.execute("UPDATE channels SET enabled=0 WHERE channel_id=?",
                    (r["channel_id"],))
        svc.store.audit(cur, subject.ref, "channel.disable", r["channel_id"],
                        args["reason"])
    return {"channel": {"channel_id": r["channel_id"], "enabled": False}}


# ---------------- human ----------------
def _human_port_check(svc, subject, task_row):
    """Human subjects may only handle tasks on ports they're granted on."""
    if subject.subject_type in ("owner", "system"):
        return
    if not task_row["target"].startswith("port:"):
        raise HiveError("FORBIDDEN", "非人工端口任务")
    port_id = task_row["target"][5:]
    svc.authz.require(subject, "human.answer", f"port:{port_id}")


def h_human_list(svc, subject, args, rid):
    if subject.subject_type not in ("owner", "system", "human"):
        raise HiveError("FORBIDDEN", "需要人工处理权限")
    sql = ("SELECT t.* FROM tasks t JOIN ports p ON 'port:'||p.port_id=t.target "
           "WHERE p.handler='human' AND t.state=?")
    params: list = [args.get("state", "queued")]
    if args.get("port"):
        sql += " AND (p.port_id=? OR p.route=?)"
        params.extend([args["port"], args["port"].lstrip("port:")])
    rows = svc.store.conn.execute(sql + " ORDER BY t.created_at, t.task_id",
                                  params).fetchall()
    items = []
    for r in rows:
        _human_port_check(svc, subject, r)
        items.append({"task_id": r["task_id"], "title": r["title"],
                      "body": r["body"], "data": json.loads(r["data"]),
                      "state": r["state"], "spec_version": r["spec_version"],
                      "created_at": r["created_at"]})
    limit = args.get("limit") or 50
    return {"items": items[:limit], "next_cursor": None}


def _human_settle(svc, subject, args, action: str):
    from .tasks import get_task, settle_task
    row = get_task(svc, args["task"])
    if not row["target"].startswith("port:"):
        raise HiveError("INVALID_ARGUMENT", "不是人工端口任务")
    prow = svc.store.conn.execute("SELECT handler FROM ports WHERE port_id=?",
                                  (row["target"][5:],)).fetchone()
    if not prow or prow["handler"] != "human":
        raise HiveError("INVALID_ARGUMENT", "不是人工端口任务")
    _human_port_check(svc, subject, row)
    result: dict = {}
    if args.get("text"):
        result["text"] = args["text"]
    if args.get("data"):
        result["data"] = args["data"]
    if args.get("attach"):
        result["artifacts"] = [{"uri": a} for a in args["attach"]]
    return settle_task(svc, subject, args["task"], action, claim_id=None,
                       result=result,
                       reason=args.get("reason"),
                       spec_version=args.get("spec_version"),
                       answered_by=subject.ref, direct=True)


def h_human_answer(svc, subject, args, rid):
    data = _human_settle(svc, subject, args, "done")
    data["answered_by"] = subject.ref
    return data


def h_human_fail(svc, subject, args, rid):
    data = _human_settle(svc, subject, args, "fail")
    data["answered_by"] = subject.ref
    return data


def h_human_trash(svc, subject, args, rid):
    data = _human_settle(svc, subject, args, "trash")
    data["answered_by"] = subject.ref
    return data
