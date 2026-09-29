"""Task domain: creation, atomic claim, terminal states, forward, tree, comments.

Implements DESIGN §6/§7 and invariants INV-02/03/05/07/08.
All state changes go through store.write() transactions with events and
idempotency committed atomically.
"""
from __future__ import annotations

import json

from .errors import HiveError
from .ids import new_id
from .timeutil import format_rfc3339, now_utc, parse_duration, parse_rfc3339

TASK_TERMINAL = ("done", "fail", "trash")


TASK_COLUMNS = ("task_id,parent_id,root_id,type,title,summary,body,data,attachments,"
                "source,target,state,urgency,callback,reply_to,refs,claim_deadline,"
                "expires_at,version,spec_version,result,created_at,updated_at,"
                "ended_at,owner_subject,claim_id,claim_epoch")


def insert_task(cur, **f) -> str:
    """Safe INSERT with column/value count always in sync."""
    f.setdefault("state", "queued")
    f.setdefault("urgency", "normal")
    f.setdefault("version", 1)
    f.setdefault("spec_version", 1)
    f.setdefault("claim_epoch", 0)
    f.setdefault("summary", "")
    f.setdefault("body", "")
    f.setdefault("data", {})
    f.setdefault("attachments", [])
    f.setdefault("refs", [])
    f.setdefault("source", {})
    cols = TASK_COLUMNS.split(",")
    vals = []
    for c in cols:
        v = f.get(c)
        if isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False)
        vals.append(v)
    ph = ",".join("?" * len(cols))
    cur.execute(f"INSERT INTO tasks({','.join(cols)}) VALUES ({ph})", vals)
    return f["task_id"]


def task_card(r) -> dict:
    return {
        "task_id": r["task_id"], "parent_id": r["parent_id"], "root_id": r["root_id"],
        "type": r["type"], "title": r["title"], "summary": r["summary"],
        "body": r["body"], "data": json.loads(r["data"]),
        "attachments": json.loads(r["attachments"]),
        "source": json.loads(r["source"]), "target": r["target"],
        "state": r["state"], "owner_subject": r["owner_subject"],
        "claim_id": r["claim_id"], "claim_epoch": r["claim_epoch"],
        "urgency": r["urgency"], "callback": json.loads(r["callback"])
        if r["callback"] else None,
        "reply_to": r["reply_to"],
        "claim_deadline": r["claim_deadline"], "expires_at": r["expires_at"],
        "version": r["version"], "spec_version": r["spec_version"],
        "result": json.loads(r["result"]) if r["result"] else None,
        "created_at": r["created_at"], "updated_at": r["updated_at"],
        "ended_at": r["ended_at"],
    }


def get_task(svc, task_id: str):
    r = svc.store.conn.execute("SELECT * FROM tasks WHERE task_id=?",
                               (task_id,)).fetchone()
    if not r:
        raise HiveError("NOT_FOUND", f"任务不存在: {task_id}")
    return r


def can_read_task(svc, subject, task_row) -> bool:
    """Read visibility per DESIGN §9.1: own personal queue, created, assigned,
    plus explicit grants (task.read on task/port or task.manage)."""
    if subject.subject_type in ("owner", "system"):
        return True
    task = task_card(task_row)
    if subject.subject_type == "agent":
        ref = f"agent:{subject.subject_id}"
        if task["target"] == ref:
            return True
        src = task["source"] or {}
        if src.get("subject") == subject.ref:
            return True
        if task["owner_subject"] == subject.subject_id:
            return True
        # subscriber of the target port can see its queue entries
        if task["target"].startswith("port:"):
            sub = svc.store.conn.execute(
                "SELECT 1 FROM subscriptions WHERE agent_id=? AND port_id=? AND enabled=1",
                (subject.subject_id, task["target"][5:])).fetchone()
            if sub:
                return True
    if subject.subject_type == "producer":
        src = task["source"] or {}
        if src.get("producer") == subject.name or src.get("subject") == subject.ref:
            return True
    if svc.authz.allowed(subject, "human.answer", task["target"]) and             task["target"].startswith("port:"):
        # humans/agents with answer rights on the port can read its tasks
        return True
    return svc.authz.allowed(subject, "task.read", f"task:{task['task_id']}",
                             task=task)


def require_task_visible(svc, subject, task_id: str):
    r = get_task(svc, task_id)
    if not can_read_task(svc, subject, r):
        raise HiveError("NOT_VISIBLE", f"任务不可见: {task_id}")
    return r


def visible_tasks(svc, subject):
    """All task rows the subject may read (used by control task list)."""
    rows = svc.store.conn.execute("SELECT * FROM tasks ORDER BY task_id").fetchall()
    return [r for r in rows if can_read_task(svc, subject, r)]


# ---------------- target resolution ----------------
def resolve_target(svc, target: str) -> tuple[str, dict]:
    """Returns (normalized_target, info). info: kind=port|agent + row."""
    if not isinstance(target, str) or ":" not in target:
        raise HiveError("INVALID_ARGUMENT",
                        f"目标格式必须为 agent:<id> 或 port:<id-or-route>: {target!r}")
    kind, _, ident = target.partition(":")
    if kind == "port":
        row = svc.store.conn.execute(
            "SELECT * FROM ports WHERE port_id=? OR route=?",
            (ident, ident)).fetchone()
        if not row:
            raise HiveError("NOT_FOUND", f"端口不存在: {target}")
        return f"port:{row['port_id']}", {"kind": "port", "port": row}
    if kind == "agent":
        row = svc.store.conn.execute("SELECT * FROM agents WHERE agent_id=?",
                                     (ident,)).fetchone()
        if not row:
            raise HiveError("NOT_FOUND", f"Agent不存在: {target}")
        if row["lifecycle"] in ("retired",):
            raise HiveError("AGENT_RETIRED", f"Agent已退役: {target}")
        if row["lifecycle"] == "retiring" and False:
            pass
        return f"agent:{row['agent_id']}", {"kind": "agent", "agent": row}
    raise HiveError("INVALID_ARGUMENT", f"目标类型不合法: {target}")


