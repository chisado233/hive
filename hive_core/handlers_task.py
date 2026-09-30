"""Handlers for inbox.* and emit.* commands."""
from __future__ import annotations

import json

from .errors import HiveError
from .tasks import (SPEC_FIELDS, add_comment, can_read_task, claim_task,
                    create_task, decline_task, forward_task, get_task,
                    require_task_visible, settle_task, task_card, task_tree)
from .timeutil import format_rfc3339


def register(svc):
    svc.register("inbox.list", h_inbox_list)
    svc.register("inbox.peek", h_inbox_peek)
    svc.register("inbox.claim", h_inbox_claim)
    svc.register("inbox.decline", h_inbox_decline)
    svc.register("inbox.tree", h_inbox_tree)
    svc.register("inbox.wait.create", h_wait_create)
    svc.register("inbox.wait.list", h_wait_list)
    svc.register("inbox.wait.show", h_wait_show)
    svc.register("inbox.wait.cancel", h_wait_cancel)
    svc.register("emit.new", h_emit_new)
    svc.register("emit.done", h_emit_done)
    svc.register("emit.fail", h_emit_fail)
    svc.register("emit.trash", h_emit_trash)
    svc.register("emit.forward", h_emit_forward)
    svc.register("emit.comment", h_emit_comment)
    svc.register("emit.batch", h_emit_batch)
    svc.register("producer.emit", h_producer_emit)


# ---------------- inbox ----------------
def _inbox_agent(args, subject):
    if args.get("agent") and args["agent"] != subject.subject_id and \
            subject.subject_type not in ("owner", "system"):
        # viewing another agent's inbox requires authorization
        raise HiveError("FORBIDDEN", "查看他人inbox需要授权（--agent 不改变调用身份）")
    return args.get("agent") or subject.subject_id


VIEW_QUERY = {
    "personal": "SELECT t.* FROM tasks t WHERE t.target=?",
    "assigned": "SELECT t.* FROM tasks t WHERE t.owner_subject=? AND t.state="
                "'claimed'",
    "available": None,  # computed
    "sent": "SELECT t.* FROM tasks t WHERE t.source LIKE ?",
    "history": "SELECT t.* FROM tasks t WHERE (t.target=? OR t.owner_subject=?) "
               "AND t.state IN ('done','fail','trash')",
    "declined": "SELECT t.* FROM tasks t WHERE t.task_id IN (SELECT task_id FROM "
                "task_declines WHERE subject_id=?)",
}


