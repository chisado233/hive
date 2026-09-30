"""Agent lifecycle, config versioning, workspace files per DESIGN §3."""
from __future__ import annotations

import json
import os
import re
import shutil

from .errors import HiveError
from .ids import new_id
from .store import workspace_key
from .timeutil import format_rfc3339, now_utc

CONFIG_TOP = {"schema_version", "name", "description", "instructions", "skills",
              "runtime", "workspace", "subscriptions", "permissions", "limits",
              "metadata"}


def default_config(name: str | None = None) -> dict:
    return {
        "schema_version": 1,
        "name": name or f"agent-{new_id('agent')[4:12]}",
        "description": "",
        "instructions": {"text": "", "files": []},
        "skills": [],
        "runtime": {"harness": None, "model": None, "yolo": True,
                    "auto_start": True, "restart_on_exit": True, "options": {}},
        "workspace": {"path": None, "context_glob": "*.md",
                      "exclude": ["agent.md", "prompt.md"], "context_entries": []},
        "subscriptions": [],
        "permissions": [],
        "limits": {"max_inflight_work": 3},
        "metadata": {},
    }


def validate_config(cfg: dict, *, partial: bool = False) -> None:
    if not isinstance(cfg, dict):
        raise HiveError("INVALID_ARGUMENT", "Agent配置必须是对象")
    unknown = set(cfg) - CONFIG_TOP
    if unknown:
        raise HiveError("SCHEMA_UNSUPPORTED", f"Agent配置未知字段: {sorted(unknown)}")
    if "schema_version" in cfg and cfg["schema_version"] != 1:
        raise HiveError("SCHEMA_UNSUPPORTED",
                        f"不支持的主版本 schema_version={cfg['schema_version']}")
    if "name" in cfg:
        n = cfg["name"]
        if not isinstance(n, str) or not (1 <= len(n) <= 128):
            raise HiveError("INVALID_ARGUMENT", "name 必须 1..128 字符")
    if "description" in cfg and len(str(cfg.get("description") or "")) > 4096:
        raise HiveError("INVALID_ARGUMENT", "description ≤4096")
    if "limits" in cfg:
        m = cfg["limits"].get("max_inflight_work", 3)
        if not isinstance(m, int) or not (1 <= m <= 1000):
            raise HiveError("INVALID_ARGUMENT", "max_inflight_work 须为 1..1000")
    if "runtime" in cfg:
        rt = cfg["runtime"]
        if not isinstance(rt, dict):
            raise HiveError("INVALID_ARGUMENT", "runtime 必须是对象")
        allowed = {"harness", "model", "yolo", "auto_start", "restart_on_exit",
                   "options"}
        if set(rt) - allowed:
            raise HiveError("SCHEMA_UNSUPPORTED",
                            f"runtime 未知字段: {sorted(set(rt) - allowed)}")
    if "workspace" in cfg:
        ws = cfg["workspace"]
        if not isinstance(ws, dict):
            raise HiveError("INVALID_ARGUMENT", "workspace 必须是对象")
        allowed = {"path", "context_glob", "exclude", "context_entries"}
        if set(ws) - allowed:
            raise HiveError("SCHEMA_UNSUPPORTED",
                            f"workspace 未知字段: {sorted(set(ws) - allowed)}")


def _merge(base: dict, patch: dict) -> dict:
    """Merge patch: objects recurse, arrays replace, null removes key."""
    out = dict(base)
    for k, v in (patch or {}).items():
        if v is None:
            out.pop(k, None)
            continue
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def get_agent(svc, agent_id: str):
    r = svc.store.conn.execute("SELECT * FROM agents WHERE agent_id=?",
                               (agent_id,)).fetchone()
    if not r:
        raise HiveError("NOT_FOUND", f"Agent不存在: {agent_id}")
    return r


