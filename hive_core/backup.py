"""Consistent backup + offline restore per DESIGN §5.7/§10/§19.1."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone

from . import SCHEMA_VERSION
from .errors import HiveError
from .timeutil import format_rfc3339, now_utc


def create_backup(svc, *, out: str, include_workspaces: bool,
                  include_credentials: bool, actor: str, reason: str,
                  request_id=None) -> dict:
    out = os.path.abspath(out)
    if os.path.exists(out) and os.listdir(out):
        raise HiveError("INVALID_ARGUMENT", f"备份目标已存在且非空: {out}")
    os.makedirs(out, exist_ok=True)
    db_out = os.path.join(out, "hive.db")
    # SQLite backup API for a consistent snapshot while running
    src = sqlite3.connect(os.path.join(svc.home, "data", "hive.db"))
    dst = sqlite3.connect(db_out)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    files = ["hive.db"]
    # effective config versions
    defs_out = os.path.join(out, "definitions")
    os.makedirs(defs_out, exist_ok=True)
    for table, kind in (("agent_config_versions", "agents"),
                        ("port_config_versions", "ports")):
        try:
            rows = svc.store.conn.execute(
                f"SELECT a.{kind[:-1] + '_id' if kind == 'agents' else 'port_id'} "
                f"AS oid, file_path FROM {table} WHERE published=1").fetchall()
        except sqlite3.Error:
            rows = []
        for r in rows:
            dest = os.path.join(defs_out, kind, r["oid"],
                                os.path.basename(r["file_path"]))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copyfile(r["file_path"], dest)
            files.append(os.path.relpath(dest, out))
    # workspaces (optional)
    ws_manifest = []
    if include_workspaces:
        ws_root = os.path.join(svc.home, "workspaces")
        if os.path.isdir(ws_root):
            for name in os.listdir(ws_root):
                src_dir = os.path.join(ws_root, name)
                if os.path.isdir(src_dir):
                    shutil.copytree(src_dir, os.path.join(out, "workspaces", name),
                                    dirs_exist_ok=True)
                    ws_manifest.append(name)
    creds_note = "excluded"
    if include_credentials:
        auth_src = os.path.join(svc.home, "auth")
        if os.path.isdir(auth_src):
            shutil.copytree(auth_src, os.path.join(out, "auth"),
                            dirs_exist_ok=True)
            creds_note = "included"
    manifest = {
        "backup_id": f"bkp_{format_rfc3339(now_utc()).replace(':', '').replace('-', '').replace('T', 'T')}",
        "created_at": format_rfc3339(now_utc()),
        "schema_version": SCHEMA_VERSION,
        "include_workspaces": include_workspaces,
        "credentials": creds_note,
        "workspaces": ws_manifest,
        "files": files,
    }
    # hash manifest + db
    sha = hashlib.sha256()
    with open(db_out, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha.update(chunk)
    manifest["db_sha256"] = sha.hexdigest()
    manifest_path = os.path.join(out, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    with svc.store.write() as cur:
        svc.store.audit(cur, actor, "system.backup", out, reason or "backup",
                        request_id, {"credentials": creds_note})
    return {"backup_id": manifest["backup_id"], "manifest_path": manifest_path,
            "files": files, "warnings": [] if creds_note == "excluded" else
            ["备份包含凭据，必须按秘密处理"]}


def restore_backup(from_dir: str, into: str, *, with_credentials: bool,
                   home_arg: str | None = None) -> dict:
    from_dir = os.path.abspath(from_dir)
    into = os.path.abspath(into)
    manifest_path = os.path.join(from_dir, "manifest.json")
    if not os.path.isfile(manifest_path):
        raise HiveError("NOT_FOUND", f"备份manifest不存在: {manifest_path}")
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    if os.path.exists(into) and os.listdir(into):
        raise HiveError("INVALID_ARGUMENT", f"恢复目标必须为空目录: {into}")
    db_src = os.path.join(from_dir, "hive.db")
    sha = hashlib.sha256()
    with open(db_src, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha.update(chunk)
    if sha.hexdigest() != manifest.get("db_sha256"):
        raise HiveError("INVALID_ARGUMENT", "数据库hash校验失败")
    os.makedirs(os.path.join(into, "data"), exist_ok=True)
    shutil.copyfile(db_src, os.path.join(into, "data", "hive.db"))
    if os.path.isdir(os.path.join(from_dir, "definitions")):
        shutil.copytree(os.path.join(from_dir, "definitions"),
                        os.path.join(into, "definitions"), dirs_exist_ok=True)
    if os.path.isdir(os.path.join(from_dir, "workspaces")):
        shutil.copytree(os.path.join(from_dir, "workspaces"),
                        os.path.join(into, "workspaces"), dirs_exist_ok=True)
    # write system.json marked recovery_hold
    import tempfile
    sysjson = {
        "initialized": True, "schema_version": manifest["schema_version"],
        "listen": "127.0.0.1:19962", "test_mode": False,
        "recovery_hold": True, "restored_from": from_dir,
        "created_at": format_rfc3339(now_utc()),
    }
    tmp = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=into,
                                      prefix=".system-", delete=False)
    json.dump(sysjson, tmp, ensure_ascii=False, indent=2)
    tmp.close()
    os.replace(tmp.name, os.path.join(into, "system.json"))
    for sub in ("auth", "runtime", "logs", "backups"):
        os.makedirs(os.path.join(into, sub), exist_ok=True)
    # post-restore ledger adjustments: pause all agents, freeze outbox,
    # revoke old credential verification (keep rows but mark revoked)
    db = sqlite3.connect(os.path.join(into, "data", "hive.db"))
    with db:
        db.execute("UPDATE agents SET lifecycle='paused'")
        db.execute("UPDATE outbox SET state='pending', next_attempt_at=NULL "
                   "WHERE state IN ('sending','retry')")
        db.execute("UPDATE credentials SET revoked_at=? WHERE revoked_at IS NULL",
                   (format_rfc3339(now_utc()),))
        db.execute("INSERT OR REPLACE INTO kv(k,v) VALUES('recovery_hold', 'true')")
        db.execute("INSERT OR REPLACE INTO kv(k,v) VALUES('clock_offset', '0')")
    db.close()
    # new owner credential for the restored home
    from .local import _init_owner
    _init_owner(into, None)
    required_actions = [
        "所有Agent已paused，需要逐个显式 control agent start",
        "recovery_hold=true：自动trigger/outbox投递/Agent启动已冻结；"
        "核对后用 control system config 提交 {\"recovery_hold\": false} 解除",
        "旧凭据已全部撤销；实例凭据在下次start时重新发行",
    ]
    if with_credentials:
        if manifest.get("credentials") != "included":
            required_actions.append("备份未包含凭据，无法恢复旧凭据")
        else:
            required_actions.append("已请求恢复旧凭据——跨环境复用风险自担")
    report = {"restored_home": into, "agents_paused": True,
              "outbox_frozen": True, "credentials_revoked": True}
    return {"restored_home": into, "recovery_report": report,
            "required_actions": required_actions}