def h_inbox_list(svc, subject, args, rid):
    view = args.get("view", "all")
    agent_id = args.get("agent") or subject.subject_id
    if args.get("agent") and args["agent"] != subject.subject_id and \
            subject.subject_type not in ("owner", "system"):
        svc.authz.require(subject, "task.read", f"agent:{args['agent']}")
    items_by_id: dict[str, dict] = {}
    memberships: dict[str, list[str]] = {}

    def add_view(name: str, rows):
        for r in rows:
            if not can_read_task(svc, subject, r):
                continue
            if args.get("type") and r["type"] != args["type"]:
                continue
            if args.get("urgency") and r["urgency"] != args["urgency"]:
                continue
            if args.get("state"):
                if r["state"] != args["state"]:
                    continue
            elif name == "assigned" and r["state"] != "claimed":
                continue
            elif name == "available" and r["state"] != "queued":
                continue
            card = task_card(r)
            items_by_id.setdefault(r["task_id"], card)
            memberships.setdefault(r["task_id"], []).append(name)

    if view in ("all", "personal"):
        add_view("personal", svc.store.conn.execute(
            "SELECT * FROM tasks WHERE target=?", (f"agent:{agent_id}",)))
    if view in ("all", "assigned"):
        add_view("assigned", svc.store.conn.execute(
            "SELECT * FROM tasks WHERE owner_subject=? AND state='claimed'",
            (agent_id,)))
    if view in ("all", "available"):
        # subscribed ports' queued tasks + personal queued work
        rows = []
        for sub in svc.store.conn.execute(
                "SELECT port_id FROM subscriptions WHERE agent_id=? AND enabled=1",
                (agent_id,)):
            rows.extend(svc.store.conn.execute(
                "SELECT * FROM tasks WHERE target=? AND state='queued'",
                (f"port:{sub['port_id']}",)))
        rows.extend(svc.store.conn.execute(
            "SELECT * FROM tasks WHERE target=? AND state='queued'",
            (f"agent:{agent_id}",)))
        if not args.get("include_declined"):
            declined = {r["task_id"] for r in svc.store.conn.execute(
                "SELECT d.task_id FROM task_declines d JOIN tasks t ON t.task_id="
                "d.task_id WHERE d.subject_id=? AND d.spec_version=t.spec_version",
                (agent_id,))}
            rows = [r for r in rows if r["task_id"] not in declined]
        add_view("available", rows)
    if view == "sent":
        add_view("sent", svc.store.conn.execute(
            "SELECT * FROM tasks WHERE source LIKE ?",
            (f'%"{subject.subject_id}"%',)))
    if view == "history":
        add_view("history", svc.store.conn.execute(
            "SELECT * FROM tasks WHERE (target=? OR owner_subject=?) AND state IN "
            "('done','fail','trash')", (f"agent:{agent_id}", agent_id)))
    if view == "declined":
        add_view("declined", svc.store.conn.execute(
            "SELECT t.* FROM tasks t JOIN task_declines d ON d.task_id=t.task_id "
            "WHERE d.subject_id=?", (agent_id,)))

    items = list(items_by_id.values())
    order = args.get("sort", "priority")
    urgency_rank = {"urgent": 0, "normal": 1, "low": 2}
    if order == "priority":
        items.sort(key=lambda t: (urgency_rank.get(t["urgency"], 3),
                                  t["claim_deadline"] or "9999",
                                  t["created_at"], t["task_id"]))
    elif order == "created":
        items.sort(key=lambda t: (t["created_at"], t["task_id"]))
    elif order == "deadline":
        items.sort(key=lambda t: (t["claim_deadline"] or t["expires_at"] or "9999",
                                  t["task_id"]))
    limit = args.get("limit") or 50
    cursor = args.get("cursor")
    start = 0
    if cursor:
        import base64
        try:
            start = int(base64.urlsafe_b64decode(cursor.encode()).decode())
        except Exception:  # noqa: BLE001
            raise HiveError("INVALID_ARGUMENT", "游标不合法")
    window = items[start:start + limit]
    for card in window:
        card["memberships"] = memberships.get(card["task_id"], [])
    next_cursor = None
    if start + limit < len(items):
        import base64
        next_cursor = base64.urlsafe_b64encode(str(start + limit).encode()).decode()
    return {"items": window, "counts": {"total": len(items)},
            "next_cursor": next_cursor, "snapshot_at": svc.now()}


def h_inbox_peek(svc, subject, args, rid):
    row = require_task_visible(svc, subject, args["task"])
    data = {"task": task_card(row)}
    if args.get("comments"):
        rows = svc.store.conn.execute(
            "SELECT * FROM comments WHERE task_id=? ORDER BY created_at, comment_id "
            "LIMIT 50", (args["task"],)).fetchall()
        data["comments"] = [{"comment_id": r["comment_id"], "author":
                             r["author_subject"], "body": r["body"],
                            "mentions": json.loads(r["mentions"]),
                            "created_at": r["created_at"],
                            "revision_of": r["revision_of"]} for r in rows]
    if args.get("history"):
        from .handlers_core import _task_history
        data["history"] = _task_history(svc, args["task"])
    data["permissions"] = {
        "can_claim": row["state"] == "queued",
        "can_comment": True,
    }
    from .waits import waits_summary
    data["wait_summary"] = waits_summary(svc, args["task"])
    if args.get("mark_seen"):
        with svc.store.write() as cur:
            cur.execute(
                "INSERT OR REPLACE INTO task_views(view_id,task_id,subject_id,"
                "seen_at) VALUES (?,?,?,?)",
                (f"view:{args['task']}:{subject.subject_id}", args["task"],
                 subject.subject_id, svc.now()))
    return data


