"""control test * handlers: seed / clock advance / fault set|clear / harness.

Test profile + owner only (DESIGN §19.3). Seed builds fixtures through the
normal registry/runtime/auth services — no --as-agent style bypass.
"""
from __future__ import annotations

import json
import os

from .errors import HiveError
from .ids import new_id
from .timeutil import format_rfc3339, now_utc, parse_duration


def register(svc):
    if not svc.test_mode:
        return
    svc.register("control.test.seed", h_seed)
    svc.register("control.test.clock.advance", h_clock_advance)
    svc.register("control.test.fault.set", h_fault_set)
    svc.register("control.test.fault.clear", h_fault_clear)
    svc.register("control.test.harness", h_test_harness)


def _require_owner(svc, subject):
    if subject.subject_type not in ("owner", "system"):
        raise HiveError("TEST_ONLY", "夹具工具仅在test profile+owner下开放")


# ---------------- seed ----------------
def h_seed(svc, subject, args, rid):
    _require_owner(svc, subject)
    scenario = args["scenario"]
    out = os.path.abspath(args["out"])
    existing = svc.store.conn.execute(
        "SELECT v FROM kv WHERE k=?", (f"seed:{scenario}",)).fetchone()
    if existing:
        manifest = _refresh_credentials(svc, subject, json.loads(existing["v"]),
                                        scenario)
        _write_manifest(out, manifest)
        return {"manifest_path": out, "scenario": scenario,
                "objects": manifest["objects"], "refreshed": True}

    prefix = scenario
    objects: dict[str, str] = {}
    credentials: dict[str, str] = {}
    cred_dir = os.path.join(svc.home, "auth", "fixtures", prefix)
    os.makedirs(cred_dir, exist_ok=True)
    ws_root = os.path.join(svc.home, "workspaces")
    files: dict[str, str] = {}

    def make_agent(name: str, *, subscriptions: list[str] | None = None,
                   grants: list[dict] | None = None, capacity: int = 3,
                   auto_start: bool = False):
        agent_id = new_id("agent")
        agent_name = f"{prefix}-{name}"
        subs = []
        for route in (subscriptions or []):
            pid = objects.get(f"port:{route}", route)
            subs.append({"port_id": pid, "enabled": True})
        perms = grants or []
        cfg = {
            "schema_version": 1, "name": agent_name,
            "description": f"fixture agent {name} ({scenario})",
            "instructions": {"text": f"你是验收夹具 {name}。", "files": []},
            "skills": [],
            "runtime": {"harness": "fixture-minimal", "model": "fixture-model",
                        "yolo": True, "auto_start": auto_start,
                        "restart_on_exit": True, "options": {}},
            "workspace": {"path": os.path.join(ws_root, agent_id),
                          "context_glob": "*.md",
                          "exclude": ["agent.md", "prompt.md"],
                          "context_entries": []},
            "subscriptions": subs,
            "permissions": perms,
            "limits": {"max_inflight_work": capacity},
            "metadata": {"scenario": scenario, "role": name},
        }
        from .agents import _publish_config
        now = format_rfc3339(now_utc())
        with svc.store.write() as cur:
            cur.execute(
                "INSERT INTO agents(agent_id,name,lifecycle,current_revision,"
                "desired_revision,applied_revision,workspace_path,created_at,"
                "updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (agent_id, agent_name, "enabled", 0, 0, 0,
                 cfg["workspace"]["path"], now, now))
            cur.execute("INSERT INTO subjects(subject_id,subject_type,name,"
                        "created_at,metadata) VALUES (?,?,?,?,?)",
                        (agent_id, "agent", agent_id, now,
                         json.dumps({"agent_id": agent_id})))
            revision = _publish_config(svc, cur, agent_id, cfg, 0, subject.ref,
                                       f"seed {scenario}")
        # static credential for CLI-driven fixture operations
        cred_path = os.path.join(cred_dir, f"{name}.json")
        from .auth import issue_credential
        with svc.store.write() as cur:
            issue_credential(cur, agent_id, kind="static", out_path=cred_path)
        return agent_id, cred_path, revision

    def make_port(route: str, handler: str, *, channels: list[str] | None = None,
                  schema: dict | None = None, defaults: dict | None = None):
        port_id = new_id("port")
        full_route = f"{prefix}/{route}"
        from .ports import DEFAULT_ACCEPTED
        now = format_rfc3339(now_utc())
        with svc.store.write() as cur:
            cur.execute(
                "INSERT INTO ports(port_id,route,name,description,handler,state,"
                "accepted_types,input_schema,defaults,visibility,revision,"
                "channel_ids,created_at,updated_at) VALUES (?,?,?,?,?,'enabled',"
                "?,?,?,'authenticated',1,?,?,?)",
                (port_id, full_route, full_route, f"fixture port {route}",
                 handler, json.dumps(DEFAULT_ACCEPTED[handler]),
                 json.dumps(schema or {}), json.dumps(defaults or {
                     "urgency": "normal", "claim_within": None,
                     "expires_in": None}),
                 json.dumps([c if str(c).startswith("chn_") else objects[c]
                            for c in (channels or [])]), now, now))
        return port_id

    # channels first (ports reference them)
    human_channel = _make_channel(svc, f"{prefix}-human-web", "human-web")
    webhook_channel = _make_channel(svc, f"{prefix}-webhook", "webhook",
                                    endpoint="http://127.0.0.1:39999/hook")
    objects["channel:human"] = human_channel
    objects["channel:webhook"] = webhook_channel

    objects["port:main"] = make_port("main", "agents")
    objects["port:private"] = make_port("private", "agents")
    objects["port:human"] = make_port("human", "human", channels=[human_channel])
    objects["port:external"] = make_port("external", "external",
                                         channels=[webhook_channel])

    # grants shared by fixture agents
    main_grants = [
        {"actions": ["task.create", "task.claim", "task.read"],
         "resources": [f"port:{objects['port:main']}"]},
        {"actions": ["task.create"], "resources": [
            f"agent:{objects.get('agent:A', 'agent:A')}",
            f"agent:{objects.get('agent:B', 'agent:B')}"]},
    ]
    a_id, a_cred, _ = make_agent("A", subscriptions=["main"])
    b_id, b_cred, _ = make_agent("B", subscriptions=["main"])
    main_grants_resolved = [
        {"actions": ["task.create", "task.claim", "task.read"],
         "resources": [f"port:{objects['port:main']}"]},
        {"actions": ["task.create", "task.read"], "resources": [
            f"agent:{a_id}", f"agent:{b_id}",
            f"port:{objects['port:human']}"]},
        {"actions": ["task.read"], "resources": [
            f"port:{objects['port:private']}"]},
    ]
    # re-publish A/B configs with resolved cross-agent grants
    from .agents import get_agent_config, _publish_config
    for agent_id, extra in ((a_id, main_grants_resolved),
                            (b_id, main_grants_resolved)):
        cfg = get_agent_config(svc, agent_id)
        cfg["permissions"] = extra
        row = svc.store.conn.execute(
            "SELECT current_revision FROM agents WHERE agent_id=?",
            (agent_id,)).fetchone()
        with svc.store.write() as cur:
            _publish_config(svc, cur, agent_id, cfg, row["current_revision"],
                            subject.ref, f"seed grants {scenario}")
    c_id, c_cred, _ = make_agent("C")  # no main subscription, no extra grants

    objects["agent:A"], objects["agent:B"], objects["agent:C"] = a_id, b_id, c_id
    credentials["A"], credentials["B"], credentials["C"] = a_cred, b_cred, c_cred

    # human subject with answer rights on the human port
    human_subject = new_id("subject")
    now = format_rfc3339(now_utc())
    human_cred = os.path.join(cred_dir, "human.json")
    with svc.store.write() as cur:
        cur.execute("INSERT INTO subjects(subject_id,subject_type,name,created_at,"
                    "metadata) VALUES (?,?,?,?,?)",
                    (human_subject, "human", f"{prefix}-human", now, "{}"))
        from .auth import issue_credential
        issue_credential(cur, human_subject, kind="static", out_path=human_cred)
        cur.execute(
            "INSERT INTO grants(grant_id,subject_id,actions,resources,delegable,"
            "created_by,created_at) VALUES (?,?,?,?,0,?,?)",
            (new_id("grant"), human_subject,
             json.dumps(["human.answer", "task.read"]),
             json.dumps([f"port:{objects['port:human']}"]), subject.ref, now))
    objects["subject:human"] = human_subject
    credentials["human"] = human_cred

    # external producer allowed to deliver to main + callback to external port
    producer_id = new_id("producer")
    producer_cred = os.path.join(cred_dir, "producer.json")
    with svc.store.write() as cur:
        cur.execute("INSERT INTO producers(producer_id,name,kind,allowed_targets,"
                    "enabled,revision,metadata,created_at) VALUES (?,?,?,?,1,1,"
                    "'{}',?)",
                    (producer_id, f"{prefix}-external", "external",
                     json.dumps([f"port:{objects['port:main']}"]), now))
        cur.execute("INSERT INTO subjects(subject_id,subject_type,name,created_at,"
                    "metadata) VALUES (?,?,?,?,?)",
                    (producer_id, "producer", producer_id, now,
                     json.dumps({"producer_id": producer_id})))
        from .auth import issue_credential
        issue_credential(cur, producer_id, kind="producer",
                         out_path=producer_cred)
        cur.execute(
            "INSERT INTO grants(grant_id,subject_id,actions,resources,delegable,"
            "created_by,created_at) VALUES (?,?,?,?,0,'system:bootstrap',?)",
            (new_id("grant"), producer_id,
             json.dumps(["task.create", "task.read"]),
             json.dumps([f"port:{objects['port:main']}",
                         f"port:{objects['port:external']}", "created_tasks"]),
             now))
    objects["producer"] = producer_id
    credentials["producer"] = producer_cred

    # observer subject (system.observe only)
    observer_subject = new_id("subject")
    observer_cred = os.path.join(cred_dir, "observer.json")
    with svc.store.write() as cur:
        cur.execute("INSERT INTO subjects(subject_id,subject_type,name,created_at,"
                    "metadata) VALUES (?,?,?,?,?)",
                    (observer_subject, "human", f"{prefix}-observer", now, "{}"))
        from .auth import issue_credential
        issue_credential(cur, observer_subject, kind="static",
                         out_path=observer_cred)
        cur.execute(
            "INSERT INTO grants(grant_id,subject_id,actions,resources,delegable,"
            "created_by,created_at) VALUES (?,?,?,?,0,?,?)",
            (new_id("grant"), observer_subject,
             json.dumps(["system.observe"]), json.dumps(["*"]), subject.ref, now))
    credentials["observer"] = observer_cred

    # live fixture instances for A/B (unique live instances per AC §2.2)
    from .runtime_ops import op_start_agent
    from .auth import Subject, SYSTEM_SUBJECT
    for agent_id in (a_id, b_id):
        op_start_agent(svc, SYSTEM_SUBJECT, agent_id, None)

    # sample accessible file
    sample_dir = os.path.join(svc.home, "fixtures", prefix)
    os.makedirs(sample_dir, exist_ok=True)
    sample = os.path.join(sample_dir, "sample.txt")
    with open(sample, "w", encoding="utf-8") as f:
        f.write(f"fixture sample for {scenario}\n")
    files["sample"] = sample
    files["workspaceA"] = os.path.join(ws_root, a_id)
    files["workspaceB"] = os.path.join(ws_root, b_id)

    manifest = {
        "scenario": scenario,
        "agents": {"A": a_id, "B": b_id, "C": c_id},
        "ports": {"main": objects["port:main"],
                  "private": objects["port:private"],
                  "human": objects["port:human"],
                  "external": objects["port:external"]},
        "producer": producer_id,
        "channels": {"human": human_channel, "webhook": webhook_channel},
        "subjects": {"human": human_subject, "producer": producer_id},
        "credentials": credentials,
        "harness": "fixture-minimal",
        "files": files,
        "webhook": {"endpoint": "http://127.0.0.1:39999/hook",
                    "received_file": os.path.join(sample_dir, "webhook-received.jsonl")},
        "objects": objects,
    }
    with svc.store.write() as cur:
        cur.execute("INSERT OR REPLACE INTO kv(k,v) VALUES (?,?)",
                    (f"seed:{scenario}",
                     json.dumps(manifest, ensure_ascii=False)))
    _write_manifest(out, manifest)
    return {"manifest_path": out, "scenario": scenario,
            "objects": {k: v for k, v in objects.items()},
            "refreshed": False}


