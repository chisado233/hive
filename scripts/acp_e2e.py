"""ACP 真实协议联调（独立测试会话，隔离目录，不触碰生产 Agent）。

链路：spawn adapter → initialize → session/new（隔离 cwd）→ 中文对话（真实流式）
→ 取消（真实 interrupt，stopReason=cancelled）→ 幂等重放 → 断开（会话存活）
→ 重接 session/load → _zsession/get_input_state 对账。

用法：python scripts/acp_e2e.py
证据输出：work/acp-e2e/e2e-evidence.json
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time

HOME = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADAPTER = os.path.join(
    "D:\\", "agent_workspace", "projects", "zcode-session-manager",
    "plugins", "session-manager", "bin", "zsession.mjs")
CWD = os.path.join(HOME, "work", "acp-e2e")
EVIDENCE: dict = {"steps": []}


def step(name: str, ok: bool, detail):
    EVIDENCE["steps"].append({"step": name, "ok": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + " :: " +
          json.dumps(detail, ensure_ascii=False)[:300])


class Acp:
    """最小 ACP stdio client：请求/通知分流，更新收集。"""

    def __init__(self):
        self.proc = subprocess.Popen(
            [sys.executable if False else "node", ADAPTER, "acp", "--stdio"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=open(os.path.join(CWD, "adapter-stderr.log"), "ab"),
            text=True, encoding="utf-8", errors="replace", bufsize=1)
        self._id = 0
        self._pending: dict[int, dict] = {}
        self.updates: list[dict] = []
        self.input_states: list[dict] = []
        self._lock = threading.Lock()
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "method" in msg and msg["method"] == "session/update":
                with self._lock:
                    self.updates.append(msg["params"])
            elif "method" in msg and msg["method"] == "_zsession/input_state":
                with self._lock:
                    self.input_states.append(msg["params"])
            elif "id" in msg:
                with self._lock:
                    self._pending[msg["id"]] = msg

    def call(self, method: str, params: dict, timeout: float = 120) -> dict:
        with self._lock:
            self._id += 1
            rid = self._id
        self.proc.stdin.write(json.dumps(
            {"jsonrpc": "2.0", "id": rid, "method": method, "params": params},
            ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            with self._lock:
                msg = self._pending.pop(rid, None)
            if msg is not None:
                if "error" in msg:
                    raise RuntimeError(json.dumps(msg["error"],
                                                  ensure_ascii=False))
                return msg.get("result") or {}
            time.sleep(0.05)
        raise TimeoutError(method)

    def updates_for(self, session_id: str, kind: str) -> list[dict]:
        with self._lock:
            return [u for u in self.updates
                    if u.get("sessionId") == session_id and
                    (u.get("update") or {}).get("sessionUpdate") == kind]

    def close(self):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def prompt_async(acp: Acp, session_id: str, text: str, request_id: str):
    box: dict = {}

    def run():
        try:
            box["result"] = acp.call("session/prompt", {
                "sessionId": session_id,
                "prompt": [{"type": "text", "text": text}],
                "_meta": {"zsession": {"requestId": request_id,
                                       "source": "hive-e2e"}}},
                timeout=180)
        except Exception as e:  # noqa: BLE001
            box["error"] = repr(e)

    threading.Thread(target=run, daemon=True).start()
    return box


def main() -> int:
    os.makedirs(CWD, exist_ok=True)
    acp = Acp()
    try:
        # 1) initialize
        init = acp.call("initialize", {
            "protocolVersion": 1,
            "clientInfo": {"name": "hive-e2e", "version": "1.0.0"}})
        ext = (init.get("_meta") or {}).get("zsession") or {}
        step("initialize", init.get("protocolVersion") == 1 and
             f"{ext.get('namespace')}/get_input_state" in
             (ext.get("methods") or []),
             {"protocolVersion": init.get("protocolVersion"),
              "extMethods": ext.get("methods"),
              "loadSession": (init.get("agentCapabilities") or {})
              .get("loadSession")})

        # 2) session/new（隔离 cwd，不注入首轮）
        new = acp.call("session/new", {
            "cwd": CWD, "_meta": {"zsession": {
                "mode": "yolo", "name": "hive-acp-e2e"}}})
        sid = new.get("sessionId")
        step("session/new", bool(sid and sid.startswith("sess_")),
             {"sessionId": sid})

        # 3) 中文对话：真实流式 + end_turn 收口
        box1 = prompt_async(acp, sid,
                            "请用一句中文介绍你自己，不要使用工具。", "req-e2e-1")
        t0 = time.monotonic()
        while "result" not in box1 and "error" not in box1:
            if time.monotonic() - t0 > 180:
                break
            time.sleep(0.3)
        chunks = acp.updates_for(sid, "agent_message_chunk")
        r1 = box1.get("result") or {}
        full = "".join((u.get("update") or {}).get("content", {}).get("text", "")
                       for u in chunks)
        step("中文对话（真实流式）",
             r1.get("stopReason") == "end_turn" and len(chunks) >= 1 and
             len(full.strip()) > 0,
             {"stopReason": r1.get("stopReason"), "chunk数": len(chunks),
              "回答样本": full.strip()[:120]})

        # 4) 幂等重放：同 requestId 同内容 → 立即返回既有结果，不重发
        t0 = time.monotonic()
        replay = acp.call("session/prompt", {
            "sessionId": sid,
            "prompt": [{"type": "text", "text": "请用一句中文介绍你自己，不要使用工具。"}],
            "_meta": {"zsession": {"requestId": "req-e2e-1"}}}, timeout=30)
        step("幂等重放（不重发）",
             replay.get("stopReason") == "end_turn" and
             time.monotonic() - t0 < 20,
             {"stopReason": replay.get("stopReason"),
              "耗时s": round(time.monotonic() - t0, 1),
              "期间新增chunk": len(acp.updates_for(sid, "agent_message_chunk"))
              - len(chunks)})

        # 5) 取消：长轮次中途 session/cancel → stopReason=cancelled
        box2 = prompt_async(acp, sid,
                            "请写一篇约4000字的短文，主题：深海探测技术史。"
                            "直接开始正文，不要提问。", "req-e2e-2")
        time.sleep(6)
        acp.call("session/cancel", {"sessionId": sid,
                                    "_meta": {"zsession": {"reason": "e2e cancel"}}})
        t0 = time.monotonic()
        while "result" not in box2 and "error" not in box2:
            if time.monotonic() - t0 > 90:
                break
            time.sleep(0.3)
        r2 = box2.get("result") or {}
        step("取消（原生 interrupt 确认）", r2.get("stopReason") == "cancelled",
             {"stopReason": r2.get("stopReason")})

        # 6) 对账：get_input_state（不触发模型调用）
        s1 = acp.call("_zsession/get_input_state",
                      {"sessionId": sid, "requestId": "req-e2e-1"})
        s2 = acp.call("_zsession/get_input_state",
                      {"sessionId": sid, "requestId": "req-e2e-2"})
        step("input_state 对账",
             s1.get("state") == "done" and s2.get("state") == "cancelled",
             {"req-e2e-1": s1.get("state"), "req-e2e-2": s2.get("state")})

        # 7) 断开桥接：stdin 关闭，受管会话存活（daemon 侧仍可查）
        sid_short = sid
        acp.close()
        time.sleep(2)
        alive = daemon_session_alive(sid_short)
        step("断开不终止会话", alive, {"session": sid_short[:16],
                                  "daemon查询": "ok" if alive else "fail"})

        # 8) 重接：新 adapter + session/load（live-forward）。契约限制：
        #    input state 存活于 adapter 连接内，跨 adapter 重启如实返回
        #    unknown —— Hive 侧对应 outcome_unknown，不盲目重发。
        acp2 = Acp()
        init2 = acp2.call("initialize", {"protocolVersion": 1,
                                         "clientInfo": {"name": "hive-e2e-re",
                                                        "version": "1.0.0"}})
        loaded = acp2.call("session/load", {"sessionId": sid, "cwd": CWD})
        s1b = acp2.call("_zsession/get_input_state",
                        {"sessionId": sid, "requestId": "req-e2e-1"})
        step("重接 load（live-forward）+ 对账如实 unknown",
             (init2 or {}).get("protocolVersion") == 1 and
             loaded.get("loaded") is True and s1b.get("state") == "unknown",
             {"loaded": loaded.get("loaded"),
              "replaySource": ((loaded.get("_meta") or {}).get("zsession") or {})
              .get("replaySource"),
              "req-e2e-1 after reload": s1b.get("state"),
              "note": "跨 adapter 重启 input state 如实 unknown（契约 §6），"
                      "Hive 侧标 outcome_unknown 不重发"})
        acp2.close()
        EVIDENCE["verdict"] = all(s["ok"] for s in EVIDENCE["steps"])
    finally:
        acp.close()
    with open(os.path.join(CWD, "e2e-evidence.json"), "w",
              encoding="utf-8") as f:
        json.dump(EVIDENCE, f, ensure_ascii=False, indent=1)
    print("\nVERDICT:", "PASS" if EVIDENCE.get("verdict") else "FAIL")
    return 0 if EVIDENCE.get("verdict") else 1


def daemon_session_alive(session_id: str) -> bool:
    """直接查 zsession daemon：桥接断开后会话仍在。"""
    import urllib.request
    cfg = json.load(open(os.path.expanduser(
        "~/.zcode/zsession/config.json"), encoding="utf-8"))
    token = (cfg.get("tokens") or {}).get("owner-local", {}).get("token")
    port = cfg.get("port", 8765)
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/sessions/{session_id}",
        headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.load(r)
        return not data.get("archivedAt")
    except Exception:  # noqa: BLE001
        return False


if __name__ == "__main__":
    sys.exit(main())
