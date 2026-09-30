"""Harness registry and adapters (fixture + process) per DESIGN §4.3/§13.4."""
from __future__ import annotations

import json
import os
import subprocess
import sys

from .errors import HiveError
from .ids import new_id
from .timeutil import format_rfc3339, now_utc

MINIMAL_CAPS = ("start", "send", "yolo", "model_at_start", "stop")
SPEC_FIELDS = {"name", "adapter", "start_argv", "send_mode", "send_argv",
               "stop_mode", "default_model", "capabilities",
               "optional_capabilities", "options", "metadata", "endpoint"}


class FixtureAdapter:
    """In-daemon simulated harness with toggleable enhanced capabilities."""

    name = "fixture-minimal"

    @staticmethod
    def state(svc, agent_id: str) -> dict:
        return svc.store.kv_get(f"fixture:{agent_id}", {}) or {}

    @staticmethod
    def _save(svc, agent_id: str, state: dict):
        svc.store.kv_set(f"fixture:{agent_id}", state)

    @classmethod
    def capabilities(cls, svc, agent_id: str | None = None) -> dict:
        caps = {"start": True, "send": True, "yolo": True, "model_at_start": True,
                "stop": True, "model_hot": False, "inspect": False, "events": False}
        if agent_id:
            st = cls.state(svc, agent_id)
            caps["model_hot"] = bool(st.get("model_hot", False))
            caps["inspect"] = bool(st.get("inspect", False))
            caps["events"] = bool(st.get("events", False))
        return caps

    @classmethod
    def start(cls, svc, agent_id: str, options: dict) -> dict:
        st = cls.state(svc, agent_id)
        st.update({"running": True, "model": options.get("model"),
                   "yolo": options.get("yolo", True), "observation": "idle",
                   "started_contexts": st.get("started_contexts", [])})
        st["started_contexts"] = (st.get("started_contexts", []) or [])[-10:] + [
            {"at": svc.now(), "context": options.get("startup_context", ""),
             "model": options.get("model")}]
        cls._save(svc, agent_id, st)
        return {"handle": f"fix:{agent_id[:12]}:{new_id('instance')[-6:]}"}

    @classmethod
    def send(cls, svc, agent_id: str, handle: str, message: str,
             delivery_id: str) -> str:
        st = cls.state(svc, agent_id)
        st["received"] = (st.get("received", []) or [])[-100:] + [
            {"message": message, "delivery_id": delivery_id, "at": svc.now()}]
        cls._save(svc, agent_id, st)
        return "accepted"

    @classmethod
    def stop(cls, svc, agent_id: str, handle: str, operation_id: str) -> str:
        if svc.fault_active("harness_stop_unknown", entity=agent_id):
            return "unknown"
        if svc.fault_active("harness_stop_fail", entity=agent_id):
            svc.consume_fault("harness_stop_fail")
            raise HiveError("HARNESS_UNAVAILABLE", "stop failed (fault)")
        st = cls.state(svc, agent_id)
        st["running"] = False
        st["observation"] = "idle"
        cls._save(svc, agent_id, st)
        return "stopped"


