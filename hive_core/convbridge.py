"""Conversation bridges: dispatch durable conv_inputs to a live session.

Bridge contract (duck-typed):
  ensure(agent_id, instance) -> {"session_id": str}     建立/返回绑定
  send_prompt(binding, input_row) -> upstream receipt dict
  cancel_turn(binding, turn_id=None) -> None
  close() -> None

FixtureBridge drives the whole UI chain with zero upstream deps (test/dev),
clearly marked source='fixture'. AcpBridge (Phase C) talks to the real
ZSession ACP adapter over stdio ndjson JSON-RPC. 浏览器超时/断开绝不重发；
重连先对账（_zsession/get_input_state），结果未知显示未知。
"""
from __future__ import annotations

import json
import queue
import subprocess
import threading
import time

from . import conversation as conv
from .errors import HiveError
from .ids import new_id


# --------------------------------------------------------------- fixture
class FixtureBridge:
    """协议级夹具：把 fixture-minimal harness 的会话模拟为一段真实形状的
    会话事件流（用户消息 → 轮次开始 → Agent 增量 → 工具卡 → 轮次完成）。
    数据明确标注 source='fixture'，UI 显示「演示模式」。"""

    name = "fixture"

    def __init__(self, svc):
        self.svc = svc

    def ensure(self, agent_id: str, inst) -> dict:
        sid = f"fixture-{agent_id}"
        existing = conv.get_binding(self.svc, agent_id)
        if existing and existing["instance_id"] == inst["instance_id"]:
            return existing
        return conv.bind_session(self.svc, agent_id, inst["instance_id"],
                                 inst["generation"], sid,
                                 project_root=None, connection_state="connected",
                                 source="fixture")

    def send_prompt(self, binding: dict, inp) -> dict:
        svc = self.svc
        sid = binding["session_id"]
        turn_id = new_id("turn")
        row_id = new_id("row")
        conv.add_conv_event(
            svc, session_id=sid, type="user.message", source="fixture",
            agent_id=inp["agent_id"], instance_id=inp["instance_id"],
            generation=inp["generation"], turn_id=turn_id,
            payload={"text": inp["text"], "source": inp["kind"],
                     "attachments": json.loads(inp["attachments"] or "[]")})
        conv.add_conv_event(svc, session_id=sid, type="turn.started",
                            source="fixture", agent_id=inp["agent_id"],
                            turn_id=turn_id, payload={"input_id": inp["input_id"]})
        conv.set_input_state(svc, inp["input_id"], "running", turn_id=turn_id)
        # 模拟一段增量回答 + 一个工具卡（延迟由 dispatcher 线程承担）
        text = ("收到你的消息（fixture 演示链路）。这是一条**模拟**回答，"
                "用于验证会话舱的流式渲染、工具卡片与队列语义；"
                "真实 ACP 上游接入后此处为真实模型输出。")
        acc = ""
        for i in range(0, len(text), 12):
            acc = text[:i + 12]
            conv.add_conv_event(
                svc, session_id=sid, type="agent.delta", source="fixture",
                agent_id=inp["agent_id"], turn_id=turn_id, row_id=row_id,
                payload={"text": acc, "state": "streaming"})
            time.sleep(0.03)
        conv.add_conv_event(
            svc, session_id=sid, type="agent.message.final", source="fixture",
            agent_id=inp["agent_id"], turn_id=turn_id, row_id=row_id,
            payload={"text": acc, "state": "completed"})
        tcid = new_id("toolcall")
        conv.add_conv_event(svc, session_id=sid, type="tool.call",
                            source="fixture", agent_id=inp["agent_id"],
                            turn_id=turn_id, tool_call_id=tcid,
                            payload={"tool": "inbox.list", "title": "查看收件箱",
                                     "state": "running"})
        time.sleep(0.15)
        conv.add_conv_event(
            svc, session_id=sid, type="tool.call", source="fixture",
            agent_id=inp["agent_id"], turn_id=turn_id, tool_call_id=tcid,
            payload={"tool": "inbox.list", "state": "completed",
                     "result": {"total": 0, "note": "fixture 模拟结果"}})
        conv.add_conv_event(svc, session_id=sid, type="turn.completed",
                            source="fixture", agent_id=inp["agent_id"],
                            turn_id=turn_id,
                            payload={"stop_reason": "end_turn"})
        return {"inputId": "fixture-" + inp["input_id"], "turn_id": turn_id}

    def cancel_turn(self, binding: dict, turn_id: str | None = None):
        # fixture 轮次很快结束；真实取消语义由 ACP 桥承担
        return {"cancelled": True, "note": "fixture 轮次已结束或极短"}


