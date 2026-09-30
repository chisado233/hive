"""Service: central object tying store, auth, clock, policy, faults together,
plus the command dispatcher with idempotency and fault injection."""
from __future__ import annotations

import hashlib
import json
import threading

from . import __version__, PROTOCOL_VERSION
from .auth import Authenticator, Authorizer, Subject
from .clock import Clock
from .errors import HiveError
from .home import HiveLogger
from .ids import new_id
from .manifest import BY_PATH, validate_arguments
from .store import Store
from .timeutil import format_rfc3339, now_utc

DEFAULT_POLICY = {
    "default_harness": None,
    "default_model": None,
    "reconcile_interval": 60,
    "notify_jitter": 3,
    "notify_retry_after": 300,
    "unclaimed_alert_after": 900,
    "cold_start_batch": 1,
    "send_unknown_retry_after": 300,
    "restart_backoff": [5, 15, 60],
    "restart_max_per_hour": 3,
    "outbox_retry": [1, 5, 30, 120, 600],
    "outbox_max_attempts": 20,
    "rpc_body_limit": 1048576,
    "idem_lease_seconds": 60,
    "context_bytes_limit": 524288,
    "recovery_hold": False,
}

FAULT_POINTS = {
    "tx_before_commit", "rpc_response_drop", "outbox_after_commit",
    "notification_drop", "harness_start_after_launch", "harness_stop_unknown",
    "config_after_publish", "event_duplicate", "event_reorder",
    "operation_before_execute",
}


def canonical_hash(payload) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True,
                                     ensure_ascii=False).encode()).hexdigest()


