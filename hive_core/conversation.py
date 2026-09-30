"""Conversation bridge core: durable per-agent input queue, conversation
event store and instance→session bindings for the ACP 会话舱.

Chat content lives in conv_* tables (never in the ops `events` feed).
The dispatcher is fed by ConversationService.tick() from the scheduler
(non-blocking: it only hands work to the bridge; I/O happens on bridge
threads). Hive task semantics are untouched — conversation events never
settle business tasks.
"""
from __future__ import annotations

import json
import queue
import threading

from .errors import HiveError
from .ids import new_id
from .timeutil import format_rfc3339, now_utc

INPUT_STATES = ("queued", "dispatched", "running", "completed",
                "cancelled", "failed", "outcome_unknown")
EVENT_RETENTION = 5000          # per session ring; older → explicit gap
HISTORY_PAGE_MAX = 200


# ---------------------------------------------------------------- bindings
def get_binding(svc, agent_id: str) -> dict | None:
    r = svc.store.conn.execute(
        "SELECT * FROM conv_bindings WHERE agent_id=?", (agent_id,)).fetchone()
    return dict(r) if r else None


def bind_session(svc, agent_id: str, instance_id: str, generation: int,
                 session_id: str, project_root: str | None,
                 connection_state: str = "connected",
                 source: str = "acp") -> dict:
    now = svc.now()
    with svc.store.write() as cur:
        cur.execute(
            "INSERT INTO conv_bindings(binding_id, agent_id, instance_id,"
            " generation, session_id, project_root, connection_state, source,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(agent_id) DO UPDATE SET instance_id=excluded.instance_id,"
            " generation=excluded.generation, session_id=excluded.session_id,"
            " project_root=excluded.project_root,"
            " connection_state=excluded.connection_state, source=excluded.source,"
            " updated_at=excluded.updated_at",
            (new_id("binding"), agent_id, instance_id, generation, session_id,
             project_root, connection_state, source, now, now))
    return get_binding(svc, agent_id)


def set_connection_state(svc, agent_id: str, state: str):
    with svc.store.write() as cur:
        cur.execute("UPDATE conv_bindings SET connection_state=?, updated_at=?"
                    " WHERE agent_id=?", (state, svc.now(), agent_id))


def live_instance_of(svc, agent_id: str):
    return svc.store.conn.execute(
        "SELECT * FROM instances WHERE agent_id=? AND phase IN "
        "('starting','live','stopping') ORDER BY generation DESC LIMIT 1",
        (agent_id,)).fetchone()


def check_instance_fresh(svc, agent_id: str, instance_id: str | None,
                         generation: int | None):
    """旧面板禁止向新实例误发：携带的 instance/generation 必须与当前活跃实例一致。"""
    inst = live_instance_of(svc, agent_id)
    if not inst:
        raise HiveError("NO_LIVE_INSTANCE", "该 Agent 当前无运行实例",
                        {"agent_id": agent_id})
    if instance_id and instance_id != inst["instance_id"]:
        raise HiveError("STALE_INSTANCE",
                        "实例已更换，请刷新会话绑定后再发送",
                        {"current_instance": inst["instance_id"],
                         "current_generation": inst["generation"]})
    if generation is not None and int(generation) != inst["generation"]:
        raise HiveError("STALE_INSTANCE",
                        "实例代际已变化，请刷新会话绑定后再发送",
                        {"current_instance": inst["instance_id"],
                         "current_generation": inst["generation"]})
    return inst