def _write_manifest(out: str, manifest: dict):
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def _refresh_credentials(svc, subject, manifest: dict, scenario: str) -> dict:
    """Re-issue static credentials for existing fixture objects."""
    cred_dir = os.path.join(svc.home, "auth", "fixtures", scenario)
    os.makedirs(cred_dir, exist_ok=True)
    from .auth import issue_credential
    mapping = {"A": manifest["agents"]["A"], "B": manifest["agents"]["B"],
               "C": manifest["agents"]["C"],
               "human": manifest["subjects"]["human"],
               "producer": manifest["producer"]}
    with svc.store.write() as cur:
        for name, sid in mapping.items():
            path = manifest["credentials"].get(name)
            if not sid or not path:
                continue
            # only static fixture credentials; instance credentials stay alive
            cur.execute(
                "UPDATE credentials SET revoked_at=? WHERE subject_id=? AND "
                "kind='static' AND revoked_at IS NULL",
                (format_rfc3339(now_utc()), sid))
            issue_credential(cur, sid, kind="static", out_path=path)
    return manifest


def _observer_id(svc, scenario: str) -> str | None:
    row = svc.store.conn.execute(
        "SELECT subject_id FROM subjects WHERE subject_type='human' AND name=?",
        (f"{scenario}-observer",)).fetchone()
    return row["subject_id"] if row else None