class ProcessAdapter:
    """Real local process harness driven by an argv template (HarnessSpec)."""

    @classmethod
    def _resolve_exe(cls, argv: list[str]) -> list[str]:
        """Windows: opencode etc. are .cmd shims; resolve via PATH."""
        if argv and sys.platform == "win32":
            import shutil
            exe = shutil.which(argv[0])
            if exe:
                return [exe] + list(argv[1:])
        return list(argv)

    @classmethod
    def _expand(cls, argv: list[str], options: dict) -> list[str]:
        out = []
        for a in argv:
            a = str(a)
            if "{model}" in a:
                model = options.get("model")
                if not model:
                    continue  # omit segment when no model
                a = a.replace("{model}", model)
            if "{workspace}" in a:
                a = a.replace("{workspace}", options.get("workspace") or ".")
            if "{startup_file}" in a:
                a = a.replace("{startup_file}", options.get("startup_file") or "")
            elif "STARTUP.md" in a and options.get("startup_file"):
                # legacy specs referencing the bare name get the runtime path
                a = a.replace("STARTUP.md", options["startup_file"])
            out.append(a)
        return out

    @classmethod
    def capabilities(cls, spec: dict) -> dict:
        return spec.get("capabilities") or {}

    @classmethod
    def start(cls, svc, agent_id: str, options: dict, spec: dict) -> dict:
        argv = cls._resolve_exe(cls._expand(spec.get("start_argv") or [],
                                             options))
        if not argv:
            raise HiveError("HARNESS_UNAVAILABLE", "start_argv 为空")
        cwd = options.get("workspace") or None
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        log_path = options.get("log_path")
        out = open(log_path, "ab") if log_path else subprocess.DEVNULL
        proc = subprocess.Popen(argv, cwd=cwd, stdout=out, stderr=out,
                                stdin=subprocess.DEVNULL if spec.get(
                                    "send_mode") != "stdin" else subprocess.PIPE,
                                creationflags=creationflags)
        return {"handle": f"pid:{proc.pid}", "pid": proc.pid}

    @classmethod
    def send_stdin(cls, svc, handle: str, message: str) -> str:
        pid = int(handle.split(":")[1])
        from .home import process_alive
        if not process_alive(pid):
            return "failed"
        return "unknown"

    @classmethod
    def send_argv(cls, svc, handle: str, message: str, spec: dict,
                  cwd: str | None = None) -> str:
        """send_mode=argv: run a short-lived CLI command with {message}."""
        argv_tmpl = spec.get("send_argv") or []
        pid = int(handle.split(":")[1])
        from .home import process_alive
        if not process_alive(pid):
            return "failed"
        argv = cls._resolve_exe([a.replace("{message}", message)
                                 for a in argv_tmpl])
        try:
            proc = subprocess.run(argv, timeout=600, capture_output=True,
                                  text=True, encoding="utf-8",
                                  errors="replace", cwd=cwd)
            return "accepted" if proc.returncode == 0 else "failed"
        except (OSError, subprocess.TimeoutExpired):
            return "failed"

    @classmethod
    def stop(cls, svc, handle: str) -> str:
        pid = int(handle.split(":")[1])
        from .home import process_alive
        if not process_alive(pid):
            return "stopped"
        try:
            if sys.platform == "win32":
                os.kill(pid, 21)  # CTRL_BREAK? use terminate below instead
            proc = subprocess.Popen(["taskkill", "/PID", str(pid), "/T", "/F"],
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
            proc.wait(timeout=10)
        except OSError:
            pass
        return "stopped" if not process_alive(pid) else "unknown"


class ZSessionAdapter:
    """Real harness via the local zsession (ZCode session orchestration) API.

    Five capabilities map natively: spawn=start, /send=send (queue/interrupt),
    mode:yolo, spawn-time model (+ /model hot swap), kill=stop.
    The bearer token is read at runtime from the protected zsession config
    file (or the env var named in spec.options.token_env) — never stored in
    the HarnessSpec, prompts or logs."""

    @staticmethod
    def _base(spec: dict) -> str:
        return (spec.get("endpoint")
                or (spec.get("options") or {}).get("endpoint")
                or "http://127.0.0.1:8765")

    @staticmethod
    def _token(spec: dict) -> str:
        import os
        env_name = (spec.get("options") or {}).get("token_env")
        if env_name and os.environ.get(env_name):
            return os.environ[env_name]
        cfg_path = os.path.join(os.path.expanduser("~"), ".zcode",
                                "zsession", "config.json")
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        tok = ((cfg.get("tokens") or {}).get("owner-local") or {}).get("token")
        if not tok:
            raise HiveError("HARNESS_UNAVAILABLE",
                            "zsession owner-local token 未配置")
        return tok

    @classmethod
    def _req(cls, spec: dict, method: str, path: str, body: dict | None = None,
             timeout: float = 60) -> dict:
        import urllib.request
        import urllib.error
        req = urllib.request.Request(
            cls._base(spec).rstrip("/") + path,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8")
            if body is not None else None,
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + cls._token(spec)},
            method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:200]
            raise HiveError("HARNESS_UNAVAILABLE",
                            f"zsession {path} -> HTTP {e.code}: {detail}")
        except (urllib.error.URLError, OSError, TimeoutError, ValueError) as e:
            raise HiveError("HARNESS_UNAVAILABLE", f"zsession 不可达: {e}")

    @classmethod
    def start(cls, spec: dict, options: dict) -> dict:
        ws = (options.get("workspace") or "").replace("\\", "/")
        body = {"workspace": ws,
                "prompt": options.get("startup_context") or "Hive Agent 已启动。",
                "mode": "yolo",
                "name": options.get("session_name") or "hive-agent"}
        model = options.get("model")
        if model:
            body["model"] = model
        data = cls._req(spec, "POST", "/sessions/spawn", body, timeout=120)
        sid = ((data.get("sessions") or [{}])[0].get("sessionId")
               if isinstance(data.get("sessions"), list)
               else data.get("sessionId"))
        if not sid:
            raise HiveError("HARNESS_UNAVAILABLE", f"zsession 未返回会话: {data}")
        # wait until the session is registered/observable (spawn->use race)
        import time as _time
        for _i in range(10):
            try:
                cls._req(spec, "GET", f"/sessions/{sid}")
                break
            except HiveError:
                _time.sleep(0.6)
        return {"handle": f"sess:{sid}"}

    @classmethod
    def send(cls, spec: dict, handle: str, message: str,
             mode: str = "queue") -> str:
        import time as _time
        sid = handle.split(":", 1)[1]
        # spawn->send can race session registration; retry briefly on 404
        for attempt in range(6):
            try:
                data = cls._req(spec, "POST", f"/sessions/{sid}/send",
                                {"text": message, "mode": mode})
                return "accepted" if data.get("ok") else "failed"
            except HiveError as e:
                if "404" in e.message and attempt < 5:
                    _time.sleep(1.0)
                    continue
                raise

    @classmethod
    def stop(cls, spec: dict, handle: str) -> str:
        import time as _time
        sid = handle.split(":", 1)[1]
        for attempt in range(6):
            try:
                cls._req(spec, "POST", f"/sessions/{sid}/kill",
                         {"force": True})
                return "stopped"
            except HiveError as e:
                if "404" in e.message and attempt < 5:
                    _time.sleep(1.0)
                    continue
                # session no longer exists after retries: the stop effect is
                # already achieved — kill is idempotent, never an error
                if "404" in e.message:
                    return "stopped"
                raise

    @classmethod
    def observe(cls, spec: dict, handle: str) -> str:
        sid = handle.split(":", 1)[1]
        data = cls._req(spec, "GET", f"/sessions/{sid}")
        status = ((data.get("session") or {}).get("status")
                  or data.get("status"))
        return {"idle": "idle", "working": "busy"}.get(status, "unknown")

    @classmethod
    def set_model(cls, spec: dict, handle: str, model: str) -> bool:
        sid = handle.split(":", 1)[1]
        data = cls._req(spec, "POST", f"/sessions/{sid}/model",
                        {"model": model})
        return bool(data.get("ok"))