def resolve_callback(svc, subject, callback: str | None) -> dict | None:
    """callback= none/self/agent:ID/port:ID -> {target: normalized} | None."""
    if callback in (None, "", "none"):
        return None
    if callback == "self":
        if subject.subject_type != "agent":
            raise HiveError("CALLBACK_TARGET_REQUIRED",
                            "非Agent主体不能使用 self 回调")
        return {"target": f"agent:{subject.subject_id}"}
    norm, info = resolve_target(svc, callback)
    if info["kind"] == "port":
        accepted = json.loads(info["port"]["accepted_types"])
        if "receipt" not in accepted:
            raise HiveError("TARGET_TYPE_REJECTED",
                            f"端口 {callback} 不接受 receipt")
        if info["port"]["handler"] == "external":
            chans = json.loads(info["port"]["channel_ids"])
            if not chans:
                raise HiveError("CHANNEL_REQUIRED", "external 端口未绑定通道")
    return {"target": norm}


# ---------------- port schema validation ----------------
def validate_against_port(svc, port_row, task_type: str, data: dict):
    accepted = json.loads(port_row["accepted_types"])
    if task_type not in accepted:
        raise HiveError("TARGET_TYPE_REJECTED",
                        f"端口 {port_row['route']} 不接受 {task_type} "
                        f"(accepted={accepted})")
    schema = json.loads(port_row["input_schema"])
    if schema:
        _validate_data_schema(schema, data, path="data")


def _validate_data_schema(schema: dict, data, path: str = "data"):
    if not isinstance(schema, dict):
        raise HiveError("SCHEMA_UNSUPPORTED", "input_schema 必须是对象")
    allowed_kw = {"type", "required", "properties", "enum", "items"}
    for kw in schema:
        if kw not in allowed_kw:
            raise HiveError("SCHEMA_UNSUPPORTED",
                            f"不支持的schema关键字: {kw}")
    t = schema.get("type")
    if t == "object":
        if not isinstance(data, dict):
            raise HiveError("TARGET_TYPE_REJECTED", f"{path} 必须是对象")
        props = schema.get("properties", {})
        for name, sub in props.items():
            _validate_data_schema(sub, data.get(name), f"{path}.{name}") \
                if name in data else None
        for req in schema.get("required", []):
            if req not in data or data.get(req) in (None, ""):
                raise HiveError("TARGET_TYPE_REJECTED",
                                f"{path}.{req} 为必填字段")
    elif t == "string":
        if data is not None and not isinstance(data, str):
            raise HiveError("TARGET_TYPE_REJECTED", f"{path} 必须是字符串")
        if "enum" in schema and data is not None and data not in schema["enum"]:
            raise HiveError("TARGET_TYPE_REJECTED",
                            f"{path}={data!r} 不在 {schema['enum']}")
    elif t == "number":
        if data is not None and not isinstance(data, (int, float)):
            raise HiveError("TARGET_TYPE_REJECTED", f"{path} 必须是数字")
    elif t == "integer":
        if data is not None and not (isinstance(data, int)
                                     and not isinstance(data, bool)):
            raise HiveError("TARGET_TYPE_REJECTED", f"{path} 必须是整数")
    elif t == "boolean":
        if data is not None and not isinstance(data, bool):
            raise HiveError("TARGET_TYPE_REJECTED", f"{path} 必须是布尔")
    elif t == "array":
        if data is not None:
            if not isinstance(data, list):
                raise HiveError("TARGET_TYPE_REJECTED", f"{path} 必须是数组")
            if "items" in schema:
                for i, item in enumerate(data):
                    _validate_data_schema(schema["items"], item, f"{path}[{i}]")


# ---------------- task creation ----------------
SPEC_FIELDS = {"type", "target", "to", "title", "summary", "body", "data",
               "attachments", "urgency", "callback", "claim_by", "claim_within",
               "expires_at", "expires_in", "reply_to", "references"}


