"""Local host commands: control system init / serve / start (per DESIGN §15.2)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request

from . import SCHEMA_VERSION, __version__
from .auth import create_subject, issue_credential
from .errors import HiveError
from .home import (DaemonLock, HiveLogger, default_endpoint, process_alive,
                   read_system_json, write_system_json)
from .store import open_store
from .timeutil import format_rfc3339, now_utc


def _init_owner(home: str, out_path: str | None) -> str:
    """Create the owner subject + credential. Returns credential file path."""
    store = open_store(home)
    try:
        existing = store.conn.execute(
            "SELECT subject_id FROM subjects WHERE subject_type='owner'").fetchone()
        with store.write() as cur:
            if existing:
                owner_id = existing["subject_id"]
            else:
                owner_id = create_subject(cur, "owner", "owner")
            out = os.path.abspath(out_path or os.path.join(home, "auth", "owner.json"))
            issue_credential(cur, owner_id, kind="owner", out_path=out)
        return out
    finally:
        store.close()


def cmd_init(args: dict) -> dict:
    home = os.path.abspath(args["home"])
    listen = args.get("listen") or "127.0.0.1:19962"
    test_mode = bool(args.get("test_mode"))
    existing = read_system_json(home)
    if existing.get("initialized"):
        sysjson = existing
        cred = args.get("owner_credential_out") or os.path.join(
            home, "auth", "owner.json")
        if not os.path.exists(cred):
            _init_owner(home, args.get("owner_credential_out"))
        return {"home": home, "schema_version": SCHEMA_VERSION,
                "credential_file": os.path.join(home, "auth", "owner.json"),
                "initialized": False, "already_initialized": True,
                "listen": sysjson.get("listen"), "test_mode": sysjson.get("test_mode")}

    for sub in ("data", "definitions/agents", "definitions/ports", "workspaces",
                "auth", "runtime", "logs", "backups"):
        os.makedirs(os.path.join(home, sub), exist_ok=True)
    sysjson = {
        "initialized": True, "schema_version": SCHEMA_VERSION,
        "version": __version__, "listen": listen, "test_mode": test_mode,
        "created_at": format_rfc3339(now_utc()),
    }
    if args.get("default_harness"):
        # DESIGN CMD-017: --default-harness 落盘为初始默认 harness（此前
        # 是死参数——接受但永不生效，RDEF-06发现）
        sysjson["default_harness"] = args["default_harness"]
    write_system_json(home, sysjson)
    cred_out = args.get("owner_credential_out") or os.path.join(home, "auth",
                                                                "owner.json")
    cred = _init_owner(home, cred_out)
    # open once to apply migrations and seed default policy/harnesses
    store = open_store(home)
    try:
        from .harness import Registry
        Registry.bootstrap_defaults(store, home)
    finally:
        store.close()
    return {"home": home, "schema_version": SCHEMA_VERSION, "credential_file": cred,
            "initialized": True, "listen": listen, "test_mode": test_mode}


def probe_health(endpoint: str, timeout: float = 2.0) -> dict | None:
    try:
        with urllib.request.urlopen(endpoint.rstrip("/") + "/v1/health",
                                    timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError):
        return None


def _wait_ready(endpoint: str, pid: int | None, deadline: float = 25.0) -> bool:
    start = time.time()
    while time.time() - start < deadline:
        if probe_health(endpoint):
            return True
        if pid and not process_alive(pid):
            return False
        time.sleep(0.4)
    return False


def cmd_start(args: dict) -> dict:
    home = os.path.abspath(args["home"])
    sysjson = read_system_json(home)
    if not sysjson.get("initialized"):
        raise HiveError("NOT_FOUND", f"home 未初始化: {home}")
    listen = args.get("listen") or sysjson.get("listen") or "127.0.0.1:19962"
    test_mode = bool(args.get("test_mode", sysjson.get("test_mode")))
    endpoint = f"http://{listen}" if not listen.startswith("http") else listen
    # local daemon lock: holder pid means an in-flight serve process
    lock = DaemonLock(home)
    for _round in range(3):
        pid = lock.holder_pid()
        if pid and process_alive(pid):
            if _wait_ready(endpoint, pid, deadline=8.0):
                return {"service_pid": pid, "endpoint": endpoint,
                        "ready": True, "already_running": True}
            if not process_alive(pid):
                continue  # holder died mid-wait (stop in progress) -> respawn
            raise HiveError("SERVICE_UNAVAILABLE",
                            "服务进程在但健康检查未通过")
        if probe_health(endpoint):
            # service reachable but lock not visible (e.g. restored home)
            return {"service_pid": None, "endpoint": endpoint, "ready": True,
                    "already_running": True}
        break
    cli = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "cli", "hive.py")
    log_out = open(os.path.join(home, "logs", "daemon.out.log"), "a", encoding="utf-8")
    log_err = open(os.path.join(home, "logs", "daemon.err.log"), "a", encoding="utf-8")
    argv = [sys.executable, cli, "--home", home, "control", "system", "serve",
            "--listen", listen]
    if test_mode:
        argv.append("--test-mode")
    creationflags = 0
    if sys.platform == "win32":
        creationflags = (subprocess.DETACHED_PROCESS
                         | subprocess.CREATE_NEW_PROCESS_GROUP)
    proc = subprocess.Popen(argv, stdout=log_out, stderr=log_err,
                            creationflags=creationflags, cwd=home,
                            start_new_session=True)
    with open(os.path.join(home, "runtime", "service.json"), "w",
              encoding="utf-8") as f:
        json.dump({"pid": proc.pid, "endpoint": endpoint,
                   "started_at": format_rfc3339(now_utc())}, f)
    if not _wait_ready(endpoint, proc.pid):
        raise HiveError("SERVICE_UNAVAILABLE",
                        "后台服务启动后未就绪，查看 logs/daemon.err.log",
                        {"pid": proc.pid})
    return {"service_pid": proc.pid, "endpoint": endpoint, "ready": True,
            "already_running": False}


def cmd_restore_local(args: dict) -> dict:
    """Offline restore into a NEW empty home (control system restore)."""
    from .backup import restore_backup
    return restore_backup(args["from_dir"], args["into"],
                          with_credentials=bool(args.get("with_credentials")),
                          home_arg=args["home"])