def h_inbox_claim(svc, subject, args, rid):
    tasks = args["task"]
    if isinstance(tasks, str):
        tasks = [tasks]
    seen: set[str] = set()
    unique = [t for t in tasks if not (t in seen or seen.add(t))]
    if len(unique) > 100:
        raise HiveError("INVALID_ARGUMENT", "一次最多领取100件")
    if args.get("spec_version") is not None and len(unique) != 1:
        raise HiveError("INVALID_ARGUMENT", "--spec-version 仅单任务可用")
    results = []
    for t in unique:
        results.append(claim_task(svc, subject, t, args.get("spec_version")))
    ok_count = sum(1 for r in results if r["ok"])
    partial = 0 < ok_count < len(results)
    data = {"results": results, "partial": partial,
            "ok": ok_count == len(results)}
    from .service import Service  # noqa: F401
    data["_claim_exit"] = 0 if ok_count == len(results) else \
        (6 if ok_count > 0 else None)
    if ok_count < len(results) and ok_count > 0:
        data["_partial_success"] = True
    return data


def h_inbox_decline(svc, subject, args, rid):
    return decline_task(svc, subject, args["task"], args["reason"])


def h_inbox_tree(svc, subject, args, rid):
    return task_tree(svc, subject, args["task"],
                     depth=args.get("depth", 4),
                     include_terminal=args.get("include_terminal", True),
                     include_comments=args.get("include_comments", False))


def h_wait_create(svc, subject, args, rid):
    from .waits import create_wait
    deps = args["on"]
    if isinstance(deps, str):
        deps = [deps]
    return create_wait(svc, subject, args["task"], deps,
                       mode=args.get("mode", "all"),
                       wait_for=args.get("wait_for"), until=args.get("until"),
                       checkpoint_refs=args.get("checkpoint"),
                       replace=bool(args.get("replace")),
                       claim_id=args.get("claim"))


def h_wait_list(svc, subject, args, rid):
    sql = "SELECT * FROM waits WHERE state=?"
    params: list = [args.get("state", "pending")]
    if args.get("task"):
        sql += " AND owner_task_id=?"
        params.append(args["task"])
    rows = svc.store.conn.execute(sql + " ORDER BY created_at, wait_id",
                                  params).fetchall()
    items = []
    from .waits import _wait_card
    for r in rows:
        trow = svc.store.conn.execute("SELECT * FROM tasks WHERE task_id=?",
                                      (r["owner_task_id"],)).fetchone()
        if trow and can_read_task(svc, subject, trow):
            items.append(_wait_card(r))
    limit = args.get("limit") or 50
    return {"items": items[:limit], "next_cursor": None}


def h_wait_show(svc, subject, args, rid):
    from .waits import _wait_card
    w = svc.store.conn.execute("SELECT * FROM waits WHERE wait_id=?",
                               (args["wait"],)).fetchone()
    if not w:
        raise HiveError("NOT_FOUND", f"等待不存在: {args['wait']}")
    trow = get_task(svc, w["owner_task_id"])
    if not can_read_task(svc, subject, trow):
        raise HiveError("NOT_VISIBLE", "归属任务不可读")
    deps = []
    for r in svc.store.conn.execute(
            "SELECT * FROM wait_dependencies WHERE wait_id=?", (args["wait"],)):
        deps.append({"dependency": r["dependency"],
                     "result": json.loads(r["result"]) if r["result"] else None})
    return {"wait": _wait_card(w), "dependencies": deps,
            "notification_status": "saved" if w["state"] in ("ready", "timed_out")
            else "pending"}


def h_wait_cancel(svc, subject, args, rid):
    from .waits import _wait_card
    w = svc.store.conn.execute("SELECT * FROM waits WHERE wait_id=?",
                               (args["wait"],)).fetchone()
    if not w:
        raise HiveError("NOT_FOUND", f"等待不存在: {args['wait']}")
    trow = get_task(svc, w["owner_task_id"])
    is_owner = trow["owner_subject"] == subject.subject_id
    if not is_owner and subject.subject_type not in ("owner", "system"):
        raise HiveError("FORBIDDEN", "只有当前责任人或有管理权限者可取消")
    if w["state"] == "pending":
        with svc.store.write() as cur:
            cur.execute("UPDATE waits SET state='cancelled', resolved_at=? WHERE "
                        "wait_id=?", (svc.now(), args["wait"]))
            svc.store.audit(cur, subject.ref, "wait.cancel", args["wait"],
                            args["reason"])
    w = svc.store.conn.execute("SELECT * FROM waits WHERE wait_id=?",
                               (args["wait"],)).fetchone()
    return {"wait": _wait_card(w)}