def create_task(svc, subject, *, target: str, task_type: str, title: str,
                parent_id: str | None, summary: str = "", body: str = "",
                data: dict | None = None, attachments: list | None = None,
                urgency: str = "normal", callback: str | None = None,
                reply_to: str | None = None, references: list | None = None,
                claim_by: str | None = None, claim_within: str | None = None,
                claim_deadline: str | None = None,
                expires_at: str | None = None, expires_in: str | None = None,
                source_extra: dict | None = None, event_id: str | None = None,
                root: bool = False, actor=None) -> dict:
    """Shared creation path for emit.new / producer / dispatch / batch / triggers."""
    if task_type not in ("work", "notice", "message"):
        raise HiveError("INVALID_ARGUMENT",
                        f"创建类型只允许 work/notice/message；receipt由系统生成")
    norm_target, info = resolve_target(svc, target)
    now = svc.now()

    # deadlines
    port_defaults = json.loads(info["port"]["defaults"]) \
        if info["kind"] == "port" else {}
    deadline = _resolve_claim_deadline(svc, claim_by, claim_within, claim_deadline,
                                       port_defaults, now)
    expiry = _resolve_expiry(svc, expires_at, expires_in, port_defaults, now)

    # permissions
    if info["kind"] == "port":
        validate_against_port(svc, info["port"], task_type, data or {})
        state = info["port"]["state"]
        if state == "retired":
            raise HiveError("PORT_RETIRED", f"端口已退役: {info['port']['route']}")
        if state == "retiring":
            raise HiveError("PORT_RETIRED", f"端口正在退役，拒绝新投递")
    else:  # agent personal target
        if task_type == "receipt":
            raise HiveError("INVALID_ARGUMENT", "receipt只由系统生成")
        agent_row = info["agent"]
        if agent_row["lifecycle"] in ("retired", "retiring"):
            raise HiveError("AGENT_RETIRED",
                            f"Agent生命周期 {agent_row['lifecycle']} 拒绝普通投递")

    actor = actor or subject
    svc.authz.require(actor, "task.create", norm_target)

    # parent lineage
    parent_row = None
    if parent_id:
        parent_row = require_task_visible(svc, actor, parent_id)
    if parent_row and not root:
        if parent_row["state"] in TASK_TERMINAL and task_type == "work":
            # allow dispatch children from terminal parents only via done-dispatch
            # which calls with explicit flag; direct emit onto terminal parent denied
            raise HiveError("TASK_TERMINAL",
                            f"父任务已终态 {parent_row['state']}，不能派生子任务")
    root_id = parent_row["root_id"] if parent_row else None  # set after insert

    cb = resolve_callback(svc, actor, callback)
    task_id = new_id("task")
    source = {"subject": actor.ref, "type": actor.subject_type}
    if actor.subject_type == "producer":
        source["producer"] = actor.name
    if source_extra:
        source.update(source_extra)
    root_id_final = parent_row["root_id"] if parent_row else task_id

    with svc.store.write() as cur:
        if svc.consume_fault("tx_before_commit"):
            raise HiveError("INTERNAL_ERROR", "故障注入：提交前失败，事务已回滚")
        insert_task(cur, task_id=task_id, parent_id=parent_id,
                    root_id=root_id_final, type=task_type, title=title,
                    summary=summary or "", body=body or "", data=data or {},
                    attachments=attachments or [], source=source,
                    target=norm_target, urgency=urgency, callback=cb,
                    reply_to=reply_to, refs=references or [],
                    claim_deadline=deadline, expires_at=expiry,
                    created_at=now, updated_at=now)
        svc.store.add_event(cur, entity_type="task", entity_id=task_id,
                            kind="task.created", actor=actor.ref,
                            payload={"target": norm_target, "type": task_type,
                                     "parent": parent_id},
                            cause_request_id=event_id)
        from .scheduler import notify_new_task
        notify_new_task(svc, cur, task_id, norm_target, task_type)
    return get_task(svc, task_id)


def _resolve_claim_deadline(svc, claim_by, claim_within, claim_deadline,
                            port_defaults, now: str):
    import datetime
    if claim_deadline:
        return claim_deadline
    if claim_by:
        return format_rfc3339(parse_rfc3339(claim_by))
    now_dt = parse_rfc3339(now)
    if claim_within is not None:
        if claim_within == "none":
            return None
        secs = parse_duration(claim_within)
        return format_rfc3339(now_dt + datetime.timedelta(seconds=secs))
    default_within = port_defaults.get("claim_within")
    if default_within:
        secs = parse_duration(default_within)
        return format_rfc3339(now_dt + datetime.timedelta(seconds=secs))
    return None


def _resolve_expiry(svc, expires_at, expires_in, port_defaults, now: str):
    import datetime
    now_dt = parse_rfc3339(now)
    if expires_at:
        return format_rfc3339(parse_rfc3339(expires_at))
    if expires_in is not None:
        if expires_in == "none":
            return None
        secs = parse_duration(expires_in)
        return format_rfc3339(now_dt + datetime.timedelta(seconds=secs))
    default_in = port_defaults.get("expires_in")
    if default_in:
        secs = parse_duration(default_in)
        return format_rfc3339(now_dt + datetime.timedelta(seconds=secs))
    return None


# ---------------- claim ----------------
def agent_limits(svc, agent_id: str) -> int:
    from .agents import get_agent_config
    cfg = get_agent_config(svc, agent_id)
    try:
        return int(cfg.get("limits", {}).get("max_inflight_work", 3))
    except (TypeError, ValueError):
        return 3


def count_inflight(svc, cur, agent_subject_id: str) -> int:
    row = cur.execute(
        "SELECT COUNT(*) c FROM tasks WHERE owner_subject=? AND state='claimed' "
        "AND type='work'", (agent_subject_id,)).fetchone()
    return row["c"]


