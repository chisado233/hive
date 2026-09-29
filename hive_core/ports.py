"""Port management per DESIGN §5."""
from __future__ import annotations

import json
import os

from .errors import HiveError
from .ids import new_id
from .timeutil import format_rfc3339, now_utc
from .tasks import _validate_data_schema

PORT_FIELDS = {"name", "description", "handler", "accepted_types", "input_schema",
               "defaults", "visibility", "channel_ids", "metadata", "route"}
DEFAULT_ACCEPTED = {"agents": ["work", "notice", "message", "receipt"],
                    "human": ["work"], "external": ["receipt", "notice", "message"]}


def validate_port_config(cfg: dict, *, handler_default="agents", creating=False):
    if not isinstance(cfg, dict):
        raise HiveError("INVALID_ARGUMENT", "PortConfig 必须是对象")
    unknown = set(cfg) - PORT_FIELDS
    if unknown:
        raise HiveError("SCHEMA_UNSUPPORTED", f"PortConfig 未知字段: {sorted(unknown)}")
    handler = cfg.get("handler", handler_default)
    if handler not in ("agents", "human", "external"):
        raise HiveError("INVALID_ARGUMENT", "handler 只能是 agents/human/external")
    if "accepted_types" in cfg:
        at = cfg["accepted_types"]
        if not isinstance(at, list) or not at:
            raise HiveError("INVALID_ARGUMENT", "accepted_types 必须为非空数组")
        allowed = DEFAULT_ACCEPTED[handler]
        bad = [t for t in at if t not in allowed]
        if bad:
            raise HiveError("SCHEMA_UNSUPPORTED",
                            f"handler={handler} 不允许类型 {bad}")
        if handler == "external" and "work" in at:
            raise HiveError("SCHEMA_UNSUPPORTED",
                            "V1 external 端口只承担结果/通知投递，不接受 work")
    if "input_schema" in cfg and cfg["input_schema"]:
        _check_schema_structure(cfg["input_schema"], path="input_schema")
    if "defaults" in cfg and cfg["defaults"]:
        d = cfg["defaults"]
        if not isinstance(d, dict):
            raise HiveError("INVALID_ARGUMENT", "defaults 必须是对象")
        for k in d:
            if k not in ("urgency", "claim_within", "expires_in"):
                raise HiveError("SCHEMA_UNSUPPORTED", f"defaults 未知键 {k}")
        if d.get("urgency") and d["urgency"] not in ("urgent", "normal", "low"):
            raise HiveError("INVALID_ARGUMENT", "defaults.urgency 非法")
        for k in ("claim_within", "expires_in"):
            if d.get(k) is not None:
                from .timeutil import parse_duration
                parse_duration(d[k])  # raises if bad
    if "visibility" in cfg and cfg["visibility"] not in ("authenticated", "public"):
        raise HiveError("INVALID_ARGUMENT", "visibility 只支持 authenticated/public")


def _resolve_channels(svc, channel_refs: list[str]) -> list[str]:
    ids = []
    for ref in channel_refs:
        row = svc.store.conn.execute(
            "SELECT channel_id, kind FROM channels WHERE channel_id=? OR name=?",
            (ref, ref)).fetchone()
        if not row:
            raise HiveError("NOT_FOUND", f"通道不存在: {ref}（先注册通道）")
        ids.append(row["channel_id"])
    return ids