class Registry:
    def __init__(self, store, home: str, svc=None):
        self.store = store
        self.home = home
        self.svc = svc

    @classmethod
    def bootstrap_defaults(cls, store, home: str):
        """Register builtin harnesses on a fresh home."""
        fixture_spec = {
            "name": "fixture-minimal", "adapter": "fixture",
            "capabilities": {"start": True, "send": True, "yolo": True,
                             "model_at_start": True, "stop": True},
            "optional_capabilities": {"model_hot": False, "inspect": False,
                                      "events": False},
            "send_mode": "adapter-defined", "stop_mode": "adapter",
            "default_model": "fixture-model",
        }
        now = format_rfc3339(now_utc())
        with store.write() as cur:
            for name, spec in (("fixture-minimal", fixture_spec),):
                cur.execute(
                    "INSERT OR REPLACE INTO harnesses(name,spec,revision,"
                    "validation_level,created_at,updated_at) VALUES (?,?,1,"
                    "'declared',?,?)",
                    (name, json.dumps(spec, ensure_ascii=False), now, now))

    def get_spec(self, name: str) -> dict:
        r = self.store.conn.execute("SELECT spec FROM harnesses WHERE name=?",
                                    (name,)).fetchone()
        if not r:
            raise HiveError("HARNESS_NOT_CONFIGURED", f"harness未注册: {name}")
        return json.loads(r["spec"])

    def list(self) -> list[dict]:
        rows = self.store.conn.execute(
            "SELECT name, spec, validation_level FROM harnesses "
            "ORDER BY name").fetchall()
        return [{"name": r["name"], "adapter": json.loads(r["spec"]).get("adapter"),
                 "validation_level": r["validation_level"],
                 "capabilities": json.loads(r["spec"]).get("capabilities"),
                 "optional_capabilities": json.loads(r["spec"]).get(
                     "optional_capabilities", {})} for r in rows]

    def adapter_for(self, name: str):
        spec = self.get_spec(name)
        kind = spec.get("adapter")
        if kind in ("fixture", "zsession"):
            return kind, spec
        return "process", spec

    # ---- lifecycle calls ----
    def start(self, svc, agent_id: str, harness: str, options: dict) -> dict:
        kind, spec = self.adapter_for(harness)
        if kind == "fixture":
            return FixtureAdapter.start(svc, agent_id, options)
        if kind == "zsession":
            options = dict(options)
            options.setdefault("session_name", f"hive-{agent_id[:12]}")
            return ZSessionAdapter.start(spec, options)
        proc_result = ProcessAdapter.start(svc, agent_id, options, spec)
        return proc_result

    def send(self, svc, agent_id: str, harness: str, handle: str, message: str,
             delivery_id: str, mode: str = "queue") -> str:
        kind, spec = self.adapter_for(harness)
        if kind == "fixture":
            return FixtureAdapter.send(svc, agent_id, handle, message, delivery_id)
        if kind == "zsession":
            return ZSessionAdapter.send(spec, handle, message, mode=mode)
        if spec.get("send_mode") == "stdin":
            return ProcessAdapter.send_stdin(svc, handle, message)
        if spec.get("send_mode") == "argv" and spec.get("send_argv"):
            import json as _json
            vrow = svc.store.conn.execute(
                "SELECT file_path FROM agent_config_versions WHERE agent_id=? "
                "AND published=1 ORDER BY revision DESC LIMIT 1",
                (agent_id,)).fetchone()
            cwd = None
            if vrow:
                try:
                    with open(vrow["file_path"], encoding="utf-8") as f:
                        cwd = _json.load(f).get("workspace", {}).get("path")
                except (OSError, ValueError):
                    cwd = None
            return ProcessAdapter.send_argv(svc, handle, message, spec, cwd=cwd)
        return "unknown"

    def stop(self, svc, agent_id: str, harness: str, handle: str,
             operation_id: str) -> str:
        kind, spec = self.adapter_for(harness)
        if kind == "fixture":
            return FixtureAdapter.stop(svc, agent_id, handle, operation_id)
        if kind == "zsession":
            return ZSessionAdapter.stop(spec, handle)
        return ProcessAdapter.stop(svc, handle)


