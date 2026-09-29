"""Subjects, credentials and authorization per DESIGN §9.

Credential files contain {"subject": "subj_..", "token": "hvt_.."}; only the
sha256 hash of a token is stored server-side. Tokens never appear in logs,
prompts, config views or reports.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets

from .errors import HiveError
from .ids import new_id, new_token
from .store import Store
from .timeutil import format_rfc3339, now_utc


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class Subject:
    def __init__(self, subject_id: str, subject_type: str, name: str,
                 instance_id: str | None = None):
        self.subject_id = subject_id
        self.subject_type = subject_type  # owner/agent/human/producer/system
        self.name = name
        self.instance_id = instance_id

    @property
    def ref(self) -> str:
        return f"{self.subject_type}:{self.subject_id}"

    def to_dict(self) -> dict:
        return {"subject_id": self.subject_id, "type": self.subject_type,
                "name": self.name,
                **({"instance_id": self.instance_id} if self.instance_id else {})}


SYSTEM_SUBJECT = Subject("subj_system", "system", "system")


def create_subject(cur, subject_type: str, name: str) -> str:
    sid = new_id("subject")
    cur.execute(
        "INSERT INTO subjects(subject_id,subject_type,name,created_at,metadata) "
        "VALUES (?,?,?,?,?)",
        (sid, subject_type, name, format_rfc3339(now_utc()), "{}"))
    return sid


def issue_credential(cur, subject_id: str, *, kind: str = "static",
                     instance_id: str | None = None,
                     out_path: str | None = None) -> tuple[str, str]:
    """Create credential row + protected file. Returns (credential_id, out_path)."""
    token = new_token()
    cred_id = new_id("credential")
    cur.execute(
        "INSERT INTO credentials(credential_id,subject_id,kind,token_hash,instance_id,"
        "created_at) VALUES (?,?,?,?,?,?)",
        (cred_id, subject_id, kind, hash_token(token), instance_id,
         format_rfc3339(now_utc())))
    if out_path is None:
        raise HiveError("INVALID_ARGUMENT", "缺少凭据输出路径")
    out_path = os.path.abspath(out_path)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"subject": subject_id, "token": token}, f)
    try:
        os.chmod(out_path, 0o600)
    except OSError:
        pass
    return cred_id, out_path


def load_credential_file(path: str) -> dict:
    """Read a local credential file (client side)."""
    if not path or not os.path.exists(path):
        raise HiveError("AUTH_REQUIRED", f"凭据文件不存在: {path}")
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        if not data.get("token") or not data.get("subject"):
            raise ValueError
        return data
    except (ValueError, OSError):
        raise HiveError("TOKEN_INVALID", "凭据文件格式不合法")


def revoke_subject_credentials(cur, subject_id: str):
    cur.execute(
        "UPDATE credentials SET revoked_at=? WHERE subject_id=? AND revoked_at IS NULL",
        (format_rfc3339(now_utc()), subject_id))


def revoke_instance_credentials(cur, instance_id: str):
    cur.execute(
        "UPDATE credentials SET revoked_at=? WHERE instance_id=? AND revoked_at IS NULL",
        (format_rfc3339(now_utc()), instance_id))


class Authenticator:
    def __init__(self, store: Store):
        self.store = store

    def authenticate(self, token: str) -> Subject:
        if not token:
            raise HiveError("AUTH_REQUIRED", "缺少凭据")
        row = self.store.conn.execute(
            "SELECT c.credential_id, c.subject_id, c.instance_id, c.revoked_at,"
            " s.subject_type, s.name FROM credentials c "
            "JOIN subjects s ON s.subject_id=c.subject_id WHERE c.token_hash=?",
            (hash_token(token),)).fetchone()
        if not row:
            raise HiveError("TOKEN_INVALID", "凭据无效")
        if row["revoked_at"]:
            raise HiveError("TOKEN_REVOKED", "凭据已撤销")
        if row["instance_id"]:
            inst = self.store.conn.execute(
                "SELECT phase, generation FROM instances WHERE instance_id=?",
                (row["instance_id"],)).fetchone()
            if not inst or inst["phase"] in ("stopped", "failed"):
                raise HiveError("INSTANCE_FENCED", "实例已结束，凭据失效")
        return Subject(row["subject_id"], row["subject_type"], row["name"],
                       row["instance_id"])

    def revoke_instance_credentials(self, cur, instance_id: str):
        cur.execute(
            "UPDATE credentials SET revoked_at=? WHERE instance_id=? AND revoked_at IS NULL",
            (format_rfc3339(now_utc()), instance_id))

    def revoke_subject_credentials(self, cur, subject_id: str):
        cur.execute(
            "UPDATE credentials SET revoked_at=? WHERE subject_id=? AND revoked_at IS NULL",
            (format_rfc3339(now_utc()), subject_id))


# ---------------- authorization ----------------
# Resource forms: "agent:<id>", "port:<id>", "task:<id>", "operation:<id>",
# "*", "own", "created_tasks", "assigned_tasks"

AGENT_BASE_ACTIONS = {
    "agent.read": ["own"],
    "task.read": ["own", "created_tasks", "assigned_tasks"],
    "task.comment": ["readable"],
    "task.wait": ["assigned_tasks"],
    "task.decline": ["readable"],
    "inbox.read": ["own"],
    "task.complete": ["valid_claim"],   # settled via claim validation
    "task.fail": ["valid_claim"],
    "task.trash": ["valid_claim"],
    "task.forward": ["valid_claim"],
    "task.claim": ["personal_work"],
}


def _grant_matches(grant_resources: list[str], action: str, resource: str | None,
                   agent_scope: set[str]) -> bool:
    for res in grant_resources:
        if res == "*":
            return True
        if res in ("own", "created_tasks", "assigned_tasks"):
            if res in agent_scope:
                return True
            continue
        if resource and (res == resource or res.startswith(resource + "/")):
            return True
    return False


class Authorizer:
    def __init__(self, store: Store):
        self.store = store

    def _active_grants(self, subject_id: str) -> list[dict]:
        now = format_rfc3339(now_utc())
        rows = self.store.conn.execute(
            "SELECT actions, resources FROM grants WHERE subject_id=? "
            "AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at>?)",
            (subject_id, now)).fetchall()
        grants = []
        for r in rows:
            try:
                grants.append({"actions": json.loads(r["actions"]),
                               "resources": json.loads(r["resources"])})
            except ValueError:
                continue
        return grants

    def allowed(self, subject: Subject, action: str,
                resource: str | None = None, *, agent_scope: set[str] | None = None,
                task: dict | None = None) -> bool:
        """The permission decision. task is the task dict when action is task.*"""
        if subject.subject_type in ("owner", "system"):
            return True
        scope = agent_scope or set()
        for g in self._active_grants(subject.subject_id):
            if action in g["actions"] or "*" in g["actions"]:
                if _grant_matches(g["resources"], action, resource, scope):
                    return True
        if subject.subject_type == "agent":
            base = AGENT_BASE_ACTIONS.get(action)
            if base:
                return self._agent_base(action, base, subject, resource,
                                        scope, task)
        return False

    def _agent_base(self, action: str, base_modes: list[str], subject: Subject,
                    resource: str | None, scope: set[str],
                    task: dict | None) -> bool:
        agent_ref = f"agent:{subject.subject_id}"
        if action == "agent.read":
            return resource in (None, agent_ref, subject.subject_id)
        if action in ("inbox.read",):
            return True
        if action == "task.read":
            if not task:
                return False
            if task.get("target") == agent_ref:
                return True
            src = task.get("source") or {}
            return (src.get("subject") == subject.ref
                    or task.get("owner_subject") == subject.subject_id)
        if action == "task.comment":
            return self.allowed(subject, "task.read", resource, agent_scope=scope,
                                task=task)
        if action == "task.decline":
            return self.allowed(subject, "task.read", resource, agent_scope=scope,
                                task=task)
        if action == "task.wait":
            return bool(task) and task.get("owner_subject") == subject.subject_id
        if action == "task.claim":
            return bool(task) and task.get("target") == agent_ref
        return False

    def require(self, subject: Subject, action: str, resource: str | None = None,
                **kw):
        if not self.allowed(subject, action, resource, **kw):
            raise HiveError("FORBIDDEN", f"缺少权限 {action}"
                            + (f" 于 {resource}" if resource else ""),
                            {"action": action, "resource": resource})


def can_grant(actor: Subject, store: Store, actions: list[str],
              resources: list[str], delegable: bool) -> bool:
    """Grant scope check: caller must hold delegable grants covering the request."""
    if actor.subject_type in ("owner", "system"):
        return True
    rows = store.conn.execute(
        "SELECT actions, resources, delegable FROM grants WHERE subject_id=? "
        "AND revoked_at IS NULL AND delegable=1", (actor.subject_id,)).fetchall()
    have_actions: set[str] = set()
    have_resources: set[str] = set()
    for r in rows:
        have_actions.update(json.loads(r["actions"]))
        have_resources.update(json.loads(r["resources"]))
    if "*" in have_actions:
        return True
    if not set(actions) <= have_actions:
        return False
    for res in resources:
        if res in ("own", "created_tasks", "assigned_tasks"):
            if res not in have_resources and "*" not in have_resources:
                return False
        elif res != "*" and res not in have_resources and "*" not in have_resources:
            return False
    return True