# ---------------------------------------------------------------- events
def add_conv_event(svc, *, session_id: str, type: str, payload: dict,
                   agent_id=None, instance_id=None, generation=None,
                   source="acp", turn_id=None, row_id=None, tool_call_id=None,
                   message_id=None, upstream_seq=None, replay=False,
                   dedupe_key: str | None = None) -> dict:
    """Append one conversation event. dedupe_key → event_id 去重（重放/重连）。"""
    eid = dedupe_key or new_id("event")
    now = svc.now()
    with svc.store.write() as cur:
        cur.execute(
            "INSERT OR IGNORE INTO conv_events(event_id, session_id, agent_id,"
            " instance_id, generation, type, source, payload, turn_id, row_id,"
            " tool_call_id, message_id, upstream_seq, replay, recorded_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (eid, session_id, agent_id, instance_id, generation, type, source,
             json.dumps(payload, ensure_ascii=False), turn_id, row_id,
             tool_call_id, message_id, upstream_seq, 1 if replay else 0, now))
        inserted = cur.execute("SELECT changes()").fetchone()[0] == 1
        if inserted:
            # per-session ring retention
            cur.execute(
                "DELETE FROM conv_events WHERE session_id=? AND cursor < "
                "(SELECT MIN(cursor) FROM (SELECT cursor FROM conv_events WHERE"
                " session_id=? ORDER BY cursor DESC LIMIT ?))",
                (session_id, session_id, EVENT_RETENTION))
    row = svc.store.conn.execute(
        "SELECT * FROM conv_events WHERE event_id=?", (eid,)).fetchone()
    ev = _ev_card(row)
    ev["_inserted"] = inserted
    _notify_subscribers(svc, ev)
    return ev


def _ev_card(r) -> dict:
    return {"cursor": r["cursor"], "event_id": r["event_id"],
            "session_id": r["session_id"], "agent_id": r["agent_id"],
            "instance_id": r["instance_id"], "generation": r["generation"],
            "type": r["type"], "source": r["source"],
            "payload": json.loads(r["payload"]), "turn_id": r["turn_id"],
            "row_id": r["row_id"], "tool_call_id": r["tool_call_id"],
            "message_id": r["message_id"], "upstream_seq": r["upstream_seq"],
            "replay": bool(r["replay"]), "recorded_at": r["recorded_at"]}


def history(svc, session_id: str, after: int = 0, limit: int = 100) -> dict:
    limit = min(max(1, limit), HISTORY_PAGE_MAX)
    rows = svc.store.conn.execute(
        "SELECT * FROM conv_events WHERE session_id=? AND cursor>? "
        "ORDER BY cursor LIMIT ?", (session_id, after, limit)).fetchall()
    earliest = svc.store.conn.execute(
        "SELECT MIN(cursor) m FROM conv_events WHERE session_id=?",
        (session_id,)).fetchone()["m"]
    gap = bool(earliest and after and after < earliest - 1)
    return {"items": [_ev_card(r) for r in rows],
            "next_after": rows[-1]["cursor"] if rows else after,
            "gap_before": gap,
            "earliest_cursor": earliest}


# ---------------- SSE fanout (in-process subscribers; store poll fallback) --
_sub_lock = threading.Lock()
_subscribers: list[queue.Queue] = []


def _notify_subscribers(svc, ev: dict):
    with _sub_lock:
        subs = list(_subscribers)
    for q in subs:
        try:
            q.put_nowait(ev)
        except queue.Full:
            pass            # slow consumer backpressure: drop, client resyncs


def subscribe() -> queue.Queue:
    q = queue.Queue(maxsize=500)
    with _sub_lock:
        _subscribers.append(q)
    return q


def unsubscribe(q: queue.Queue):
    with _sub_lock:
        if q in _subscribers:
            _subscribers.remove(q)


# ---------------------------------------------------------------- inputs
def input_card(r) -> dict:
    return {"input_id": r["input_id"], "request_id": r["request_id"],
            "agent_id": r["agent_id"], "instance_id": r["instance_id"],
            "generation": r["generation"], "session_id": r["session_id"],
            "mode": r["mode"], "kind": r["kind"], "text": r["text"],
            "attachments": json.loads(r["attachments"]),
            "state": r["state"], "upstream_input_id": r["upstream_input_id"],
            "turn_id": r["turn_id"], "error": r["error"],
            "created_by": r["created_by"], "created_at": r["created_at"],
            "dispatched_at": r["dispatched_at"], "started_at": r["started_at"],
            "ended_at": r["ended_at"]}