# ------------------------------------------------------------------- ACP
class AcpBridge:
    """真实 ACP stdio client（Phase C）。生命周期：
    spawn 子进程 → initialize →（按绑定）session/new|load → session/prompt …
    所有 I/O 在专用线程；dispatcher 只投递到 _outbox。桥断开时所有
    dispatched/running 输入标记 outcome_unknown（重连先对账，不盲目重放）。"""

    name = "acp"

    def __init__(self, svc, adapter_argv: list[str], env: dict | None = None):
        self.svc = svc
        self.argv = adapter_argv
        self.env = env
        self.proc: subprocess.Popen | None = None
        self._rpc_id = 0
        self._pending: dict[int, queue.Queue] = {}
        self._outbox: queue.Queue = queue.Queue()
        self._alive = threading.Event()
        self._lock = threading.Lock()
        self.capabilities: dict = {}
        self.last_error: str | None = None
        # adapter 进程内已 track 的会话（断线重启后必须重新 session/load）
        self._loaded_sessions: set[str] = set()
        # 会话内消息分组计数（上游 chunk 无 rowId，按会话+序号聚合）
        self._msg_seq: dict[str, int] = {}

    # ---------------- lifecycle ----------------
    def start(self):
        import os
        import sys as _sys
        if self.proc and self.proc.poll() is None:
            return
        import shutil
        argv = list(self.argv)
        if argv and not os.path.isabs(argv[0]):
            argv[0] = shutil.which(argv[0]) or argv[0]
        errlog = open(
            os.path.join(self.svc.home, "logs", "acp-adapter.log"),
            "a", encoding="utf-8")
        creationflags = 0
        if _sys.platform == "win32":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        self.proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=errlog, text=True, encoding="utf-8", errors="replace",
            bufsize=1, creationflags=creationflags)
        self._alive.set()
        threading.Thread(target=self._reader, name="acp-reader",
                         daemon=True).start()
        threading.Thread(target=self._writer, name="acp-writer",
                         daemon=True).start()
        res = self.call("initialize", {
            "protocolVersion": 1,
            "clientInfo": {"name": "hive-console", "version": "1.0.0"},
        }, timeout=30)
        self.capabilities = (res or {}).get("agentCapabilities") or {}
        self.svc.logger.info("acp.connected",
                             capabilities=json.dumps(self.capabilities))

    def close(self):
        self._alive.clear()
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.close()
            except OSError:
                pass
            try:
                self.proc.terminate()
            except OSError:
                pass

    # ---------------- JSON-RPC plumbing ----------------
    def _reader(self):
        buf = ""
        f = self.proc.stdout
        while self._alive.is_set():
            ch = f.read(1)
            if not ch:
                break
            if ch != "\n":
                buf += ch
                continue
            line = buf.strip()
            buf = ""
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                self.svc.logger.error("acp.bad_frame", line=line[:200])
                continue
            self._on_message(msg)
        self._on_disconnect()

    def _writer(self):
        while self._alive.is_set():
            try:
                frame = self._outbox.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self.proc.stdin.write(frame + "\n")
                self.proc.stdin.flush()
            except (OSError, ValueError):
                self._on_disconnect()
                return

    def _on_message(self, msg: dict):
        if "id" in msg and ("result" in msg or "error" in msg):
            q = self._pending.pop(msg["id"], None)
            if q:
                q.put(msg)
        elif "method" in msg and "id" in msg:
            self._on_reverse_request(msg)      # agent → client request
        elif "method" in msg:
            self._on_notification(msg)

    def _on_disconnect(self):
        if not self._alive.is_set():
            return
        self._alive.clear()
        self.last_error = "ACP 桥接断开"
        self._loaded_sessions.clear()      # adapter 进程将退出/已退出
        # 所有未决 RPC 失败
        for q in list(self._pending.values()):
            q.put({"error": {"code": -32000, "message": "bridge disconnected"}})
        self._pending.clear()
        # 未决输入标记 outcome_unknown（不盲目重放）
        with self.svc.store.write() as cur:
            cur.execute(
                "UPDATE conv_inputs SET state='outcome_unknown', ended_at=? "
                "WHERE state IN ('dispatched','running')", (self.svc.now(),))
        self.svc.logger.error("acp.disconnected")

    def call(self, method: str, params: dict, timeout: float = 60) -> dict:
        with self._lock:
            self._rpc_id += 1
            rid = self._rpc_id
        q: queue.Queue = queue.Queue()
        self._pending[rid] = q
        self._outbox.put(json.dumps(
            {"jsonrpc": "2.0", "id": rid, "method": method, "params": params},
            ensure_ascii=False))
        try:
            msg = q.get(timeout=timeout)
        except queue.Empty:
            self._pending.pop(rid, None)
            raise HiveError("UPSTREAM_TIMEOUT",
                            f"ACP 请求超时: {method}", {"retryable": True})
        if "error" in msg:
            e = msg["error"]
            raise HiveError("UPSTREAM_ERROR",
                            f"ACP {method}: {e.get('message')}",
                            {"upstream": e})
        return msg.get("result") or {}

    def _notify(self, method: str, params: dict):
        self._outbox.put(json.dumps(
            {"jsonrpc": "2.0", "method": method, "params": params},
            ensure_ascii=False))

    # ---------------- inbound events ----------------
    def _on_notification(self, msg: dict):
        m = msg.get("method")
        p = msg.get("params") or {}
        if m == "session/update":
            self._project_update(p)
        elif m == f"{EXT}/input_state":
            self._reconcile_input(p)

    def _on_reverse_request(self, msg: dict):
        # session/request_permission：登记待决权限卡，答复由 UI 显式给出
        p = msg.get("params") or {}
        rid = msg.get("id")
        meta = (p.get("_meta") or {}).get("zsession") or {}
        agent_id = meta.get("agentId")
        if msg.get("method") == "session/request_permission" and agent_id:
            conv.add_conv_event(
                self.svc, session_id=p.get("sessionId", ""),
                type="permission.request", source="acp", agent_id=agent_id,
                payload={"request_id": rid, "tool": p.get("toolCall"),
                         "options": p.get("options"), "state": "pending"},
                dedupe_key=f"perm-{rid}")
        else:
            # 未实现的反向请求：明确取消，不悬挂
            self._outbox.put(json.dumps({"jsonrpc": "2.0", "id": rid,
                "error": {"code": -32601, "message": "method not implemented"}},
                ensure_ascii=False))

    def _project_update(self, p: dict):
        """上游 session/update 投影。上游帧（见 ACP_INTEGRATION.md §4）：
        {sessionId, update:{sessionUpdate, content?|toolCallId?|title?|status?}}
        chunk 无 rowId 且为字符级 append —— 按会话+序号聚合消息行，
        payload.append 由投影层与前端累加。"""
        sid = p.get("sessionId")
        upd = p.get("update") or {}
        kind = upd.get("sessionUpdate")
        meta = (p.get("_meta") or {}).get("zsession") or {}
        agent_id = meta.get("agentId")
        if not agent_id:
            b = self.svc.store.conn.execute(
                "SELECT * FROM conv_bindings WHERE session_id=?",
                (sid,)).fetchone()
            agent_id = b["agent_id"] if b else None
        base = {"session_id": sid, "source": "acp", "agent_id": agent_id,
                "upstream_seq": meta.get("seq")}
        if kind == "user_message_chunk":
            # 新一轮用户输入：递增消息分组
            n = self._msg_seq.get(sid, 0) + 1
            self._msg_seq[sid] = n
            conv.add_conv_event(
                self.svc, type="user.message", row_id=f"msg-{sid}-{n}",
                payload={"text": (upd.get("content") or {}).get("text", ""),
                         "source": "user"}, **base)
        elif kind == "agent_message_chunk":
            row = self._current_row(sid, "msg")
            conv.add_conv_event(
                self.svc, type="agent.delta", row_id=row,
                payload={"append": (upd.get("content") or {}).get("text", ""),
                         "state": "streaming"}, **base)
        elif kind == "agent_thought_chunk":
            row = self._current_row(sid, "thd")
            conv.add_conv_event(
                self.svc, type="agent.thought", row_id=row,
                payload={"append": (upd.get("content") or {}).get("text", ""),
                         "state": "streaming"}, **base)
        elif kind in ("tool_call", "tool_call_update"):
            title = upd.get("title") or "tool"
            tool = title.split(":", 1)[0].strip() or "tool"
            status = upd.get("status") or "running"
            state = {"pending": "running", "in_progress": "running",
                     "completed": "completed"}.get(status, "running")
            payload = {"tool": tool, "title": title, "state": state}
            for c in (upd.get("content") or []):
                txt = (c or {}).get("text") or ""
                if not txt:
                    continue
                # 上游仅在 success/error 终态附 content：输出/错误摘要
                payload.setdefault("result" if status == "completed"
                                   else "error", txt)
            conv.add_conv_event(
                self.svc, type="tool.call",
                tool_call_id=upd.get("toolCallId") or title,
                payload=payload, **base)

    def _current_row(self, sid: str, prefix: str) -> str:
        key = f"_row_{prefix}_{sid}"
        row = getattr(self, key, None)
        if not row:
            n = self._msg_seq.get(sid, 0) + 1
            self._msg_seq[sid] = n
            row = f"{prefix}-{sid}-{n}"
            setattr(self, key, row)
        return row

    def finalize_turn(self, sid: str, state: str = "completed"):
        """轮次收口：当前消息/思考行落终态，行分组重置（下一轮起新行）。"""
        b = self.svc.store.conn.execute(
            "SELECT * FROM conv_bindings WHERE session_id=?", (sid,)).fetchone()
        agent_id = b["agent_id"] if b else None
        for prefix in ("msg", "thd"):
            key = f"_row_{prefix}_{sid}"
            row = getattr(self, key, None)
            if row:
                conv.add_conv_event(
                    self.svc, session_id=sid,
                    type="agent.message.final" if prefix == "msg"
                    else "thought.final",
                    source="acp", agent_id=agent_id, row_id=row,
                    payload={"state": state})
                delattr(self, key)

    def _reconcile_input(self, p: dict):
        rid = p.get("requestId")
        if not rid:
            return
        row = self.svc.store.conn.execute(
            "SELECT * FROM conv_inputs WHERE request_id=?", (rid,)).fetchone()
        if not row:
            return
        state = p.get("state")
        mapping = {"accepted": "dispatched", "started": "running",
                   "done": "completed", "cancelled": "cancelled",
                   "rejected": "failed", "unknown": "outcome_unknown"}
        if state in mapping:
            conv.set_input_state(self.svc, row["input_id"], mapping[state],
                                 upstream_input_id=p.get("inputId"),
                                 turn_id=p.get("turnId"))

    # ---------------- bridge contract ----------------
    def _execution_root(self, agent_id: str) -> str:
        try:
            from .agents import get_agent_config
            cfg = get_agent_config(self.svc, agent_id)
            return ((cfg.get("runtime") or {}).get("options") or {}).get(
                "execution_root") or self.svc.home
        except HiveError:
            return self.svc.home

    def ensure(self, agent_id: str, inst) -> dict:
        existing = conv.get_binding(self.svc, agent_id)
        if existing and existing["instance_id"] == inst["instance_id"] \
                and existing["session_id"] in self._loaded_sessions:
            return existing
        # 实例的 zsession 句柄即真实会话；session/load live-forward 接入
        # （不复制、不新建；adapter 重启后重新 load）
        handle = inst["handle"] or ""
        sid = handle.split(":", 1)[1] if handle.startswith("sess:") else None
        if not sid:
            raise HiveError("UPSTREAM_UNSUPPORTED",
                            "该实例没有可接入的 ZSession 会话句柄",
                            {"handle": handle})
        if self.capabilities.get("loadSession"):
            res = self.call("session/load", {
                "sessionId": sid, "cwd": self._execution_root(agent_id)})
            if not res.get("loaded", True):
                reason = ((res.get("_meta") or {}).get("zsession") or {}).get(
                    "reason") or "unknown"
                raise HiveError("UPSTREAM_UNSUPPORTED",
                                f"会话不可安全接入: {reason}",
                                {"session_id": sid})
        else:
            raise HiveError("UPSTREAM_UNSUPPORTED",
                            "上游未声明 loadSession 能力")
        self._loaded_sessions.add(sid)
        return conv.bind_session(self.svc, agent_id, inst["instance_id"],
                                 inst["generation"], sid,
                                 project_root=self._execution_root(agent_id),
                                 connection_state="connected", source="acp")

    def send_prompt(self, binding: dict, inp) -> dict:
        meta = {"zsession": {
            "requestId": inp["request_id"], "inputId": inp["input_id"],
            "agentId": inp["agent_id"], "instanceId": inp["instance_id"],
            "generation": inp["generation"], "source": "hive-console"}}
        blocks = [{"type": "text", "text": inp["text"]}]
        # 阻塞至真实轮次结束（上游以 stopReason 收口；忙时 -32011 冲突）
        return self.call("session/prompt", {
            "sessionId": binding["session_id"], "prompt": blocks,
            "_meta": meta}, timeout=24 * 3600)

    def cancel_turn(self, binding: dict, turn_id: str | None = None):
        # session/cancel 是请求：确认送达上游；轮次取消以 prompt 返回
        # stopReason=cancelled / input_state 通知为准
        self.call("session/cancel", {"sessionId": binding["session_id"],
                                     "_meta": {"zsession": {"turnId": turn_id}}})
        return {"cancel_requested": True}


