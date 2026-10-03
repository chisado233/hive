"""Home resolution, system.json, single-writer daemon lock, sanitized logs."""
from __future__ import annotations

import ctypes
import json
import os
import re
import sys
import tempfile

from .errors import HiveError

DEFAULT_ENDPOINT = "http://127.0.0.1:19962"
TOKEN_PATTERNS = [
    re.compile(r"hvt_[A-Za-z0-9_\-]{20,}"),
    re.compile(r"(?i)(bearer\s+)[^\s\"]+"),
]

_SECRET_KEYS = {"token", "secret", "password", "api_key", "credential", "key"}


def sanitize(obj):
    """Recursively redact secrets from any structure before logging."""
    if isinstance(obj, dict):
        return {k: ("***" if any(s in k.lower() for s in _SECRET_KEYS) else sanitize(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize(v) for v in obj]
    if isinstance(obj, str):
        s = obj
        for pat in TOKEN_PATTERNS:
            s = pat.sub("***", s)
        return s
    return obj


def resolve_home(explicit: str | None) -> str:
    if explicit:
        home = os.path.abspath(explicit)
    elif os.environ.get("HIVE_HOME"):
        home = os.path.abspath(os.environ["HIVE_HOME"])
    else:
        home = os.path.abspath(os.path.join(_project_root(), ".hive"))
    return home


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def default_endpoint() -> str:
    return os.environ.get("HIVE_URL") or DEFAULT_ENDPOINT


def read_system_json(home: str) -> dict:
    path = os.path.join(home, "system.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_system_json(home: str, data: dict) -> None:
    path = os.path.join(home, "system.json")
    os.makedirs(home, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=home,
                                      prefix=".system-", delete=False)
    json.dump(data, tmp, ensure_ascii=False, indent=2)
    tmp.close()
    os.replace(tmp.name, path)


def process_alive(pid: int) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        if sys.platform == "win32":
            from ctypes import wintypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL,
                                            wintypes.DWORD]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE,
                                                    ctypes.POINTER(wintypes.DWORD)]
            kernel32.GetExitCodeProcess.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION,
                                          False, int(pid))
            if not handle:
                return False
            try:
                # An exited process can still have open handles and therefore
                # OpenProcess succeeds. Only STILL_ACTIVE means it is running;
                # otherwise stale daemon locks would block all future starts.
                code = wintypes.DWORD()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return True  # cannot prove exit: retain single-writer fence
                return code.value == STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class DaemonLock:
    """Single-writer lock: runtime/daemon.lock holds pid; stale locks detected."""

    def __init__(self, home: str):
        self.path = os.path.join(home, "runtime", "daemon.lock")
        self.held = False

    def try_acquire(self) -> int | None:
        """Acquire for current process. Returns holder pid if already locked."""
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    pid = int(json.load(f).get("pid", 0))
            except (ValueError, OSError, json.JSONDecodeError):
                pid = 0
            if pid == os.getpid():
                self.held = True
                return None
            if pid and process_alive(pid):
                return pid
            # stale lock
            os.remove(self.path)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid()}, f)
        self.held = True
        return None

    def release(self):
        if self.held and os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    if int(json.load(f).get("pid", 0)) == os.getpid():
                        os.remove(self.path)
            except (OSError, ValueError, json.JSONDecodeError):
                pass
        self.held = False

    def holder_pid(self) -> int | None:
        if not os.path.exists(self.path):
            return None
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return int(json.load(f).get("pid", 0)) or None
        except (OSError, ValueError, json.JSONDecodeError):
            return None


class HiveLogger:
    """JSON-lines log at <home>/logs/hive.log; always sanitized."""

    LEVELS = {"debug": 10, "info": 20, "warning": 30, "error": 40}

    def __init__(self, home: str, level: str = "info"):
        self.path = os.path.join(home, "logs", "hive.log")
        self.level_no = self.LEVELS.get(level, 20)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def log(self, level: str, event: str, **fields):
        if self.LEVELS.get(level, 20) < self.level_no:
            return
        from .timeutil import format_rfc3339, now_utc
        rec = {"ts": format_rfc3339(now_utc()), "level": level, "event": event}
        rec.update(sanitize(fields))
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def debug(self, event, **f):
        self.log("debug", event, **f)

    def info(self, event, **f):
        self.log("info", event, **f)

    def warning(self, event, **f):
        self.log("warning", event, **f)

    def error(self, event, **f):
        self.log("error", event, **f)

    def tail(self, n: int = 100, level: str | None = None,
             since: str | None = None) -> list[dict]:
        out = []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            return []
        min_no = self.LEVELS.get(level, 0) if level else 0
        for line in lines[-min(n * 4, 40000):]:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if self.LEVELS.get(rec.get("level"), 20) < min_no:
                continue
            if since and rec.get("ts", "") < since:
                continue
            out.append(rec)
        return out[-n:]