def register_harness(svc, subject, args, rid) -> dict:
    svc.authz.require(subject, "system.admin")
    spec = args.get("input")
    if not isinstance(spec, dict):
        raise HiveError("INVALID_ARGUMENT", "HarnessSpec 必须是对象")
    unknown = set(spec) - SPEC_FIELDS
    if unknown:
        raise HiveError("SCHEMA_UNSUPPORTED", f"HarnessSpec 未知字段: {sorted(unknown)}")
    name = spec.get("name")
    if not name or not isinstance(name, str):
        raise HiveError("REQUIRED_ARGUMENT", "HarnessSpec.name 必填")
    caps = spec.get("capabilities") or {}
    missing = [c for c in MINIMAL_CAPS if not caps.get(c)]
    if missing:
        raise HiveError("SCHEMA_UNSUPPORTED",
                        f"最小能力缺失: {missing}；不满足五项最小接入")
    adapter = spec.get("adapter")
    if adapter not in ("fixture", "process", "zsession"):
        raise HiveError("SCHEMA_UNSUPPORTED",
                        "adapter 只支持 fixture/process/zsession")
    if adapter == "process":
        argv = spec.get("start_argv")
        if not isinstance(argv, list) or not argv or \
                not all(isinstance(a, str) for a in argv):
            raise HiveError("INVALID_ARGUMENT",
                            "start_argv 必须为非空字符串数组（不是拼接shell）")
    existing = svc.store.conn.execute("SELECT revision FROM harnesses WHERE name=?",
                                      (name,)).fetchone()
    if existing and not args.get("replace"):
        raise HiveError("INVALID_ARGUMENT",
                        f"harness已存在: {name}（用 --replace 显式替换）")
    revision = (existing["revision"] + 1) if existing else 1
    now = format_rfc3339(now_utc())
    with svc.store.write() as cur:
        cur.execute(
            "INSERT OR REPLACE INTO harnesses(name,spec,revision,validation_level,"
            "created_at,updated_at) VALUES (?,?,?,?,?,?)",
            (name, json.dumps(spec, ensure_ascii=False), revision, "declared",
             now, now))
        svc.store.audit(cur, subject.ref, "harness.register", name,
                        args["reason"], rid, {"revision": revision})
    return {"harness": {"name": name}, "revision": revision,
            "validation_level": "declared"}


