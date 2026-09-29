"""Built-in triggers: manual / interval / cron per DESIGN CMD-077..083."""
from __future__ import annotations

import datetime
import hashlib
import json

from .errors import HiveError
from .ids import new_id
from .timeutil import format_rfc3339, parse_rfc3339

TRIGGER_FIELDS = {"name", "kind", "producer", "task", "every", "cron", "timezone",
                  "enabled", "missed", "metadata"}
TRIGGER_SPEC_FIELDS = {"type", "target", "title", "summary", "body", "data",
                       "attachments", "urgency", "callback", "claim_by",
                       "claim_within", "expires_at", "expires_in", "reply_to"}


def validate_timezone(tz: str):
    import re
    if tz in ("UTC", "Z", "utc"):
        return "UTC"
    if re.match(r"^[+-]\d{2}:\d{2}$", tz):
        hh, mm = int(tz[1:3]), int(tz[4:6])
        if hh > 23 or mm > 59 or (hh == 0 and mm == 0 and tz[0] == "-"):
            raise HiveError("DEADLINE_INVALID", f"时区偏移不合法: {tz}")
        return tz
    raise HiveError("SCHEMA_UNSUPPORTED",
                    f"V1 仅支持 UTC 或固定偏移 ±HH:MM，不支持 {tz}")


def parse_cron_field(expr: str, lo: int, hi: int) -> list[int] | None:
    """Five-field cron subset: * , - /. Returns sorted allowed values or None."""
    import re
    values: set[int] = set()
    for part in expr.split(","):
        m = re.match(r"^(\*|\d+-\d+|\d+)(?:/(\d+))?$", part.strip())
        if not m:
            return None
        base, step = m.group(1), m.group(2)
        step = int(step) if step else 1
        if step <= 0:
            return None
        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            a, b = base.split("-")
            start, end = int(a), int(b)
            if start < lo or end > hi or start > end:
                return None
        else:
            v = int(base)
            if v < lo or v > hi:
                return None
            start = end = v
            if m.group(2):
                return None  # step on single number is meaningless/unsupported
        values.update(range(start, end + 1, step))
    return sorted(values)


def compile_cron(expr: str):
    fields = expr.split()
    if len(fields) != 5:
        raise HiveError("INVALID_ARGUMENT", "cron 必须为五字段：分 时 日 月 周")
    minute = parse_cron_field(fields[0], 0, 59)
    hour = parse_cron_field(fields[1], 0, 23)
    dom = parse_cron_field(fields[2], 1, 31)
    month = parse_cron_field(fields[3], 1, 12)
    dow = parse_cron_field(fields[4], 0, 6)
    if any(v is None for v in (minute, hour, dom, month, dow)):
        raise HiveError("INVALID_ARGUMENT", f"cron 表达式不合法: {expr}")
    return {"minute": minute, "hour": hour, "dom": dom, "month": month,
            "dow": dow}


def next_cron_time(compiled: dict, after_utc: datetime.datetime,
                   tz_offset_minutes: int = 0) -> datetime.datetime:
    """Next fire time strictly after `after_utc`, computed in the trigger TZ."""
    local = after_utc.astimezone(datetime.timezone.utc) + \
        datetime.timedelta(minutes=tz_offset_minutes)
    local = local.replace(second=0, microsecond=0) + datetime.timedelta(minutes=1)
    for _ in range(366 * 24 * 60):  # bounded search: one year of minutes
        if local.minute in compiled["minute"] and local.hour in compiled["hour"] \
                and local.month in compiled["month"]:
            py_dow = local.weekday()  # Mon=0..Sun=6
            cron_dow = 0 if py_dow == 6 else py_dow + 1  # cron: Sun=0..Sat=6
            dom_ok = local.day in compiled["dom"]
            dow_ok = cron_dow in compiled["dow"]
            dom_restricted = compiled["dom"] != list(range(1, 32))
            dow_restricted = compiled["dow"] != list(range(0, 7))
            if dom_restricted and dow_restricted:
                ok = dom_ok or dow_ok  # standard cron OR semantics
            else:
                ok = dom_ok and dow_ok
            if ok:
                return _to_utc(local, tz_offset_minutes)
        local += datetime.timedelta(minutes=1)
    raise HiveError("INVALID_ARGUMENT", "一年内找不到下次触发时间")


def _to_utc(local_dt: datetime.datetime, tz_offset_minutes: int):
    return local_dt.replace(
        tzinfo=datetime.timezone(datetime.timedelta(minutes=tz_offset_minutes))
    ).astimezone(datetime.timezone.utc)


