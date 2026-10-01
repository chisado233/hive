"""从 zsession daemon 读取会话历史（只读，服务端使用；token 不下发浏览器）。

历史来源优先级：
1. daemon 磁盘事件日志 ~/.zcode/zsession/events/events.jsonl（全量留存，
   daemon 内存环只有 5000 条，老的会话行早已滚出——这是"历史缺失"的根因）
2. daemon HTTP /events（内存环，兜底）

上游 /sessions/:id/messages 在 websocket 模式存在 schema 缺陷
（info.messageId undefined → 500），不在 Hive 侧修上游，走事件流行重建。
"""
from __future__ import annotations

import json
import os

from .errors import HiveError

EVENTS_FILE = os.path.expanduser(os.path.join(
    "~", ".zcode", "zsession", "events", "events.jsonl"))

# (session_id) -> (file_size, mtime, messages)
_history_cache: dict[str, tuple] = {}
MAX_ROWS_PER_SESSION = 600


def _iter_session_rows_file(session_id: str):
    """从磁盘事件日志重建该会话的行序列（op 序应用）。"""
    if not os.path.exists(EVENTS_FILE):
        raise HiveError("HARNESS_UNAVAILABLE", "zsession 事件日志不存在")
    rows: dict[str, dict] = {}
    order: list[str] = []
    with open(EVENTS_FILE, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            # 粗筛：行里不含 sessionId 直接跳过（90MB 文件的关键性能点）
            if session_id not in line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("sessionId") != session_id or ev.get("type") != "session_row":
                continue
            d = ev.get("data") or {}
            row = d.get("row")
            op = d.get("op")
            rid = d.get("rowId") or (row or {}).get("rowId")
            if rid is None:
                continue
            if op in ("row.appended", "row.upserted") and row:
                rows[rid] = row
                if rid not in order:
                    order.append(rid)
            elif op == "row.delta" and rid in rows:
                if isinstance(d.get("append"), str):
                    rows[rid]["text"] = (rows[rid].get("text") or "") + d["append"]
            elif op == "row.removed":
                rows.pop(rid, None)
    return rows, order


def _rows_to_messages(rows: dict, order: list) -> list[dict]:
    messages = []
    for rid in order[-MAX_ROWS_PER_SESSION:]:
        row = rows.get(rid)
        if not row:
            continue
        kind = row.get("kind")
        text = row.get("text") or ""
        state = row.get("state") or row.get("status") or "completed"
        if kind == "userInput":
            messages.append({"kind": "user", "text": text, "state": "completed",
                             "at": None, "source": "history"})
        elif kind == "assistantText":
            messages.append({"kind": "assistant", "text": text,
                             "state": "completed", "at": None,
                             "source": "history"})
        elif kind == "reasoning":
            messages.append({"kind": "thought", "text": text,
                             "state": "completed", "at": None,
                             "source": "history"})
        elif kind == "toolCall":
            messages.append({"kind": "tool",
                             "tool": row.get("toolName") or "tool",
                             "title": (row.get("inputText") or "")[:120],
                             "state": {"success": "completed",
                                       "error": "failed",
                                       "cancelled": "cancelled"}.get(
                                           state, "completed"),
                             "result": (row.get("outputText") or "")[:1000],
                             "error": (row.get("errorText") or "")[:500] or None,
                             "at": None, "source": "history"})
    return messages


def _daemon_cfg(svc):
    path = os.path.expanduser(os.path.join("~", ".zcode", "zsession",
                                           "config.json"))
    if not os.path.exists(path):
        raise HiveError("HARNESS_UNAVAILABLE", "zsession 配置不存在")
    cfg = json.load(open(path, encoding="utf-8"))
    token = (cfg.get("tokens") or {}).get("owner-local", {}).get("token")
    if not token:
        raise HiveError("HARNESS_UNAVAILABLE", "zsession owner-local token 未配置")
    return f"http://127.0.0.1:{cfg.get('port', 8765)}", token


def _get(svc, path: str) -> dict:
    import urllib.request
    base, token = _daemon_cfg(svc)
    req = urllib.request.Request(base + path,
                                 headers={"Authorization": f"Bearer {token}",
                                          "X-ZS-Channel": "hive-console"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.load(r)
    except Exception as e:  # noqa: BLE001
        raise HiveError("HARNESS_UNAVAILABLE", f"zsession 读取失败: {e}",
                        {"retryable": True})
    if isinstance(data, dict) and data.get("ok") is False:
        raise HiveError("HARNESS_UNAVAILABLE",
                        f"zsession 拒绝: {(data.get('error') or {}).get('message')}")
    return data


def fetch_session_state(svc, session_id: str) -> dict:
    d = _get(svc, f"/sessions/{session_id}")
    return {"status": d.get("status"),
            "model": d.get("model"),
            "mode": d.get("mode"),
            "turn": d.get("turn"),
            "name": d.get("name"),
            "created_at": d.get("createdAt"),
            "archived": bool(d.get("archivedAt"))}


def _history_via_api(svc, session_id: str, limit: int) -> dict:
    """上游 /sessions/:id/history（DEF-14 修复后）：message+part 直读、
    分页游标 seq。返回按序文本消息（工具行不在此表，经实时事件获得）。"""
    data = _get(svc, f"/sessions/{session_id}/history?limit={limit}")
    items = data.get("items") or []
    # part 按 messageSeq 聚合（partSeq 顺序拼接）
    msgs: dict[int, dict] = {}
    for it in items:
        ms = it.get("messageSeq", it.get("seq", 0))
        m = msgs.setdefault(ms, {"role": it.get("role"), "parts": []})
        m["parts"].append((it.get("partSeq") or 0, it.get("text") or ""))
    out = []
    for ms in sorted(msgs):
        m = msgs[ms]
        text = "".join(t for _, t in sorted(m["parts"]))
        kind = {"user": "user", "assistant": "assistant"}.get(m["role"])
        if not kind or not text.strip():
            continue
        out.append({"kind": kind, "text": text, "state": "completed",
                    "at": None, "source": "history"})
    total = data.get("count", len(items))
    truncated = bool(data.get("oldestSeq") and data["oldestSeq"] > 1) or \
        len(items) >= limit
    return {"messages": out,
            "note": "存在更早历史（分页游标可继续翻）" if truncated else None,
            "count": len(out)}


def fetch_session_history(svc, session_id: str, limit: int = 3000) -> dict:
    """优先上游会话级分页历史；失败回退磁盘事件日志（含工具行）。"""
    try:
        return _history_via_api(svc, session_id, limit)
    except HiveError:
        pass
    if os.path.exists(EVENTS_FILE):
        st = os.stat(EVENTS_FILE)
        cached = _history_cache.get(session_id)
        if cached and cached[0] == st.st_size and cached[1] == st.st_mtime:
            return {"messages": cached[2], "note": None,
                    "count": len(cached[2])}
        rows, order = _iter_session_rows_file(session_id)
        messages = _rows_to_messages(rows, order)
        _history_cache[session_id] = (st.st_size, st.st_mtime, messages)
        if len(_history_cache) > 40:
            _history_cache.pop(next(iter(_history_cache)))
        return {"messages": messages, "note": None, "count": len(messages)}
    # 兜底：daemon 内存环
    data = _get(svc, f"/events?cursor=0&limit={limit}")
    events = data.get("events") or []
    rows: dict[str, dict] = {}
    order: list[str] = []
    for ev in events:
        if ev.get("sessionId") != session_id or ev.get("type") != "session_row":
            continue
        d = ev.get("data") or {}
        row = d.get("row")
        op = d.get("op")
        rid = d.get("rowId") or (row or {}).get("rowId")
        if rid is None:
            continue
        if op in ("row.appended", "row.upserted") and row:
            rows[rid] = row
            if rid not in order:
                order.append(rid)
        elif op == "row.delta" and rid in rows:
            if isinstance(d.get("append"), str):
                rows[rid]["text"] = (rows[rid].get("text") or "") + d["append"]
        elif op == "row.removed":
            rows.pop(rid, None)
    messages = _rows_to_messages(rows, order)
    return {"messages": messages,
            "note": "历史来自 daemon 内存事件环，早期内容可能已滚出",
            "count": len(messages)}
