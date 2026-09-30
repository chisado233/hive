"""HTTP daemon: /v1/health, /v1/commands, /v1/schema, /v1/tasks, /v1/human/*,
/human/ web page. CLI and human web share the same domain handlers."""
from __future__ import annotations

import json
import os
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import PROTOCOL_VERSION, __version__
from .auth import SYSTEM_SUBJECT, Subject
from .errors import HiveError
from .home import DaemonLock, HiveLogger, read_system_json
from .ids import new_id
from .service import Service
from .store import open_store


def make_handler(service: Service):
    logger = service.logger

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "hive/" + __version__

        def log_message(self, fmt, *a):  # route access logs to sanitized logger
            logger.debug("http", request=self.path.split("?")[0])

        # ---------- helpers ----------
        def _send(self, status: int, obj: dict):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _ok(self, data: dict, request_id: str, status: int = 200,
                warnings: list | None = None):
            self._send(status, {"ok": True, "request_id": request_id,
                                "data": data, "warnings": warnings or []})

        def _err(self, err: HiveError, request_id: str):
            self._send(err.http_status, {"ok": False, "request_id": request_id,
                                         "error": err.to_dict()})

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            if length > 4 * 1024 * 1024:
                raise HiveError("INVALID_ARGUMENT", "请求体过大")
            raw = self.rfile.read(length)
            if not raw:
                return {}
            try:
                data = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                raise HiveError("INVALID_JSON", "请求体不是合法JSON")
            if not isinstance(data, dict):
                raise HiveError("INVALID_ARGUMENT", "请求体必须是对象")
            return data

        def _subject(self):
            auth = self.headers.get("Authorization") or ""
            token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
            if not token:
                return None, None
            subject = service.authn.authenticate(token)
            return subject, token

        def _idem_header(self, body: dict) -> str | None:
            key = self.headers.get("Idempotency-Key")
            rid = body.get("request_id")
            if key and rid and str(key) != str(rid):
                raise HiveError("INVALID_ARGUMENT",
                                "Idempotency-Key 与 body.request_id 不一致")
            return str(key or rid or "") or None

        # ---------- routes ----------
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            try:
                if path == "/v1/health":
                    return self._send(200, {"ok": True, "ready": True,
                                            "version": __version__,
                                            "protocol_version": PROTOCOL_VERSION})
                if path == "/v1/schema":
                    subject, _ = self._subject()
                    if subject is None:
                        raise HiveError("AUTH_REQUIRED", "schema 需要认证")
                    from .manifest import schema_export
                    return self._ok(schema_export(test_mode=service.test_mode),
                                    new_id("request"))
                if path == "/v1/tasks":
                    return self._tasks_query(parsed)
                if path.startswith("/v1/tasks/"):
                    return self._task_get(path.rsplit("/", 1)[-1])
                if path == "/v1/human/tasks":
                    return self._human_tasks(parsed)
                if path == "/human" or path == "/human/":
                    return self._human_page(parsed)
                if path.startswith("/human/answer"):
                    return self._human_page(parsed)
                raise HiveError("NOT_FOUND", f"路径不存在: {path}")
            except HiveError as e:
                self._err(e, new_id("request"))
            except Exception as e:  # noqa: BLE001
                logger.error("http.get", path=path, error=repr(e))
                raise
                # BaseHTTPRequestHandler will close the connection

        def do_POST(self):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            request_id = new_id("request")
            try:
                if path == "/v1/commands":
                    body = self._body()
                    request_id = self._idem_header(body) or request_id
                    subject, _ = self._subject()
                    if subject is None:
                        raise HiveError("AUTH_REQUIRED", "缺少凭据")
                    command = body.get("command") or ""
                    arguments = body.get("arguments") or {}
                    if not isinstance(command, str) or not isinstance(arguments, dict):
                        raise HiveError("INVALID_ARGUMENT", "command/arguments 格式错误")
                    data, _new = service.dispatch(subject, command, arguments,
                                                   request_id)
                    status = 201 if command == "emit.new" else 200
                    async_ops = data.get("operation_id") if isinstance(data, dict) else None
                    if async_ops and data.get("accepted_only"):
                        status = 202
                    return self._ok(data, request_id, status=status)
                if path == "/v1/tasks":
                    body = self._body()
                    request_id = self._idem_header(body) or request_id
                    subject, _ = self._subject()
                    if subject is None:
                        raise HiveError("AUTH_REQUIRED", "缺少凭据")
                    spec = dict(body.get("spec") or {})
                    if body.get("event_id"):
                        spec["event_id"] = body["event_id"]
                    if body.get("root"):
                        spec["root"] = True
                    elif body.get("parent"):
                        spec["parent"] = body["parent"]
                    spec["_via_http_tasks"] = True
                    data, _ = service.dispatch(subject, "producer.emit", spec,
                                                request_id)
                    return self._ok(data, request_id, status=201)
                if path.startswith("/v1/human/tasks/") and path.endswith("/answer"):
                    task_id = path.split("/")[4]
                    body = self._body()
                    request_id = self._idem_header(body) or request_id
                    subject, _ = self._subject()
                    if subject is None:
                        raise HiveError("AUTH_REQUIRED", "缺少凭据")
                    args = {"task": task_id,
                            "spec_version": int(body.get("spec_version") or 0),
                            "request_id": request_id}
                    result = body.get("result") or {}
                    if isinstance(result.get("text"), str):
                        args["text"] = result["text"]
                    if isinstance(result.get("data"), dict):
                        args["data"] = result["data"]
                    if isinstance(result.get("attachments"), list):
                        args["attach"] = result["attachments"]
                    data, _ = service.dispatch(subject, "control.human.answer",
                                                args, request_id)
                    return self._ok(data, request_id)
                raise HiveError("NOT_FOUND", f"路径不存在: {path}")
            except HiveError as e:
                self._err(e, request_id)
            except Exception as e:  # noqa: BLE001
                import traceback
                logger.error("http.post", path=path, error=repr(e),
                             tb=traceback.format_exc()[-1200:])
                self._err(HiveError("INTERNAL_ERROR", "内部错误"), request_id)

        # ---------- domain-backed GET endpoints ----------
        def _tasks_query(self, parsed):
            subject, _ = self._subject()
            if subject is None:
                raise HiveError("AUTH_REQUIRED", "缺少凭据")
            data, _ = service.dispatch(subject, "control.task.list", {}, None)
            self._ok(data, new_id("request"))

        def _task_get(self, task_id):
            subject, _ = self._subject()
            if subject is None:
                raise HiveError("AUTH_REQUIRED", "缺少凭据")
            data, _ = service.dispatch(subject, "control.task.show",
                                        {"task": task_id}, None)
            self._ok(data, new_id("request"))

        def _human_tasks(self, parsed):
            subject, _ = self._subject()
            if subject is None:
                raise HiveError("AUTH_REQUIRED", "缺少凭据")
            data, _ = service.dispatch(subject, "control.human.list", {}, None)
            self._ok(data, new_id("request"))

        def _human_page(self, parsed):
            from .webhuman import render_human_page
            qs = dict(urllib.parse.parse_qsl(parsed.query))
            token = qs.get("token", "")
            try:
                body, ctype = render_human_page(service, token, qs)
            except HiveError as e:
                self._send(e.http_status, {"ok": False, "request_id": new_id("request"),
                                           "error": e.to_dict()})
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def run_daemon(home: str, listen: str, test_mode: bool, log_level: str) -> dict:
    sysjson = read_system_json(home)
    if not sysjson.get("initialized"):
        raise HiveError("NOT_FOUND", f"home 未初始化: {home}")
    if test_mode and not sysjson.get("test_mode"):
        raise HiveError("TEST_ONLY", "该home未初始化为test profile")
    if not test_mode and sysjson.get("test_mode"):
        test_mode = True  # serve an init'd test home defaults to test mode
    lock = DaemonLock(home)
    holder = lock.try_acquire()
    if holder is not None:
        raise HiveError("SERVICE_UNAVAILABLE",
                        f"单写者冲突：daemon 已由 pid {holder} 持有", {"holder_pid": holder})
    logger = HiveLogger(home, log_level)
    store = open_store(home)
    service = Service(store, home=home, test_mode=test_mode, logger=logger)
    # init-time --default-harness seeds the policy when none was set online
    _dh = sysjson.get("default_harness")
    if _dh and not (store.kv_get("policy", {}) or {}).get("default_harness"):
        pol = service.policy()
        pol["default_harness"] = _dh
        store.kv_set("policy", pol)
    # runtime subsystems
    from .harness import Registry
    service.harnesses = Registry(store, home, service)
    from .scheduler import Scheduler
    service.scheduler = Scheduler(service)
    httpd = ThreadingHTTPServer(("127.0.0.1", int(listen.split(":")[1])),
                                make_handler(service))
    httpd.daemon_threads = True
    ready = {"ok": True, "event": "daemon.ready", "pid": os.getpid(),
             "endpoint": f"http://127.0.0.1:{listen.split(':')[1]}",
             "test_mode": test_mode, "version": __version__}
    print(json.dumps(ready, ensure_ascii=False), flush=True)
    logger.info("daemon.ready", pid=os.getpid(), endpoint=ready["endpoint"])
    service.idem_startup_sweep()  # DEF-04: clear orphaned reservations
    service.scheduler.start()
    httpd.timeout = 0.5
    try:
        while not service.shutdown.is_set():
            httpd.handle_request()
    finally:
        service.scheduler.stop()
        httpd.server_close()
        lock.release()
        store.close()
        logger.info("daemon.stopped", pid=os.getpid())
    return ready
