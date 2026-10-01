"""从 zsession daemon 读取会话历史（只读，服务端使用；token 不下发浏览器）。

历史来源：daemon /events 事件流中的 session_row 行（row.appended/upserted/
delta，含完整行对象）。上游 /sessions/:id/messages 在 websocket 模式存在
schema 缺陷（info.messageId undefined → 500），故走事件流重建。
限制：深度受 daemon 事件留存约束；在响应中以 note 如实标注。
"""
from __future__ import annotations

import json

from .errors import HiveError


def _daemon_cfg(svc):
    import json as _json
    import os
    path = os.path.expanduser(os.path.join("~", ".zcode", "zsession",
                                           "config.json"))
    if not os.path.exists(path):
        raise HiveError("HARNESS_UNAVAILABLE", "zsession 配置不存在")
    cfg = _json.load(open(path, encoding="utf-8"))
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


def fetch_session_history(svc, session_id: str, limit: int = 3000) -> dict:
    """从 daemon 事件流重建会话历史。返回按序消息行（真实行级内容）。"""
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
            # 字符级增量补到最新文本（历史重建只需最终文本）
            if isinstance(d.get("append"), str):
                cur = rows[rid]
                cur["text"] = (cur.get("text") or "") + d["append"]
        elif op == "row.removed":
            rows.pop(rid, None)
    messages = []
    for rid in order:
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
    truncated = len(events) >= limit
    return {"messages": messages,
            "note": ("历史来自 zsession 事件流留存；更早内容以 ZCode 会话本体为准"
                     if truncated else None),
            "count": len(messages)}