EXT = "_zsession"


# ------------------------------------------------------------- supervisor
class ConversationService:
    """挂在 scheduler 上：每个 tick 非阻塞地派发各 Agent 的下一条输入。
    会话同时最多一个活动 prompt（dispatched/running）。"""

    def __init__(self, svc, bridge):
        self.svc = svc
        self.bridge = bridge
        self._working: set[str] = set()   # agent_id 正在桥线程上派发
        self._threads: list[threading.Thread] = []
        self._bridge_next_try = 0.0
        self._cooldown: dict[str, float] = {}   # agent_id → 重试不早于该时刻

    def tick(self):
        self._supervise_bridge()
        rows = self.svc.store.conn.execute(
            "SELECT DISTINCT agent_id FROM conv_inputs WHERE state='queued'"
        ).fetchall()
        for r in rows:
            agent_id = r["agent_id"]
            if agent_id in self._working:
                continue
            import time as _t
            if self._cooldown.get(agent_id, 0) > _t.monotonic():
                continue
            if conv.active_input(self.svc, agent_id):
                continue                       # 一次只跑一个 prompt
            inst = conv.live_instance_of(self.svc, agent_id)
            if not inst:
                continue
            self._working.add(agent_id)
            t = threading.Thread(target=self._dispatch_one,
                                 args=(agent_id, dict(inst)), daemon=True,
                                 name=f"conv-dispatch-{agent_id[-6:]}")
            t.start()
            self._threads.append(t)

    def _supervise_bridge(self):
        """ACP 桥断线重启监督（15s 退避）。fixture 桥无进程，直接跳过。"""
        if not hasattr(self.bridge, "_alive"):
            return
        if self.bridge._alive.is_set():
            return
        import time as _t
        if _t.monotonic() < self._bridge_next_try:
            return
        self._bridge_next_try = _t.monotonic() + 15
        try:
            self.bridge.start()
            self.svc.logger.info("acp.reconnected")
        except Exception as e:  # noqa: BLE001
            self.svc.logger.error("acp.restart_failed", error=repr(e))

    def _dispatch_one(self, agent_id: str, inst: dict):
        try:
            inp = conv.next_queued(self.svc, agent_id)
            if not inp:
                return
            conv.set_input_state(self.svc, inp["input_id"], "dispatched")
            binding = self.bridge.ensure(agent_id, inst)
            if not binding.get("session_id"):
                raise HiveError("UPSTREAM_UNSUPPORTED", "会话绑定缺少 session_id")
            conv.set_input_state(self.svc, inp["input_id"], "dispatched",
                                 session_id=binding["session_id"])
            receipt = self.bridge.send_prompt(binding, inp)
            stop = (receipt or {}).get("stopReason") or "end_turn"
            fin = "cancelled" if stop == "cancelled" else "completed"
            if hasattr(self.bridge, "finalize_turn"):
                self.bridge.finalize_turn(binding["session_id"], fin)
            if stop == "cancelled":
                conv.set_input_state(self.svc, inp["input_id"], "cancelled",
                                     upstream_input_id=(receipt or {}).get("inputId"))
            else:
                conv.set_input_state(self.svc, inp["input_id"], "completed",
                                     upstream_input_id=(receipt or {}).get("inputId"),
                                     turn_id=(receipt or {}).get("turn_id"))
        except HiveError as e:
            self._fail_input(agent_id, e)
            self.svc.logger.error("conv.dispatch_failed", agent=agent_id,
                                  error=repr(e))
        except Exception as e:  # noqa: BLE001 - 持久化失败原因，绝不静默
            self._fail_input(agent_id, e)
            self.svc.logger.error("conv.dispatch_failed", agent=agent_id,
                                  error=repr(e))
        finally:
            self._working.discard(agent_id)

    def _fail_input(self, agent_id: str, e):
        """失败落账：忙冲突=回队重试（排队由 Hive 决策）；超时/断线=结果未知
        （不盲目重放）；其余=failed。"""
        upstream_code = ((getattr(e, "details", {}) or {}).get("upstream") or {}) \
            .get("code")
        if upstream_code == -32011:
            # managed-write conflict：会话忙。输入回 queued 稍后重发
            # （上游尚未登记该 requestId，重发安全）；10s 冷却防热循环。
            inp = conv.next_queued(self.svc, agent_id)
            if inp is None:
                inp = conv.active_input(self.svc, agent_id)
            if inp is not None and inp["state"] in ("queued", "dispatched"):
                conv.set_input_state(self.svc, inp["input_id"], "queued")
            import time as _t
            self._cooldown[agent_id] = _t.monotonic() + 10
            self.svc.logger.warning("conv.busy_requeued", agent=agent_id)
            return
        inp = conv.next_queued(self.svc, agent_id)
        if inp is None:
            inp = conv.active_input(self.svc, agent_id)
        if inp is None or inp["state"] not in ("queued", "dispatched"):
            return
        state = "outcome_unknown" if e.code in ("UPSTREAM_TIMEOUT",
                                                "UPSTREAM_ERROR") else "failed"
        try:
            conv.set_input_state(self.svc, inp["input_id"], state,
                                 error=f"{e.code}: {e.message}"[:500])
        except Exception:  # noqa: BLE001
            pass

    def stop(self):
        self.bridge.close()