def create_port(svc, subject, args, rid) -> dict:
    svc.authz.require(subject, "port.create")
    route = args.get("route") or ""
    if not (1 <= len(route) <= 128):
        raise HiveError("INVALID_ARGUMENT", "route 长度须 1..128")
    cfg_in = dict(args.get("input") or {})
    handler = cfg_in.get("handler") or args.get("handler") or "agents"
    cfg_in["handler"] = handler
    channels = args.get("channel") or cfg_in.get("channel_ids") or []
    if handler in ("human", "external"):
        if not channels:
            raise HiveError("CHANNEL_REQUIRED",
                            f"{handler} 端口必须绑定通道（先注册通道）")
        ch_ids = _resolve_channels(svc, channels)
        if handler == "external":
            # V1: single webhook channel
            if len(ch_ids) != 1:
                raise HiveError("CHANNEL_REQUIRED", "external 端口 V1 只绑定一个通道")
            row = svc.store.conn.execute("SELECT kind FROM channels WHERE channel_id=?",
                                         (ch_ids[0],)).fetchone()
            if row["kind"] != "webhook":
                raise HiveError("TARGET_TYPE_REJECTED", "external 端口只绑 webhook 通道")
        cfg_in["channel_ids"] = ch_ids
        cfg_in["accepted_types"] = cfg_in.get(
            "accepted_types", DEFAULT_ACCEPTED[handler])
        if handler == "external" and "work" in cfg_in["accepted_types"]:
            raise HiveError("SCHEMA_UNSUPPORTED", "external 端口不接受 work")
    else:
        cfg_in.setdefault("accepted_types", DEFAULT_ACCEPTED["agents"])
    validate_port_config(cfg_in, handler_default=handler, creating=True)
    if svc.store.conn.execute("SELECT 1 FROM ports WHERE route=?", (route,)).fetchone():
        raise HiveError("INVALID_ARGUMENT", f"路由名已存在: {route}")
    port_id = new_id("port")
    defaults = {"urgency": "normal", "claim_within": None, "expires_in": None}
    defaults.update(cfg_in.get("defaults") or {})
    now = format_rfc3339(now_utc())
    versions_dir = os.path.join(svc.home, "definitions", "ports", port_id, "versions")
    os.makedirs(versions_dir, exist_ok=True)
    file_path = os.path.join(versions_dir, "1.json")
    cfg_doc = {"route": route, "name": args.get("name") or cfg_in.get("name") or route,
               "description": cfg_in.get("description", ""),
               "handler": handler,
               "accepted_types": cfg_in.get("accepted_types"),
               "input_schema": cfg_in.get("input_schema", {}),
               "defaults": defaults,
               "visibility": cfg_in.get("visibility", "authenticated"),
               "channel_ids": cfg_in.get("channel_ids", []),
               "metadata": cfg_in.get("metadata", {})}
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(cfg_doc, f, ensure_ascii=False, indent=2)
    with svc.store.write() as cur:
        cur.execute(
            "INSERT INTO ports(port_id,route,name,description,handler,state,"
            "accepted_types,input_schema,defaults,visibility,revision,channel_ids,"
            "created_at,updated_at) VALUES (?,?,?,?,?,'enabled',?,?,?,?,1,?,?,?)",
            (port_id, route, cfg_doc["name"], cfg_doc["description"], handler,
             json.dumps(cfg_doc["accepted_types"]),
             json.dumps(cfg_doc["input_schema"]),
             json.dumps(defaults), cfg_doc["visibility"],
             json.dumps(cfg_doc["channel_ids"]), now, now))
        svc.store.audit(cur, subject.ref, "port.create", f"port:{port_id}",
                        args.get("reason") or "create port", rid)
        svc.store.add_event(cur, entity_type="port", entity_id=port_id,
                            kind="port.created", actor=subject.ref)
    row = svc.store.conn.execute("SELECT * FROM ports WHERE port_id=?",
                                 (port_id,)).fetchone()
    from .handlers_core import _port_card
    return {"port": _port_card(row)}


def update_port(svc, subject, args, rid) -> dict:
    from .handlers_core import _resolve_port_id, _port_card
    port_id = _resolve_port_id(svc, args["port"])
    svc.authz.require(subject, "port.update", f"port:{port_id}")
    row = svc.store.conn.execute("SELECT * FROM ports WHERE port_id=?",
                                 (port_id,)).fetchone()
    if args.get("if_version") is not None and args["if_version"] != row["revision"]:
        raise HiveError("VERSION_CONFLICT", "端口版本冲突")
    patch = args.get("input")
    if not isinstance(patch, dict):
        raise HiveError("INVALID_ARGUMENT", "端口补丁必须是对象")
    validate_port_config(patch)
    if patch.get("route") and patch["route"] != row["route"]:
        if svc.store.conn.execute("SELECT 1 FROM ports WHERE route=?",
                                  (patch["route"],)).fetchone():
            raise HiveError("INVALID_ARGUMENT", "路由名已存在")
    current = {"route": row["route"], "name": row["name"],
               "description": row["description"], "handler": row["handler"],
               "accepted_types": json.loads(row["accepted_types"]),
               "input_schema": json.loads(row["input_schema"]),
               "defaults": json.loads(row["defaults"]),
               "visibility": row["visibility"],
               "channel_ids": json.loads(row["channel_ids"])}
    from .agents import _merge
    new_cfg = current if args.get("replace") else _merge(current, patch)
    if args.get("replace"):
        new_cfg = _merge({"route": row["route"], "name": row["name"],
                          "handler": row["handler"]}, patch)
    validate_port_config(new_cfg)
    revision = row["revision"] + 1
    versions_dir = os.path.join(svc.home, "definitions", "ports", port_id, "versions")
    os.makedirs(versions_dir, exist_ok=True)
    with open(os.path.join(versions_dir, f"{revision}.json"), "w",
              encoding="utf-8") as f:
        json.dump(new_cfg, f, ensure_ascii=False, indent=2)
    now = format_rfc3339(now_utc())
    with svc.store.write() as cur:
        cur.execute(
            "UPDATE ports SET route=?,name=?,description=?,accepted_types=?,"
            "input_schema=?,defaults=?,visibility=?,revision=?,updated_at=? "
            "WHERE port_id=?",
            (new_cfg["route"], new_cfg["name"], new_cfg["description"],
             json.dumps(new_cfg["accepted_types"]),
             json.dumps(new_cfg["input_schema"]),
             json.dumps(new_cfg["defaults"]), new_cfg["visibility"], revision, now,
             port_id))
        svc.store.audit(cur, subject.ref, "port.update", f"port:{port_id}",
                        args["reason"], rid, {"revision": revision})
        svc.store.add_event(cur, entity_type="port", entity_id=port_id,
                            entity_version=revision, kind="port.updated",
                            actor=subject.ref)
    row = svc.store.conn.execute("SELECT * FROM ports WHERE port_id=?",
                                 (port_id,)).fetchone()
    return {"port": _port_card(row), "revision": revision, "warnings": []}