def _make_channel(svc, name: str, kind: str, endpoint: str | None = None) -> str:
    channel_id = new_id("channel")
    now = format_rfc3339(now_utc())
    with svc.store.write() as cur:
        cur.execute(
            "INSERT INTO channels(channel_id,name,kind,endpoint,credential_ref,"
            "enabled,retry_policy,revision,metadata,created_at) VALUES (?,?,?,?,"
            "NULL,1,NULL,1,'{}',?)",
            (channel_id, name, kind, endpoint, now))
    return channel_id


# ---------------- clock ----------------
def h_clock_advance(svc, subject, args, rid):
    _require_owner(svc, subject)
    seconds = parse_duration(args["by"])
    svc.clock.advance(seconds)
    svc.store.kv_set("clock_offset", svc.clock.offset_seconds)
    # run deadline/trigger scans immediately
    processed_deadlines = 0
    if svc.scheduler:
        svc.scheduler.run_deadline_scan()
        svc.scheduler.run_triggers()
        svc.scheduler.run_notifications()
        svc.scheduler.run_cold_start()
    processed = svc.store.conn.execute(
        "SELECT COUNT(*) c FROM tasks WHERE state IN ('done','fail','trash') "
        "AND ended_at>=?", (svc.now(),)).fetchone()["c"]
    return {"now": svc.now(), "processed_deadlines": processed,
            "processed_triggers": 0,
            "clock_offset_seconds": svc.clock.offset_seconds}


