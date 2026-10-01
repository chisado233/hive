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
        self._boot_ts = time.monotonic()
        self._turn_row: dict[str, dict] = {}
        # 待决权限请求：conv request_id → {"rpc_id": 上游帧id, "session_id"}
        self._pending_perms: dict[str, dict] = {}
        # RDEF-03：load 回放窗口（此间的 chunk 视为历史，不入库）
        self._replaying: set[str] = set()

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
        # session/request_permission：登记待决权限卡并保留 rpc id，
        # 答复由 answer_permission 显式送达（DEF-11 真实答复链路）
        p = msg.get("params") or {}
        rid = msg.get("id")
        if msg.get("method") == "session/request_permission":
            meta = (p.get("_meta") or {}).get("zsession") or {}
            agent_id = meta.get("agentId")
            sid = p.get("sessionId", "")
            if not agent_id:
                b = self.svc.store.conn.execute(
                    "SELECT agent_id FROM conv_bindings WHERE session_id=?",
                    (sid,)).fetchone()
                agent_id = b["agent_id"] if b else None
            key = f"perm-{rid}"
            self._pending_perms[key] = {"rpc_id": rid, "session_id": sid}
            conv.add_conv_event(
                self.svc, session_id=sid,
                type="permission.request", source="acp", agent_id=agent_id,
                payload={"request_id": key,
                         "tool": ((p.get("toolCall") or {}).get("title")
                                  or p.get("toolCall")),
                         "options": p.get("options"), "state": "pending"},
                dedupe_key=key)
        else:
            # 未实现的反向请求：明确取消，不悬挂
            self._outbox.put(json.dumps({"jsonrpc": "2.0", "id": rid,
                "error": {"code": -32601, "message": "method not implemented"}},
                ensure_ascii=False))

    def answer_permission(self, request_id: str, answer: dict):
        """把 UI 选择送回原声待决请求（真实 JSON-RPC 响应帧）。"""
        rec = self._pending_perms.pop(request_id, None)
        if not rec:
            raise HiveError("NOT_FOUND", "权限请求不存在或已答复/过期")
        outcome = answer.get("optionId") or answer.get("option_id") \
            or answer.get("outcome") or "reject"
        self._outbox.put(json.dumps({
            "jsonrpc": "2.0", "id": rec["rpc_id"],
            "result": {"outcome": {"outcome": "selected",
                                   "optionId": outcome}}},
            ensure_ascii=False))
        # 记录答复事实（权限卡状态更新）
        b = self.svc.store.conn.execute(
            "SELECT agent_id FROM conv_bindings WHERE session_id=?",
            (rec["session_id"],)).fetchone()
        conv.add_conv_event(
            self.svc, session_id=rec["session_id"], type="permission.resolved",
            source="acp", agent_id=b["agent_id"] if b else None,
            payload={"request_id": request_id, "option": outcome})

    def _project_update(self, p: dict):
        """上游 session/update 投影。回放帧（_meta.zsession.replay，兼容
        params 级与 update 级两种位置）与 load 回放窗口内的帧均不入库——
        历史统一走 /history 分页，避免双写。"""
        meta = (p.get("_meta") or {}).get("zsession") or {}
        upd0 = p.get("update") or {}
        umeta = (upd0.get("_meta") or {}).get("zsession") or {}
        if meta.get("replay") or umeta.get("replay"):
            return
        sid = p.get("sessionId")
        if sid in self._replaying:
            return
        upd = upd0
        kind = upd.get("sessionUpdate")
        agent_id = meta.get("agentId")
        if not agent_id:
            b = self.svc.store.conn.execute(
                "SELECT * FROM conv_bindings WHERE session_id=?",
                (sid,)).fetchone()
            agent_id = b["agent_id"] if b else None
        base = {"session_id": sid, "source": "acp", "agent_id": agent_id,
                "upstream_seq": meta.get("seq")}
        if kind == "user_message_chunk":
            # 新一轮用户输入
            conv.add_conv_event(
                self.svc, type="user.message",
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
        # 行 id 绑定到持久 input_id（跨重启稳定）；无活动轮次时用
        # 本进程启动时间戳兜底（绝不与历史行碰撞合并）
        key = f"_row_{prefix}_{sid}"
        row = getattr(self, key, None)
        if row:
            return row
        turn = getattr(self, "_turn_row", {}).get(sid)
        if turn:
            row = turn[prefix]
        else:
            row = f"{prefix}-{sid}-live-{int(self._boot_ts)}"
        setattr(self, key, row)
        return row

    def begin_turn(self, sid: str, input_id: str):
        """派发开始时登记本轮行分组：msg/thd 行 id 由持久 input_id 派生。"""
        if not hasattr(self, "_turn_row"):
            self._turn_row = {}
        self._turn_row[sid] = {"msg": f"msg-{input_id}", "thd": f"thd-{input_id}"}
        for prefix in ("msg", "thd"):
            try:
                delattr(self, f"_row_{prefix}_{sid}")
            except AttributeError:
                pass

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
        # SDEF-01/RDEF-14：原生 started 事实 → turn.started（DB 幂等）；
        # busy 重试的派发 attempt 不会到达这里
        if state == "started":
            conv.ensure_turn_started(
                self.svc, row["session_id"] or p.get("sessionId", ""),
                row["agent_id"], row["input_id"], "acp")

    # ---------------- bridge contract ----------------
    def _execution_root(self, agent_id: str) -> str:
        try:
            from .agents import get_agent_config
            cfg = get_agent_config(self.svc, agent_id)
            return ((cfg.get("runtime") or {}).get("options") or {}).get(
                "execution_root") or self.svc.home
        except HiveError:
            return self.svc.home

    def _writer_token(self, agent_id: str, generation: int) -> str | None:
        """SDEF-03：受管写入凭据——每 agent+generation 一个高熵 token，
        存 <home>/runtime/conv-writer-keys/（0600，不进 conv_bindings/
        日志/协议帧回显）。实例换代自然换 key（旧代际被上游拒绝）。

        运行时启用由 HIVE_ACP_WRITER_KEY=1 控制：上游 adapter 的
        DaemonClient.post 丢弃 writeHeaders 第三参（acp.mjs:50，SDEF-05
        范围），key 绑定后 adapter 自身写请求不带头会被 409 拒绝。
        机制已实现并核实 writerKeyBound；上游修复头转发后置环境变量启用。"""
        import os as _os
        if _os.environ.get("HIVE_ACP_WRITER_KEY") != "1":
            return None
        import secrets as _secrets
        d = _os.path.join(self.svc.home, "runtime", "conv-writer-keys")
        _os.makedirs(d, exist_ok=True)
        path = _os.path.join(d, f"{agent_id}.g{generation}.key")
        if _os.path.exists(path):
            return open(path, "r", encoding="utf-8").read().strip()
        tok = "hvwt_" + _secrets.token_urlsafe(32)
        with open(path, "w", encoding="utf-8") as f:
            f.write(tok)
        try:
            _os.chmod(path, 0o600)
        except OSError:
            pass
        return tok

    def _release_claim(self, session_id: str, owner: str):
        """释放本方受管登记（owner 直通；失败仅记录，不阻断）。"""
        try:
            import json as _json
            import urllib.request as _ur
            from .zsession_read import _daemon_cfg
            base, token = _daemon_cfg(self.svc)
            req = _ur.Request(
                base + "/acp/release",
                data=_json.dumps({"sessionId": session_id,
                                  "owner": owner}).encode(),
                headers={"Authorization": f"Bearer {token}",
                         "Content-Type": "application/json"}, method="POST")
            with _ur.urlopen(req, timeout=10) as r:
                body = _json.load(r)
            self.svc.logger.info("conv.claim_released",
                                 session=session_id[-8:],
                                 released=bool(body.get("released", True)))
        except Exception as e:  # noqa: BLE001
            self.svc.logger.warning("conv.claim_release_failed",
                                    session=session_id[-8:],
                                    error=repr(e)[:120])

    def _writer_bound(self, session_id: str) -> bool | None:
        """核实 claim 的 writerKeyHash 已绑定（不把 claim 存在当凭据启用）。"""
        try:
            from .zsession_read import _get
            claims = _get(self.svc, "/acp/claims") or {}
            for c in (claims.get("claims") or []):
                if c.get("sessionId") == session_id:
                    return bool(c.get("writerKeyHash"))
        except Exception:  # noqa: BLE001
            return None
        return None

    def ensure(self, agent_id: str, inst) -> dict:
        existing = conv.get_binding(self.svc, agent_id)
        if existing and existing["instance_id"] == inst["instance_id"] \
                and existing["session_id"] in self._loaded_sessions:
            return existing
        # 实例的 zsession 句柄即真实会话；session/load 接入
        # （不复制、不新建；adapter 重启后重新 load）
        handle = inst["handle"] or ""
        sid = handle.split(":", 1)[1] if handle.startswith("sess:") else None
        if not sid:
            raise HiveError("UPSTREAM_UNSUPPORTED",
                            "该实例没有可接入的 ZSession 会话句柄",
                            {"handle": handle})
        if self.capabilities.get("loadSession"):
            # RDEF-07/SDEF-03：登记真实 owner、代际与受管写入凭据
            meta = {"zsession": {
                "ownerId": f"hive/{agent_id}",
                "instanceId": inst["instance_id"],
                "generation": inst["generation"],
                "agentId": agent_id}}
            wt = self._writer_token(agent_id, inst["generation"])
            if wt:
                meta["zsession"]["writerToken"] = wt
            else:
                # 弱声明模式：先释放本 agent 名下可能残留的带 key claim
                # （否则旧凭据持续拒绝写，且 adapter claim 冲突被静默吞掉）
                self._release_claim(sid, f"hive/{agent_id}")
            # RDEF-03 消费者防御：load 期间上游会先回放历史帧（可能缺
            # replay 标记——上游 RDEF-03 未修）；历史统一走 /history 分页，
            # 回放窗口内的 chunk 一律丢弃，不双写。
            self._replaying.add(sid)
            try:
                self.call("session/load", {
                    "sessionId": sid,
                    "cwd": self._execution_root(agent_id),
                    "_meta": meta})
            except HiveError:
                raise
            finally:
                self._replaying.discard(sid)
        else:
            raise HiveError("UPSTREAM_UNSUPPORTED",
                            "上游未声明 loadSession 能力")
        bound = self._writer_bound(sid)
        self.svc.logger.info("conv.claim", session=sid[-8:],
                             writer_key_bound=bound)
        self._loaded_sessions.add(sid)
        return conv.bind_session(self.svc, agent_id, inst["instance_id"],
                                 inst["generation"], sid,
                                 project_root=self._execution_root(agent_id),
                                 connection_state="connected", source="acp")

    def query_input_state(self, session_id: str, request_id: str) -> dict:
        """上游持久输入对账（不触发模型调用）。"""
        try:
            return self.call("_zsession/get_input_state",
                             {"sessionId": session_id,
                              "requestId": request_id}, timeout=15) or {}
        except HiveError:
            return {}

    def send_prompt(self, binding: dict, inp) -> dict:
        meta = {"zsession": {
            "requestId": inp["request_id"], "inputId": inp["input_id"],
            "agentId": inp["agent_id"], "instanceId": inp["instance_id"],
            "generation": inp["generation"], "source": "hive-console",
            "ownerId": f"hive/{inp['agent_id']}"}}
        wt = self._writer_token(inp["agent_id"], inp["generation"])
        if wt:
            meta["zsession"]["writerToken"] = wt
        blocks = [{"type": "text", "text": inp["text"]}]
        # 阻塞至真实轮次结束（上游以 stopReason 收口；忙时 -32011 冲突）
        receipt = self.call("session/prompt", {
            "sessionId": binding["session_id"], "prompt": blocks,
            "_meta": meta}, timeout=24 * 3600)
        # RDEF-01 消费者防御：协议成功 ≠ 业务完成。用持久输入状态核实，
        # 防"忙窗口把 requestId 登记成 rejected 后重试命中终态短路返回
        # end_turn"的假成功路径。
        st = self.query_input_state(binding["session_id"], inp["request_id"])
        state = st.get("state")
        if state == "rejected":
            raise HiveError("UPSTREAM_ERROR",
                            "上游将该输入登记为 rejected（忙窗口污染）——"
                            "协议返回成功但无真实执行，按失败落账",
                            {"upstream_state": state,
                             "stopReason": (receipt or {}).get("stopReason"),
                             "retryable": True})
        if state and state not in ("done", "cancelled"):
            # started/accepted 但 prompt 已返回：结果可疑，如实标注
            self.svc.logger.warning("conv.suspicious_receipt",
                                    state=state,
                                    request=inp["request_id"])
        receipt = dict(receipt or {})
        receipt["_verified_state"] = state
        return receipt

    def cancel_turn(self, binding: dict, turn_id: str | None = None):
        """DEF-08：session/cancel 按官方规范是无 id 通知，不等响应。
        「取消完成」以 prompt 返回 cancelled / input_state 通知为准。"""
        self._notify("session/cancel", {"sessionId": binding["session_id"],
                                        "_meta": {"zsession": {"turnId": turn_id}}})
        return {"cancel_requested": True}

    def answer_permission(self, request_id, answer: dict):
        """DEF-11：上游 request_permission 桥接未实现（ACP_INTEGRATION §2）——
        如实返回不支持，不假成功。"""
        raise HiveError("NOT_IMPLEMENTED",
                        "上游尚未桥接工具权限请求（request_permission），"
                        "无法送达答复；yolo 会话由授权策略自动处理")


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
        self._eager_attach()
        self._periodic_reconcile()
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
        """ACP 桥断线重启监督（15s 退避）+ 重连后持久对账（RDEF-04）。"""
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
            threading.Thread(target=self._safe_reconcile, daemon=True,
                             name="conv-reconcile").start()
        except Exception as e:  # noqa: BLE001
            self.svc.logger.error("acp.restart_failed", error=repr(e))

    def _safe_reconcile(self):
        try:
            self.reconcile_once()
        except Exception as e:  # noqa: BLE001
            self.svc.logger.error("conv.reconcile_failed", error=repr(e))

    def _periodic_reconcile(self):
        """FDEF-01：存在 unknown 输入且桥活着时，每 30s 低频对账
        （目标 ≤60s 内按原生依据收口；合法长轮不受影响——working 会话
        的归属对账直接跳过）。"""
        if not self._bridge_ready():
            return
        import time as _t
        if _t.monotonic() < getattr(self, "_next_reconcile", 0):
            return
        n = self.svc.store.conn.execute(
            "SELECT COUNT(*) FROM conv_inputs WHERE state='outcome_unknown'"
        ).fetchone()[0]
        if not n:
            self._next_reconcile = _t.monotonic() + 30
            return
        self._next_reconcile = _t.monotonic() + 30
        threading.Thread(target=self._safe_reconcile, daemon=True,
                         name="conv-reconcile-periodic").start()

    def _eager_attach(self):
        """把活跃 zsession 实例的会话接到桥上（只读 attach，不启动实例、
        不发送内容）：会话舱对任意在跑 Agent 实时同步。每 tick 最多 2 个，
        失败冷却 60s。"""
        if not hasattr(self.bridge, "_alive") or not self.bridge._alive.is_set():
            return
        rows = self.svc.store.conn.execute(
            "SELECT i.* FROM instances i WHERE i.phase IN ('live','starting') "
            "AND i.handle LIKE 'sess:%' ORDER BY i.started_at").fetchall()
        import time as _t
        budget = 2
        for inst in rows:
            if budget <= 0:
                break
            agent_id = inst["agent_id"]
            b = conv.get_binding(self.svc, agent_id)
            if b and b["instance_id"] == inst["instance_id"] and \
                    b["session_id"] in getattr(self.bridge, "_loaded_sessions", set()):
                continue
            if self._cooldown.get("attach:" + agent_id, 0) > _t.monotonic():
                continue
            if agent_id in self._working:
                continue
            self._working.add(agent_id)
            budget -= 1
            threading.Thread(target=self._attach_one, args=(dict(inst),),
                             daemon=True,
                             name=f"conv-attach-{agent_id[-6:]}").start()

    def _attach_one(self, inst: dict):
        agent_id = inst["agent_id"]
        try:
            self.bridge.ensure(agent_id, inst)
        except Exception as e:  # noqa: BLE001
            import time as _t
            self._cooldown["attach:" + agent_id] = _t.monotonic() + 60
            self.svc.logger.warning("conv.attach_failed", agent=agent_id,
                                    error=repr(e)[:200])
        finally:
            self._working.discard(agent_id)

    def _bridge_ready(self) -> bool:
        b = self.bridge
        if not hasattr(b, "_alive"):
            return True                      # fixture 桥无进程
        return b._alive.is_set() and bool(b.capabilities)

    def _dispatch_one(self, agent_id: str, inst: dict):
        inp = None
        # 桥未就绪（bootstrap/重连中）：回队短冷却，不把输入打成 failed
        if not self._bridge_ready():
            import time as _t
            self._cooldown[agent_id] = _t.monotonic() + 2
            return
        try:
            inp = conv.next_queued(self.svc, agent_id)
            if not inp:
                return
            input_id = inp["input_id"]      # RDEF-01：本路径固定输入身份
            conv.set_input_state(self.svc, input_id, "dispatched")
            binding = self.bridge.ensure(agent_id, inst)
            if not binding.get("session_id"):
                raise HiveError("UPSTREAM_UNSUPPORTED", "会话绑定缺少 session_id")
            conv.set_input_state(self.svc, input_id, "dispatched",
                                 session_id=binding["session_id"])
            sid = binding["session_id"]
            turn_id = f"turn-{input_id}"
            if hasattr(self.bridge, "begin_turn"):
                self.bridge.begin_turn(sid, input_id)
            # SDEF-01/RDEF-14：turn.started 由上游 input_state=started 事实
            # 驱动（见 _reconcile_input，DB 级幂等）；busy 重试是派发 attempt，
            # 不预写 started
            receipt = self.bridge.send_prompt(binding, inp)
            # 收口兜底：轮次真实结束但 started 通知缺失时补一次（幂等）
            conv.ensure_turn_started(self.svc, sid, agent_id, input_id,
                                     self.bridge.name)
            stop = (receipt or {}).get("stopReason") or "end_turn"
            fin = "cancelled" if stop == "cancelled" else "completed"
            if hasattr(self.bridge, "finalize_turn"):
                self.bridge.finalize_turn(sid, fin)
            conv.add_conv_event(
                self.svc, session_id=sid,
                type="turn.cancelled" if stop == "cancelled"
                else "turn.completed",
                source=self.bridge.name, agent_id=agent_id, turn_id=turn_id,
                payload={"stop_reason": stop,
                         "verified_state": (receipt or {}).get("_verified_state")})
            if stop == "cancelled":
                conv.set_input_state(self.svc, input_id, "cancelled",
                                     upstream_input_id=(receipt or {}).get("inputId"),
                                     turn_id=turn_id)
            else:
                conv.set_input_state(self.svc, input_id, "completed",
                                     upstream_input_id=(receipt or {}).get("inputId"),
                                     turn_id=turn_id)
        except HiveError as e:
            self._fail_input(agent_id, inp, e)
            self.svc.logger.error("conv.dispatch_failed", agent=agent_id,
                                  error=repr(e))
        except Exception as e:  # noqa: BLE001 - 持久化失败原因，绝不静默
            wrapped = HiveError("INTERNAL_ERROR",
                                f"{type(e).__name__}: {e}"[:400])
            self._fail_input(agent_id, inp, wrapped)
            self.svc.logger.error("conv.dispatch_failed", agent=agent_id,
                                  error=repr(e))
        finally:
            self._working.discard(agent_id)

    def _fail_input(self, agent_id: str, inp, e: HiveError):
        """失败落账（RDEF-01：输入身份由调用方传入，绝不重新猜测）。
        TDEF-02：本函数自身必须在任何存储/上游异常下可靠落账——
        全函数兜底，绝不让异常处理器抛出第二个异常把输入永卡 dispatched。"""
        try:
            self._fail_input_inner(agent_id, inp, e)
        except Exception as e2:  # noqa: BLE001
            # 落账自身失败（存储/上游查询）：至少保证 _working 释放与
            # 诊断可见，输入保持原状态等待 reconcile；不盲重发
            self.svc.logger.error(
                "conv.fail_input_broken", agent=agent_id,
                original=repr(e)[:200], handler_error=repr(e2)[:300])
            try:
                iid = inp["input_id"] if inp is not None and "input_id" in inp.keys()                     else None
                if iid:
                    conv.set_input_state(self.svc, iid, "outcome_unknown",
                                         error=f"落账异常: {repr(e2)[:200]}")
            except Exception:  # noqa: BLE001
                self.svc.logger.critical("conv.fail_input_lost",
                                         agent=agent_id, iid=str(iid))

    def _fail_input_inner(self, agent_id: str, inp, e: HiveError):
        # TDEF-02：边界统一类型契约——sqlite3.Row → dict（Row 无 .get）
        if inp is not None and not isinstance(inp, dict):
            try:
                inp = dict(inp)
            except (TypeError, ValueError):
                inp = None
        """失败落账：忙冲突=回队重试（排队由 Hive 决策）；
        已登记 rejected=派生新 requestId 重置被污染的预约（旧上游兼容，
        新合同 retryable_busy 不触发）；超时/断线=结果未知（不盲目重放）；
        其余=failed。"""
        upstream_code = ((getattr(e, "details", {}) or {}).get("upstream") or {}) \
            .get("code")
        if upstream_code == -32011:
            import time as _t
            if inp is not None and inp["state"] in ("queued", "dispatched"):
                # 上游忙窗口可能已把该 requestId 登记为终态 rejected（上游
                # RDEF-01 未修）——重试会命中终态短路假成功。先查持久状态；
                # 被污染则派生新 requestId（保留血缘），否则原 id 回队。
                poisoned = False
                if hasattr(self.bridge, "query_input_state") and inp.get("session_id"):  # dict 边界已转
                    st = self.bridge.query_input_state(
                        inp["session_id"], inp["request_id"])
                    if st.get("state") == "rejected":
                        poisoned = True
                if poisoned:
                    new_rid = f"{inp['request_id']}#r{int(_t.time())}"
                    with self.svc.store.write() as cur:
                        cur.execute(
                            "UPDATE conv_inputs SET request_id=?, state='queued',"
                            " error=? WHERE input_id=?",
                            (new_rid,
                             f"busy窗口预约被上游登记rejected，派生新id重试"
                             f"（原 {inp['request_id']}）", inp["input_id"]))
                    self.svc.logger.warning(
                        "conv.busy_poisoned_derived", agent=agent_id,
                        old=inp["request_id"], new=new_rid)
                else:
                    conv.set_input_state(self.svc, inp["input_id"], "queued")
            self._cooldown[agent_id] = _t.monotonic() + 10
            self.svc.logger.warning("conv.busy_requeued", agent=agent_id)
            return
        if inp is None or inp["state"] not in ("queued", "dispatched"):
            return
        state = "outcome_unknown" if e.code in ("UPSTREAM_TIMEOUT",
                                                "UPSTREAM_ERROR") else "failed"
        try:
            conv.set_input_state(self.svc, inp["input_id"], state,
                                 error=f"{e.code}: {e.message}"[:500])
        except Exception:  # noqa: BLE001
            pass

    def reconcile_once(self):
        """FDEF-01/RDEF-04：unknown 输入对账——三层：
        1) 上游持久登记 done/cancelled/rejected/failed → 按事实收口；
        2) started/unknown + 会话空闲 → 原生归属对账：在 /sessions/:id/history
           中精确匹配本输入全文的 user 回显（我方原文的 1:1 身份链接，非
           正文相似），其后存在 assistant 终稿且会话已空闲 → 按事实补
           completed（attribution 记录依据）；
        3) 无强证据保持 unknown（前端显示处置入口），不猜、不重发。"""
        if not hasattr(self.bridge, "query_input_state"):
            return
        rows = self.svc.store.conn.execute(
            "SELECT * FROM conv_inputs WHERE state='outcome_unknown'").fetchall()
        for inp in rows:
            sid = inp["session_id"] or (conv.get_binding(
                self.svc, inp["agent_id"]) or {}).get("session_id")
            if not sid:
                continue
            st = self.bridge.query_input_state(sid, inp["request_id"])
            state = st.get("state")
            if state == "done":
                self._settle_reconciled(inp, sid, "completed",
                                        st.get("stopReason") or "end_turn",
                                        {"layer": "registry"})
            elif state == "cancelled":
                conv.set_input_state(self.svc, inp["input_id"], "cancelled",
                                     error=None)
            elif state == "rejected":
                conv.set_input_state(
                    self.svc, inp["input_id"], "failed",
                    error="对账：上游登记 rejected（未真实执行）")
            elif state == "failed":
                conv.set_input_state(self.svc, inp["input_id"], "failed",
                                     error="对账：上游登记 failed")
            elif state in ("started", "unknown", None):
                self._reconcile_by_native_history(inp, sid, st)
            # accepted/reserved：仍在途，保留 unknown 待下轮
        if rows:
            self.svc.logger.info("conv.reconciled", count=len(rows))

    def _settle_reconciled(self, inp, sid: str, terminal: str,
                           stop_reason: str, evidence: dict):
        # 状态条件更新（终态不被覆盖）+ 事件 DB 幂等（重复对账不双写）
        conv.set_input_state(self.svc, inp["input_id"], terminal, error=None,
                             turn_id=f"turn-{inp['input_id']}")
        conv.add_conv_event(
            self.svc, session_id=sid, type="turn.completed", source="reconcile",
            agent_id=inp["agent_id"], turn_id=f"turn-{inp['input_id']}",
            payload={"stop_reason": stop_reason, "reconciled": True,
                     **evidence},
            dedupe_key=f"reconcile-settle-{inp['input_id']}")

    def _reconcile_by_native_history(self, inp, sid: str, st: dict):
        """原生归属对账（FDEF-01 核心）：只在「会话当前空闲」时尝试——
        working/waiting 说明轮次可能仍在跑（保留 unknown，不提前 completed）。"""
        try:
            from .zsession_read import _get
            info = _get(self.svc, f"/sessions/{sid}")
            status = info.get("status")
            if status in ("working", "waiting_input", "spawning"):
                return                       # 仍在跑：可理解的"恢复中"
            # 空闲族（idle/inactive/dead/archived 等）：允许按历史归属对账；
            # 未知状态值不冒进，跳过等待下轮
            if status not in ("idle", "inactive", "dead", "archived", None):
                return
            hist = _get(self.svc, f"/sessions/{sid}/history?limit=200") \
                if status != "archived" else {"items": []}
            items = hist.get("items") or []
            want = (inp["text"] or "").strip()
            if not want:
                return
            # 找本输入全文的 user 回显（1:1 身份链接；时间窗仅辅助定位）
            match_idx = None
            for i, it in enumerate(items):
                if it.get("role") == "user" and \
                        (it.get("text") or "").strip() == want:
                    match_idx = i
            if match_idx is None:
                return                       # 无归属证据：保留 unknown+入口
            # 其后存在 assistant 终稿 → 该轮已自然完成
            has_answer = any(
                it.get("role") == "assistant" and (it.get("text") or "").strip()
                for it in items[match_idx + 1:])
            if not has_answer:
                return
            self._settle_reconciled(
                inp, sid, "completed", "end_turn",
                {"layer": "native-history",
                 "attribution": "user-echo-exact-match",
                 "registry_state": st.get("state"),
                 "native_input_id": st.get("inputId")})
            self.svc.logger.info(
                "conv.reconcile_native", input=inp["input_id"][-8:],
                session=sid[-8:])
        except Exception as e:  # noqa: BLE001
            self.svc.logger.warning("conv.reconcile_native_failed",
                                    input=inp["input_id"][-8:],
                                    error=repr(e)[:160])

    def stop(self):
        self.bridge.close()