def claim_task(svc, subject, task_id: str, spec_version: int | None = None,
               claim_hint: str | None = None) -> dict:
    """Atomic claim per IMPLEMENTATION_PLAN §5.1. Returns result dict for batch."""
    with svc.store.write() as cur:
        row = cur.execute("SELECT * FROM tasks WHERE task_id=?",
                          (task_id,)).fetchone()
        if not row:
            return {"task_id": task_id, "ok": False,
                    "error": {"code": "NOT_FOUND", "message": "任务不存在"}}
        task = task_card(row)
        # visibility
        if not can_read_task(svc, subject, row):
            return {"task_id": task_id, "ok": False,
                    "error": {"code": "NOT_VISIBLE", "message": "任务不可见"}}
        if row["state"] == "claimed":
            if row["owner_subject"] == subject.subject_id:
                return {"task_id": task_id, "ok": True, "claim_id": row["claim_id"],
                        "already_owned": True, "claim_epoch": row["claim_epoch"],
                        "task": task}
            return {"task_id": task_id, "ok": False,
                    "error": {"code": "TASK_ALREADY_CLAIMED", "message": "任务已被领取"}}
        if row["state"] in TASK_TERMINAL:
            return {"task_id": task_id, "ok": False,
                    "error": {"code": "TASK_TERMINAL", "message": "任务已终态"}}
        if row["type"] == "receipt":
            if row["target"].startswith("agent:"):
                pass  # personal receipts can be claimed by target agent
            else:
                return {"task_id": task_id, "ok": False,
                        "error": {"code": "INVALID_ARGUMENT",
                                  "message": "端口回执由授权处理者消费，不走work领取"}}
        # claim deadline: queued work past claim_deadline fails at deadline scan;
        # racing claim vs deadline: if deadline passed, refuse here too
        now = svc.now()
        if row["claim_deadline"] and now >= row["claim_deadline"]:
            return {"task_id": task_id, "ok": False,
                    "error": {"code": "TASK_TERMINAL",
                              "message": "领取期限已过（将由到期扫描结算）"}}
        if spec_version is not None and spec_version != row["spec_version"]:
            return {"task_id": task_id, "ok": False,
                    "error": {"code": "TASK_SPEC_CHANGED", "message": "任务要求已变化"}}
        # target checks
        if row["target"].startswith("port:"):
            port_id = row["target"][5:]
            prow = cur.execute("SELECT * FROM ports WHERE port_id=?",
                               (port_id,)).fetchone()
            if prow["handler"] == "agents":
                if prow["state"] != "enabled":
                    return {"task_id": task_id, "ok": False,
                            "error": {"code": "PORT_PAUSED",
                                      "message": f"端口状态 {prow['state']}"}}
                if subject.subject_type == "agent":
                    sub = cur.execute(
                        "SELECT enabled FROM subscriptions WHERE agent_id=? AND "
                        "port_id=?", (subject.subject_id, port_id)).fetchone()
                    if not sub or not sub["enabled"]:
                        return {"task_id": task_id, "ok": False,
                                "error": {"code": "FORBIDDEN",
                                          "message": "无有效订阅，不能从共享端口领取"}}
            if not svc.authz.allowed(subject, "task.claim", row["target"],
                                     task=task):
                return {"task_id": task_id, "ok": False,
                        "error": {"code": "FORBIDDEN", "message": "缺少领取权限"}}
        elif row["target"].startswith("agent:"):
            if row["target"] != f"agent:{subject.subject_id}" and \
                    subject.subject_type != "owner":
                return {"task_id": task_id, "ok": False,
                        "error": {"code": "FORBIDDEN", "message": "个人目标只允许本人领取"}}
        # capacity: work counts, notices/messages don't
        if row["type"] == "work":
            inflight = count_inflight(svc, cur, subject.subject_id)
            limit = agent_limits(svc, subject.subject_id) \
                if subject.subject_type == "agent" else 100000
            if inflight >= limit:
                return {"task_id": task_id, "ok": False,
                        "error": {"code": "CAPACITY_EXCEEDED",
                                  "message": f"在途work {inflight} 已达上限 {limit}"}}
        # CAS queued -> claimed
        claim_id = claim_hint or new_id("claim")
        epoch = row["claim_epoch"] + 1
        updated = cur.execute(
            "UPDATE tasks SET state='claimed', owner_subject=?, claim_id=?, "
            "claim_epoch=?, claim_deadline=NULL, updated_at=? WHERE task_id=? "
            "AND state='queued'",
            (subject.subject_id, claim_id, epoch, now, task_id))
        if updated.rowcount != 1:
            return {"task_id": task_id, "ok": False,
                    "error": {"code": "TASK_ALREADY_CLAIMED",
                              "message": "并发竞争失败"}}
        cur.execute(
            "INSERT INTO task_claims(claim_id,task_id,subject_id,instance_id,epoch,"
            "created_at) VALUES (?,?,?,?,?,?)",
            (claim_id, task_id, subject.subject_id, subject.instance_id, epoch, now))
        svc.store.add_event(cur, entity_type="task", entity_id=task_id,
                            entity_version=epoch, kind="task.claimed",
                            actor=subject.ref,
                            payload={"claim_id": claim_id, "epoch": epoch})
        return {"task_id": task_id, "ok": True, "claim_id": claim_id,
                "claim_epoch": epoch, "task": task_card(
                    cur.execute("SELECT * FROM tasks WHERE task_id=?",
                                (task_id,)).fetchone())}