def get_agent_config(svc, agent_id: str, revision: int | None = None) -> dict:
    row = svc.store.conn.execute(
        "SELECT file_path FROM agent_config_versions WHERE agent_id=? AND published=1 "
        "ORDER BY revision DESC LIMIT 1" if revision is None else
        "SELECT file_path FROM agent_config_versions WHERE agent_id=? AND revision=? "
        "AND published=1", (agent_id,) if revision is None else (agent_id, revision)
    ).fetchone()
    if not row:
        return default_config()
    with open(row["file_path"], "r", encoding="utf-8") as f:
        return json.load(f)


def get_agent_subject(svc, agent_id: str) -> str | None:
    """Agents share subject_id with agent_id for simplicity of grants."""
    r = svc.store.conn.execute(
        "SELECT subject_id FROM subjects WHERE subject_type='agent' AND name=?",
        (agent_id,)).fetchone()
    if r:
        return r["subject_id"]
    r = svc.store.conn.execute(
        "SELECT subject_id FROM subjects WHERE subject_id=?", (agent_id,)).fetchone()
    return r["subject_id"] if r else None


def _workspace_root(svc, agent_id: str) -> str:
    """Default workspace location; NEVER used when cfg sets workspace.path."""
    return os.path.join(svc.home, "workspaces", agent_id)


def resolve_workspace(svc, agent_id: str, cfg: dict) -> str:
    """Single source of truth for the effective workspace (DEF-01):
    config workspace.path (already absolute at create/update) else default."""
    path = (cfg.get("workspace") or {}).get("path")
    if path:
        return path
    return _workspace_root(svc, agent_id)


def _workspace_in_use(svc, agent_id: str, path: str) -> str | None:
    """Another agent bound to the same physical workspace? (RDEF-07:
    comparison uses the normalized workspace_key — case, trailing
    separators, .., junction/short-name aliases all resolve to one key —
    plus the DB unique key index as the concurrency backstop.)"""
    from .store import workspace_key
    key = workspace_key(path)
    if not key:
        return None
    for row in svc.store.conn.execute(
            "SELECT agent_id, workspace_path, workspace_key FROM agents "
            "WHERE agent_id<>?", (agent_id,)):
        other_key = row["workspace_key"] or workspace_key(
            row["workspace_path"])
        if other_key and other_key == key:
            return row["agent_id"]
    return None


def _workspace_conflict_error(clash: str, path: str) -> HiveError:
    return HiveError("INVALID_ARGUMENT",
                     f"工位已被 agent {clash} 使用（同一物理目录，含别名形式），"
                     "不能共享覆盖系统派生文件",
                     {"workspace": path, "bound_to": clash})


