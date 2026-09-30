"""SQLite storage: schema, migrations, transaction helper with events/outbox.

Invariants per DESIGN §11 implemented here:
- agent_id unique over live-instance slot (partial unique index)
- (agent_id, port_id) subscription unique
- idempotency key unique per (subject, command, request_id)
- receipts / notices / trigger events / channel deliveries semantic unique keys
- WAL + busy_timeout; writes serialized through BEGIN IMMEDIATE + process lock
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager

from . import SCHEMA_VERSION
from .errors import HiveError
from .ids import new_id
from .timeutil import format_rfc3339, now_utc

MIGRATIONS: list[str] = [
    # 001 initial schema
    """
    CREATE TABLE subjects (
      subject_id TEXT PRIMARY KEY, subject_type TEXT NOT NULL,
      name TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
      metadata TEXT NOT NULL DEFAULT '{}'
    );
    CREATE TABLE credentials (
      credential_id TEXT PRIMARY KEY, subject_id TEXT NOT NULL,
      kind TEXT NOT NULL DEFAULT 'static', token_hash TEXT NOT NULL,
      instance_id TEXT, created_at TEXT NOT NULL, revoked_at TEXT
    );
    CREATE INDEX idx_credentials_subject ON credentials(subject_id);
    CREATE TABLE grants (
      grant_id TEXT PRIMARY KEY, subject_id TEXT NOT NULL,
      actions TEXT NOT NULL, resources TEXT NOT NULL,
      delegable INTEGER NOT NULL DEFAULT 0,
      created_by TEXT NOT NULL, created_at TEXT NOT NULL,
      expires_at TEXT, revoked_at TEXT
    );
    CREATE TABLE agents (
      agent_id TEXT PRIMARY KEY, name TEXT NOT NULL,
      lifecycle TEXT NOT NULL DEFAULT 'enabled',
      current_revision INTEGER NOT NULL DEFAULT 1,
      desired_revision INTEGER NOT NULL DEFAULT 1,
      applied_revision INTEGER NOT NULL DEFAULT 1,
      pending_fields TEXT NOT NULL DEFAULT '[]',
      workspace_path TEXT, personal_port_id TEXT,
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
      retired_at TEXT
    );
    CREATE TABLE agent_config_versions (
      agent_id TEXT NOT NULL, revision INTEGER NOT NULL,
      file_path TEXT NOT NULL, published INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL, published_at TEXT,
      PRIMARY KEY (agent_id, revision)
    );
    CREATE TABLE ports (
      port_id TEXT PRIMARY KEY, route TEXT NOT NULL UNIQUE,
      name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
      handler TEXT NOT NULL DEFAULT 'agents', state TEXT NOT NULL DEFAULT 'enabled',
      accepted_types TEXT NOT NULL, input_schema TEXT NOT NULL DEFAULT '{}',
      defaults TEXT NOT NULL, visibility TEXT NOT NULL DEFAULT 'authenticated',
      revision INTEGER NOT NULL DEFAULT 1, channel_ids TEXT NOT NULL DEFAULT '[]',
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE subscriptions (
      agent_id TEXT NOT NULL, port_id TEXT NOT NULL,
      enabled INTEGER NOT NULL DEFAULT 1,
      created_at TEXT NOT NULL, created_by TEXT NOT NULL,
      PRIMARY KEY (agent_id, port_id)
    );
    CREATE TABLE instances (
      instance_id TEXT PRIMARY KEY, agent_id TEXT NOT NULL,
      harness TEXT NOT NULL, handle TEXT, config_revision INTEGER NOT NULL,
      generation INTEGER NOT NULL, phase TEXT NOT NULL,
      observation TEXT NOT NULL DEFAULT 'unknown',
      started_at TEXT NOT NULL, ended_at TEXT, error TEXT
    );
    CREATE UNIQUE INDEX idx_instances_live_slot
      ON instances(agent_id) WHERE phase IN ('starting','live','stopping');
    CREATE TABLE tasks (
      task_id TEXT PRIMARY KEY, parent_id TEXT, root_id TEXT NOT NULL,
      type TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '',
      body TEXT NOT NULL DEFAULT '', data TEXT NOT NULL DEFAULT '{}',
      attachments TEXT NOT NULL DEFAULT '[]',
      source TEXT NOT NULL, target TEXT NOT NULL,
      state TEXT NOT NULL DEFAULT 'queued',
      owner_subject TEXT, claim_id TEXT, claim_epoch INTEGER NOT NULL DEFAULT 0,
      urgency TEXT NOT NULL DEFAULT 'normal', callback TEXT,
      reply_to TEXT, refs TEXT NOT NULL DEFAULT '[]',
      claim_deadline TEXT, expires_at TEXT,
      version INTEGER NOT NULL DEFAULT 1, spec_version INTEGER NOT NULL DEFAULT 1,
      result TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
      ended_at TEXT
    );
    CREATE INDEX idx_tasks_parent ON tasks(parent_id);
    CREATE INDEX idx_tasks_root ON tasks(root_id);
    CREATE INDEX idx_tasks_state ON tasks(state);
    CREATE INDEX idx_tasks_target ON tasks(target);
    CREATE TABLE task_claims (
      claim_id TEXT PRIMARY KEY, task_id TEXT NOT NULL,
      subject_id TEXT NOT NULL, instance_id TEXT,
      epoch INTEGER NOT NULL, created_at TEXT NOT NULL,
      invalidated_at TEXT, invalidated_reason TEXT
    );
    CREATE INDEX idx_claims_task ON task_claims(task_id);
    CREATE TABLE task_transfers (
      transfer_id TEXT PRIMARY KEY, task_id TEXT NOT NULL,
      from_target TEXT NOT NULL, to_target TEXT NOT NULL,
      epoch INTEGER NOT NULL, actor TEXT NOT NULL, reason TEXT NOT NULL,
      created_at TEXT NOT NULL
    );
    CREATE TABLE task_declines (
      decline_id TEXT PRIMARY KEY, task_id TEXT NOT NULL,
      subject_id TEXT NOT NULL, spec_version INTEGER NOT NULL,
      reason TEXT NOT NULL, created_at TEXT NOT NULL,
      UNIQUE (task_id, subject_id, spec_version)
    );
    CREATE TABLE task_views (
      view_id TEXT PRIMARY KEY, task_id TEXT NOT NULL,
      subject_id TEXT NOT NULL, seen_at TEXT NOT NULL,
      UNIQUE (task_id, subject_id)
    );
    CREATE TABLE comments (
      comment_id TEXT PRIMARY KEY, task_id TEXT NOT NULL,
      author_subject TEXT NOT NULL, body TEXT NOT NULL,
      mentions TEXT NOT NULL DEFAULT '[]', revision_of TEXT,
      checkpoint_refs TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL
    );
    CREATE INDEX idx_comments_task ON comments(task_id);
    CREATE TABLE waits (
      wait_id TEXT PRIMARY KEY, owner_task_id TEXT NOT NULL,
      mode TEXT NOT NULL DEFAULT 'all', state TEXT NOT NULL DEFAULT 'pending',
      deadline TEXT, checkpoint_refs TEXT NOT NULL DEFAULT '[]',
      created_by TEXT NOT NULL, created_at TEXT NOT NULL,
      resolved_at TEXT, notified_subject TEXT
    );
    CREATE INDEX idx_waits_task ON waits(owner_task_id);
    CREATE TABLE wait_dependencies (
      wait_id TEXT NOT NULL, dependency TEXT NOT NULL,
      result TEXT, PRIMARY KEY (wait_id, dependency)
    );
    CREATE TABLE operations (
      operation_id TEXT PRIMARY KEY, request_id TEXT NOT NULL,
      actor TEXT NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL,
      expected_version INTEGER, expected_instance TEXT,
      args TEXT NOT NULL DEFAULT '{}', reason TEXT NOT NULL DEFAULT '',
      state TEXT NOT NULL DEFAULT 'accepted',
      result TEXT, error TEXT, outcome_unknown INTEGER NOT NULL DEFAULT 0,
      callback TEXT, created_at TEXT NOT NULL,
      started_at TEXT, ended_at TEXT
    );
    CREATE INDEX idx_ops_request ON operations(request_id);
    CREATE TABLE events (
      event_id TEXT PRIMARY KEY, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
      entity_version INTEGER, kind TEXT NOT NULL,
      occurred_at TEXT NOT NULL, recorded_at TEXT NOT NULL,
      actor TEXT, cause_request_id TEXT, payload TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX idx_events_entity ON events(entity_type, entity_id);
    CREATE TABLE outbox (
      delivery_id TEXT PRIMARY KEY, semantic_key TEXT NOT NULL UNIQUE,
      kind TEXT NOT NULL, target TEXT NOT NULL, task_id TEXT,
      payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
      attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT,
      last_error TEXT, created_at TEXT NOT NULL, sent_at TEXT
    );
    CREATE INDEX idx_outbox_state ON outbox(state);
    CREATE TABLE idempotency (
      subject_id TEXT NOT NULL, command TEXT NOT NULL, request_id TEXT NOT NULL,
      payload_hash TEXT NOT NULL, response TEXT, created_at TEXT NOT NULL,
      PRIMARY KEY (subject_id, command, request_id)
    );
    CREATE TABLE producers (
      producer_id TEXT PRIMARY KEY, name TEXT NOT NULL,
      kind TEXT NOT NULL, allowed_targets TEXT NOT NULL,
      enabled INTEGER NOT NULL DEFAULT 1, revision INTEGER NOT NULL DEFAULT 1,
      metadata TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
    );
    CREATE TABLE producer_events (
      producer_id TEXT NOT NULL, event_id TEXT NOT NULL, task_id TEXT NOT NULL,
      content_hash TEXT NOT NULL, created_at TEXT NOT NULL,
      PRIMARY KEY (producer_id, event_id)
    );
    CREATE TABLE triggers (
      trigger_id TEXT PRIMARY KEY, name TEXT NOT NULL,
      kind TEXT NOT NULL, producer_id TEXT NOT NULL,
      task_spec TEXT NOT NULL, every_seconds INTEGER, cron TEXT,
      timezone TEXT NOT NULL DEFAULT 'UTC', enabled INTEGER NOT NULL DEFAULT 0,
      missed TEXT NOT NULL DEFAULT 'skip', last_cursor TEXT,
      revision INTEGER NOT NULL DEFAULT 1, next_run_at TEXT,
      metadata TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
    );
    CREATE TABLE channels (
      channel_id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
      kind TEXT NOT NULL, endpoint TEXT, credential_ref TEXT,
      enabled INTEGER NOT NULL DEFAULT 1, retry_policy TEXT,
      revision INTEGER NOT NULL DEFAULT 1, metadata TEXT NOT NULL DEFAULT '{}',
      created_at TEXT NOT NULL
    );
    CREATE TABLE channel_deliveries (
      task_id TEXT NOT NULL, channel_id TEXT NOT NULL,
      delivery_id TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
      PRIMARY KEY (task_id, channel_id)
    );
    CREATE TABLE harnesses (
      name TEXT PRIMARY KEY, spec TEXT NOT NULL,
      revision INTEGER NOT NULL DEFAULT 1,
      validation_level TEXT NOT NULL DEFAULT 'declared',
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE audit_log (
      audit_id TEXT PRIMARY KEY, actor TEXT NOT NULL, action TEXT NOT NULL,
      target TEXT NOT NULL DEFAULT '', request_id TEXT,
      reason TEXT NOT NULL DEFAULT '', details TEXT NOT NULL DEFAULT '{}',
      created_at TEXT NOT NULL
    );
    CREATE TABLE kv (k TEXT PRIMARY KEY, v TEXT NOT NULL);
    CREATE TABLE notifications (
      notify_id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, dedupe_key TEXT NOT NULL,
      message TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
      attempts INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL, sent_at TEXT,
      UNIQUE (agent_id, dedupe_key)
    );
    """,
    # 002 idempotency lease for crash-safe takeover (DEF-04)
    "ALTER TABLE idempotency ADD COLUMN lease_until TEXT",
    # 003 notification jitter window (DEF-05)
    "ALTER TABLE notifications ADD COLUMN send_after TEXT",
    # 004 executor fencing for slow-command lease takeover (RDEF-01): the
    # takeover bumps the generation, so an old lease holder resuming after a
    # takeover can never clobber the new executor's backfilled response
    "ALTER TABLE idempotency ADD COLUMN executor_gen INTEGER NOT NULL DEFAULT 0",
    # 005 normalized workspace ownership key (RDEF-07): unique index makes
    # concurrent same-directory binding fail at the DB level (one writer
    # wins, the other gets INVALID_ARGUMENT, derived files never clobber)
    "ALTER TABLE agents ADD COLUMN workspace_key TEXT",
]

def _split_statements(script: str) -> list[str]:
    out, buf = [], []
    for line in script.splitlines():
        if line.strip().startswith("--"):
            continue
        buf.append(line)
        if line.rstrip().endswith(";"):
            stmt = "\n".join(buf).strip().rstrip(";").strip()
            if stmt:
                out.append(stmt)
            buf = []
    tail = "\n".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def workspace_key(path) -> str | None:
    """Normalized identity of a workspace path for ownership comparison
    (RDEF-07): absolute + `..`/trailing-separator resolution + junction and
    8.3-short-name expansion when the target exists + OS case folding, so
    aliases of one physical directory can never bind two agents."""
    import sys
    if not path:
        return None
    p = os.path.abspath(os.path.normpath(str(path)))
    if os.path.exists(p):
        try:
            p = os.path.realpath(p)
        except OSError:
            pass
        if sys.platform == "win32":
            try:
                import ctypes
                buf = ctypes.create_unicode_buffer(1024)
                n = ctypes.windll.kernel32.GetLongPathNameW(p, buf,
                                                            len(buf))
                if n and buf.value:
                    p = buf.value
            except Exception:  # noqa: BLE001
                pass
    return os.path.normcase(os.path.normpath(p))


_EXTRA = [
    "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)",
]


class Store:
    """Per-thread connections: readers never observe another thread's
    half-applied transaction; writes serialize via RLock + BEGIN IMMEDIATE."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._write_lock = threading.RLock()
        self._local = threading.local()
        self.conn  # establish + migrate on the creating thread's connection

    def _new_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.isolation_level = None  # manual transactions
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    @property
    def conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._new_conn()
            self._local.conn = conn
            with self._write_lock:
                self._migrate_on(conn)
        return conn

    def close(self):
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
            conn.close()
            self._local.conn = None

    # ---------- migrations ----------
    def _migrate_on(self, conn: sqlite3.Connection):
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        try:
            for stmt in _EXTRA:
                cur.execute(stmt)
            row = cur.execute(
                "SELECT MAX(version) v FROM schema_migrations").fetchone()
            current = row["v"] or 0
            applied = 0
            for i, script in enumerate(MIGRATIONS, start=1):
                if i <= current:
                    continue
                for stmt in _split_statements(script):
                    cur.execute(stmt)
                cur.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?,?)",
                    (i, format_rfc3339(now_utc())))
                applied = i
            if applied >= 5 or (current >= 5 and applied == 0):
                # RDEF-07: backfill workspace keys (oldest agent wins a
                # legacy duplicate; the loser keeps NULL and stays covered
                # by the runtime path check), then enforce uniqueness
                cur.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_agents_wskey ON "
                    "agents(workspace_key) WHERE workspace_key IS NOT NULL")
                rows = cur.execute(
                    "SELECT agent_id, workspace_path FROM agents WHERE "
                    "workspace_key IS NULL AND workspace_path IS NOT NULL "
                    "ORDER BY created_at").fetchall()
                for r in rows:
                    k = workspace_key(r["workspace_path"])
                    if not k:
                        continue
                    try:
                        cur.execute(
                            "UPDATE agents SET workspace_key=? WHERE "
                            "agent_id=? AND workspace_key IS NULL",
                            (k, r["agent_id"]))
                    except sqlite3.IntegrityError:
                        pass  # legacy duplicate of an older binding
            cur.execute("INSERT OR REPLACE INTO kv(k,v) VALUES('schema_version',?)",
                        (str(SCHEMA_VERSION),))
            cur.execute("COMMIT")
        except BaseException:
            cur.execute("ROLLBACK")
            raise

    def migrate(self):
        self.conn  # touch: creates + migrates on this thread

    @property
    def schema_version(self) -> int:
        row = self.conn.execute("SELECT MAX(version) v FROM schema_migrations").fetchone()
        return row["v"] or 0

    # ---------- transactions ----------
    @contextmanager
    def write(self):
        """Serialized write transaction on this thread's connection. Reentrant:
        a nested write() joins the outer transaction (emit batch / dispatch)."""
        with self._write_lock:
            depth = getattr(self._local, "tx_depth", 0)
            if depth > 0:
                self._local.tx_depth = depth + 1
                try:
                    yield self.conn.cursor()
                finally:
                    self._local.tx_depth = depth
                return
            cur = self.conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            self._local.tx_depth = 1
            try:
                yield cur
                cur.execute("COMMIT")
            except BaseException:
                cur.execute("ROLLBACK")
                raise
            finally:
                self._local.tx_depth = 0

    @contextmanager
    def read(self):
        cur = self.conn.cursor()
        cur.execute("BEGIN DEFERRED")
        try:
            yield cur
        finally:
            cur.execute("COMMIT")

    # ---------- events / outbox / kv helpers ----------
    @staticmethod
    def add_event(cur, *, entity_type: str, entity_id: str, kind: str,
                  actor: str | None = "system",
                  payload: dict | None = None, entity_version: int | None = None,
                  occurred_at: str | None = None,
                  cause_request_id: str | None = None,
                  event_id: str | None = None) -> str:
        ts = occurred_at or format_rfc3339(now_utc())
        eid = event_id or new_id("event")
        cur.execute(
            "INSERT INTO events(event_id,entity_type,entity_id,entity_version,kind,"
            "occurred_at,recorded_at,actor,cause_request_id,payload) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (eid, entity_type, entity_id, entity_version, kind, ts,
             format_rfc3339(now_utc()), actor, cause_request_id,
             json.dumps(payload or {}, ensure_ascii=False)))
        # event_duplicate fault: replay the same event_id; the PK absorbs it
        # (real dedup-by-event_id evidence, no consumer-side special casing)
        Store._maybe_duplicate_event(cur, eid)
        return eid

    @staticmethod
    def _maybe_duplicate_event(cur, eid: str):
        try:
            row = cur.execute("SELECT v FROM kv WHERE k='faults'").fetchone()
            if not row:
                return
            faults = json.loads(row[0])
            f = faults.get("event_duplicate") or {}
            if not f:
                return
            if f.get("mode", "once") == "once":
                faults.pop("event_duplicate", None)
                cur.execute("INSERT OR REPLACE INTO kv(k,v) VALUES(?,?)",
                            ("faults", json.dumps(faults, ensure_ascii=False)))
            dup = cur.execute(
                "SELECT COUNT(*) c FROM events WHERE event_id=?", (eid,)
            ).fetchone()["c"]
            if dup != 1:
                raise RuntimeError("event_id dedup violated")
        except sqlite3.OperationalError:
            pass

    @staticmethod
    def add_outbox(cur, *, semantic_key: str, kind: str, target: str,
                   payload: dict, task_id: str | None = None,
                   next_attempt_at: str | None = None) -> str:
        did = new_id("delivery")
        cur.execute(
            "INSERT INTO outbox(delivery_id,semantic_key,kind,target,task_id,payload,"
            "state,next_attempt_at,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (did, semantic_key, kind, target, task_id,
             json.dumps(payload, ensure_ascii=False), "pending",
             next_attempt_at or format_rfc3339(now_utc()),
             format_rfc3339(now_utc())))
        return did

    def kv_get(self, key: str, default=None):
        row = self.conn.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
        return json.loads(row["v"]) if row else default

    def kv_set(self, key: str, value):
        with self.write() as cur:
            cur.execute("INSERT OR REPLACE INTO kv(k,v) VALUES(?,?)",
                        (key, json.dumps(value, ensure_ascii=False)))

    def audit(self, cur, actor: str, action: str, target: str, reason: str = "",
              request_id: str | None = None, details: dict | None = None):
        cur.execute(
            "INSERT INTO audit_log(audit_id,actor,action,target,request_id,reason,"
            "details,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (new_id("audit"), actor, action, target, request_id, reason,
             json.dumps(details or {}, ensure_ascii=False),
             format_rfc3339(now_utc())))

def open_store(home: str) -> Store:
    db_path = os.path.join(home, "data", "hive.db")
    try:
        return Store(db_path)
    except sqlite3.Error as exc:  # pragma: no cover
        raise HiveError("STORAGE_UNAVAILABLE", f"数据库不可用: {exc}")