# ---------------- emit ----------------
def h_emit_new(svc, subject, args, rid):
    if not (args.get("root") or args.get("parent")):
        raise HiveError("REQUIRED_ARGUMENT", "必须显式 --root 或 --parent 之一")
    if args.get("root") and args.get("parent"):
        raise HiveError("MUTUALLY_EXCLUSIVE", "--root 与 --parent 互斥")
    spec = args.get("spec")
    content_keys = {"to", "type", "title", "summary", "body", "urgency",
                    "reply_to", "data", "callback", "attach"}
    if spec is not None:
        unknown = set(spec) - SPEC_FIELDS
        if unknown:
            raise HiveError("SCHEMA_UNSUPPORTED", f"TaskSpec 未知字段: {sorted(unknown)}")
        task_type = spec.get("type", "work")
        target = spec.get("target")
        title = spec.get("title")
        body = spec.get("body", "")
        data = spec.get("data") or {}
        attachments = spec.get("attachments") or []
        urgency = spec.get("urgency", "normal")
        callback = spec.get("callback", "none")
        reply_to = spec.get("reply_to")
        claim_by = spec.get("claim_by")
        claim_within = spec.get("claim_within")
        expires_at = spec.get("expires_at")
        expires_in = spec.get("expires_in")
        summary = spec.get("summary", "")
    else:
        if not args.get("to"):
            raise HiveError("REQUIRED_ARGUMENT", "缺少 --to")
        if not args.get("title"):
            raise HiveError("REQUIRED_ARGUMENT", "缺少 --title")
        task_type = args.get("type", "work")
        target = args["to"]
        title = args["title"]
        body = args.get("body") or ""
        data = args.get("data") or {}
        attachments = args.get("attach") or []
        urgency = args.get("urgency", "normal")
        callback = args.get("callback", "none")
        reply_to = args.get("reply_to")
        claim_by = args.get("claim_by")
        claim_within = args.get("claim_within")
        expires_at = args.get("expires_at")
        expires_in = args.get("expires_in")
        summary = args.get("summary") or ""
    if task_type == "work" and not body and not data:
        raise HiveError("INVALID_ARGUMENT", "work 至少要有 body 或非空 data")
    row = create_task(svc, subject, target=target, task_type=task_type,
                      title=title, parent_id=args.get("parent"),
                      summary=summary, body=body, data=data,
                      attachments=attachments, urgency=urgency,
                      callback=callback, reply_to=reply_to,
                      claim_by=claim_by, claim_within=claim_within,
                      expires_at=expires_at, expires_in=expires_in)
    return {"task": task_card(row), "delivery": {"accepted": True},
            "source": json.loads(row["source"])}