def validate_harness(svc, subject, args, rid) -> dict:
    """Declared validation or a real five-capability smoke run."""
    svc.authz.require(subject, "system.observe")
    name = args["name"]
    registry = svc.harnesses or Registry(svc.store, svc.home, svc)
    if not args.get("smoke"):
        spec = registry.get_spec(name)
        caps = spec.get("capabilities") or {}
        checks = [{"capability": c, "passed": bool(caps.get(c)),
                   "evidence": "declared（未实测）"}
                  for c in MINIMAL_CAPS]
        return {"checks": checks, "overall": "declared",
                "validation_level": "declared"}
    workspace = args.get("workspace")
    if not workspace:
        raise HiveError("REQUIRED_ARGUMENT", "--smoke 需要 --workspace 隔离目录")
    alt = args.get("alternate_model")
    if not alt:
        raise HiveError("REQUIRED_ARGUMENT", "--smoke 需要 --alternate-model")
    model1 = args.get("model") or registry.get_spec(name).get("default_model")
    os.makedirs(workspace, exist_ok=True)
    with open(os.path.join(workspace, "STARTUP.md"), "w", encoding="utf-8") as f:
        f.write("# Smoke 启动说明\n\n这是五能力演练的隔离工位。收到消息请简短回应。\n")
    checks: list[dict] = []

    test_agent = f"smoke-{new_id('agent')[4:10]}"
    test_id = new_id("agent")
    spec_for_smoke = registry.get_spec(name)
    kind = spec_for_smoke.get("adapter")
    # 1) start
    handle = None
    try:
        res = registry.start(svc, test_id, name,
                             {"model": model1, "yolo": True,
                              "workspace": workspace})
        handle = res.get("handle")
        checks.append({"capability": "start", "passed": bool(handle),
                       "evidence": f"handle={handle}"})
    except HiveError as e:
        checks.append({"capability": "start", "passed": False,
                       "evidence": e.message})
    # 2) send
    if handle:
        st = registry.send(svc, test_id, name, handle, "smoke message",
                           "smoke-1")
        checks.append({"capability": "send", "passed": st in ("accepted",),
                       "evidence": f"send={st}"})
    else:
        checks.append({"capability": "send", "passed": False, "evidence": "no handle"})
    # 3) yolo — verified by session readback when the adapter supports it
    #    (zsession), otherwise by the launch configuration actually used
    yolo_ev = "started with yolo=True"
    yolo_ok = True
    if handle and handle.startswith("sess:"):
        try:
            info = ZSessionAdapter._req(spec_for_smoke, "GET",
                                        f"/sessions/{handle[5:]}")
            sess = info.get("session") or info
            mode = sess.get("mode") or (sess.get("meta") or {}).get("mode")
            if mode:
                yolo_ok = (str(mode).lower() in ("yolo", "full", "*"))
                yolo_ev = f"session mode readback={mode}"
            else:
                yolo_ev = "session readback lacks mode field; "                           "launched with yolo=True"
        except Exception as e:  # noqa: BLE001
            yolo_ev = f"readback failed ({e!r:.60}); launched with yolo=True"
    checks.append({"capability": "yolo", "passed": yolo_ok,
                   "evidence": yolo_ev})
    # 4) model switch via stop+start, verified by session readback (model
    #    field) when available
    if handle:
        registry.stop(svc, test_id, name, handle, "smoke")
        res2 = registry.start(svc, test_id, name,
                              {"model": alt, "yolo": True, "workspace": workspace})
        handle2 = res2.get("handle")
        ev = f"restarted with model={alt}"
        ok4 = bool(handle2)
        if handle2 and handle2.startswith("sess:"):
            try:
                info2 = ZSessionAdapter._req(spec_for_smoke, "GET",
                                             f"/sessions/{handle2[5:]}")
                m2 = ((info2.get("session") or info2).get("model") or {})
                actual = m2.get("modelId") or m2.get("id") or ""
                if actual:
                    ok4 = actual.lower() == alt.split("/")[-1].lower()
                    ev = f"session model readback={actual} (want {alt})"
            except Exception as e:  # noqa: BLE001
                ev += f"; readback failed ({e!r:.60})"
        checks.append({"capability": "model_at_start", "passed": ok4,
                       "evidence": ev})
        handle = handle2
    else:
        checks.append({"capability": "model_at_start", "passed": False,
                       "evidence": "no handle"})
    # 5) stop
    if handle:
        st = registry.stop(svc, test_id, name, handle, "smoke")
        checks.append({"capability": "stop", "passed": st == "stopped",
                       "evidence": f"stop={st}"})
    else:
        checks.append({"capability": "stop", "passed": False, "evidence": "no handle"})
    overall = "passed" if all(c["passed"] for c in checks) else "failed"
    with svc.store.write() as cur:
        cur.execute("UPDATE harnesses SET validation_level=? WHERE name=?",
                    ("smoke:" + overall, name))
    return {"checks": checks, "overall": overall}