def retire_port(svc, subject, args, rid) -> dict:
    from .handlers_core import _resolve_port_id
    port_id = _resolve_port_id(svc, args["port"])
    svc.authz.require(subject, "port.retire", f"port:{port_id}")
    row = svc.store.conn.execute("SELECT * FROM ports WHERE port_id=?",
                                 (port_id,)).fetchone()
    plan = args.get("plan") or {}
    if not isinstance(plan, dict) or not plan.get("reason"):
        raise HiveError("REQUIRED_ARGUMENT", "RetirePlan 需要 reason")
    open_tasks = svc.store.conn.execute(
        "SELECT task_id, state, title FROM tasks WHERE target=? AND state IN "
        "('queued','claimed')", (f"port:{port_id}",)).fetchall()
    blockers = []
    dispositions = []
    handled = {item.get("task_id"): item for item in plan.get("work", [])
               if isinstance(item, dict)}
    for t in open_tasks:
        item = handled.get(t["task_id"])
        if not item:
            blockers.append({"task_id": t["task_id"], "state": t["state"],
                             "title": t["title"],
                             "why": "计划未覆盖"})
            continue
        dispositions.append({"task_id": t["task_id"], "action": item.get("action")})
    if args.get("dry_run"):
        return {"operation_id": None, "blockers": blockers,
                "dispositions": dispositions, "dry_run": True}
    if blockers:
        with svc.store.write() as cur:
            cur.execute("UPDATE ports SET state='retiring', updated_at=? "
                        "WHERE port_id=?", (format_rfc3339(now_utc()), port_id))
        return {"operation_id": None, "blockers": blockers,
                "dispositions": dispositions,
                "state": "retiring"}
    # apply dispositions then retire
    with svc.store.write() as cur:
        for item in plan.get("work", []):
            tid = item.get("task_id")
            action = item.get("action")
            if action == "forward":
                from .tasks import forward_task
                trow = cur.execute("SELECT * FROM tasks WHERE task_id=?",
                                   (tid,)).fetchone()
                if trow and trow["state"] in ("queued", "claimed"):
                    cur.execute(
                        "UPDATE tasks SET state='queued', owner_subject=NULL, "
                        "claim_id=NULL, claim_epoch=claim_epoch+1, target=?, "
                        "updated_at=? WHERE task_id=?",
                        (item.get("target", f"port:{port_id}"),
                         format_rfc3339(now_utc()), tid))
            elif action in ("trash", "fail"):
                trow = cur.execute("SELECT state FROM tasks WHERE task_id=?",
                                   (tid,)).fetchone()
                if trow and trow["state"] in ("queued", "claimed"):
                    cur.execute(
                        "UPDATE tasks SET state=?, ended_at=?, result=?, updated_at=? "
                        "WHERE task_id=?",
                        (action, format_rfc3339(now_utc()),
                         json.dumps({"reason": item.get("reason", "port retire"),
                                     "ended_by": subject.ref,
                                     "ended_at": format_rfc3339(now_utc())}),
                         format_rfc3339(now_utc()), tid))
        cur.execute("UPDATE ports SET state='retired', updated_at=? WHERE port_id=?",
                    (format_rfc3339(now_utc()), port_id))
        svc.store.audit(cur, subject.ref, "port.retire", f"port:{port_id}",
                        args["reason"], rid)
        svc.store.add_event(cur, entity_type="port", entity_id=port_id,
                            kind="port.retired", actor=subject.ref)
    return {"operation_id": None, "blockers": [], "dispositions": dispositions,
            "state": "retired"}


def _check_schema_structure(schema, path="input_schema"):
    """Registration-time check: keywords/types only, no payload enforcement."""
    allowed_kw = {"type", "required", "properties", "enum", "items"}
    if not isinstance(schema, dict):
        raise HiveError("SCHEMA_UNSUPPORTED", f"{path} 必须是对象")
    for kw in schema:
        if kw not in allowed_kw:
            raise HiveError("SCHEMA_UNSUPPORTED", f"不支持的schema关键字: {kw}")
    if schema.get("type") not in (None, "object", "string", "number", "integer",
                                  "boolean", "array"):
        raise HiveError("SCHEMA_UNSUPPORTED", f"{path}.type 非法")
    for name, sub in (schema.get("properties") or {}).items():
        _check_schema_structure(sub, f"{path}.{name}")
    if "items" in schema:
        _check_schema_structure(schema["items"], f"{path}.items")