def h_producer_emit(svc, subject, args, rid):
    """POST /v1/tasks path for producers with event_id dedup."""
    import hashlib
    spec = {k: v for k, v in args.items()
            if k not in ("event_id", "root", "parent", "_via_http_tasks")}
    event_id = args.get("event_id")
    if event_id:
        existing = svc.store.conn.execute(
            "SELECT task_id, content_hash FROM producer_events WHERE producer_id=? "
            "AND event_id=?", (subject.subject_id, event_id)).fetchone()
        payload_hash = hashlib.sha256(json.dumps(
            spec, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if existing:
            if existing["content_hash"] != payload_hash:
                raise HiveError("PRODUCER_EVENT_CONFLICT",
                                "同event_id不同内容")
            row = get_task(svc, existing["task_id"])
            return {"task": task_card(row), "delivery": {"accepted": True},
                    "source": json.loads(row["source"]), "deduplicated": True}
    result = h_emit_new(svc, subject, {"root": True, "to": spec.get("target"),
                                       "type": spec.get("type", "work"),
                                       "title": spec.get("title"),
                                       "body": spec.get("body", ""),
                                       "data": spec.get("data") or {},
                                       "callback": spec.get("callback", "none"),
                                       "urgency": spec.get("urgency", "normal"),
                                       "claim_within": spec.get("claim_within"),
                                       "expires_in": spec.get("expires_in"),
}, rid)
    if event_id:
        payload_hash = hashlib.sha256(json.dumps(
            spec, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with svc.store.write() as cur:
            cur.execute(
                "INSERT OR IGNORE INTO producer_events(producer_id,event_id,task_id,"
                "content_hash,created_at) VALUES (?,?,?,?,?)",
                (subject.subject_id, event_id, result["task"]["task_id"],
                 payload_hash, svc.now()))
    return result


def _result_from(args) -> dict:
    result: dict = {}
    if args.get("text"):
        result["text"] = args["text"]
    if args.get("data"):
        result["data"] = args["data"]
    if args.get("attach"):
        result["artifacts"] = [{"uri": a} for a in args["attach"]]
    return result


def h_emit_done(svc, subject, args, rid):
    result = settle_task(svc, subject, args["task"], "done",
                         claim_id=args.get("claim"),
                         result=_result_from(args),
                         dispatch=args.get("dispatch"))
    return result


def h_emit_fail(svc, subject, args, rid):
    result = settle_task(svc, subject, args["task"], "fail",
                         claim_id=args.get("claim"),
                         result=_result_from(args) | {
                             "reason_code": args.get("code", "FAILED")},
                         reason=args["reason"])
    return result


def h_emit_trash(svc, subject, args, rid):
    result = _result_from(args)
    if args.get("instruction"):
        result["instruction_ref"] = args["instruction"]
    return settle_task(svc, subject, args["task"], "trash",
                       claim_id=args.get("claim"), result=result,
                       reason=args["reason"])


def h_emit_forward(svc, subject, args, rid):
    return forward_task(svc, subject, args["task"], args["to"], args["reason"],
                        claim_id=args.get("claim"),
                        claim_by=args.get("claim_by"),
                        claim_within=args.get("claim_within"),
                        if_version=args.get("if_version"))


def h_emit_comment(svc, subject, args, rid):
    return add_comment(svc, subject, args["task"], args.get("text") or "",
                       mentions_extra=args.get("mention"),
                       revision_of=args.get("revision_of"),
                       checkpoint_refs=args.get("checkpoint"), rid=rid)


# ---------------- batch ----------------
BATCH_OPS = {"new", "done", "fail", "trash", "forward", "comment", "wait"}


def h_emit_batch(svc, subject, args, rid):
    doc = args.get("input")
    if not isinstance(doc, dict) or not isinstance(doc.get("operations"), list):
        raise HiveError("INVALID_ARGUMENT", "EmitBatch 需要 {operations:[...]}")
    ops = doc["operations"]
    if not (1 <= len(ops) <= 100):
        raise HiveError("INVALID_ARGUMENT", "batch 操作数须为 1..100")
    for op in ops:
        if not isinstance(op, dict) or op.get("op") not in BATCH_OPS:
            raise HiveError("INVALID_ARGUMENT",
                            f"batch 只允许 {sorted(BATCH_OPS)}")
    refs: dict[str, str] = {}
    results: list[dict] = []
    event_ids: list[str] = []

    def resolve_ref(ref: str) -> str:
        if ref.startswith("$"):
            if ref[1:] not in refs:
                raise HiveError("INVALID_ARGUMENT", f"未定义或前向引用 {ref}")
            return refs[ref[1:]]
        return ref

    # two-phase: static validation first, then all ops in ONE transaction
    _static_validate_batch(svc, subject, ops)

    with svc.store.write():
        for op in ops:
            kind = op["op"]
            if kind == "new":
                spec = op.get("task") or {}
                parent = op.get("parent")
                if not parent and not op.get("root", False):
                    raise HiveError("INVALID_ARGUMENT",
                                    "batch new 需要 root 或 parent")
                if parent:
                    parent = resolve_ref(parent) if parent.startswith("$") \
                        else parent
                row = create_task(svc, subject, target=spec["target"],
                                  task_type=spec.get("type", "work"),
                                  title=spec["title"], parent_id=parent,
                                  summary=spec.get("summary", ""),
                                  body=spec.get("body", ""),
                                  data=spec.get("data") or {},
                                  attachments=spec.get("attachments") or [],
                                  urgency=spec.get("urgency", "normal"),
                                  callback=spec.get("callback", "none"),
                                  claim_within=spec.get("claim_within"),
                                  expires_in=spec.get("expires_in"))
                if op.get("ref"):
                    if op["ref"] in refs:
                        raise HiveError("INVALID_ARGUMENT",
                                        f"ref 重复 {op['ref']}")
                    refs[op["ref"]] = row["task_id"]
                results.append({"op": "new", "task_id": row["task_id"],
                                "ok": True})
            elif kind in ("done", "fail", "trash"):
                task_id = resolve_ref(op["task"])
                res = {"done": h_emit_done, "fail": h_emit_fail,
                       "trash": h_emit_trash}[kind](
                    svc, subject, {"task": task_id,
                                   **({"claim": op.get("claim")}
                                      if op.get("claim") else {}),
                                   **({"reason": op["reason"]}
                                      if kind in ("fail", "trash") else {}),
                                   **({"dispatch": op["dispatch"]}
                                      if kind == "done" and op.get("dispatch")
                                      else {})}, rid)
                results.append({"op": kind, "task_id": task_id, "ok": True,
                                "result": res})
            elif kind == "forward":
                task_id = resolve_ref(op["task"])
                res = forward_task(svc, subject, task_id, op["to"], op["reason"],
                                   claim_id=op.get("claim"),
                                   claim_by=op.get("claim_by"),
                                   claim_within=op.get("claim_within"))
                results.append({"op": "forward", "task_id": task_id, "ok": True,
                                "result": res})
            elif kind == "comment":
                task_id = resolve_ref(op["task"])
                res = add_comment(svc, subject, task_id, op.get("text") or "",
                                  mentions_extra=op.get("mentions"),
                                  revision_of=op.get("revision_of"),
                                  checkpoint_refs=op.get("checkpoint_refs"))
                results.append({"op": "comment", "task_id": task_id, "ok": True,
                                "result": res})
            elif kind == "wait":
                task_id = resolve_ref(op["task"])
                deps = []
                for d in op["on"]:
                    v = resolve_ref(d)
                    if not v.startswith(("task:", "operation:")):
                        v = "task:" + v
                    deps.append(v)
                from .waits import create_wait
                res = create_wait(svc, subject, task_id, deps,
                                  mode=op.get("mode", "all"),
                                  wait_for=op.get("wait_for"),
                                  until=op.get("until"),
                                  checkpoint_refs=op.get("checkpoint_refs"),
                                  replace=bool(op.get("replace")),
                                  claim_id=op.get("claim"))
                results.append({"op": "wait", "task_id": task_id, "ok": True,
                                "result": res})
    return {"results": results, "refs": refs, "event_ids": event_ids}


def _static_validate_batch(svc, subject, ops):
    """Pre-validate refs and required fields so the batch cannot half-apply."""
    defined: set[str] = set()
    for op in ops:
        kind = op.get("op")
        if kind == "new":
            spec = op.get("task")
            if not isinstance(spec, dict) or not spec.get("target") or \
                    not spec.get("title"):
                raise HiveError("INVALID_ARGUMENT", "batch new 的 task 需要 "
                                                       "target/title")
            if not op.get("parent") and not op.get("root"):
                raise HiveError("INVALID_ARGUMENT", "batch new 需要 parent/root")
            if op.get("parent") and not op["parent"].startswith("$"):
                pass  # existing id, checked at execution
            if op.get("ref"):
                if op["ref"] in defined:
                    raise HiveError("INVALID_ARGUMENT", f"ref 重复 {op['ref']}")
                defined.add(op["ref"])
        else:
            for field in ("task",):
                v = op.get(field)
                if isinstance(v, str) and v.startswith("$") and v[1:] not in defined:
                    raise HiveError("INVALID_ARGUMENT", f"前向/未定义引用 {v}")
            if kind in ("fail", "trash", "forward", "comment") and \
                    not op.get("reason") and kind != "comment":
                raise HiveError("REQUIRED_ARGUMENT", f"batch {kind} 需要 reason")
            if kind == "comment" and not (op.get("text") or "").strip():
                raise HiveError("REQUIRED_ARGUMENT", "batch comment 需要 text")
            if kind == "wait" and not op.get("on"):
                raise HiveError("REQUIRED_ARGUMENT", "batch wait 需要 on")