def _publish_config(svc, cur, agent_id: str, cfg: dict, base_revision: int,
                    actor_ref: str, reason: str, rid=None,
                    bump_revision=True) -> int:
    """Write immutable version file then publish pointer + projections."""
    revision = base_revision + 1 if bump_revision else base_revision
    versions_dir = os.path.join(svc.home, "definitions", "agents", agent_id,
                                "versions")
    os.makedirs(versions_dir, exist_ok=True)
    file_path = os.path.join(versions_dir, f"{revision}.json")
    tmp = file_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, file_path)
    cur.execute("INSERT OR REPLACE INTO agent_config_versions(agent_id,revision,"
                "file_path,published,created_at,published_at) VALUES (?,?,?,1,?,?)",
                (agent_id, revision, file_path, format_rfc3339(now_utc()),
                 format_rfc3339(now_utc())))
    import sqlite3 as _sq
    try:
        cur.execute("UPDATE agents SET current_revision=?, desired_revision=?, "
                    "name=COALESCE(?,name), workspace_path=COALESCE(?,workspace_path), "
                    "workspace_key=COALESCE(?,workspace_key), updated_at=? "
                    "WHERE agent_id=?",
                    (revision, revision, cfg.get("name"),
                     (cfg.get("workspace") or {}).get("path"),
                     workspace_key((cfg.get("workspace") or {})
                                   .get("path")) if (cfg.get("workspace")
                                                     or {}).get("path") else None,
                     format_rfc3339(now_utc()), agent_id))
    except _sq.IntegrityError:
        # concurrent bind of the same physical directory (unique index) —
        # the whole update rolls back, the original agent's files untouched
        raise _workspace_conflict_error(
            "concurrent-bind", (cfg.get("workspace") or {}).get("path") or "")
    # subscription projection: enabled flags from config.subscriptions
    cur.execute("DELETE FROM subscriptions WHERE agent_id=?", (agent_id,))
    for sub in cfg.get("subscriptions", []):
        pid = sub.get("port_id") or sub.get("port")
        if not pid:
            continue
        prow = cur.execute("SELECT port_id, handler FROM ports WHERE port_id=? OR "
                           "route=?", (pid, str(pid).lstrip("port:"))).fetchone()
        if not prow or prow["handler"] != "agents":
            continue
        cur.execute("INSERT OR REPLACE INTO subscriptions(agent_id,port_id,enabled,"
                    "created_at,created_by) VALUES (?,?,?,?,?)",
                    (agent_id, prow["port_id"],
                     1 if sub.get("enabled", True) else 0,
                     format_rfc3339(now_utc()), actor_ref))
    # permissions projection: replace config-derived grants
    cur.execute("DELETE FROM grants WHERE created_by LIKE ?",
                (f"config:agent:{agent_id}:%",))
    for perm in cfg.get("permissions", []):
        cur.execute(
            "INSERT INTO grants(grant_id,subject_id,actions,resources,delegable,"
            "created_by,created_at) VALUES (?,?,?,?,0,?,?)",
            (new_id("grant"), agent_id, json.dumps(perm.get("actions", [])),
             json.dumps(perm.get("resources", [])),
             f"config:agent:{agent_id}:{revision}",
             format_rfc3339(now_utc())))
    svc.store.add_event(cur, entity_type="agent", entity_id=agent_id,
                        entity_version=revision, kind="agent.config.published",
                        actor=actor_ref, payload={"revision": revision})
    svc.store.audit(cur, actor_ref, "agent.update", f"agent:{agent_id}", reason, rid,
                    {"revision": revision})
    if svc.consume_fault("config_after_publish"):
        # derived-file generation failed AFTER publish: remove stale copies so a
        # later ensure_workspace rebuilds from the effective revision (AC-ID-04)
        ws = cfg.get("workspace", {}).get("path")
        if ws and os.path.isdir(ws):
            for name in ("config.json", "prompt.md"):
                try:
                    os.remove(os.path.join(ws, name))
                except OSError:
                    pass
        return revision
    _rebuild_workspace(svc, agent_id, cfg, revision)
    return revision


def _rebuild_workspace(svc, agent_id: str, cfg: dict, revision: int):
    ws = resolve_workspace(svc, agent_id, cfg)
    os.makedirs(os.path.join(ws, "history"), exist_ok=True)
    # system protocol
    from .contextgen import render_agent_md, render_prompt_md
    with open(os.path.join(ws, "agent.md"), "w", encoding="utf-8") as f:
        f.write(render_agent_md(svc))
    cfg_view = {k: v for k, v in cfg.items() if k != "permissions"}
    with open(os.path.join(ws, "config.json"), "w", encoding="utf-8") as f:
        json.dump({"revision": revision, "config": cfg_view}, f, ensure_ascii=False,
                  indent=2)
    with open(os.path.join(ws, "prompt.md"), "w", encoding="utf-8") as f:
        f.write(render_prompt_md(svc, agent_id, cfg))