def enqueue_input(svc, subject, *, agent_id: str, text: str, mode: str,
                  request_id: str, kind: str = "user",
                  attachments: list | None = None,
                  instance_id: str | None = None,
                  generation: int | None = None) -> dict:
    """持久入队（幂等：request_id 唯一约束 + 内容比对）。同一 Agent 的
    wake 类输入按未派发合并；用户消息绝不合并。"""
    if mode not in ("queue", "interrupt"):
        raise HiveError("INVALID_ARGUMENT", "mode 必须是 queue/interrupt")
    if kind == "user" and not (text or "").strip() and not attachments:
        raise HiveError("REQUIRED_ARGUMENT", "消息内容为空")
    inst = check_instance_fresh(svc, agent_id, instance_id, generation)
    now = svc.now()
    with svc.store.write() as cur:
        existing = cur.execute(
            "SELECT * FROM conv_inputs WHERE agent_id=? AND request_id=?",
            (agent_id, request_id)).fetchone()
        if existing:
            if existing["text"] != text:
                raise HiveError("IDEMPOTENCY_CONFLICT",
                                "相同 request-id 但内容不同",
                                {"request_id": request_id})
            return {"input": input_card(existing), "replayed": True}
        if kind == "wake":
            # 唤醒类合并：已有未派发 wake 直接复用
            pending = cur.execute(
                "SELECT * FROM conv_inputs WHERE agent_id=? AND kind='wake' "
                "AND state='queued'", (agent_id,)).fetchone()
            if pending:
                return {"input": input_card(pending), "coalesced": True}
        binding = get_binding(svc, agent_id)
        iid = new_id("input")
        cur.execute(
            "INSERT INTO conv_inputs(input_id, request_id, agent_id,"
            " instance_id, generation, session_id, mode, kind, text,"
            " attachments, state, created_by, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (iid, request_id, agent_id, inst["instance_id"], inst["generation"],
             binding and binding["session_id"], mode, kind, text,
             json.dumps(attachments or [], ensure_ascii=False), "queued",
             subject.ref if subject else None, now))
    return {"input": input_card(svc.store.conn.execute(
        "SELECT * FROM conv_inputs WHERE input_id=?", (iid,)).fetchone()),
        "replayed": False}


def set_input_state(svc, input_id: str, state: str, **fields):
    if state not in INPUT_STATES:
        raise HiveError("INVALID_ARGUMENT", f"输入状态非法: {state}")
    cols = {"dispatched": "dispatched_at", "running": "started_at"}
    end_states = {"completed", "cancelled", "failed", "outcome_unknown"}
    now = svc.now()
    sets = ["state=?"]
    params: list = [state]
    if state in cols:
        sets.append(f"{cols[state]}=?"); params.append(now)
    if state in end_states:
        sets.append("ended_at=?"); params.append(now)
    for k in ("upstream_input_id", "turn_id", "error", "session_id"):
        if k in fields:
            sets.append(f"{k}=?"); params.append(fields[k])
    params.append(input_id)
    with svc.store.write() as cur:
        cur.execute(f"UPDATE conv_inputs SET {', '.join(sets)}"
                    " WHERE input_id=?", params)


def list_inputs(svc, agent_id: str, limit: int = 50) -> list[dict]:
    rows = svc.store.conn.execute(
        "SELECT * FROM conv_inputs WHERE agent_id=? ORDER BY created_at DESC,"
        " input_id LIMIT ?", (agent_id, min(max(1, limit), 200))).fetchall()
    return [input_card(r) for r in rows]


def next_queued(svc, agent_id: str):
    return svc.store.conn.execute(
        "SELECT * FROM conv_inputs WHERE agent_id=? AND state='queued'"
        " ORDER BY created_at, input_id LIMIT 1", (agent_id,)).fetchone()


def active_input(svc, agent_id: str):
    return svc.store.conn.execute(
        "SELECT * FROM conv_inputs WHERE agent_id=? AND state IN "
        "('dispatched','running') ORDER BY created_at DESC LIMIT 1",
        (agent_id,)).fetchone()