# ---------------- terminal ----------------
def _validate_claim_for_terminal(svc, subject, row, claim_id: str | None,
                                 admin: bool = False):
    """Returns None if OK; raises HiveError. Owner path: task.manage grant."""
    if row["state"] in TASK_TERMINAL:
        raise HiveError("TASK_TERMINAL",
                        f"任务已终态 {row['state']}，结果不可改写")
    task = task_card(row)
    if row["type"] == "work":
        if claim_id and claim_id == row["claim_id"]:
            crow = svc.store.conn.execute(
                "SELECT * FROM task_claims WHERE claim_id=?", (claim_id,)).fetchone()
            if not crow or crow["invalidated_at"]:
                raise HiveError("CLAIM_FENCED", "领取凭据已失效")
            if crow["subject_id"] != subject.subject_id:
                raise HiveError("FORBIDDEN", "领取凭据不属于当前主体")
            return
        if admin:
            svc.authz.require(subject, "task.manage", f"task:{row['task_id']}",
                              task=task)
            return
        if svc.authz.allowed(subject, "task.manage", f"task:{row['task_id']}",
                             task=task):
            return
        raise HiveError("CLAIM_FENCED" if claim_id else "FORBIDDEN",
                        "work 需要有效领取凭据或管理权限")
    # non-work: personal target direct; shared port needs claim or manage
    if row["target"] == f"agent:{subject.subject_id}":
        return
    if claim_id and claim_id == row["claim_id"]:
        crow = svc.store.conn.execute(
            "SELECT * FROM task_claims WHERE claim_id=?", (claim_id,)).fetchone()
        if crow and not crow["invalidated_at"] and \
                crow["subject_id"] == subject.subject_id:
            return
        raise HiveError("CLAIM_FENCED", "领取凭据已失效")
    if svc.authz.allowed(subject, "task.manage", f"task:{row['task_id']}", task=task):
        return
    if row["owner_subject"] == subject.subject_id:
        return
    raise HiveError("FORBIDDEN", "共享端口轻量任务须先领取或持有管理权限")


def settle_task(svc, subject, task_id: str, action: str, *, claim_id: str | None,
                result: dict, reason: str | None = None,
                dispatch: list | None = None, admin: bool = False,
                answered_by: str | None = None, spec_version: int | None = None,
                actor_label: str | None = None, direct: bool = False) -> dict:
    """done/fail/trash with optional atomic dispatch. DESIGN §5.2, INV-05/06."""
    if action not in TASK_TERMINAL:
        raise HiveError("INVALID_ARGUMENT", f"非法终态: {action}")
    # pre-validate dispatch specs BEFORE any write (all-or-nothing)
    prepared_children = []
    if dispatch:
        if len(dispatch) < 1 or len(dispatch) > 100:
            raise HiveError("INVALID_ARGUMENT", "dispatch 需要 1..100 个 TaskSpec")
        for spec in dispatch:
            if not isinstance(spec, dict):
                raise HiveError("INVALID_ARGUMENT", "dispatch 项必须是 TaskSpec 对象")
            unknown = set(spec) - SPEC_FIELDS
            if unknown:
                raise HiveError("SCHEMA_UNSUPPORTED",
                                f"TaskSpec 未知字段: {sorted(unknown)}")

    now = svc.now()
    with svc.store.write() as cur:
        if svc.consume_fault("tx_before_commit"):
            raise HiveError("INTERNAL_ERROR", "故障注入：提交前失败，事务已回滚")
        row = cur.execute("SELECT * FROM tasks WHERE task_id=?",
                          (task_id,)).fetchone()
        if not row:
            raise HiveError("NOT_FOUND", f"任务不存在: {task_id}")
        # already-terminal always reports TASK_TERMINAL (never overwritten)
        if row["state"] in TASK_TERMINAL:
            raise HiveError("TASK_TERMINAL",
                            f"任务已终态 {row['state']}，结果不可改写")
        # stale-claim fencing: an invalidated claim of the caller yields
        # CLAIM_FENCED even when the task is no longer visible to them
        if claim_id:
            crow = cur.execute(
                "SELECT * FROM task_claims WHERE claim_id=?", (claim_id,)
            ).fetchone()
            if crow and crow["subject_id"] == subject.subject_id and                     crow["invalidated_at"] and crow["task_id"] == task_id:
                raise HiveError("CLAIM_FENCED",
                                "领取凭据已失效（转交/回收/过期），不能提交结果")
        if not can_read_task(svc, subject, row):
            raise HiveError("NOT_VISIBLE", f"任务不可见: {task_id}")
        if spec_version is not None and spec_version != row["spec_version"]:
            raise HiveError("TASK_SPEC_CHANGED", "问题版本已变化，需刷新")
        if not direct:
            _validate_claim_for_terminal(svc, subject, row, claim_id,
                                         admin=admin)
        ended_by = answered_by or subject.ref
        result_full = dict(result or {})
        result_full.setdefault("text", "")
        result_full["artifacts"] = result_full.get("artifacts") or []
        if action in ("fail", "trash"):
            result_full["reason"] = reason
            result_full["reason_code"] = result_full.get("reason_code") or (
                "FAILED" if action == "fail" else "DISCARDED")
        result_full["ended_by"] = ended_by
        result_full["ended_at"] = now
        updated = cur.execute(
            "UPDATE tasks SET state=?, result=?, ended_at=?, updated_at=?, "
            "owner_subject=CASE WHEN ?='done' THEN owner_subject ELSE owner_subject END,"
            "version=version+1 WHERE task_id=? AND state NOT IN ('done','fail','trash')",
            (action, json.dumps(result_full, ensure_ascii=False), now, now, action,
             task_id))
        if updated.rowcount != 1:
            raise HiveError("TASK_TERMINAL", "任务已终态")
        # invalidate claim
        if row["claim_id"]:
            cur.execute("UPDATE task_claims SET invalidated_at=?, "
                        "invalidated_reason=? WHERE claim_id=? AND invalidated_at IS NULL",
                        (now, action, row["claim_id"]))
        terminal_event = svc.store.add_event(
            cur, entity_type="task", entity_id=task_id, entity_version=row["version"] + 1,
            kind="task.terminal", actor=ended_by,
            payload={"state": action, "result": result_full})
        # receipts for callback
        receipt_ids: list[str] = []
        cb = json.loads(row["callback"]) if row["callback"] else None
        if cb and row["type"] != "receipt":
            from .delivery import create_receipt
            rid = create_receipt(svc, cur, source_row=row, terminal_event_id=terminal_event,
                                 state=action, result=result_full, callback=cb)
            if rid:
                receipt_ids.append(rid)
        # cancel pending wait of this task (parent terminal cancels its wait)
        from .waits import cancel_waits_for_task
        cancel_waits_for_task(svc, cur, task_id, now,
                              note="父任务已终态，等待自动取消")
        # advance waits depending on this task
        from .waits import advance_waits
        advance_waits(svc, cur, task_id)
        # atomic dispatch children
        children: list[dict] = []
        if dispatch:
            for spec in dispatch:
                child = _insert_child(svc, cur, parent_row=row, spec=spec,
                                      actor=subject, now=now)
                children.append(task_card(child))
        if svc.consume_fault("outbox_after_commit"):
            # receipt row already in this tx; the *sending* after commit is
            # what this fault delays — simulate by parking outbox next_attempt
            cur.execute("UPDATE outbox SET next_attempt_at=datetime('now','+1 hour') "
                        "WHERE state='pending'")
    return {"task": task_card(get_task(svc, task_id)), "children": children,
            "receipt_ids": receipt_ids, "terminal_event_id": terminal_event}