def tz_offset_minutes(tz: str) -> int:
    if tz in ("UTC", "Z", "utc"):
        return 0
    sign = 1 if tz[0] == "+" else -1
    return sign * (int(tz[1:3]) * 60 + int(tz[4:6]))


def validate_task_spec(spec: dict):
    if not isinstance(spec, dict):
        raise HiveError("INVALID_ARGUMENT", "TaskSpec 必须是对象")
    unknown = set(spec) - TRIGGER_SPEC_FIELDS
    if unknown:
        raise HiveError("SCHEMA_UNSUPPORTED", f"TaskSpec 未知字段: {sorted(unknown)}")
    if not spec.get("target"):
        raise HiveError("REQUIRED_ARGUMENT", "TaskSpec.target 必填")
    if not spec.get("title"):
        raise HiveError("REQUIRED_ARGUMENT", "TaskSpec.title 必填")
    if spec.get("callback") == "self":
        raise HiveError("CALLBACK_TARGET_REQUIRED",
                        "无agent身份不能callback=self")
    if spec.get("type") not in (None, "work", "notice", "message"):
        raise HiveError("INVALID_ARGUMENT", "TaskSpec.type 非法")


def create_trigger(svc, subject, args, rid) -> dict:
    svc.authz.require(subject, "trigger.manage")
    kind = args.get("kind")
    if kind not in ("manual", "interval", "cron"):
        raise HiveError("INVALID_ARGUMENT", "kind 只能是 manual/interval/cron")
    prow = svc.store.conn.execute("SELECT * FROM producers WHERE producer_id=?",
                                  (args["producer"],)).fetchone()
    if not prow or prow["kind"] != "builtin" or not prow["enabled"]:
        raise HiveError("INVALID_ARGUMENT", "producer 必须为启用的 builtin 主体")
    spec = args.get("task") or {}
    validate_task_spec(spec)
    every_seconds = None
    cron = None
    compiled = None
    if kind == "interval":
        if not args.get("every"):
            raise HiveError("REQUIRED_ARGUMENT", "interval 需要 --every")
        from .timeutil import parse_duration
        every_seconds = parse_duration(args["every"])
    elif kind == "cron":
        if not args.get("cron"):
            raise HiveError("REQUIRED_ARGUMENT", "cron 需要 --cron")
        cron = args["cron"]
        compiled = compile_cron(cron)
    else:
        if args.get("every") or args.get("cron"):
            raise HiveError("INVALID_ARGUMENT", "manual 不需要调度参数")
    tz = validate_timezone(args.get("timezone") or "UTC")
    trigger_id = new_id("trigger")
    now = svc.now()
    next_run = None
    if kind == "interval" and args.get("enabled"):
        next_run = format_rfc3339(parse_rfc3339(now)
                                  + datetime.timedelta(seconds=every_seconds))
    elif kind == "cron" and args.get("enabled"):
        next_run = format_rfc3339(next_cron_time(compiled, parse_rfc3339(now),
                                                 tz_offset_minutes(tz)))
    with svc.store.write() as cur:
        cur.execute(
            "INSERT INTO triggers(trigger_id,name,kind,producer_id,task_spec,"
            "every_seconds,cron,timezone,enabled,missed,revision,next_run_at,"
            "metadata,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (trigger_id, args["name"], kind, args["producer"],
             json.dumps(spec, ensure_ascii=False), every_seconds, cron, tz,
             1 if args.get("enabled") else 0, args.get("missed", "skip"), 1,
             next_run, "{}", now))
        svc.store.audit(cur, subject.ref, "trigger.create", trigger_id,
                        "create trigger", rid)
    return {"trigger": _trigger_card(svc, trigger_id)}


def _trigger_card(svc, trigger_id: str) -> dict:
    r = svc.store.conn.execute("SELECT * FROM triggers WHERE trigger_id=?",
                               (trigger_id,)).fetchone()
    return {"trigger_id": r["trigger_id"], "name": r["name"], "kind": r["kind"],
            "producer_id": r["producer_id"], "every_seconds": r["every_seconds"],
            "cron": r["cron"], "timezone": r["timezone"],
            "enabled": bool(r["enabled"]), "missed": r["missed"],
            "next_run_at": r["next_run_at"], "revision": r["revision"]}