def pending_permission(svc, agent_id: str):
    r = svc.store.conn.execute(
        "SELECT * FROM conv_events WHERE agent_id=? AND type='permission.request' "
        "ORDER BY cursor DESC LIMIT 1", (agent_id,)).fetchone()
    return _ev_card(r) if r else None


# ------------------------------------------------------------- projections
def project_messages(svc, session_id: str, limit: int = 400) -> dict:
    """把事件流投影成会话视图：用户/Agent/系统消息 + 工具卡片 + 轮次状态。
    delta 合并、replace 覆盖、replay 标记保留；工具按 tool_call_id 归并。"""
    items = history(svc, session_id, after=0, limit=limit)["items"]
    messages: list[dict] = []
    by_row: dict[str, dict] = {}
    turns = []
    for ev in items:
        p = ev["payload"] or {}
        t = ev["type"]
        rid = ev.get("row_id")
        if t == "user.message":
            messages.append({"kind": "user", "id": ev["event_id"],
                             "text": p.get("text", ""),
                             "attachments": p.get("attachments") or [],
                             "at": ev["recorded_at"],
                             "source": p.get("source", "user"),
                             "state": p.get("state", "completed")})
        elif t in ("agent.delta", "agent.message", "agent.thought"):
            if rid and rid in by_row and t == "agent.delta":
                m = by_row[rid]
                m["text"] = p.get("text", m["text"])
                m["state"] = p.get("state", "streaming")
                m["at"] = ev["recorded_at"]
            else:
                m = {"kind": "assistant" if t != "agent.thought" else "thought",
                     "id": rid or ev["event_id"], "text": p.get("text", ""),
                     "state": p.get("state", "streaming"),
                     "at": ev["recorded_at"],
                     "replay": ev["replay"]}
                messages.append(m)
                if rid:
                    by_row[rid] = m
        elif t == "agent.message.final" and rid and rid in by_row:
            by_row[rid]["state"] = "completed"
            by_row[rid]["text"] = p.get("text", by_row[rid]["text"])
        elif t == "tool.call":
            tcid = ev.get("tool_call_id") or rid or ev["event_id"]
            if tcid in by_row:
                card = by_row[tcid]
                card["state"] = p.get("state", card["state"])
                if p.get("result") is not None:
                    card["result"] = p.get("result")
                if p.get("error"):
                    card["error"] = p.get("error")
                card["at"] = ev["recorded_at"]
            else:
                card = {"kind": "tool", "id": tcid,
                        "tool": p.get("tool") or p.get("name") or "tool",
                        "title": p.get("title") or "",
                        "state": p.get("state", "running"),
                        "args": p.get("args"), "result": p.get("result"),
                        "at": ev["recorded_at"]}
                messages.append(card)
                by_row[tcid] = card
        elif t == "turn.started":
            turns.append({"turn_id": ev.get("turn_id"), "state": "running",
                          "at": ev["recorded_at"]})
        elif t in ("turn.completed", "turn.failed", "turn.cancelled"):
            tid = ev.get("turn_id")
            for tr in reversed(turns):
                if tr["turn_id"] == tid:
                    tr["state"] = {"turn.completed": "completed",
                                   "turn.failed": "failed",
                                   "turn.cancelled": "cancelled"}[t]
                    tr["stop_reason"] = p.get("stop_reason")
                    tr["usage"] = p.get("usage")
                    break
        elif t == "permission.request":
            messages.append({"kind": "permission", "id": ev["event_id"],
                             "tool": p.get("tool"), "options": p.get("options") or [],
                             "request_id": p.get("request_id"),
                             "state": p.get("state", "pending"),
                             "at": ev["recorded_at"]})
        elif t == "system.note":
            messages.append({"kind": "system", "id": ev["event_id"],
                             "text": p.get("text", ""), "at": ev["recorded_at"]})
    current_turn = next((tr for tr in reversed(turns)
                         if tr["state"] == "running"), None)
    return {"messages": messages, "turns": turns[-20:],
            "current_turn": current_turn}