def _insert_child(svc, cur, parent_row, spec: dict, actor, now: str):
    """Create a child task inside an open transaction (dispatch/batch path)."""
    task_type = spec.get("type", "work")
    target = spec.get("target") or spec.get("to")
    if not target:
        raise HiveError("REQUIRED_ARGUMENT", "dispatch 子任务缺少 target")
    if task_type == "receipt":
        raise HiveError("INVALID_ARGUMENT", "不能派生 receipt")
    norm_target, info = resolve_target(svc, target)
    if info["kind"] == "port":
        prow = info["port"]
        if prow["state"] in ("retired", "retiring"):
            raise HiveError("PORT_RETIRED", f"端口 {prow['route']} 拒绝新投递")
        validate_against_port(svc, prow, task_type, spec.get("data") or {})
        svc.authz.require(actor, "task.create", norm_target)
    else:
        svc.authz.require(actor, "task.create", norm_target)
    title = spec.get("title")
    if not title or not (1 <= len(title) <= 256):
        raise HiveError("INVALID_ARGUMENT", "子任务 title 必须 1..256")
    body = spec.get("body", "")
    data = spec.get("data") or {}
    if task_type == "work" and not body and not data:
        raise HiveError("INVALID_ARGUMENT", "work 至少有 body 或非空 data")
    cb = resolve_callback(svc, actor, spec.get("callback", "none"))
    deadline = _resolve_claim_deadline(
        svc, spec.get("claim_by"), spec.get("claim_within"), None,
        json.loads(info["port"]["defaults"]) if info["kind"] == "port" else {}, now)
    expiry = _resolve_expiry(
        svc, spec.get("expires_at"), spec.get("expires_in"),
        json.loads(info["port"]["defaults"]) if info["kind"] == "port" else {}, now)
    task_id = new_id("task")
    source = {"subject": actor.ref, "type": actor.subject_type}
    insert_task(cur, task_id=task_id, parent_id=parent_row["task_id"],
                root_id=parent_row["root_id"], type=task_type, title=title,
                summary=spec.get("summary", ""), body=body, data=data,
                attachments=spec.get("attachments") or [], source=source,
                target=norm_target, urgency=spec.get("urgency", "normal"),
                callback=cb, reply_to=spec.get("reply_to"), refs=[],
                claim_deadline=deadline, expires_at=expiry,
                created_at=now, updated_at=now)
    svc.store.add_event(cur, entity_type="task", entity_id=task_id,
                        kind="task.created", actor=actor.ref,
                        payload={"target": norm_target, "parent":
                                 parent_row["task_id"]})
    from .scheduler import notify_new_task
    notify_new_task(svc, cur, task_id, norm_target, task_type)
    return cur.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()