def create_agent(svc, subject, args, rid) -> dict:
    svc.authz.require(subject, "agent.create")
    cfg_in = dict(args.get("input") or {})
    validate_config(cfg_in, partial=True)
    cfg = default_config(cfg_in.get("name"))
    cfg = _merge(cfg, cfg_in)
    for key, opt in (("name", "name"), ("harness", None), ("model", None)):
        pass
    if args.get("name"):
        cfg["name"] = args["name"]
    if args.get("harness"):
        cfg["runtime"]["harness"] = args["harness"]
    if args.get("model"):
        cfg["runtime"]["model"] = args["model"]
    if args.get("workspace"):
        cfg["workspace"]["path"] = args["workspace"]
    if args.get("auto_start") is not None:
        cfg["runtime"]["auto_start"] = bool(args["auto_start"])
    agent_id = new_id("agent")
    ws_path = cfg["workspace"]["path"] or _workspace_root(svc, agent_id)
    cfg["workspace"]["path"] = os.path.abspath(os.path.normpath(ws_path))
    clash = _workspace_in_use(svc, agent_id, cfg["workspace"]["path"])
    if clash:
        raise _workspace_conflict_error(clash, cfg["workspace"]["path"])
    now = format_rfc3339(now_utc())
    import sqlite3 as _sq
    with svc.store.write() as cur:
        try:
            cur.execute("INSERT INTO agents(agent_id,name,lifecycle,"
                        "current_revision,desired_revision,applied_revision,"
                        "workspace_path,workspace_key,created_at,updated_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (agent_id, cfg["name"], "enabled", 0, 0, 0,
                         cfg["workspace"]["path"],
                         workspace_key(cfg["workspace"]["path"]), now, now))
        except _sq.IntegrityError:
            # concurrent bind of the same physical directory lost the race
            # (unique workspace_key index) — no derived file was touched
            raise _workspace_conflict_error("concurrent-bind",
                                            cfg["workspace"]["path"])
        from .auth import create_subject
        cur.execute("INSERT INTO subjects(subject_id,subject_type,name,created_at,"
                    "metadata) VALUES (?,?,?,?,?)",
                    (agent_id, "agent", agent_id, now,
                     json.dumps({"agent_id": agent_id})))
        revision = _publish_config(svc, cur, agent_id, cfg, 0, subject.ref,
                                   args.get("reason") or "create agent", rid)
    data = {"agent": {"agent_id": agent_id, "name": cfg["name"],
                      "lifecycle": "enabled", "workspace": cfg["workspace"]["path"]},
            "config_revision": revision,
            "personal_target": f"agent:{agent_id}"}
    if args.get("start"):
        from .runtime_ops import op_start_agent
        data["start_operation"] = op_start_agent(svc, subject, agent_id, None)
    return data


def clone_agent(svc, subject, args, rid) -> dict:
    src = get_agent(svc, args["agent"])
    svc.authz.require(subject, "agent.read", f"agent:{src['agent_id']}")
    svc.authz.require(subject, "agent.create")
    src_cfg = get_agent_config(svc, src["agent_id"])
    # permission anti-smuggling: caller must be able to grant src permissions
    from .auth import can_grant
    for perm in src_cfg.get("permissions", []):
        if not can_grant(subject, svc.store, perm.get("actions", []),
                         perm.get("resources", []), False):
            raise HiveError("GRANT_SCOPE_EXCEEDED",
                            "源Agent权限超出调用者可授予范围，拒绝偷渡")
    cfg = _merge(default_config(), src_cfg)
    cfg["name"] = args.get("name") or (src["name"] + "-copy")
    explicit_ws = None
    if args.get("workspace"):
        # RDEF-08: an EXPLICITLY requested workspace is validated like
        # create — conflicts are rejected, never silently re-defaulted
        explicit_ws = os.path.abspath(os.path.normpath(args["workspace"]))
        cfg["workspace"]["path"] = explicit_ws
    agent_id = new_id("agent")
    if explicit_ws:
        clash = _workspace_in_use(svc, agent_id, explicit_ws)
        if clash:
            raise _workspace_conflict_error(clash, explicit_ws)
    else:
        # inherited/absent: a clone NEVER shares the source's physical
        # workspace — an inherited clash (the normal case, since configs
        # store concrete paths) allocates the copy's own default workspace
        inherited = cfg["workspace"]["path"] or _workspace_root(svc, agent_id)
        clash = _workspace_in_use(svc, agent_id, inherited)
        cfg["workspace"]["path"] = (_workspace_root(svc, agent_id)
                                    if clash else inherited)
    now = format_rfc3339(now_utc())
    import sqlite3 as _sq
    with svc.store.write() as cur:
        try:
            cur.execute("INSERT INTO agents(agent_id,name,lifecycle,"
                        "current_revision,desired_revision,applied_revision,"
                        "workspace_path,workspace_key,created_at,updated_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (agent_id, cfg["name"], "enabled", 0, 0, 0,
                         cfg["workspace"]["path"],
                         workspace_key(cfg["workspace"]["path"]), now, now))
        except _sq.IntegrityError:
            raise _workspace_conflict_error("concurrent-bind",
                                            cfg["workspace"]["path"])
        cur.execute("INSERT INTO subjects(subject_id,subject_type,name,created_at,"
                    "metadata) VALUES (?,?,?,?,?)",
                    (agent_id, "agent", agent_id, now,
                     json.dumps({"agent_id": agent_id})))
        revision = _publish_config(svc, cur, agent_id, cfg, 0, subject.ref,
                                   args.get("reason") or "clone agent", rid)
    data = {"new_agent": {"agent_id": agent_id, "name": cfg["name"]},
            "source_agent_id": src["agent_id"], "config_revision": revision}
    if args.get("start"):
        from .runtime_ops import op_start_agent
        data["start_operation"] = op_start_agent(svc, subject, agent_id, None)
    return data


