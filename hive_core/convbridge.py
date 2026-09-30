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

    # ---------------- lifecycle ----------------
    def start(self):
        import os
        import sys as _sys
        if self.proc and self.proc.poll() is None:
            return
        from .harness import _resolve_exe
        argv = list(self.argv)
        argv[0] = _resolve_exe(argv[0]) or argv[0]
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
        sid = p.get("sessionId")
        upd = p.get("update") or {}
        kind = upd.get("sessionUpdate")
        meta = (p.get("_meta") or {}).get("zsession") or {}
        agent_id = meta.get("agentId")
        binding_agent = agent_id
        if not binding_agent:
            b = self.svc.store.conn.execute(
                "SELECT * FROM conv_bindings WHERE session_id=?",
                (sid,)).fetchone()
            binding_agent = b["agent_id"] if b else None
        base = {"session_id": sid, "source": "acp", "agent_id": binding_agent,
                "upstream_seq": (p.get("_meta") or {}).get("zsession", {}).get("seq")}
        if kind == "agent_message_chunk":
            content = upd.get("content") or {}
            text = content.get("text", "")
            row = upd.get("rowId") or meta.get("rowId") or "cur"
            conv.add_conv_event(
                self.svc, type="agent.delta", row_id=row,
                payload={"text": text, "state": "streaming", "append": True},
                dedupe_key=None, **base)
        elif kind == "tool_call":
            conv.add_conv_event(
                self.svc, type="tool.call",
                tool_call_id=upd.get("toolCallId"),
                payload={"tool": upd.get("title"), "state": "running",
                         "args": upd.get("rawInput")}, **base)
        elif kind == "tool_call_update":
            conv.add_conv_event(
                self.svc, type="tool.call",
                tool_call_id=upd.get("toolCallId"),
                payload={"tool": upd.get("title"), "state":
                         (upd.get("status") or "running"),
                         "result": upd.get("rawOutput")}, **base)

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
                   "completed": "completed", "cancelled": "cancelled",
                   "rejected": "failed", "unknown": "outcome_unknown"}
        if state in mapping:
            conv.set_input_state(self.svc, row["input_id"], mapping[state],
                                 upstream_input_id=p.get("inputId"),
                                 turn_id=p.get("turnId"))

    # ---------------- bridge contract ----------------
    def ensure(self, agent_id: str, inst) -> dict:
        existing = conv.get_binding(self.svc, agent_id)
        if existing and existing["instance_id"] == inst["instance_id"]:
            return existing
        # 增强模式：实例的 zsession 句柄即真实会话；按能力接入
        handle = inst["handle"] or ""
        sid = handle.split(":", 1)[1] if handle.startswith("sess:") else None
        if not sid:
            raise HiveError("UPSTREAM_UNSUPPORTED",
                            "该实例没有可接入的 ZSession 会话句柄",
                            {"handle": handle})
        if self.capabilities.get("loadSession"):
            try:
                self.call("session/load", {"sessionId": sid, "cwd": ""})
            except HiveError:
                # 不可安全接入：保持只读绑定，历史由 zsession 自持
                pass
        return conv.bind_session(self.svc, agent_id, inst["instance_id"],
                                 inst["generation"], sid,
                                 project_root=None,
                                 connection_state="connected", source="acp")

    def send_prompt(self, binding: dict, inp) -> dict:
        meta = {"zsession": {
            "requestId": inp["request_id"], "inputId": inp["input_id"],
            "agentId": inp["agent_id"], "instanceId": inp["instance_id"],
            "generation": inp["generation"], "source": "hive-console"}}
        blocks = [{"type": "text", "text": inp["text"]}]
        return self.call("session/prompt", {
            "sessionId": binding["session_id"], "prompt": blocks,
            "_meta": meta}, timeout=24 * 3600)   # 轮次由后台管理，不设短超时

    def cancel_turn(self, binding: dict, turn_id: str | None = None):
        self._notify("session/cancel", {"sessionId": binding["session_id"],
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

    def tick(self):
        rows = self.svc.store.conn.execute(
            "SELECT DISTINCT agent_id FROM conv_inputs WHERE state='queued'"
        ).fetchall()
        for r in rows:
            agent_id = r["agent_id"]
            if agent_id in self._working:
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
            conv.set_input_state(self.svc, inp["input_id"], "completed",
                                 upstream_input_id=(receipt or {}).get("inputId"),
                                 turn_id=(receipt or {}).get("turn_id"))
        except Exception as e:  # noqa: BLE001 - 持久化失败原因，绝不静默
            try:
                inp = conv.next_queued(self.svc, agent_id)
                if inp is None:
                    # 输入可能已被推进到 dispatched——按当前活动行标记
                    inp = conv.active_input(self.svc, agent_id)
                if inp is not None and inp["state"] in ("queued", "dispatched"):
                    conv.set_input_state(self.svc, inp["input_id"], "failed",
                                         error=repr(e)[:500])
            except Exception:  # noqa: BLE001
                pass
            self.svc.logger.error("conv.dispatch_failed", agent=agent_id,
                                  error=repr(e))
        finally:
            self._working.discard(agent_id)

    def stop(self):
        self.bridge.close()