# ---------------- forward ----------------
def forward_task(svc, subject, task_id: str, to: str, reason: str,
                 claim_id: str | None = None, claim_by=None, claim_within=None,
                 if_version: int | None = None, admin: bool = False) -> dict:
    """Forward keeps Task ID; new claim epoch; no terminal receipt. INV-08."""
    now = svc.now()
    norm_target, info = resolve_target(svc, to)
    with svc.store.write() as cur:
        if svc.consume_fault("tx_before_commit"):
            raise HiveError("INTERNAL_ERROR", "故障注入：提交前失败，事务已回滚")
        row = cur.execute("SELECT * FROM tasks WHERE task_id=?",
                          (task_id,)).fetchone()
        if not row:
            raise HiveError("NOT_FOUND", f"任务不存在: {task_id}")
        if not can_read_task(svc, subject, row):
            raise HiveError("NOT_VISIBLE", f"任务不可见: {task_id}")
        if row["state"] in TASK_TERMINAL:
            raise HiveError("TASK_TERMINAL", "终态任务不能转交")
        if if_version is not None and if_version != row["version"]:
            raise HiveError("VERSION_CONFLICT", "任务版本已变化")
        # authority: current claim holder, or admin
        if not admin:
            if row["state"] != "claimed":
                raise HiveError("RECLAIM_REQUIRED" if row["state"] == "claimed"
                                else "INVALID_ARGUMENT",
                                "queued任务的管理转交请用 control task forward")
            if not claim_id or claim_id != row["claim_id"]:
                raise HiveError("CLAIM_FENCED", "转交需要当前有效领取凭据")
            crow = cur.execute("SELECT * FROM task_claims WHERE claim_id=?",
                               (claim_id,)).fetchone()
            if not crow or crow["invalidated_at"] or \
                    crow["subject_id"] != subject.subject_id:
                raise HiveError("CLAIM_FENCED", "领取凭据已失效")
        else:
            if row["state"] == "claimed":
                raise HiveError("RECLAIM_REQUIRED",
                                "claimed任务请用 control task reclaim")
            svc.authz.require(subject, "task.manage", f"task:{task_id}")
        # target validation
        if info["kind"] == "port":
            prow = info["port"]
            accepted = json.loads(prow["accepted_types"])
            if row["type"] not in accepted:
                raise HiveError("TARGET_TYPE_REJECTED",
                                f"目标端口不接受 {row['type']}")
            if prow["state"] in ("retired", "retiring"):
                raise HiveError("PORT_RETIRED", "目标端口停止接收")
        else:
            if row["type"] == "receipt":
                raise HiveError("INVALID_ARGUMENT", "receipt 不能这样转交")
            if info["agent"]["lifecycle"] in ("retired", "retiring"):
                raise HiveError("AGENT_RETIRED", "目标Agent拒绝新投递")
        # expires check
        if row["expires_at"] and now >= row["expires_at"]:
            raise HiveError("TASK_TERMINAL", "业务有效期已过，拒绝转交")
        port_defaults = json.loads(info["port"]["defaults"]) \
            if info["kind"] == "port" else {}
        deadline = _resolve_claim_deadline(svc, claim_by, claim_within, None,
                                           port_defaults, now)
        epoch = row["claim_epoch"] + 1
        cur.execute(
            "UPDATE tasks SET target=?, state='queued', owner_subject=NULL, "
            "claim_id=NULL, claim_epoch=?, claim_deadline=?, version=version+1, "
            "updated_at=? WHERE task_id=?",
            (norm_target, epoch, deadline, now, task_id))
        if row["claim_id"]:
            cur.execute("UPDATE task_claims SET invalidated_at=?, "
                        "invalidated_reason='forward' WHERE claim_id=? AND "
                        "invalidated_at IS NULL", (now, row["claim_id"]))
        transfer_id = new_id("transfer")
        cur.execute(
            "INSERT INTO task_transfers(transfer_id,task_id,from_target,to_target,"
            "epoch,actor,reason,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (transfer_id, task_id, row["target"], norm_target, epoch,
             subject.ref, reason, now))
        svc.store.add_event(cur, entity_type="task", entity_id=task_id,
                            entity_version=epoch, kind="task.forwarded",
                            actor=subject.ref,
                            payload={"to": norm_target, "epoch": epoch})
        from .scheduler import notify_new_task
        notify_new_task(svc, cur, task_id, norm_target, row["type"])
        from .waits import waits_summary
        new_row = cur.execute("SELECT * FROM tasks WHERE task_id=?",
                              (task_id,)).fetchone()
        return {"task": task_card(new_row), "transfer_id": transfer_id,
                "claim_epoch": epoch, "wait_summary": waits_summary(svc, task_id)}


# ---------------- comments ----------------
MENTION_RE = None  # compiled lazily


def parse_mentions(body: str) -> list[str]:
    import re
    global MENTION_RE
    if MENTION_RE is None:
        MENTION_RE = re.compile(r"@agt_[A-Za-z0-9]+")
    return sorted({m.lstrip("@") for m in MENTION_RE.findall(body or "")})


def add_comment(svc, subject, task_id: str, body: str, mentions_extra: list | None,
                revision_of: str | None = None, checkpoint_refs: list | None = None,
                rid: str | None = None) -> dict:
    if not body or not body.strip():
        raise HiveError("REQUIRED_ARGUMENT", "评论正文不能为空")
    row = require_task_visible(svc, subject, task_id)
    mentions = set(parse_mentions(body))
    for m in mentions_extra or []:
        m = m.strip()
        mentions.add(m[1:] if m.startswith("@") else m)
    # validate all mentioned agents can read the task BEFORE writing anything
    from .agents import get_agent_subject
    for m in mentions:
        agent_row = svc.store.conn.execute(
            "SELECT * FROM agents WHERE agent_id=?", (m,)).fetchone()
        if not agent_row:
            raise HiveError("INVALID_ARGUMENT", f"@的目标Agent不存在: {m}")
        agent_subject = m  # agent subject id mirrors agent id
        from .auth import Subject as Subj
        agent_subj = Subj(agent_subject, "agent",
                          agent_row["name"])
        if not can_read_task(svc, agent_subj, row):
            raise HiveError("FORBIDDEN",
                            f"@目标 {m} 无权读取该任务；整笔评论拒绝")
    now = svc.now()
    comment_id = new_id("comment")
    notice_ids: list[str] = []
    with svc.store.write() as cur:
        if svc.consume_fault("tx_before_commit"):
            raise HiveError("INTERNAL_ERROR", "故障注入：提交前失败，事务已回滚")
        if revision_of:
            old = cur.execute("SELECT * FROM comments WHERE comment_id=?",
                              (revision_of,)).fetchone()
            if not old or old["task_id"] != task_id:
                raise HiveError("NOT_FOUND", "被修订评论不存在或不属于该任务")
        cur.execute(
            "INSERT INTO comments(comment_id,task_id,author_subject,body,mentions,"
            "revision_of,checkpoint_refs,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (comment_id, task_id, subject.ref, body,
             json.dumps(sorted(mentions)), revision_of,
             json.dumps(checkpoint_refs or []), now))
        svc.store.add_event(cur, entity_type="comment", entity_id=comment_id,
                            kind="comment.created", actor=subject.ref,
                            payload={"task": task_id, "mentions": sorted(mentions)})
        for m in sorted(mentions):
            agent_subject = get_agent_subject(svc, m)
            notice_id = new_id("task")
            insert_task(cur, task_id=notice_id, parent_id=None,
                        root_id=notice_id, type="notice", title="评论提到了你",
                        summary=f"{row['title'][:100]}", body="",
                        data={"task_id": task_id, "comment_id": comment_id,
                              "excerpt": body[:200]},
                        attachments=[],
                        source={"subject": subject.ref,
                                "type": subject.subject_type},
                        target=f"agent:{m}", reply_to=comment_id,
                        refs=[f"task:{task_id}", f"comment:{comment_id}"],
                        created_at=now, updated_at=now)
            svc.store.add_event(cur, entity_type="task", entity_id=notice_id,
                                kind="task.created", actor=subject.ref,
                                payload={"target": f"agent:{m}",
                                         "type": "notice",
                                         "reason": "comment.mention"})
            notice_ids.append(notice_id)
    return {"comment": {"comment_id": comment_id, "task_id": task_id,
                        "author": subject.ref, "body": body,
                        "mentions": sorted(mentions), "created_at": now,
                        "revision_of": revision_of},
            "notice_ids": notice_ids, "mentions": sorted(mentions)}