def update_agent(svc, subject, args, rid) -> dict:
    row = get_agent(svc, args["agent"])
    svc.authz.require(subject, "agent.update", f"agent:{row['agent_id']}")
    current = get_agent_config(svc, row["agent_id"], row["current_revision"])
    patch = args.get("input")
    if not isinstance(patch, dict):
        raise HiveError("INVALID_ARGUMENT", "配置补丁必须是对象")
    if args.get("if_version") is not None and \
            args["if_version"] != row["current_revision"]:
        raise HiveError("VERSION_CONFLICT",
                        f"配置版本冲突：当前 {row['current_revision']}")
    if args.get("replace"):
        validate_config(patch)
        new_cfg = _merge(default_config(patch.get("name")), patch)
    else:
        merged = _merge(current, patch)
        validate_config(merged)
        new_cfg = merged
    if "agent_id" in patch:
        raise HiveError("INVALID_ARGUMENT", "agent_id 不可指定")
    new_ws = ((new_cfg.get("workspace") or {}).get("path"))
    cur_ws = ((current.get("workspace") or {}).get("path"))
    if new_ws and (not cur_ws or workspace_key(new_ws) != workspace_key(cur_ws)):
        clash = _workspace_in_use(svc, row["agent_id"], new_ws)
        if clash:
            raise _workspace_conflict_error(clash, new_ws)
    # permissions changes need grant scope
    if patch.get("permissions"):
        from .auth import can_grant
        for perm in new_cfg.get("permissions", []):
            if not can_grant(subject, svc.store, perm.get("actions", []),
                             perm.get("resources", []), False):
                raise HiveError("GRANT_SCOPE_EXCEEDED",
                                "权限修改超出可授予范围")
    with svc.store.write() as cur:
        revision = _publish_config(svc, cur, row["agent_id"], new_cfg,
                                   row["current_revision"], subject.ref,
                                   args["reason"], rid)
    pending_fields = []
    for k in patch:
        if k in ("instructions", "skills", "workspace"):
            pending_fields.append(k)
    data = {"desired_revision": revision, "applied_revision": revision,
            "pending_fields": pending_fields}
    if args.get("apply") == "restart":
        from .runtime_ops import op_restart_agent
        op = op_restart_agent(svc, subject, row["agent_id"], args.get("reason"))
        data["operation_id"] = op["operation_id"]
    return data