class Service:
    def __init__(self, store: Store, *, home: str, test_mode: bool = False,
                 logger: HiveLogger | None = None):
        self.store = store
        self.home = home
        self.test_mode = test_mode
        self.logger = logger or HiveLogger(home)
        self.authn = Authenticator(store)
        self.authz = Authorizer(store)
        self.started_at = now_utc()
        self.shutdown = threading.Event()
        self.handlers: dict[str, object] = {}
        offset = float(store.kv_get("clock_offset", 0) or 0)
        self.clock = Clock(offset)
        self._faults_lock = threading.Lock()
        # late-bound subsystems (set by runtime/scheduler)
        self.harnesses = None       # harness.Registry
        self.scheduler = None       # scheduler.Scheduler
        self.web_sender = None      # function(kind,target,payload) for webhook
        self._register_handlers()

    # ---------------- policy ----------------
    def policy(self) -> dict:
        pol = dict(DEFAULT_POLICY)
        pol.update(store_policy := self.store.kv_get("policy", {}) or {})
        return pol

    def policy_revision(self) -> int:
        return int(self.store.kv_get("policy_revision", 1) or 1)

    def now(self) -> str:
        return format_rfc3339(self.clock.now())

    # ---------------- faults ----------------
    def fault_active(self, point: str, entity: str | None = None) -> bool:
        faults = self.store.kv_get("faults", {}) or {}
        f = faults.get(point)
        if not f:
            return False
        if entity and isinstance(f.get("value"), dict):
            allowed = f["value"].get("entity")
            if allowed and allowed != entity:
                return False
        return True

    def consume_fault(self, point: str) -> bool:
        """For once-mode faults: returns True (and disarms) if the fault fires."""
        with self._faults_lock:
            if not self.fault_active(point):
                return False
            faults = self.store.kv_get("faults", {}) or {}
            f = faults.get(point) or {}
            if f.get("mode", "once") == "once":
                faults.pop(point, None)
                self.store.kv_set("faults", faults)
            return True

    # ---------------- dispatch ----------------
    INTERNAL_ROUTES = {"producer.emit"}

    # Fast ledger commands: idempotency reserve + handler + response backfill
    # commit in ONE transaction — a crash leaves either nothing (rollback,
    # reservation included) or everything (business effect + response).
    IDEM_ATOMIC_PREFIXES = ("emit.", "inbox.", "producer.emit",
                            "control.task.")
    # Slow commands (harness calls / long operations) cannot hold the global
    # write lock for their whole duration: they use lease-based takeover.
    IDEM_SLOW_PREFIXES = ("control.agent.", "control.harness.",
                          "control.test.", "control.system.",
                          "control.operation.", "control.port.",
                          "control.subscription.", "control.permission.",
                          "control.producer.", "control.trigger.",
                          "control.channel.", "control.human.",
                          "control.event.", "control.audit.",
                          "control.delivery.")

    def dispatch(self, subject: Subject, command: str, arguments: dict,
                 request_id: str | None = None) -> tuple[dict, bool]:
        """Returns (envelope_data, is_new). Raises HiveError on failure."""
        cmd = BY_PATH.get(command)
        if not cmd:
            if command in self.INTERNAL_ROUTES:
                cmd = None  # internal route: handler validates its own args
            else:
                raise HiveError("COMMAND_UNKNOWN", f"未知命令: {command}")
        if cmd and cmd.path[1] == "test" and not self.test_mode:
            raise HiveError("TEST_ONLY", "test命令仅在test profile可用")
        request_id = request_id or new_id("request")
        normalized = dict(arguments or {})
        # idempotency: only for state-changing commands (non-list queries excluded
        # cheaply by command having a mutation handler); we apply to all commands
        # with explicit request ids and all mutations.
        is_query = bool(cmd) and cmd.path[0] in ("inbox",) and cmd.path[1] in (
            "list", "peek", "tree", "wait") and cmd.path[-1] in ("list", "show") \
            or command in ("control.system.status", "control.system.log")
        payload_hash = canonical_hash(
            {k: v for k, v in normalized.items() if k != "request_id"})

        handler = self.handlers.get(command)
        if handler is None:
            raise HiveError("NOT_IMPLEMENTED", f"命令已定义但尚未实现: {command}")
        args = validate_arguments(cmd, normalized) if cmd else normalized

        atomic = command.startswith(self.IDEM_ATOMIC_PREFIXES)
        idem_key = None
        if is_query or not request_id:
            result = handler(self, subject, args, request_id)
            return self._finish_dispatch(result, command, None)

        idem_key = (subject.subject_id, command, request_id)
        kill_in_tx = False
        if atomic and self.fault_active("idem_kill_in_tx"):
            # disarm in its OWN committed tx first: the kill below rolls
            # back the business tx, and the disarm must survive the crash
            self.consume_fault("idem_kill_in_tx")
            kill_in_tx = True
        if atomic:
            # reserve + execute + backfill in one transaction; a concurrent
            # same-key request serializes on the write lock and then replays
            with self.store.write() as cur:
                replay = self._idem_replay_check(cur, idem_key, payload_hash)
                if replay is not None:
                    return replay, False
                cur.execute(
                    "INSERT OR IGNORE INTO idempotency(subject_id,command,"
                    "request_id,payload_hash,response,created_at,lease_until)"
                    " VALUES (?,?,?,?,NULL,?,NULL)",
                    (idem_key[0], idem_key[1], idem_key[2], payload_hash,
                     format_rfc3339(now_utc())))
                if cur.execute("SELECT changes()").fetchone()[0] != 1:
                    # lost the insert race inside this tx — replay the winner
                    replay = self._idem_replay_check(cur, idem_key,
                                                     payload_hash)
                    if replay is not None:
                        return replay, False
                    raise HiveError("STORAGE_UNAVAILABLE",
                                    "同request-id占位冲突", {"retryable": True})
                result = handler(self, subject, args, request_id)
                if kill_in_tx:
                    # real crash path: business rows + reservation + response
                    # are ALL uncommitted — process death rolls everything back
                    import os as _os
                    _os._exit(70)
                cur.execute(
                    "UPDATE idempotency SET response=? WHERE subject_id=? "
                    "AND command=? AND request_id=? AND response IS NULL",
                    (json.dumps(result, ensure_ascii=False), *idem_key))
            return self._finish_dispatch(result, command, idem_key)

        # slow path: lease reservation, then handler outside the tx
        replay = self._idem_reserve_leased(idem_key, payload_hash)
        if replay is not None:
            return replay, False
        if self.consume_fault("idem_kill_after_reserve"):
            # real crash path: reservation committed (own tx), handler never
            # ran, process dies — orphan lease must later be takeable
            import os as _os
            _os._exit(70)
        try:
            result = handler(self, subject, args, request_id)
        except BaseException:
            with self.store.write() as cur:
                cur.execute("DELETE FROM idempotency WHERE subject_id=? "
                            "AND command=? AND request_id=? AND response "
                            "IS NULL", idem_key)
            raise
        self._idem_store(idem_key, payload_hash, result)
        return self._finish_dispatch(result, command, idem_key)

    def _finish_dispatch(self, result, command, idem_key):
        if self.consume_fault("rpc_response_drop"):
            raise HiveError("TRANSPORT_UNAVAILABLE",
                            "响应已按故障点丢弃；请求可能已提交，请用相同request-id重试",
                            {"retryable": True})
        return result, True

    def _idem_replay_check(self, cur, key, payload_hash):
        """Inside a tx: replay if the key already carries a response."""
        row = cur.execute(
            "SELECT payload_hash, response FROM idempotency WHERE "
            "subject_id=? AND command=? AND request_id=?", key).fetchone()
        if not row:
            return None
        if row["payload_hash"] != payload_hash:
            raise HiveError("IDEMPOTENCY_CONFLICT",
                            "相同request-id但参数不同",
                            {"request_id": key[2]})
        if row["response"] is not None:
            return json.loads(row["response"])
        return None

    def _idem_lease_seconds(self) -> int:
        try:
            return int(self.policy().get("idem_lease_seconds", 60))
        except (TypeError, ValueError):
            return 60

    def _idem_reserve_leased(self, key, payload_hash):
        """Slow-path reservation with a lease. Returns cached response for
        replay, None when this request owns the execution.

        Orphaned reservations (holder died before backfill) become takeable
        once the lease expires: a same-key retry CAS-extends the lease and
        re-executes — effect safety comes from the command itself being
        merge-tolerant (slot-unique starts, idempotent stops)."""
        import time as _time
        lease = self._idem_lease_seconds()
        deadline = _time.monotonic() + min(lease + 5, 30)
        while True:
            import datetime as _dt
            now = format_rfc3339(now_utc())
            lease_until = format_rfc3339(
                now_utc() + _dt.timedelta(seconds=lease))
            with self.store.write() as cur:
                row = cur.execute(
                    "SELECT payload_hash, response, lease_until FROM "
                    "idempotency WHERE subject_id=? AND command=? AND "
                    "request_id=?", key).fetchone()
                if row is None:
                    cur.execute(
                        "INSERT INTO idempotency(subject_id,command,"
                        "request_id,payload_hash,response,created_at,"
                        "lease_until) VALUES (?,?,?,?,NULL,?,?)",
                        (key[0], key[1], key[2], payload_hash, now,
                         lease_until))
                    return None
                if row["payload_hash"] != payload_hash:
                    raise HiveError("IDEMPOTENCY_CONFLICT",
                                    "相同request-id但参数不同",
                                    {"request_id": key[2]})
                if row["response"] is not None:
                    return json.loads(row["response"])
                expired = (row["lease_until"] is None
                           or row["lease_until"] <= now)
                if expired:
                    took = cur.execute(
                        "UPDATE idempotency SET lease_until=? WHERE "
                        "subject_id=? AND command=? AND request_id=? AND "
                        "response IS NULL AND (lease_until IS NULL OR "
                        "lease_until<=?)",
                        (lease_until, key[0], key[1], key[2], now)).rowcount
                    if took == 1:
                        return None  # lease taken over by us
            if _time.monotonic() > deadline:
                raise HiveError("STORAGE_UNAVAILABLE",
                                "同request-id请求仍在执行中；占位在租约到期后"
                                "可用相同request-id接管重试",
                                {"retryable": True,
                                 "lease_seconds": lease})
            _time.sleep(0.3)

    def idem_startup_sweep(self):
        """Daemon start: clear reservations whose lease expired long ago
        (holder died before backfill). Kept rows keep the conflict check."""
        now = format_rfc3339(now_utc())
        with self.store.write() as cur:
            cur.execute("DELETE FROM idempotency WHERE response IS NULL "
                        "AND lease_until IS NOT NULL AND lease_until<?", (now,))

    def _idem_store(self, key, payload_hash, result):
        with self.store.write() as cur:
            cur.execute(
                "UPDATE idempotency SET response=?, payload_hash=? WHERE "
                "subject_id=? AND command=? AND request_id=?",
                (json.dumps(result, ensure_ascii=False), payload_hash, *key))

    def status(self) -> dict:
        pending = self.store.conn.execute(
            "SELECT COUNT(*) c FROM outbox WHERE state IN ('pending','retry')"
        ).fetchone()["c"]
        running = self.store.conn.execute(
            "SELECT COUNT(*) c FROM operations WHERE state IN ('accepted','running')"
        ).fetchone()["c"]
        uptime = int((now_utc() - self.started_at).total_seconds())
        warnings = []
        if self.policy().get("recovery_hold"):
            warnings.append("recovery_hold: 自动运行/投递已被冻结，待显式解除")
        return {"version": __version__, "protocol_version": PROTOCOL_VERSION,
                "uptime": uptime, "home": self.home, "test_mode": self.test_mode,
                "pending_outbox": pending, "running_operations": running,
                "warnings": warnings}

    # ---------------- handler registration ----------------
    def _register_handlers(self):
        from . import handlers_core
        from . import handlers_task
        from . import handlers_ext
        from . import handlers_test
        for mod in (handlers_core, handlers_task, handlers_ext, handlers_test):
            mod.register(self)

    def register(self, command: str, fn):
        self.handlers[command] = fn