# ---------------- faults ----------------
def h_fault_set(svc, subject, args, rid):
    _require_owner(svc, subject)
    point = args["point"]
    value = args.get("value") or {}
    if not isinstance(value, dict):
        raise HiveError("INVALID_ARGUMENT", "value 必须是对象")
    faults = svc.store.kv_get("faults", {}) or {}
    faults[point] = {"mode": args.get("mode", "once"), "value": value,
                     "set_at": svc.now()}
    svc.store.kv_set("faults", faults)
    return {"fault": {"point": point, "mode": args.get("mode", "once"),
                      "value": value}}


def h_fault_clear(svc, subject, args, rid):
    _require_owner(svc, subject)
    faults = svc.store.kv_get("faults", {}) or {}
    if args.get("point"):
        faults.pop(args["point"], None)
    else:
        faults = {}
    svc.store.kv_set("faults", faults)
    # release operation_before_execute pauses
    if svc.store.kv_get("op_pause"):
        svc.store.kv_set("op_pause", None)
    return {"cleared": args.get("point") or "all"}


# ---------------- test harness control ----------------
def h_test_harness(svc, subject, args, rid):
    _require_owner(svc, subject)
    agent_id = args["agent"]
    row = svc.store.conn.execute("SELECT * FROM agents WHERE agent_id=?",
                                 (agent_id,)).fetchone()
    if not row:
        raise HiveError("NOT_FOUND", f"Agent不存在: {agent_id}")
    action = args["action"]
    from .harness import FixtureAdapter
    st = FixtureAdapter.state(svc, agent_id)
    events = []
    if action in ("idle", "busy"):
        inst = svc.store.conn.execute(
            "SELECT * FROM instances WHERE agent_id=? AND phase IN "
            "('live','starting')", (agent_id,)).fetchone()
        if inst:
            with svc.store.write() as cur:
                cur.execute("UPDATE instances SET observation=? WHERE "
                            "instance_id=?", (action, inst["instance_id"]))
        st["observation"] = action
        FixtureAdapter._save(svc, agent_id, st)
    elif action == "crash":
        from .runtime_ops import confirm_crash
        res = confirm_crash(svc, agent_id)
        events.append(res)
    elif action == "stop-confirm":
        from .runtime_ops import confirm_stop
        events.append(confirm_stop(svc, agent_id))
    elif action in ("inspect-off", "events-off", "model-hot-off"):
        key = action.replace("-off", "")
        st[key] = False
        FixtureAdapter._save(svc, agent_id, st)
    else:
        raise HiveError("INVALID_ARGUMENT", f"未知action: {action}")
    return {"fixture_state": FixtureAdapter.state(svc, agent_id), "events": events}
