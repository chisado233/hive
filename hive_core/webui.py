"""Hive Web UI adapter ("蜂群指挥台" backend).

Mounted under /ui by server.py. Same-origin static hosting plus a narrow JSON
API that reuses Service.dispatch / domain handlers for every business effect —
this module adds no competing source of truth for tasks, agents, approvals or
scores. What it adds is exactly what a browser client cannot do with the V1
command gateway alone:

- cookie session auth (tokens never touch JS storage or URLs),
- aggregated read models for the star map (one poll, per-section permission
  degradation instead of all-or-nothing FORBIDDEN),
- upload + authorized file preview/download with HTTP Range (video),
- project-asset browsing strictly contained to registered project roots,
- UI-schema field validation for human answers (required/range/step/choice),
  re-checked server-side on every submit.
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import queue
import re
import secrets
import urllib.parse

from .auth import Subject
from .errors import HiveError
from .ids import new_id
from .timeutil import format_rfc3339, now_utc

WEB_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), os.pardir,
                                         "hive_web"))
SESSION_COOKIE = "hive_ui"
SESSION_TTL_SECONDS = 7 * 24 * 3600
DEFAULT_UPLOAD_CAP = 512 * 1024 * 1024
OVERVIEW_FLOW_TERMINAL_WINDOW_S = 900   # keep recently-terminal tasks visible
OVERVIEW_EVENT_LIMIT = 200

# Commands the console is allowed to invoke through /ui/api/command.
# Reads + the mutations the UI actually exposes. control test * and system
# init/serve/start/stop/restore are deliberately absent.
UI_COMMAND_WHITELIST = {
    "inbox.list", "inbox.peek", "inbox.tree",
    "inbox.wait.list", "inbox.wait.show",
    "emit.new", "emit.comment", "emit.forward",
    "control.system.status",
    "control.agent.list", "control.agent.show", "control.agent.start",
    "control.agent.wake", "control.agent.send", "control.agent.stop",
    "control.agent.restart", "control.agent.model",
    "control.port.list", "control.port.show",
    "control.subscription.list",
    "control.task.list", "control.task.show", "control.task.reclaim",
    "control.task.forward", "control.task.trash",
    "control.operation.list", "control.operation.show",
    "control.event.list", "control.delivery.list",
    "control.harness.list", "control.harness.show",
    "control.human.list", "control.human.answer", "control.human.fail",
    "control.human.trash",
    "control.permission.list",
}

VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}
AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".m4a", ".flac"}
TEXT_EXTS = {".md", ".txt", ".json", ".py", ".js", ".css", ".html", ".srt",
             ".vtt", ".csv", ".log", ".yaml", ".yml", ".toml"}


# ---------------------------------------------------------------- sessions
def _sessions(svc) -> dict:
    return svc.store.kv_get("ui_sessions", {}) or {}


def _save_sessions(svc, sessions: dict):
    svc.store.kv_set("ui_sessions", sessions)


def create_session(svc, token: str) -> tuple[str, Subject]:
    subject = svc.authn.authenticate(token)          # raises on bad token
    if subject.subject_type not in ("owner", "human", "system"):
        raise HiveError("FORBIDDEN",
                        "指挥台仅面向人类/管理者身份；Agent 请使用 CLI 凭据")
    sid = secrets.token_urlsafe(32)
    sessions = _sessions(svc)
    now = format_rfc3339(now_utc())
    # drop expired sessions while we are here
    sessions = {k: v for k, v in sessions.items()
                if (v.get("expires_at") or "") > now}
    sessions[hashlib.sha256(sid.encode()).hexdigest()] = {
        "subject_id": subject.subject_id,
        "subject_type": subject.subject_type,
        "name": subject.name,
        "created_at": now,
        "expires_at": format_rfc3339(_ts_plus(SESSION_TTL_SECONDS)),
    }
    _save_sessions(svc, sessions)
    return sid, subject


def _ts_plus(seconds: float):
    import datetime as _dt
    return now_utc() + _dt.timedelta(seconds=seconds)


def resolve_session(svc, cookie_header: str | None) -> Subject | None:
    if not cookie_header:
        return None
    sid = None
    for part in cookie_header.split(";"):
        k, _, v = part.strip().partition("=")
        if k == SESSION_COOKIE:
            sid = v
            break
    if not sid:
        return None
    rec = _sessions(svc).get(hashlib.sha256(sid.encode()).hexdigest())
    if not rec:
        return None
    if (rec.get("expires_at") or "") <= format_rfc3339(now_utc()):
        return None
    row = svc.store.conn.execute(
        "SELECT subject_id, subject_type, name FROM subjects WHERE subject_id=?",
        (rec["subject_id"],)).fetchone()
    if not row:
        return None
    return Subject(row["subject_id"], row["subject_type"], row["name"])


def destroy_session(svc, cookie_header: str | None):
    if not cookie_header:
        return
    for part in cookie_header.split(";"):
        k, _, v = part.strip().partition("=")
        if k == SESSION_COOKIE and v:
            sessions = _sessions(svc)
            sessions.pop(hashlib.sha256(v.encode()).hexdigest(), None)
            _save_sessions(svc, sessions)


# ---------------------------------------------------------------- projects
def normalize_path(p: str | None) -> str:
    if not p:
        return ""
    p = str(p).replace("/", "\\").strip()
    p = os.path.normpath(p)
    return p.lower().rstrip("\\")


def _agent_cfg(svc, agent_id: str) -> dict:
    from .agents import get_agent_config
    try:
        return get_agent_config(svc, agent_id)
    except HiveError:
        return {}


def project_registry(svc) -> dict:
    """Derive projects from agents' runtime execution_root (normalized).

    Key: metadata.project when every agent in the group agrees on one,
    else the normalized root path. Agents whose root cannot be determined
    land in the implicit "unassigned" group — we never guess paths.
    """
    rows = svc.store.conn.execute(
        "SELECT agent_id, name, lifecycle FROM agents "
        "WHERE lifecycle != 'retired'").fetchall()
    groups: dict[str, dict] = {}
    for r in rows:
        cfg = _agent_cfg(svc, r["agent_id"])
        meta = cfg.get("metadata") or {}
        root = (cfg.get("runtime") or {}).get("options", {}).get(
            "execution_root") or ""
        nroot = normalize_path(root)
        if not nroot:
            continue
        g = groups.setdefault(nroot, {
            "root": os.path.normpath(str(root).replace("/", "\\")),
            "names": set(), "agents": []})
        if meta.get("project"):
            g["names"].add(str(meta["project"]))
        g["agents"].append(r["agent_id"])
    projects = {}
    for nroot, g in groups.items():
        key = sorted(g["names"])[0] if g["names"] else nroot
        info = {"key": key, "root": g["root"], "agents": g["agents"],
                "has_project_dir": os.path.isdir(
                    os.path.join(g["root"], "project")),
                "assets_dir": os.path.join(g["root"], "assets")
                if os.path.isdir(os.path.join(g["root"], "assets")) else None}
        pj = os.path.join(g["root"], "project")
        for fname, label in (("active.json", "active"),
                             ("authorizations.json", "authorizations")):
            fpath = os.path.join(pj, fname)
            if os.path.isfile(fpath):
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        info[label] = json.load(f)
                except (ValueError, OSError):
                    info[label] = None
        projects[key] = info
    return projects


def _allowed_roots(svc) -> list[str]:
    roots = []
    for p in project_registry(svc).values():
        roots.append(normalize_path(p["root"]))
    roots.append(normalize_path(svc.home))
    return [r for r in roots if r]


def _path_allowed(path: str, roots: list[str]) -> bool:
    np = normalize_path(path)
    if not np:
        return False
    for r in roots:
        if np == r or np.startswith(r + "\\"):
            return True
    return False


# ---------------------------------------------------------------- overview
def _latest_models(svc) -> dict:
    """agent_id -> model actually used at last successful start (event log)."""
    rows = svc.store.conn.execute(
        "SELECT entity_id, payload FROM events WHERE kind='agent.started' "
        "ORDER BY recorded_at, event_id").fetchall()
    out = {}
    for r in rows:
        try:
            m = json.loads(r["payload"]).get("model")
        except ValueError:
            m = None
        if m:
            out[r["entity_id"]] = m
    return out


def build_overview(svc, subject: Subject, since: str | None) -> dict:
    from .agents import agent_runtime_summary
    from .tasks import visible_tasks, task_card

    can_observe = svc.authz.allowed(subject, "system.observe")
    can_discover = svc.authz.allowed(subject, "agent.discover")
    data: dict = {"ok_sections": [], "errors": {}, "server_time": svc.now(),
                  "can_observe": can_observe}

    # --- projects & agents -------------------------------------------------
    if can_discover:
        projects = project_registry(svc)
        agent_projects = {}
        for key, p in projects.items():
            for aid in p["agents"]:
                agent_projects[aid] = key
        models = _latest_models(svc)
        rows = svc.store.conn.execute(
            "SELECT * FROM agents WHERE lifecycle != 'retired' "
            "ORDER BY created_at, agent_id").fetchall()
        agents = []
        for r in rows:
            cfg = _agent_cfg(svc, r["agent_id"])
            meta = cfg.get("metadata") or {}
            rt = (cfg.get("runtime") or {})
            try:
                summary = agent_runtime_summary(svc, r["agent_id"])
            except Exception:  # noqa: BLE001 - observation must not kill map
                summary = {"instances": [], "active_instance": None,
                           "responsibility": {"claimed_tasks": [],
                                              "claimed_count": 0,
                                              "personal_queued": 0,
                                              "pending_waits": 0}}
            active = summary.get("active_instance")
            # 声明的资产读取关系：config 引用的上下文文件落在项目 assets/ 下
            declared_asset_read = False
            proot = None
            for key, p in projects.items():
                if r["agent_id"] in p["agents"]:
                    proot = p
                    break
            if proot and proot.get("assets_dir"):
                aroot = normalize_path(proot["assets_dir"])
                refs = list((cfg.get("workspace") or {}).get("context_entries")
                            or []) + list((cfg.get("instructions") or {})
                                          .get("files") or [])
                declared_asset_read = any(
                    normalize_path(str(x)).startswith(aroot + "\\")
                    for x in refs)
            agents.append({
                "agent_id": r["agent_id"], "name": r["name"],
                "lifecycle": r["lifecycle"],
                "project": agent_projects.get(r["agent_id"]),
                "line": meta.get("line"), "domain": meta.get("domain"),
                "role": meta.get("role"), "agent_key": meta.get("agent_key"),
                "harness": (rt.get("harness")),
                "model_desired": rt.get("model"),
                "model_route": meta.get("model_route"),
                "model_actual": models.get(r["agent_id"]),
                "execution_root": (rt.get("options") or {}).get(
                    "execution_root"),
                "workspace": r["workspace_path"],
                "instance_count": len([i for i in summary.get("instances", [])
                                       if i.get("phase") in
                                       ("starting", "live", "stopping")]),
                "active_instance": active,
                "observation": (active or {}).get("observation"),
                "responsibility": summary.get("responsibility"),
                "declared_asset_read": declared_asset_read,
            })
        data["projects"] = [
            {"key": p["key"], "root": p["root"], "agents": p["agents"],
             "has_project_dir": p["has_project_dir"],
             "has_assets": bool(p.get("assets_dir")),
             "active": p.get("active"),
             "authorizations": p.get("authorizations")}
            for p in projects.values()]
        data["agents"] = agents
        data["ok_sections"].append("agents")
    else:
        data["errors"]["agents"] = "缺少 agent.discover 权限"

    # --- ports & subscriptions --------------------------------------------
    if svc.authz.allowed(subject, "port.discover"):
        prows = svc.store.conn.execute(
            "SELECT * FROM ports WHERE state != 'retired'").fetchall()
        data["ports"] = [{
            "port_id": p["port_id"], "route": p["route"], "name": p["name"],
            "handler": p["handler"], "state": p["state"],
            "visibility": p["visibility"]} for p in prows]
        srows = svc.store.conn.execute(
            "SELECT agent_id, port_id, enabled FROM subscriptions").fetchall()
        data["subscriptions"] = [dict(s) for s in srows]
        data["ok_sections"].append("ports")
    else:
        data["errors"]["ports"] = "缺少 port.discover 权限"

    # --- task flows (active + recently terminal) ---------------------------
    try:
        rows = visible_tasks(svc, subject)
        flows = []
        import datetime as _dt
        cutoff = format_rfc3339(now_utc() - _dt.timedelta(
            seconds=OVERVIEW_FLOW_TERMINAL_WINDOW_S))
        for r in rows:
            if r["state"] in ("queued", "claimed"):
                flows.append(task_card(r))
            elif (r["ended_at"] or "") >= cutoff:
                flows.append(task_card(r))
        flows.sort(key=lambda t: t["created_at"])
        data["flows"] = flows[-400:]
        data["ok_sections"].append("flows")
    except Exception as e:  # noqa: BLE001
        data["errors"]["flows"] = repr(e)

    # --- human inbox counts -------------------------------------------------
    if subject.subject_type in ("owner", "system", "human"):
        hrows = svc.store.conn.execute(
            "SELECT t.task_id, t.state, t.target FROM tasks t JOIN ports p "
            "ON 'port:'||p.port_id=t.target WHERE p.handler='human' "
            "AND t.state IN ('queued','claimed')").fetchall()
        counts = {"queued": 0, "claimed": 0}
        seen_rows = svc.store.conn.execute(
            "SELECT task_id FROM task_views WHERE subject_id=?",
            (subject.subject_id,)).fetchall()
        seen = {r["task_id"] for r in seen_rows}
        unread = 0
        for r in hrows:
            counts[r["state"]] = counts.get(r["state"], 0) + 1
            if r["state"] == "queued" and r["task_id"] not in seen:
                unread += 1
        data["human"] = {"pending": counts.get("queued", 0),
                         "processing": counts.get("claimed", 0),
                         "unread": unread}
        data["ok_sections"].append("human")

    # --- events (change feed) ----------------------------------------------
    if can_observe:
        sql = "SELECT * FROM events"
        params: list = []
        if since:
            sql += " WHERE recorded_at > ?"
            params.append(since)
        sql += " ORDER BY recorded_at, event_id LIMIT ?"
        params.append(OVERVIEW_EVENT_LIMIT)
        erows = svc.store.conn.execute(sql, params).fetchall()
        data["events"] = [{
            "event_id": e["event_id"], "entity": e["entity_type"] + ":" +
            e["entity_id"], "kind": e["kind"], "actor": e["actor"],
            "occurred_at": e["occurred_at"], "recorded_at": e["recorded_at"],
            "payload": json.loads(e["payload"])} for e in erows]
        latest = svc.store.conn.execute(
            "SELECT MAX(recorded_at) m FROM events").fetchone()["m"]
        data["event_cursor"] = latest
    return data


# ------------------------------------------------------------- human inbox
def _human_visible(svc, subject: Subject, row) -> bool:
    if subject.subject_type in ("owner", "system"):
        return True
    if subject.subject_type != "human":
        return False
    if not row["target"].startswith("port:"):
        return False
    return svc.authz.allowed(subject, "human.answer", row["target"])


def human_inbox(svc, subject: Subject, state: str, limit: int = 100) -> dict:
    from .tasks import task_card
    if subject.subject_type not in ("owner", "system", "human"):
        raise HiveError("FORBIDDEN", "需要人工处理权限")
    rows = svc.store.conn.execute(
        "SELECT t.* FROM tasks t JOIN ports p ON 'port:'||p.port_id=t.target "
        "WHERE p.handler='human' AND t.state=? "
        "ORDER BY t.created_at DESC, t.task_id LIMIT ?",
        (state, min(max(1, limit), 200))).fetchall()
    seen_rows = svc.store.conn.execute(
        "SELECT task_id, seen_at FROM task_views WHERE subject_id=?",
        (subject.subject_id,)).fetchall()
    seen = {r["task_id"]: r["seen_at"] for r in seen_rows}
    items = []
    for r in rows:
        if not _human_visible(svc, subject, r):
            continue
        card = task_card(r)
        card["seen_at"] = seen.get(r["task_id"])
        items.append(card)
    return {"items": items}


def validate_ui_answers(ui: dict, answers: dict):
    """Server-side re-validation of the UI field contract (hive-ui/1).

    Required fields must be present; scores in range and on step; select
    values must be declared option ids. Untouched sliders are absent (None)
    — a missing required score fails, an optional one stays null.
    """
    fields = (ui or {}).get("fields") or []
    errors = []
    for f in fields:
        fid = f.get("id")
        if not fid:
            continue
        ftype = f.get("type")
        label = f.get("label") or fid
        required = bool(f.get("required"))
        val = answers.get(fid)
        empty = val is None or val == "" or val == []
        if required and empty:
            errors.append(f"必填项未填写: {label}")
            continue
        if empty:
            continue
        if ftype == "score":
            if not isinstance(val, (int, float)) or isinstance(val, bool):
                errors.append(f"评分必须是数字: {label}")
                continue
            lo = f.get("min", 0)
            hi = f.get("max", 100)
            if val < lo or val > hi:
                errors.append(f"评分超出范围 {lo}..{hi}: {label}")
            step = f.get("step")
            if step:
                k = (val - lo) / step
                if abs(k - round(k)) > 1e-9:
                    errors.append(f"评分不符合步长 {step}: {label}")
        elif ftype == "single_select":
            ids = {o.get("id") for o in (f.get("options") or [])}
            if val not in ids:
                errors.append(f"选项不在声明集合内: {label}")
        elif ftype == "multi_select":
            ids = {o.get("id") for o in (f.get("options") or [])}
            if not isinstance(val, list) or any(v not in ids for v in val):
                errors.append(f"多选值不在声明集合内: {label}")
        elif ftype in ("text", "long_text"):
            if not isinstance(val, str):
                errors.append(f"文本字段类型错误: {label}")
    if errors:
        raise HiveError("INVALID_ARGUMENT", "回答未通过校验",
                        {"field_errors": errors})


# ------------------------------------------------------------------ files
def _safe_name(name: str) -> str:
    name = os.path.basename(name or "file")
    return re.sub(r"[^\w.\-\u4e00-\u9fff]+", "_", name)[:120] or "file"


def save_upload(svc, subject: Subject, filename: str, mime: str, body: bytes,
                cap: int) -> dict:
    if len(body) > cap:
        raise HiveError("INVALID_ARGUMENT",
                        f"文件超过大小上限 {cap // (1024 * 1024)}MB")
    fid = new_id("file")
    fname = _safe_name(filename)
    root = os.path.join(svc.home, "webfiles", fid)
    os.makedirs(root, exist_ok=False)
    fpath = os.path.join(root, fname)
    with open(fpath, "wb") as f:
        f.write(body)
    sha = hashlib.sha256(body).hexdigest()
    meta = {"file_id": fid, "name": fname, "mime": mime or
            mimetypes.guess_type(fname)[0] or "application/octet-stream",
            "size": len(body), "sha256": sha,
            "uploaded_by": subject.ref,
            "created_at": format_rfc3339(now_utc())}
    with open(os.path.join(root, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    return {"file_id": fid, "ref": f"hive-file:{fid}", "name": fname,
            "mime": meta["mime"], "size": len(body), "sha256": sha,
            "path": fpath}


def load_upload_meta(svc, file_id: str) -> tuple[dict, str]:
    if not re.fullmatch(r"file_[A-Za-z0-9]+", file_id or ""):
        raise HiveError("NOT_FOUND", "文件不存在")
    root = os.path.join(svc.home, "webfiles", file_id)
    mpath = os.path.join(root, "meta.json")
    if not os.path.isfile(mpath):
        raise HiveError("NOT_FOUND", "文件不存在或已清理")
    with open(mpath, "r", encoding="utf-8") as f:
        meta = json.load(f)
    return meta, os.path.join(root, meta["name"])


def classify_file(name: str, mime: str) -> str:
    ext = os.path.splitext(name or "")[1].lower()
    if ext in VIDEO_EXTS or (mime or "").startswith("video/"):
        return "video"
    if ext in IMAGE_EXTS or (mime or "").startswith("image/"):
        return "image"
    if ext in AUDIO_EXTS or (mime or "").startswith("audio/"):
        return "audio"
    if ext in TEXT_EXTS or (mime or "").startswith("text/"):
        return "text"
    return "binary"


# ------------------------------------------------------------- HTTP wiring
class WebUI:
    """Route handler invoked by server.py for any /ui path."""

    def __init__(self, service):
        self.svc = service

    # -- entry points called from the HTTP handler --
    def handle_get(self, h, parsed) -> bool:
        path = parsed.path
        if path in ("/ui", "/ui/"):
            return self._static(h, "index.html")
        if path.startswith("/ui/api/"):
            return self._api_get(h, path[len("/ui/api/"):], parsed)
        if path.startswith("/ui/"):
            return self._static(h, path[len("/ui/"):])
        return False

    def handle_post(self, h, parsed, body: bytes) -> bool:
        path = parsed.path
        if not path.startswith("/ui/api/"):
            return False
        return self._api_post(h, path[len("/ui/api/"):], parsed, body)

    # -- static --
    def _static(self, h, rel: str):
        rel = urllib.parse.unquote(rel).lstrip("/") or "index.html"
        fpath = os.path.normpath(os.path.join(WEB_ROOT, rel))
        if not (fpath == WEB_ROOT or fpath.startswith(WEB_ROOT + os.sep)) \
                or not os.path.isfile(fpath):
            self._json(h, 404, {"ok": False, "error":
                                {"code": "NOT_FOUND", "message": "资源不存在"}})
            return True
        ctype = mimetypes.guess_type(fpath)[0] or "application/octet-stream"
        if fpath.endswith(".js"):
            ctype = "text/javascript; charset=utf-8"
        elif fpath.endswith(".css"):
            ctype = "text/css; charset=utf-8"
        elif fpath.endswith(".html"):
            ctype = "text/html; charset=utf-8"
        with open(fpath, "rb") as f:
            content = f.read()
        h.send_response(200)
        h.send_header("Content-Type", ctype)
        h.send_header("Content-Length", str(len(content)))
        h.send_header("Cache-Control", "no-cache")
        h.send_header("X-Content-Type-Options", "nosniff")
        h.end_headers()
        h.wfile.write(content)
        return True

    # -- helpers --
    def _json(self, h, status: int, obj: dict, headers: dict | None = None):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        h.send_response(status)
        h.send_header("Content-Type", "application/json; charset=utf-8")
        h.send_header("Content-Length", str(len(body)))
        h.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            h.send_header(k, v)
        h.end_headers()
        h.wfile.write(body)

    def _err(self, h, e: HiveError):
        self._json(h, e.http_status,
                   {"ok": False, "error": e.to_dict()})

    def _subject_or_none(self, h) -> Subject | None:
        return resolve_session(self.svc, h.headers.get("Cookie"))

    def _require_subject(self, h) -> Subject:
        subject = self._subject_or_none(h)
        if subject is None:
            raise HiveError("AUTH_REQUIRED", "会话无效或已过期，请重新登录")
        return subject

    def _read_json(self, body: bytes) -> dict:
        if not body:
            return {}
        try:
            data = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise HiveError("INVALID_JSON", "请求体不是合法JSON")
        if not isinstance(data, dict):
            raise HiveError("INVALID_ARGUMENT", "请求体必须是对象")
        return data

    def _dispatch(self, subject, command, args, rid=None):
        return self.svc.dispatch(subject, command, args, rid)[0]

    # -- API GET --
    def _api_get(self, h, rel: str, parsed) -> bool:
        qs = dict(urllib.parse.parse_qsl(parsed.query))
        try:
            if rel == "session":
                subject = self._subject_or_none(h)
                if subject is None:
                    self._json(h, 200, {"ok": True, "authenticated": False})
                else:
                    self._json(h, 200, {"ok": True, "authenticated": True,
                                        "subject": subject.to_dict(),
                                        "ref": subject.ref})
                return True

            subject = self._require_subject(h)

            if rel == "overview":
                self._json(h, 200, {"ok": True, "data": build_overview(
                    self.svc, subject, qs.get("since") or None)})
                return True

            if rel == "projects":
                self._json(h, 200, {"ok": True, "data": {
                    "projects": list(project_registry(self.svc).values())}})
                return True

            if rel.startswith("project/"):
                return self._project_get(h, rel[len("project/"):], subject, qs)

            if rel.startswith("agent/"):
                rest = urllib.parse.unquote(rel[len("agent/"):])
                if "/conversation" in rest:
                    agent_id, _, sub = rest.partition("/conversation")
                    return self._conversation_get(h, agent_id, sub, subject, qs)
                agent_id = rest
                data = self._dispatch(subject, "control.agent.show",
                                      {"agent": agent_id, "config": True,
                                       "runtime": True})
                models = _latest_models(self.svc)
                data["model_actual"] = models.get(agent_id)
                tasks = self._dispatch(subject, "control.task.list",
                                       {"agent": agent_id, "limit": 100})
                data["tasks"] = tasks.get("items", [])
                waits = self._dispatch(subject, "inbox.wait.list",
                                       {"state": "pending"})
                data["waits"] = [w for w in waits.get("items", [])]
                return self._json(h, 200, {"ok": True, "data": data}) or True

            if rel.startswith("task/"):
                task_id = urllib.parse.unquote(rel[len("task/"):])
                return self._task_get(h, task_id, subject, qs)

            if rel == "human/inbox":
                state = qs.get("state", "queued")
                if state not in ("queued", "claimed", "done", "fail", "trash"):
                    raise HiveError("INVALID_ARGUMENT", "state 非法")
                data = human_inbox(self.svc, subject, state,
                                   int(qs.get("limit") or 100))
                self._json(h, 200, {"ok": True, "data": data})
                return True

            if rel.startswith("file/"):
                return self._file_get(h, rel[len("file/"):], subject, qs)

            if rel == "task-file":
                return self._task_file_get(h, subject, qs)

            raise HiveError("NOT_FOUND", f"路径不存在: /ui/api/{rel}")
        except HiveError as e:
            self._err(h, e)
            return True
        except Exception as e:  # noqa: BLE001
            self.svc.logger.error("webui.get", path=rel, error=repr(e))
            self._err(h, HiveError("INTERNAL_ERROR", "内部错误"))
            return True

    def _task_get(self, h, task_id: str, subject, qs) -> bool:
        data = self._dispatch(subject, "control.task.show",
                              {"task": task_id, "history": True})
        peek = self._dispatch(subject, "inbox.peek",
                              {"task": task_id, "comments": True,
                               "mark_seen": bool(qs.get("mark_seen"))})
        data["comments"] = peek.get("comments") or []
        data["wait_summary"] = peek.get("wait_summary")
        # hydration for rendering related nodes
        for rel_key in ("parent_id", "reply_to"):
            rid = (data["task"].get(rel_key))
            if rid:
                try:
                    sub = self._dispatch(subject, "control.task.show",
                                         {"task": rid})
                    data.setdefault("related", {})[rid] = sub["task"]
                except HiveError:
                    pass
        self._json(h, 200, {"ok": True, "data": data})
        return True

    # -- project browsing (contained) --
    def _project_get(self, h, rel: str, subject, qs) -> bool:
        projects = project_registry(self.svc)
        if "/" in rel:
            key, sub = rel.split("/", 1)
        else:
            key, sub = rel, ""
        key = urllib.parse.unquote(key)
        proj = projects.get(key)
        if not proj:
            raise HiveError("NOT_FOUND", f"项目未注册: {key}")
        if subject.subject_type not in ("owner", "system"):
            hp = [p for p in (self.svc.store.conn.execute(
                "SELECT port_id FROM ports WHERE handler='human'").fetchall())]
            allowed = any(svc_allowed(self.svc, subject, p["port_id"])
                          for p in hp)
            if not allowed:
                raise HiveError("FORBIDDEN", "无该项目资产浏览权限")
        root = proj["root"]
        if sub == "assets":
            rel_path = qs.get("path") or ""
            base = proj.get("assets_dir") or root
            target = os.path.normpath(os.path.join(base, rel_path))
            if not _path_allowed(target, [normalize_path(base)]):
                raise HiveError("FORBIDDEN", "路径越界")
            if os.path.isfile(target):
                return self._serve_file(h, target, download=qs.get("download"))
            if not os.path.isdir(target):
                raise HiveError("NOT_FOUND", "目录不存在")
            entries = []
            for name in sorted(os.listdir(target)):
                fp = os.path.join(target, name)
                try:
                    st = os.stat(fp)
                except OSError:
                    continue
                entries.append({
                    "name": name, "dir": os.path.isdir(fp),
                    "size": None if os.path.isdir(fp) else st.st_size,
                    "mtime": format_rfc3339(
                        __import__("datetime").datetime.fromtimestamp(
                            st.st_mtime, __import__("datetime").timezone.utc)),
                    "kind": classify_file(name, mimetypes.guess_type(name)[0]
                                          or "")})
            self._json(h, 200, {"ok": True, "data": {
                "project": key, "path": rel_path, "base": base,
                "entries": entries}})
            return True
        if sub == "file":
            rel_path = qs.get("path") or ""
            target = os.path.normpath(os.path.join(root, rel_path))
            if not _path_allowed(target, [normalize_path(root)]):
                raise HiveError("FORBIDDEN", "路径越界")
            if not os.path.isfile(target):
                raise HiveError("NOT_FOUND", "文件不存在")
            return self._serve_file(h, target, download=qs.get("download"))
        if sub in ("", "info"):
            pj = os.path.join(root, "project")
            docs = {}
            for fname in ("TASK_CONTRACT.md", "BRIEF.md", "TASTE.md",
                          "MODELS.md", "CONFIGURATION.md", "TRIGGERS.md"):
                fp = os.path.join(pj, fname)
                if os.path.isfile(fp):
                    with open(fp, "r", encoding="utf-8") as f:
                        docs[fname] = f.read()
            self._json(h, 200, {"ok": True, "data": {
                "project": proj, "docs": docs}})
            return True
        raise HiveError("NOT_FOUND", f"路径不存在: /ui/api/project/{rel}")

    # -- file serving --
    def _file_get(self, h, rel: str, subject, qs) -> bool:
        if rel.endswith("/info"):
            meta, _ = load_upload_meta(self.svc, rel[:-len("/info")])
            return self._json(h, 200, {"ok": True, "data": meta}) or True
        meta, fpath = load_upload_meta(self.svc, rel)
        return self._serve_file(h, fpath, mime=meta.get("mime"),
                                download=qs.get("download"),
                                name=meta.get("name"))

    def _task_file_get(self, h, subject, qs) -> bool:
        """Serve an attachment referenced by a readable task, when the file
        lives under a registered project root or the hive home. Never serves
        arbitrary local paths."""
        from .tasks import get_task, can_read_task
        task_id = qs.get("task") or ""
        uri = qs.get("uri") or ""
        row = get_task(self.svc, task_id)
        if not can_read_task(self.svc, subject, row):
            raise HiveError("NOT_VISIBLE", "任务不可见")
        card_attach = json.loads(row["attachments"] or "[]")
        result = json.loads(row["result"] or "null") or {}
        uris = list(card_attach) + [a.get("uri") for a in
                                    (result.get("artifacts") or [])
                                    if isinstance(a, dict)]
        # UI 内容块中声明的引用同样属于本任务（hive-ui/1 blocks[].ref）
        try:
            tdata = json.loads(row["data"] or "{}")
            for b in ((tdata.get("ui") or {}).get("blocks") or []):
                if isinstance(b, dict) and b.get("ref"):
                    uris.append(b["ref"])
            for a in (result.get("data") or {}).get("ui_attachments") or []:
                if isinstance(a, dict) and a.get("ref"):
                    uris.append(a["ref"])
        except ValueError:
            pass
        comments = self.svc.store.conn.execute(
            "SELECT checkpoint_refs FROM comments WHERE task_id=?",
            (task_id,)).fetchall()
        for c in comments:
            uris.extend(json.loads(c["checkpoint_refs"] or "[]"))
        if uri not in uris:
            raise HiveError("FORBIDDEN", "该引用不属于此任务")
        if uri.startswith("hive-file:"):
            meta, fpath = load_upload_meta(self.svc, uri[len("hive-file:"):])
            return self._serve_file(h, fpath, mime=meta.get("mime"),
                                    download=qs.get("download"),
                                    name=meta.get("name"))
        if not os.path.isabs(uri):
            raise HiveError("INVALID_ARGUMENT", "引用不是可解析的文件路径")
        if not _path_allowed(uri, _allowed_roots(self.svc)):
            raise HiveError(
                "FORBIDDEN", "文件不在受管目录（项目根/Hive home）内，拒绝预览",
                {"uri": uri})
        if not os.path.isfile(uri):
            raise HiveError("NOT_FOUND", "文件不存在或已被移动",
                            {"uri": uri})
        return self._serve_file(h, uri, download=qs.get("download"))

    def _serve_file(self, h, fpath: str, mime: str | None = None,
                    download=None, name: str | None = None) -> bool:
        mime = mime or mimetypes.guess_type(fpath)[0] or \
            "application/octet-stream"
        name = name or os.path.basename(fpath)
        size = os.path.getsize(fpath)
        range_header = h.headers.get("Range")
        start, end, status = 0, size - 1, 200
        if range_header:
            m = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
            if m:
                if m.group(1):
                    start = int(m.group(1))
                    end = int(m.group(2)) if m.group(2) else size - 1
                else:  # suffix range: last N bytes
                    start = max(0, size - int(m.group(2)))
                end = min(end, size - 1)
                if start > end:
                    h.send_response(416)
                    h.send_header("Content-Range", f"bytes */{size}")
                    h.end_headers()
                    return True
                status = 206
        length = end - start + 1
        h.send_response(status)
        h.send_header("Content-Type", mime)
        h.send_header("Accept-Ranges", "bytes")
        h.send_header("Content-Length", str(length))
        h.send_header("X-Content-Type-Options", "nosniff")
        disposition = "attachment" if download else "inline"
        quoted = urllib.parse.quote(name)
        h.send_header("Content-Disposition",
                      f"{disposition}; filename*=UTF-8''{quoted}")
        if status == 206:
            h.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        h.end_headers()
        with open(fpath, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(256 * 1024, remaining))
                if not chunk:
                    break
                h.wfile.write(chunk)
                remaining -= len(chunk)
        return True

    # -- API POST --
    def _api_post(self, h, rel: str, parsed, body: bytes) -> bool:
        qs = dict(urllib.parse.parse_qsl(parsed.query))
        try:
            if rel == "session":
                data = self._read_json(body)
                sid, subject = create_session(self.svc, data.get("token") or "")
                cookie = (f"{SESSION_COOKIE}={sid}; Path=/ui; HttpOnly; "
                          f"SameSite=Strict")
                self._json(h, 200, {"ok": True, "subject": subject.to_dict(),
                                    "ref": subject.ref},
                           {"Set-Cookie": cookie})
                return True
            if rel == "logout":
                destroy_session(self.svc, h.headers.get("Cookie"))
                self._json(h, 200, {"ok": True},
                           {"Set-Cookie": f"{SESSION_COOKIE}=; Path=/ui; "
                                          f"Max-Age=0; HttpOnly"})
                return True

            subject = self._require_subject(h)
            # CSRF: cookie session + custom header (not settable cross-origin
            # without preflight) on every mutation.
            if (h.headers.get("X-Requested-With") or "") != "hive-ui":
                raise HiveError("FORBIDDEN", "缺少防跨站请求头")

            if rel == "upload":
                cap = int(self.svc.policy().get("ui_upload_max_bytes")
                          or DEFAULT_UPLOAD_CAP)
                meta = save_upload(self.svc, subject,
                                   qs.get("name") or "file",
                                   qs.get("mime") or "", body, cap)
                self._json(h, 201, {"ok": True, "data": meta})
                return True

            data = self._read_json(body)
            rid = data.get("request_id") or new_id("request")

            if rel == "human/answer":
                return self._human_answer(h, subject, data, rid)
            if rel == "human/fail":
                args = {"task": data.get("task") or "",
                        "spec_version": int(data.get("spec_version") or 0),
                        "reason": data.get("reason") or ""}
                out = self._dispatch(subject, "control.human.fail", args, rid)
                self._json(h, 200, {"ok": True, "request_id": rid,
                                    "data": out})
                return True
            if rel == "comment":
                args = {"task": data.get("task") or "",
                        "text": data.get("text") or ""}
                if data.get("mentions"):
                    args["mention"] = list(data["mentions"])
                if data.get("revision_of"):
                    args["revision_of"] = data["revision_of"]
                if data.get("attachments"):
                    args["checkpoint"] = list(data["attachments"])
                out = self._dispatch(subject, "emit.comment", args, rid)
                self._json(h, 200, {"ok": True, "request_id": rid,
                                    "data": out})
                return True
            if rel == "command":
                command = data.get("command") or ""
                if command not in UI_COMMAND_WHITELIST:
                    raise HiveError("FORBIDDEN",
                                    f"指挥台不开放的命令: {command}")
                args = data.get("arguments") or {}
                if not isinstance(args, dict):
                    raise HiveError("INVALID_ARGUMENT", "arguments 必须是对象")
                out = self._dispatch(subject, command, args, rid)
                self._json(h, 200, {"ok": True, "request_id": rid,
                                    "data": out})
                return True

            if rel.startswith("agent/"):
                rest = urllib.parse.unquote(rel[len("agent/"):])
                if "/conversation" in rest:
                    agent_id, _, sub = rest.partition("/conversation")
                    return self._conversation_post(h, agent_id, sub, subject,
                                                   data, rid)

            raise HiveError("NOT_FOUND", f"路径不存在: /ui/api/{rel}")
        except HiveError as e:
            self._err(h, e)
            return True
        except Exception as e:  # noqa: BLE001
            import traceback
            self.svc.logger.error("webui.post", path=rel, error=repr(e),
                                  tb=traceback.format_exc()[-1200:])
            self._err(h, HiveError("INTERNAL_ERROR", "内部错误"))
            return True

    def _human_answer(self, h, subject, data, rid):
        from .tasks import get_task
        task_id = data.get("task") or ""
        row = get_task(self.svc, task_id)
        tdata = json.loads(row["data"] or "{}")
        ui = tdata.get("ui") or {}
        answers = (data.get("data") or {}).get("answers")
        if ui.get("fields") and answers is not None:
            validate_ui_answers(ui, answers)
        elif ui.get("fields"):
            # structured question exists but no answers object at all
            validate_ui_answers(ui, {})
        result_data = dict(data.get("data") or {})
        args = {"task": task_id,
                "spec_version": int(data.get("spec_version") or 0)}
        if isinstance(data.get("text"), str) and data.get("text"):
            args["text"] = data["text"]
        if result_data:
            args["data"] = result_data
        if data.get("attachments"):
            args["attach"] = list(data["attachments"])
        out = self._dispatch(subject, "control.human.answer", args, rid)
        self._json(h, 200, {"ok": True, "request_id": rid, "data": out})
        return True

    # ---------------------------------------------------- conversation API
    def _conv_authz(self, subject, agent_id: str, action: str):
        # 会话查看/发送/取消按 agent 资源授权；owner/system 全通
        if subject.subject_type in ("owner", "system"):
            return
        self.svc.authz.require(subject, action, f"agent:{agent_id}")

    def _conversation_get(self, h, agent_id: str, sub: str, subject, qs):
        from . import conversation as conv
        from .agents import get_agent  # noqa: F401  (existence check below)
        get_agent(self.svc, agent_id)   # NOT_FOUND if absent
        if sub in ("", "/"):
            self._conv_authz(subject, agent_id, "conversation.read")
            binding = conv.get_binding(self.svc, agent_id)
            inst = conv.live_instance_of(self.svc, agent_id)
            bridge = getattr(self.svc, "conversation", None)
            data = {
                "agent_id": agent_id,
                "instance": ({"instance_id": inst["instance_id"],
                              "generation": inst["generation"],
                              "phase": inst["phase"],
                              "handle": inst["handle"]} if inst else None),
                "binding": binding,
                "bridge": (bridge.bridge.name if bridge else None),
                "bridge_connected": bool(
                    binding and binding.get("connection_state") == "connected"),
                "capabilities": getattr(getattr(bridge, "bridge", None),
                                        "capabilities", {}) or {},
                "active_input": (conv.input_card(conv.active_input(
                    self.svc, agent_id)) if conv.active_input(
                    self.svc, agent_id) else None),
                "pending_permission": conv.pending_permission(
                    self.svc, agent_id),
            }
            # 上游历史：实例的真实 ZCode 会话内容（含绑定前的历史），
            # 与桥捕获的增量（conv_events，绑定后）按时间水印拼接
            session_id = (binding or {}).get("session_id")
            if not session_id and inst and (inst["handle"] or "").startswith("sess:"):
                session_id = inst["handle"].split(":", 1)[1]
            if session_id:
                from . import zsession_read as zr
                try:
                    data["session_state"] = zr.fetch_session_state(
                        self.svc, session_id)
                except HiveError as e:
                    data["session_state_error"] = e.message
                try:
                    hist = zr.fetch_session_history(self.svc, session_id)
                    watermark = (binding or {}).get("created_at")
                    data["upstream_history"] = hist["messages"]
                    data["upstream_note"] = hist.get("note")
                    data["upstream_watermark"] = watermark
                except HiveError as e:
                    data["upstream_error"] = e.message
            if binding:
                proj = conv.project_messages(self.svc, binding["session_id"],
                                             limit=120)
                data["tail"] = proj
            self._json(h, 200, {"ok": True, "data": data})
            return True
        if sub == "/history":
            self._conv_authz(subject, agent_id, "conversation.read")
            binding = conv.get_binding(self.svc, agent_id)
            if not binding:
                raise HiveError("NO_BINDING", "该 Agent 还没有会话绑定")
            after = int(qs.get("after") or 0)
            limit = int(qs.get("limit") or 100)
            data = conv.history(self.svc, binding["session_id"],
                                after=after, limit=limit)
            self._json(h, 200, {"ok": True, "data": data})
            return True
        if sub == "/inputs":
            self._conv_authz(subject, agent_id, "conversation.read")
            data = {"items": conv.list_inputs(self.svc, agent_id)}
            self._json(h, 200, {"ok": True, "data": data})
            return True
        if sub == "/stream":
            self._conv_authz(subject, agent_id, "conversation.read")
            return self._conversation_stream(h, agent_id, qs)
        raise HiveError("NOT_FOUND", f"会话接口不存在: {sub}")

    def _conversation_post(self, h, agent_id: str, sub: str, subject,
                           data: dict, rid: str):
        from . import conversation as conv
        from .agents import get_agent
        get_agent(self.svc, agent_id)
        if sub == "/input":
            self._conv_authz(subject, agent_id, "conversation.send")
            out = conv.enqueue_input(
                self.svc, subject, agent_id=agent_id,
                text=data.get("text") or "", mode=data.get("mode") or "queue",
                request_id=rid, kind="user",
                attachments=data.get("attachments"),
                instance_id=data.get("instance_id"),
                generation=data.get("generation"))
            self._json(h, 200, {"ok": True, "request_id": rid, "data": out})
            return True
        if sub == "/cancel":
            self._conv_authz(subject, agent_id, "conversation.cancel")
            binding = conv.get_binding(self.svc, agent_id)
            if not binding:
                raise HiveError("NO_BINDING", "该 Agent 没有会话绑定")
            conv.check_instance_fresh(self.svc, agent_id,
                                      data.get("instance_id"),
                                      data.get("generation"))
            active = conv.active_input(self.svc, agent_id)
            if not active:
                self._json(h, 200, {"ok": True, "request_id": rid,
                                    "data": {"cancelled": False,
                                             "note": "当前没有活动轮次"}})
                return True
            conv.set_input_state(self.svc, active["input_id"], "cancelled")
            bridge = getattr(self.svc, "conversation", None)
            if bridge:
                try:
                    bridge.bridge.cancel_turn(binding, active["turn_id"])
                except HiveError as e:
                    self._json(h, 200, {"ok": True, "request_id": rid,
                                        "data": {"cancelled": False,
                                                 "upstream_error": e.message}})
                    return True
            self._json(h, 200, {"ok": True, "request_id": rid,
                                "data": {"cancelled": True,
                                         "input_id": active["input_id"]}})
            return True
        if sub == "/permission":
            self._conv_authz(subject, agent_id, "conversation.send")
            bridge = getattr(self.svc, "conversation", None)
            req_id = data.get("request_id")
            answer = data.get("answer") or {}
            if bridge and hasattr(bridge.bridge, "answer_permission"):
                bridge.bridge.answer_permission(req_id, answer)
            self._json(h, 200, {"ok": True, "request_id": rid,
                                "data": {"answered": True}})
            return True
        raise HiveError("NOT_FOUND", f"会话接口不存在: {sub}")

    def _conversation_stream(self, h, agent_id: str, qs) -> bool:
        """SSE：订阅该 Agent 会话事件。只读通道；断线后客户端以
        ?after=<cursor> 或 Last-Event-ID 恢复，缺口由 history 补齐。"""
        from . import conversation as conv
        binding = conv.get_binding(self.svc, agent_id)
        after = int(qs.get("after") or h.headers.get("Last-Event-ID") or 0)
        h.send_response(200)
        h.send_header("Content-Type", "text/event-stream; charset=utf-8")
        h.send_header("Cache-Control", "no-cache")
        h.send_header("Connection", "keep-alive")
        h.send_header("X-Accel-Buffering", "no")
        h.end_headers()
        h.close_connection = True
        q = conv.subscribe()
        try:
            # 先补历史缺口
            if binding:
                backlog = conv.history(self.svc, binding["session_id"],
                                       after=after, limit=200)
                for ev in backlog["items"]:
                    self._sse_write(h, ev)
                after = backlog["next_after"]
            self._sse_write(h, {"type": "hello", "cursor": after,
                                "agent_id": agent_id,
                                "server_time": self.svc.now()})
            import time as _t
            last_beat = _t.monotonic()
            while not self.svc.shutdown.is_set():
                try:
                    ev = q.get(timeout=2.0)
                except queue.Empty:
                    ev = None
                if ev is not None and ev.get("agent_id") == agent_id and \
                        ev.get("cursor", 0) > after:
                    self._sse_write(h, ev)
                    after = ev["cursor"]
                now = _t.monotonic()
                if now - last_beat > 15:
                    self._sse_write(h, {"type": "heartbeat",
                                        "server_time": self.svc.now()})
                    last_beat = now
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass           # 客户端断开：关闭网页通道，不影响 ACP/实例
        finally:
            conv.unsubscribe(q)
        return True

    @staticmethod
    def _sse_write(h, obj: dict):
        payload = json.dumps(obj, ensure_ascii=False)
        frame = (f"id: {obj.get('cursor', '')}\nevent: {obj.get('type', 'message')}\n"
                 f"data: {payload}\n\n").encode("utf-8")
        h.wfile.write(frame)
        h.wfile.flush()


def svc_allowed(svc, subject, port_id: str) -> bool:
    return svc.authz.allowed(subject, "human.answer", f"port:{port_id}")