def agent_card(row) -> dict:
    return {"agent_id": row["agent_id"], "name": row["name"],
            "lifecycle": row["lifecycle"],
            "workspace": row["workspace_path"],
            "created_at": row["created_at"]}


def agent_runtime_summary(svc, agent_id: str) -> dict:
    row = get_agent(svc, agent_id)
    instances = svc.store.conn.execute(
        "SELECT * FROM instances WHERE agent_id=? ORDER BY started_at DESC LIMIT 10",
        (agent_id,)).fetchall()
    claimed = svc.store.conn.execute(
        "SELECT task_id, state, type, title FROM tasks WHERE owner_subject=? AND "
        "state='claimed' ORDER BY created_at", (agent_id,)).fetchall()
    personal = svc.store.conn.execute(
        "SELECT COUNT(*) c FROM tasks WHERE target=? AND state='queued'",
        (f"agent:{agent_id}",)).fetchone()["c"]
    waits = svc.store.conn.execute(
        "SELECT COUNT(*) c FROM waits w JOIN tasks t ON t.task_id=w.owner_task_id "
        "WHERE t.owner_subject=? AND w.state='pending'", (agent_id,)).fetchone()["c"]
    live = [i for i in instances if i["phase"] in ("starting", "live", "stopping")]
    # RDEF-03: the crash-restart plan state is queryable per agent
    restart_plan = svc.store.kv_get(f"restart:{agent_id}", {}) or {}
    return {
        "lifecycle": row["lifecycle"],
        "restart_plan": restart_plan or None,
        "instances": [{"instance_id": i["instance_id"], "phase": i["phase"],
                       "observation": i["observation"],
                       "generation": i["generation"],
                       "started_at": i["started_at"], "ended_at": i["ended_at"],
                       "error": i["error"]} for i in instances],
        "active_instance": {"instance_id": live[0]["instance_id"],
                            "phase": live[0]["phase"],
                            "observation": live[0]["observation"]}
        if live else None,
        "responsibility": {
            "claimed_tasks": [{"task_id": c["task_id"], "type": c["type"],
                               "title": c["title"]} for c in claimed],
            "claimed_count": len(claimed),
            "personal_queued": personal,
            "pending_waits": waits,
        },
        "desired_revision": row["desired_revision"],
        "applied_revision": row["applied_revision"],
        "pending_fields": json.loads(row["pending_fields"]),
    }


def build_context_summary(svc, agent_id: str) -> str:
    """Realtime responsibility summary injected at instance start."""
    rt = agent_runtime_summary(svc, agent_id)
    lines = [f"# 当前责任摘要（账本实时生成 {svc.now()}）",
             f"- 生命周期: {rt['lifecycle']}",
             f"- 在途work: {rt['responsibility']['claimed_count']} 件"]
    for t in rt["responsibility"]["claimed_tasks"]:
        lines.append(f"  - {t['task_id']} [{t['type']}] {t['title']}")
    lines.append(f"- 个人队列待处理: {rt['responsibility']['personal_queued']}")
    lines.append(f"- pending等待: {rt['responsibility']['pending_waits']}")
    pending = svc.store.conn.execute(
        "SELECT w.wait_id, w.owner_task_id, w.mode FROM waits w WHERE w.state="
        "'pending' AND w.owner_task_id IN (SELECT task_id FROM tasks WHERE "
        "owner_subject=?)", (agent_id,)).fetchall()
    for w in pending:
        lines.append(f"  - 等待 {w['wait_id']} 归属 {w['owner_task_id']} mode={w['mode']}")
    ready = svc.store.conn.execute(
        "SELECT w.wait_id, w.state FROM waits w WHERE w.state IN ('ready',"
        "'timed_out') AND w.owner_task_id IN (SELECT task_id FROM tasks WHERE "
        "owner_subject=?) AND w.resolved_at > datetime('now','-1 day')",
        (agent_id,)).fetchall()
    for w in ready:
        lines.append(f"- 等待结果待查看: {w['wait_id']} -> {w['state']}")
    return "\n".join(lines)