def fire_trigger(svc, subject, trigger_id: str, event_id: str, data: dict,
                 *, scheduled_time: str | None = None) -> dict:
    """Fire once; producer event dedup by (producer_id, event_id)."""
    t = svc.store.conn.execute("SELECT * FROM triggers WHERE trigger_id=?",
                               (trigger_id,)).fetchone()
    if not t:
        raise HiveError("NOT_FOUND", f"触发器不存在: {trigger_id}")
    spec = json.loads(t["task_spec"])
    merged = dict(spec.get("data") or {})
    for k, v in (data or {}).items():
        if k in ("target", "source", "state", "owner", "type", "title"):
            raise HiveError("INVALID_ARGUMENT", f"data 不能覆盖系统字段 {k}")
        merged[k] = v
    event_ref = f"{t['trigger_id']}:{event_id}" + \
        (f"@{scheduled_time}" if scheduled_time else "")
    existing = svc.store.conn.execute(
        "SELECT task_id FROM producer_events WHERE producer_id=? AND event_id=?",
        (t["producer_id"], event_ref)).fetchone()
    if existing:
        return {"task_id": existing["task_id"], "deduplicated": True}
    content_hash = hashlib.sha256(json.dumps(
        {"spec": spec, "data": merged, "scheduled": scheduled_time},
        sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    # producers' subject_id mirrors producer_id (created at producer create)
    from .auth import Subject
    producer_subject = Subject(t["producer_id"], "producer", t["producer_id"])
    task = None
    from .tasks import create_task
    task = create_task(
        svc, producer_subject, target=spec["target"],
        task_type=spec.get("type", "work"), title=spec["title"],
        parent_id=None, summary=spec.get("summary", ""),
        body=spec.get("body", ""), data=merged,
        attachments=spec.get("attachments") or [],
        urgency=spec.get("urgency", "normal"),
        callback=spec.get("callback", "none"),
        reply_to=spec.get("reply_to"),
        claim_by=spec.get("claim_by"), claim_within=spec.get("claim_within"),
        expires_at=spec.get("expires_at"), expires_in=spec.get("expires_in"),
        source_extra={"trigger": t["trigger_id"],
                      "trigger_event": event_ref,
                      "scheduled_time": scheduled_time},
        root=True)
    with svc.store.write() as cur:
        cur.execute(
            "INSERT OR IGNORE INTO producer_events(producer_id,event_id,task_id,"
            "content_hash,created_at) VALUES (?,?,?,?,?)",
            (t["producer_id"], event_ref, task["task_id"], content_hash,
             svc.now()))
    return {"task_id": task["task_id"], "deduplicated": False}


def run_due_triggers(svc):
    now = svc.now()
    rows = svc.store.conn.execute(
        "SELECT * FROM triggers WHERE enabled=1 AND next_run_at IS NOT NULL AND "
        "next_run_at<=?", (now,)).fetchall()
    for t in rows:
        due_at = parse_rfc3339(t["next_run_at"])
        try:
            fire_trigger(svc, None, t["trigger_id"],
                         event_id=t["next_run_at"], data={},
                         scheduled_time=t["next_run_at"])
        except Exception as e:  # noqa: BLE001
            svc.logger.warning("trigger.fire.failed", trigger=t["trigger_id"],
                               error=repr(e))
        # compute next run (skip missed ones; 'once' collapses them)
        nxt = _advance(svc, t, due_at)
        with svc.store.write() as cur:
            cur.execute("UPDATE triggers SET next_run_at=?, last_cursor=? WHERE "
                        "trigger_id=?", (format_rfc3339(nxt), t["next_run_at"],
                                         t["trigger_id"]))


def _advance(svc, t, due_at: datetime.datetime) -> datetime.datetime:
    now = parse_rfc3339(svc.now())
    if t["kind"] == "interval":
        step = datetime.timedelta(seconds=t["every_seconds"] or 60)
        nxt = due_at + step
        while nxt <= now:
            if t["missed"] == "once":
                # collapse all missed into one: next from now
                nxt = now + step
                break
            nxt += step
        return nxt
    if t["kind"] == "cron":
        compiled = compile_cron(t["cron"])
        tzoff = tz_offset_minutes(t["timezone"])
        nxt = next_cron_time(compiled, due_at, tzoff)
        if t["missed"] == "skip":
            while nxt <= now:
                nxt = next_cron_time(compiled, nxt, tzoff)
        elif nxt <= now:
            nxt = next_cron_time(compiled, now, tzoff)
        return nxt
    return now