# ---------------- decline ----------------
def decline_task(svc, subject, task_id: str, reason: str):
    row = require_task_visible(svc, subject, task_id)
    if row["type"] != "work":
        raise HiveError("INVALID_ARGUMENT", "只能拒绝 work")
    if row["state"] != "queued":
        raise HiveError("TASK_ALREADY_CLAIMED" if row["state"] == "claimed"
                        else "TASK_TERMINAL",
                        "已持有的 work 不能 decline，请 fail/trash/forward")
    if row["owner_subject"] == subject.subject_id:
        raise HiveError("TASK_ALREADY_CLAIMED", "已持有该任务，不能拒绝")
    decline_id = new_id("decline")
    now = svc.now()
    with svc.store.write() as cur:
        cur.execute(
            "INSERT OR REPLACE INTO task_declines(decline_id,task_id,subject_id,"
            "spec_version,reason,created_at) VALUES (?,?,?,?,?,?)",
            (decline_id, task_id, subject.subject_id, row["spec_version"], reason,
             now))
        svc.store.add_event(cur, entity_type="task", entity_id=task_id,
                            kind="task.declined", actor=subject.ref,
                            payload={"subject": subject.subject_id,
                                     "spec_version": row["spec_version"]})
    return {"decline_id": decline_id, "task_id": task_id,
            "spec_version": row["spec_version"]}


# ---------------- tree ----------------
def task_tree(svc, subject, root_task_id: str, depth: int = 4,
              include_terminal: bool = True, include_comments: bool = False):
    root = require_task_visible(svc, subject, root_task_id)
    rid = root["root_id"]
    all_rows = svc.store.conn.execute(
        "SELECT * FROM tasks WHERE root_id=? ORDER BY created_at, task_id",
        (rid,)).fetchall()
    by_parent: dict[str, list] = {}
    for r in all_rows:
        by_parent.setdefault(r["parent_id"], []).append(r)
    nodes, edges = [], []
    counts = {"queued": 0, "claimed": 0, "done": 0, "fail": 0, "trash": 0}
    open_work = 0
    truncated = False

    def walk(row, level):
        nonlocal truncated, open_work
        visible = can_read_task(svc, subject, row)
        if visible:
            card = task_card(row)
            if include_comments:
                cs = svc.store.conn.execute(
                    "SELECT COUNT(*) c FROM comments WHERE task_id=?",
                    (row["task_id"],)).fetchone()["c"]
                card["comment_count"] = cs
            nodes.append({"task": card, "depth": level})
            counts[row["state"]] = counts.get(row["state"], 0) + 1
            if row["type"] == "work" and row["state"] in ("queued", "claimed"):
                open_work += 1
        else:
            nodes.append({"task": {"task_id": row["task_id"],
                                   "state": row["state"],
                                   "restricted": True}, "depth": level})
        children = by_parent.get(row["task_id"], [])
        if level >= depth:
            if children:
                truncated = True
            return
        for ch in children:
            if not include_terminal and ch["state"] in TASK_TERMINAL \
                    and not ch["task_id"] == root_task_id:
                # keep ancestors' structure; skip terminal leaves when excluded
                kids = by_parent.get(ch["task_id"], [])
                if not kids:
                    continue
            edges.append({"from": row["task_id"], "to": ch["task_id"]})
            walk(ch, level + 1)

    root_row = svc.store.conn.execute("SELECT * FROM tasks WHERE task_id=?",
                                      (rid,)).fetchone()
    if root_row["task_id"] == root_task_id:
        walk(root_row, 0)
    else:
        # descendant query: walk still from tree root, marking query node
        walk(root_row, 0)
    return {"nodes": nodes, "edges": edges, "counts_by_state": counts,
            "open_work_count": open_work, "truncated": truncated}
