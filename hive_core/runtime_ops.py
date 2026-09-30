"""Instance lifecycle operations per DESIGN §4: start/wake/send/stop/restart/model.

Slot invariant INV-01: exactly one live slot per agent; concurrent starts merge;
unknown stop outcomes retain the slot.
"""
from __future__ import annotations

import json
import os

from .errors import HiveError
from .ids import new_id
from .timeutil import format_rfc3339, now_utc


def _new_op(svc, cur, subject, action: str, target: str, args: dict,
            reason: str, rid: str | None, callback=None,
            expected_version=None, expected_instance=None) -> str:
    op_id = new_id("operation")
    cur.execute(
        "INSERT INTO operations(operation_id,request_id,actor,action,target,"
        "expected_version,expected_instance,args,reason,state,callback,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,'accepted',?,?)",
        (op_id, rid or new_id("request"), subject.ref, action, target,
         expected_version, expected_instance,
         json.dumps(args, ensure_ascii=False), reason, callback,
         format_rfc3339(now_utc())))
    return op_id


def _finish_op(svc, cur, op_id: str, state: str, result: dict | None = None,
               error: dict | None = None, outcome_unknown: bool = False,
               started: bool = True):
    cur.execute(
        "UPDATE operations SET state=?, result=?, error=?, outcome_unknown=?, "
        "started_at=COALESCE(started_at,?), ended_at=? WHERE operation_id=?",
        (state, json.dumps(result, ensure_ascii=False) if result else None,
         json.dumps(error, ensure_ascii=False) if error else None,
         1 if outcome_unknown else 0,
         format_rfc3339(now_utc()) if started else None,
         format_rfc3339(now_utc()) if state in ("succeeded", "failed", "cancelled")
         else None, op_id))
    from .waits import advance_operation_waits
    advance_operation_waits(svc, cur, op_id)


def live_instance(svc, agent_id: str):
    return svc.store.conn.execute(
        "SELECT * FROM instances WHERE agent_id=? AND phase IN "
        "('starting','live','stopping') ORDER BY started_at DESC",
        (agent_id,)).fetchone()


def ensure_workspace(svc, agent_id: str, cfg: dict):
    """Rebuild derived workspace files (config.json/prompt.md) from the
    effective revision if missing — AC-ID-04: agents never self-repair."""
    from .agents import _rebuild_workspace
    row = svc.store.conn.execute("SELECT current_revision FROM agents WHERE "
                                 "agent_id=?", (agent_id,)).fetchone()
    ws = cfg.get("workspace", {}).get("path")
    if not ws or not os.path.isdir(ws):
        return
    if not os.path.isfile(os.path.join(ws, "config.json")) or \
            not os.path.isfile(os.path.join(ws, "prompt.md")):
        _rebuild_workspace(svc, agent_id, cfg, row["current_revision"])


def _build_startup_context(svc, agent_id: str, cfg: dict) -> str:
    from .contextgen import gather_context
    from .agents import build_context_summary
    parts = []
    ws = cfg.get("workspace", {}).get("path")
    ctx = gather_context(svc, agent_id, cfg)
    if ctx.get("too_large"):
        raise HiveError("CONTEXT_TOO_LARGE",
                        "工位上下文超过 context_bytes_limit",
                        {"files": ctx.get("offenders", []),
                         "limit": svc.policy().get("context_bytes_limit")})
    if ctx.get("workspace_missing"):
        raise HiveError("WORKSPACE_UNAVAILABLE",
                        f"工位目录不存在: {ctx['workspace_missing']}")
    # system protocol + individual prompt (generated, always current);
    # both MUST exist in the effective workspace (DEF-01: missing protocol
    # in the startup brief is an error, never silently skipped)
    agent_md = os.path.join(ws, "agent.md")
    prompt_md = os.path.join(ws, "prompt.md")
    for p in (agent_md, prompt_md):
        if not os.path.isfile(p):
            raise HiveError("WORKSPACE_UNAVAILABLE",
                            f"工位缺少系统派生文件: {p}",
                            {"expected": p, "workspace": ws})
        with open(p, "r", encoding="utf-8") as f:
            parts.append(f.read())
    for fobj in ctx["files"]:
        parts.append(f"\n\n--- 工位文件 {fobj['name']} ---\n{fobj['content']}")
    missing = ctx.get("missing") or []
    parts.append("\n\n--- 实时责任摘要 ---\n" + build_context_summary(svc, agent_id))
    parts.append(f"\n\n[上下文加载报告] 加载文件: "
                 f"{[f['name'] for f in ctx['files']]}; 缺失额外入口: {missing}"
                 if missing else "")
    return "\n".join(parts), missing


def op_start_agent(svc, subject, agent_id: str, callback, rid=None,
                   model_override=None) -> dict:
    from .agents import get_agent, get_agent_config
    row = get_agent(svc, agent_id)
    if row["lifecycle"] == "retired":
        raise HiveError("AGENT_RETIRED", "retired Agent 不能启动")
    # paused -> enabled on explicit start
    with svc.store.write() as cur:
        if row["lifecycle"] == "paused":
            cur.execute("UPDATE agents SET lifecycle='enabled', updated_at=? "
                        "WHERE agent_id=?",
                        (format_rfc3339(now_utc()), agent_id))
        elif row["lifecycle"] == "retiring":
            cur.execute("UPDATE agents SET lifecycle='enabled', updated_at=? "
                        "WHERE agent_id=?",
                        (format_rfc3339(now_utc()), agent_id))
    existing = live_instance(svc, agent_id)
    if existing:
        return {"operation_id": None, "instance_id": existing["instance_id"],
                "already_running": True,
                "phase": existing["phase"]}
    # only genuinely-accepted (not yet executed) starts merge; 'running'
    # ops with outcome_unknown are stale once the slot is free — supersede
    pending = svc.store.conn.execute(
        "SELECT * FROM operations WHERE action='agent.start' AND target=? AND "
        "state='accepted'", (f"agent:{agent_id}",)).fetchone()
    if pending:
        return {"operation_id": pending["operation_id"], "instance_id": None,
                "already_running": True, "accepted_only": True}
    with svc.store.write() as cur:
        cur.execute(
            "UPDATE operations SET state='failed', ended_at=?, error=? WHERE "
            "action='agent.start' AND target=? AND state='running'",
            (svc.now(), json.dumps({"code": "RUNTIME_OUTCOME_UNKNOWN",
                                    "message": "槽位已释放，陈旧启动操作失效"}),
             f"agent:{agent_id}"))
    cfg = get_agent_config(svc, agent_id)
    harness = cfg.get("runtime", {}).get("harness") or \
        svc.policy().get("default_harness")
    if not harness:
        raise HiveError("HARNESS_NOT_CONFIGURED",
                        "Agent未配置harness且系统无默认harness；注册与收件不受影响")
    model = model_override or cfg.get("runtime", {}).get("model") or \
        svc.policy().get("default_model")
    instance_id = new_id("instance")
    now = format_rfc3339(now_utc())
    gen = svc.store.conn.execute(
        "SELECT COALESCE(MAX(generation),0) g FROM instances WHERE agent_id=?",
        (agent_id,)).fetchone()["g"] + 1
    # occupy the unique slot first (concurrent starts collide here)
    with svc.store.write() as cur:
        cur.execute(
            "INSERT INTO instances(instance_id,agent_id,harness,handle,"
            "config_revision,generation,phase,observation,started_at) "
            "VALUES (?,?,?,?,?,?,'starting','unknown',?)",
            (instance_id, agent_id, harness, None,
             svc.store.conn.execute("SELECT current_revision FROM agents WHERE "
                                    "agent_id=?", (agent_id,)).fetchone()[
                 "current_revision"], gen, now))
        op_id = _new_op(svc, cur, subject, "agent.start", f"agent:{agent_id}",
                        {"harness": harness, "model": model}, "start", rid,
                        callback)
    try:
        ensure_workspace(svc, agent_id, cfg)
        context, missing = _build_startup_context(svc, agent_id, cfg)
    except HiveError as e:
        with svc.store.write() as cur:
            cur.execute("UPDATE instances SET phase='failed', ended_at=?, error=? "
                        "WHERE instance_id=?",
                        (svc.now(), e.message, instance_id))
            _finish_op(svc, cur, op_id, "failed", error=e.to_dict())
        raise
    warnings = [f"上下文额外入口缺失: {m}" for m in missing] if missing else []
    # execution_root (runtime.options.execution_root): the host session
    # runs in a shared project root while workspace.path stays the agent's
    # private workstation (config/derived files/context/uniqueness are
    # still keyed to the workstation). No execution_root -> legacy
    # behaviour unchanged.
    rt_opts = cfg.get("runtime", {}).get("options") or {}
    workstation = cfg.get("workspace", {}).get("path")
    execution_root = rt_opts.get("execution_root")
    registry = svc.harnesses
    enriched_context = None
    startup_file_path = None
    try:
        cred_dir = os.path.join(svc.home, "runtime", "credentials")
        # real harnesses: build the full startup brief (context + connection
        # info), persist it as STARTUP.md and pass it to the adapter
        try:
            kind0, spec0 = registry.adapter_for(harness)
        except HiveError:
            kind0, spec0 = "process", {}
        if kind0 in ("process", "zsession"):
            wsdir = cfg.get("workspace", {}).get("path")
            if wsdir and os.path.isdir(wsdir):
                _ctx_text, _missing0 = _build_startup_context(svc, agent_id,
                                                              cfg)
                from .home import read_system_json
                _listen = read_system_json(svc.home).get("listen",
                                                         "127.0.0.1:19962")
                _cli = os.path.join(os.path.dirname(os.path.dirname(
                    os.path.abspath(__file__))), "cli", "hive.py")
                _cred_file = os.path.join(cred_dir, f"{instance_id}.json")
                _ctx_text += (
                    "\n\n## 运行接入信息（实例凭据为文件路径，非秘密正文）\n"
                    f"- CLI 入口: python \"{_cli}\"\n"
                    f"- 服务地址: http://{_listen}\n"
                    f"- 本实例凭据文件: {_cred_file}\n"
                    f"- 用法示例: python \"{_cli}\" --home \"{svc.home}\" "
                    f"--endpoint http://{_listen} --credential-file "
                    f"\"{_cred_file}\" inbox list\n"
                    f"- PROJECT_ROOT（会话运行根目录/实际cwd）: "
                    f"{execution_root or wsdir}\n"
                    f"- WORKSTATION_ROOT（本Agent独立工位）: {wsdir}\n")
                # DEF-02: the brief is a per-instance runtime artifact — it
                # lives under home/runtime/startups/, never in the business
                # workspace, so business-md scanning can never re-ingest it
                startup_dir = os.path.join(svc.home, "runtime", "startups",
                                           agent_id)
                os.makedirs(startup_dir, exist_ok=True)
                startup_file = os.path.join(startup_dir, f"{instance_id}.md")
                with open(startup_file, "w", encoding="utf-8") as f:
                    f.write(_ctx_text)
                enriched_context = _ctx_text
                startup_file_path = startup_file
        os.makedirs(cred_dir, exist_ok=True)
        start_options = {
            "model": model, "yolo": bool(cfg.get("runtime", {}).get("yolo", True)),
            "workspace": execution_root or workstation,
            "workstation_path": workstation,
            "startup_context": context,
            "credential_file_hint": os.path.join(cred_dir, f"{instance_id}.json"),
            "log_path": os.path.join(svc.home, "logs",
                                     f"instance-{agent_id}-{instance_id}.log"),
        }
        if execution_root:
            start_options["execution_root"] = execution_root
        if rt_opts.get("session_name"):
            start_options["session_name"] = rt_opts["session_name"]
        if enriched_context is not None:
            start_options["startup_context"] = enriched_context
        if startup_file_path is not None:
            start_options["startup_file"] = startup_file_path
        res = registry.start(svc, agent_id, harness, start_options)
    except HiveError as e:
        with svc.store.write() as cur:
            cur.execute("UPDATE instances SET phase='failed', ended_at=?, error=? "
                        "WHERE instance_id=?", (svc.now(), e.message, instance_id))
            _finish_op(svc, cur, op_id, "failed", error=e.to_dict())
        raise
    handle = res.get("handle")
    start_fault = svc.fault_active("harness_start_after_launch", entity=agent_id)
    if start_fault:
        svc.consume_fault("harness_start_after_launch")  # disarm once-mode
    cred_path = os.path.join(cred_dir, f"{instance_id}.json")
    with svc.store.write() as cur:
        from .auth import issue_credential
        agent_subject = agent_id  # subject id mirrors agent id
        cur.execute("INSERT OR IGNORE INTO subjects(subject_id,subject_type,name,"
                    "created_at,metadata) VALUES (?,?,?,?,?)",
                    (agent_subject, "agent", agent_id, now,
                     json.dumps({"agent_id": agent_id})))
        issue_credential(cur, agent_subject, kind="instance",
                         instance_id=instance_id, out_path=cred_path)
        cur.execute(
            "UPDATE instances SET handle=?, phase='live', observation='unknown' "
            "WHERE instance_id=?", (handle, instance_id))
        cur.execute("UPDATE agents SET applied_revision=(SELECT current_revision "
                    "FROM agents WHERE agent_id=?) WHERE agent_id=?",
                    (agent_id, agent_id))
        if start_fault:
            _finish_op(svc, cur, op_id, "running",
                       result={"instance_id": instance_id, "handle": handle,
                               "note": "启动响应丢失，凭受管记录待核实"},
                       outcome_unknown=True)
        else:
            _finish_op(svc, cur, op_id, "succeeded",
                       result={"instance_id": instance_id, "handle": handle,
                               "credential_file": cred_path, "model": model})
        # RDEF-03: a successful explicit start clears any restart plan
        # (manual intervention resolves scheduled/failed/awaiting states)
        svc.store.kv_set(f"restart:{agent_id}", {})
        svc.store.add_event(cur, entity_type="agent", entity_id=agent_id,
                            entity_version=gen, kind="agent.started",
                            actor=subject.ref,
                            payload={"instance_id": instance_id,
                                     "harness": harness, "model": model})
    return {"operation_id": op_id, "instance_id": instance_id,
            "already_running": False, "warnings": warnings,
            "outcome_unknown": start_fault}


def op_wake_agent(svc, subject, agent_id: str, message: str) -> dict:
    from .agents import get_agent, get_agent_config
    row = get_agent(svc, agent_id)
    cfg = get_agent_config(svc, agent_id)
    if row["lifecycle"] == "paused":
        return {"operation_id": None, "coalesced": False, "reason":
                "AGENT_PAUSED", "note": "paused 状态不自动启用"}
    if row["lifecycle"] in ("retired", "retiring"):
        return {"operation_id": None, "coalesced": False, "reason":
                f"AGENT_{row['lifecycle'].upper()}"}
    existing = live_instance(svc, agent_id)
    if existing:
        # merge into a pending notification (dedupe per agent)
        _queue_notification(svc, agent_id, message or "请查看inbox",
                            dedupe="wake")
        return {"operation_id": None, "coalesced": True,
                "instance_id": existing["instance_id"]}
    if not cfg.get("runtime", {}).get("auto_start", True):
        return {"operation_id": None, "coalesced": False,
                "reason": "AUTO_START_DISABLED"}
    return op_start_agent(svc, subject, agent_id, None,
                          rid=f"wake-{new_id('request')[4:12]}")


def _queue_notification(svc, agent_id: str, message: str, dedupe: str):
    with svc.store.write() as cur:
        cur.execute(
            "INSERT OR IGNORE INTO notifications(notify_id,agent_id,dedupe_key,"
            "message,state,created_at) VALUES (?,?,?,?,'pending',?)",
            (new_id("view"), agent_id, dedupe, message, svc.now()))


def op_send_agent(svc, subject, agent_id: str, args, rid) -> dict:
    from .agents import get_agent, get_agent_config
    row = get_agent(svc, agent_id)
    svc.authz.require(subject, "agent.send", f"agent:{agent_id}")
    if row["lifecycle"] == "paused" and subject.subject_type != "owner":
        raise HiveError("AGENT_PAUSED", "paused 默认拒绝运行指令（不暗中start）")
    inst = live_instance(svc, agent_id)
    if args.get("instance") and (not inst or
                                 inst["instance_id"] != args["instance"]):
        raise HiveError("VERSION_CONFLICT", "实例ID不匹配当前实例")
    mode = args.get("mode", "queue")
    text = args.get("text") or ""
    if mode == "interrupt":
        cfg = get_agent_config(svc, agent_id)
        harness = cfg.get("runtime", {}).get("harness")
        caps = svc.harnesses.get_spec(harness).get("capabilities", {}) \
            if harness else {}
        # D-05 fix: interrupt may be declared in optional_capabilities
        # (zcode-zsession declares it there) — honor both places.
        opt_caps = (svc.harnesses.get_spec(harness).get(
            "optional_capabilities", {}) if harness else {})
        # unified native-interrupt key: steer OR interrupt (zsession declares
        # interrupt:true) — a declared capability must actually be used
        native_interrupt = bool(caps.get("steer") or caps.get("interrupt")
                                or opt_caps.get("steer")
                                or opt_caps.get("interrupt"))
        # fixture: model_hot/steer not native -> no interrupt unless restart
        fixture_hot = False
        if harness and svc.harnesses.get_spec(harness).get("adapter") == "fixture":
            from .harness import FixtureAdapter
            fixture_hot = FixtureAdapter.capabilities(svc, agent_id).get("events")
        if native_interrupt or fixture_hot:
            result = {"delivery_mode": "interrupt"}
            with svc.store.write() as cur:
                op_id = _new_op(svc, cur, subject, "agent.send",
                                f"agent:{agent_id}", {"mode": mode, "text": text},
                                args.get("reason", ""), rid)
            send_mode = "interrupt" if native_interrupt else "queue"
            svc.harnesses.send(svc, agent_id, harness, inst["handle"], text,
                               op_id, mode=send_mode, wait_idle=True)
            with svc.store.write() as cur:
                _finish_op(svc, cur, op_id, "succeeded", result)
            return {"operation_id": op_id, "delivery_mode": "interrupt",
                    "degraded": False}
        if args.get("fallback") == "restart":
            stop = op_stop_agent(svc, subject, agent_id, args.get("reason", ""),
                                 grace="0s", force=True,
                                 instance_expected=args.get("instance"))
            start = op_start_agent(svc, subject, agent_id, None)
            new_inst = start.get("instance_id")
            if new_inst:
                inst2 = live_instance(svc, agent_id)
                svc.harnesses.send(svc, agent_id, harness, inst2["handle"], text,
                                   "restart-send")
            return {"operation_id": stop.get("operation_id"),
                    "delivery_mode": "restart-then-send", "degraded": True,
                    "new_instance_id": new_inst}
        raise HiveError("HARNESS_UNAVAILABLE",
                        "无原生interrupt支持且fallback=reject", {"degraded": False})
    # queue mode: live instance gets the message directly (wake semantics);
    # otherwise a durable notification brings the agent up via cold start
    inst_q = live_instance(svc, agent_id)
    if inst_q:
        result = "unknown"
        try:
            result = svc.harnesses.send(svc, agent_id, inst_q["harness"],
                                        inst_q["handle"],
                                        text or "请查看inbox", "send-direct",
                                        wait_idle=True)
        except Exception as e:  # noqa: BLE001
            svc.logger.warning("send.direct.failed", agent=agent_id,
                               error=repr(e))
        delivery = "send" if result == "accepted" else "queued-retry"
        if result != "accepted":
            _queue_notification(svc, agent_id, text or "请查看inbox",
                                dedupe="send")
        return {"operation_id": None, "delivery_mode": delivery,
                "degraded": False, "send_result": result}
    _queue_notification(svc, agent_id, text or "请查看inbox", dedupe="send")
    return {"operation_id": None, "delivery_mode": "queue", "degraded": False}


def _grace_seconds(grace, force: bool) -> float:
    if force or grace in (None, ""):
        return 0.0
    from .timeutil import parse_duration
    try:
        return float(parse_duration(str(grace)))
    except (ValueError, TypeError):
        return 0.0


def _grace_stop(svc, subject, agent_id: str, inst, op_id: str,
                grace_s: float, grace_raw, reason: str,
                then_start: bool = False) -> dict:
    """RDEF-09: the grace window is REAL — hint, wait, then terminate.

    Accepted asynchronously: the instance goes 'stopping' (no new wake
    delivery; claims and progress commits keep working under existing
    permissions, credentials stay valid until termination), a save/handoff
    hint is sent best-effort, and a persistent kv plan lets the scheduler
    terminate at the deadline (also across daemon restarts)."""
    import datetime
    from .timeutil import parse_rfc3339
    now = svc.now()
    deadline = format_rfc3339(parse_rfc3339(now)
                              + datetime.timedelta(seconds=grace_s))
    with svc.store.write() as cur:
        cur.execute("UPDATE instances SET phase='stopping' WHERE "
                    "instance_id=?", (inst["instance_id"],))
    hint = (f"[系统] 实例将在 {grace_raw} 后停止（期限 {deadline}）：请立即保存"
            "进度、写交接说明并停止领取新工作；持有任务保留，权限在终止前有效。")
    hint_sent = True
    try:
        svc.harnesses.send(svc, agent_id, inst["harness"], inst["handle"],
                           hint, op_id)
    except Exception as e:  # noqa: BLE001
        hint_sent = False
        svc.logger.warning("grace.hint_failed", agent=agent_id,
                           error=repr(e)[:120])
    svc.store.kv_set(f"gracestop:{op_id}", {
        "agent_id": agent_id, "instance_id": inst["instance_id"],
        "op_id": op_id, "deadline": deadline, "grace": grace_raw,
        "hint_sent": hint_sent, "then_start": then_start,
        "actor": subject.ref, "reason": reason,
        "harness": inst["harness"], "handle": inst["handle"]})
    with svc.store.write() as cur:
        _finish_op(svc, cur, op_id, "running",
                   result={"instance_id": inst["instance_id"],
                           "grace": grace_raw, "grace_deadline": deadline,
                           "hint_sent": hint_sent,
                           "note": "已受理：宽限期内提示保存，到期终止"})
    return {"operation_id": op_id, "instance_id": inst["instance_id"],
            "lifecycle": "enabled", "instance_stop_result": "grace",
            "grace": grace_raw, "grace_deadline": deadline,
            "hint_sent": hint_sent, "grace_pending": True}


def op_stop_agent(svc, subject, agent_id: str, reason: str, grace=None,
                  force=False, instance_expected=None, rid=None) -> dict:
    from .agents import get_agent
    row = get_agent(svc, agent_id)
    inst = live_instance(svc, agent_id)
    with svc.store.write() as cur:
        op_id = _new_op(svc, cur, subject, "agent.stop", f"agent:{agent_id}",
                        {"grace": grace, "force": force}, reason, rid)
    if not inst:
        with svc.store.write() as cur:
            cur.execute("UPDATE agents SET lifecycle='paused', updated_at=? "
                        "WHERE agent_id=?", (format_rfc3339(now_utc()), agent_id))
            _finish_op(svc, cur, op_id, "succeeded",
                       {"note": "无运行实例", "lifecycle": "paused"})
        return {"operation_id": op_id, "lifecycle": "paused",
                "instance_stop_result": "none"}
    if instance_expected and inst["instance_id"] != instance_expected:
        with svc.store.write() as cur:
            _finish_op(svc, cur, op_id, "failed",
                       error={"code": "VERSION_CONFLICT",
                              "message": "实例ID不匹配当前实例"})
        raise HiveError("VERSION_CONFLICT", "实例ID不匹配当前实例")
    grace_s = _grace_seconds(grace, force)
    if grace_s > 0:
        # a grace window already pending for this instance merges into it
        for row in svc.store.conn.execute(
                "SELECT k, v FROM kv WHERE k LIKE 'gracestop:%'"):
            import json as _jg
            try:
                gst = _jg.loads(row["v"])
            except ValueError:
                continue
            if gst.get("instance_id") == inst["instance_id"]:
                return {"operation_id": gst.get("op_id"),
                        "instance_id": inst["instance_id"],
                        "lifecycle": "enabled",
                        "instance_stop_result": "grace",
                        "grace_deadline": gst.get("deadline"),
                        "grace_pending": True,
                        "note": "已有宽限停止在进行中，合并受理"}
        if inst["phase"] != "stopping":
            return _grace_stop(svc, subject, agent_id, inst, op_id, grace_s,
                               str(grace), reason)
    cfg_harness = inst["harness"]
    try:
        stop_result = svc.harnesses.stop(svc, agent_id, cfg_harness,
                                         inst["handle"], op_id)
    except HiveError as e:
        with svc.store.write() as cur:
            _finish_op(svc, cur, op_id, "failed", error=e.to_dict())
        raise
    now = svc.now()
    if stop_result == "stopped":
        with svc.store.write() as cur:
            cur.execute("UPDATE instances SET phase='stopped', ended_at=?, "
                        "observation='idle' WHERE instance_id=?",
                        (now, inst["instance_id"]))
            svc.authn.revoke_instance_credentials(cur, inst["instance_id"])
            cur.execute("UPDATE agents SET lifecycle='paused', updated_at=? "
                        "WHERE agent_id=?", (now, agent_id))
            _finish_op(svc, cur, op_id, "succeeded",
                       {"instance_id": inst["instance_id"], "stopped": True,
                        "lifecycle": "paused"})
            svc.store.add_event(cur, entity_type="agent", entity_id=agent_id,
                                entity_version=inst["generation"],
                                kind="agent.stopped", actor=subject.ref,
                                payload={"instance_id": inst["instance_id"]})
        return {"operation_id": op_id, "lifecycle": "paused",
                "instance_stop_result": "stopped",
                "instance_id": inst["instance_id"]}
    if stop_result == "unknown":
        with svc.store.write() as cur:
            cur.execute("UPDATE instances SET phase='stopping' WHERE instance_id=?",
                        (inst["instance_id"],))
            _finish_op(svc, cur, op_id, "running",
                       {"instance_id": inst["instance_id"],
                        "note": "停止结果未知，槽位保留"},
                       outcome_unknown=True)
        return {"operation_id": op_id, "lifecycle": "paused",
                "instance_stop_result": "unknown",
                "outcome_unknown": True}
    with svc.store.write() as cur:
        cur.execute("UPDATE instances SET phase='failed', ended_at=?, error=? "
                    "WHERE instance_id=?", (now, "stop failed", inst["instance_id"]))
        _finish_op(svc, cur, op_id, "failed",
                   error={"code": "HARNESS_UNAVAILABLE", "message": "stop failed"})
    raise HiveError("HARNESS_UNAVAILABLE", "停止失败")


def confirm_stop(svc, agent_id: str):
    """test harness stop-confirm: release an unknown-stop slot."""
    inst = live_instance(svc, agent_id)
    if not inst or inst["phase"] != "stopping":
        return {"released": False}
    with svc.store.write() as cur:
        cur.execute("UPDATE instances SET phase='stopped', ended_at=? WHERE "
                    "instance_id=?", (svc.now(), inst["instance_id"]))
        svc.authn.revoke_instance_credentials(cur, inst["instance_id"])
        cur.execute("UPDATE operations SET state='succeeded', ended_at=?, "
                    "result=COALESCE(result, '{}') WHERE action='agent.stop' AND "
                    "target=? AND state='running'", (svc.now(), f"agent:{agent_id}"))
    return {"released": True, "instance_id": inst["instance_id"]}


def op_restart_agent(svc, subject, agent_id: str, reason: str, grace=None,
                     force=False, instance_expected=None, rid=None) -> dict:
    from .agents import get_agent
    get_agent(svc, agent_id)
    old = live_instance(svc, agent_id)
    old_id = old["instance_id"] if old else None
    grace_s = _grace_seconds(grace, force)
    if old and grace_s > 0 and old["phase"] != "stopping":
        # graceful replacement: the grace stop is accepted async; the
        # scheduler starts the replacement instance right after the old
        # instance terminates at the deadline
        with svc.store.write() as cur:
            op_id = _new_op(svc, cur, subject, "agent.restart",
                            f"agent:{agent_id}",
                            {"grace": grace, "force": force,
                             "mode": "grace"}, reason, rid)
        res = _grace_stop(svc, subject, agent_id, old, op_id, grace_s,
                          str(grace), reason, then_start=True)
        return {"operation_id": op_id, "old_instance_id": old_id,
                "new_instance_id": None, "grace_pending": True,
                "note": "宽限停止已受理，到期终止后自动启动替身实例"}
    if old:
        if instance_expected and old["instance_id"] != instance_expected:
            raise HiveError("VERSION_CONFLICT", "实例ID不匹配当前实例")
        stop = op_stop_agent(svc, subject, agent_id, reason, grace, force,
                             instance_expected)
        if stop.get("instance_stop_result") == "unknown":
            return {"operation_id": stop["operation_id"], "old_instance_id": old_id,
                    "new_instance_id": None, "outcome_unknown": True,
                    "note": "旧实例停止结果未知，暂不启动替身"}
    # restart makes the agent enabled
    with svc.store.write() as cur:
        cur.execute("UPDATE agents SET lifecycle='enabled', updated_at=? "
                    "WHERE agent_id=?", (format_rfc3339(now_utc()), agent_id))
    start = op_start_agent(svc, subject, agent_id, None)
    result = {"operation_id": start.get("operation_id"), "old_instance_id": old_id,
              "new_instance_id": start.get("instance_id")}
    if start.get("warnings"):
        result["warnings"] = start["warnings"]
    if start.get("outcome_unknown"):
        result["outcome_unknown"] = True
    return result


def op_model_agent(svc, subject, agent_id: str, args, rid) -> dict:
    from .agents import get_agent, get_agent_config, update_agent
    get_agent(svc, agent_id)
    svc.authz.require(subject, "agent.update", f"agent:{agent_id}")
    if (args.get("model") is None) and not args.get("default"):
        raise HiveError("REQUIRED_ARGUMENT", "需要 --model 或 --default")
    cfg = get_agent_config(svc, agent_id)
    apply_mode = args.get("apply", "auto")
    row = svc.store.conn.execute("SELECT current_revision FROM agents WHERE "
                                 "agent_id=?", (agent_id,)).fetchone()
    if args.get("if_version") is not None and \
            args["if_version"] != row["current_revision"]:
        raise HiveError("VERSION_CONFLICT", "配置版本冲突")
    new_model = None if args.get("default") else args["model"]
    patch = {"runtime": dict(cfg.get("runtime") or {})}
    patch["runtime"]["model"] = new_model
    # direct config publish without full update_agent reason plumbing
    from .agents import _publish_config, get_agent_config as gac
    cur_cfg = gac(svc, agent_id)
    cur_cfg["runtime"]["model"] = new_model
    lifecycle = svc.store.conn.execute("SELECT lifecycle FROM agents WHERE "
                                       "agent_id=?", (agent_id,)).fetchone()["lifecycle"]
    paused = lifecycle == "paused"
    with svc.store.write() as cur:
        revision = _publish_config(svc, cur, agent_id, cur_cfg,
                                   row["current_revision"], subject.ref,
                                   args["reason"], rid)
    harness = cur_cfg.get("runtime", {}).get("harness")
    hot = False
    if harness and svc.harnesses.get_spec(harness).get("adapter") == "fixture":
        from .harness import FixtureAdapter
        hot = FixtureAdapter.capabilities(svc, agent_id).get("model_hot")
    if paused or apply_mode == "next-start":
        return {"operation_id": None, "desired_model": new_model,
                "apply_mode": "next-start", "pending": True}
    inst = live_instance(svc, agent_id)
    if not inst:
        return {"operation_id": None, "desired_model": new_model,
                "apply_mode": "next-start", "pending": True,
                "note": "无运行实例，下次start生效"}
    if apply_mode == "auto" and hot:
        svc.harnesses.send(svc, agent_id, harness, inst["handle"],
                           f"[config] model -> {new_model}", "model-hot")
        return {"operation_id": None, "desired_model": new_model,
                "apply_mode": "hot", "pending": False}
    # restart-based switch (auto without hot / explicit restart)
    res = op_restart_agent(svc, subject, agent_id, args["reason"], force=True)
    return {"operation_id": res.get("operation_id"), "desired_model": new_model,
            "apply_mode": "restart", "pending": False,
            "new_instance_id": res.get("new_instance_id")}


def confirm_crash(svc, agent_id: str) -> dict:
    """test harness crash: confirmed exit; maybe auto-restart per policy."""
    from .agents import get_agent, get_agent_config
    row = get_agent(svc, agent_id)
    inst = live_instance(svc, agent_id)
    if not inst:
        return {"crashed": False}
    cfg = get_agent_config(svc, agent_id)
    with svc.store.write() as cur:
        cur.execute("UPDATE instances SET phase='failed', ended_at=?, error="
                    "'crash (confirmed)', observation='unknown' WHERE instance_id=?",
                    (svc.now(), inst["instance_id"]))
        svc.authn.revoke_instance_credentials(cur, inst["instance_id"])
        svc.store.add_event(cur, entity_type="agent", entity_id=agent_id,
                            entity_version=inst["generation"], kind="agent.crashed",
                            payload={"instance_id": inst["instance_id"]})
        _fail_running_ops(cur, agent_id)
    if row["lifecycle"] == "enabled" and cfg.get("runtime", {}).get(
            "restart_on_exit", True):
        return {"crashed": True, **_schedule_crash_restart(svc, agent_id)}
    return {"crashed": True, "restarted": False}


def _schedule_crash_restart(svc, agent_id: str) -> dict:
    """RDEF-03: the restart plan is a RECOVERABLE state machine persisted in
    kv restart:<agent> — {attempts, next_allowed_at, state, fail_count,
    last_error, started_at, op_id}. Scheduling only sets state=scheduled;
    the scheduler's transition to starting (and back on failure) is atomic
    per tick, so a crash between consume and start, or a failing start,
    leaves a plan that continues instead of vanishing."""
    import datetime
    from .timeutil import parse_rfc3339
    pol = svc.policy()
    backoff = pol.get("restart_backoff") or [5, 15, 60]
    max_restart = pol.get("restart_max_per_hour", 3)
    now = svc.now()
    cutoff = format_rfc3339(parse_rfc3339(now) - datetime.timedelta(hours=1))
    recent = svc.store.conn.execute(
        "SELECT COUNT(*) c FROM events WHERE entity_type='agent' AND "
        "entity_id=? AND kind='agent.started' AND occurred_at>=?",
        (agent_id, cutoff)).fetchone()["c"]
    if recent >= max_restart:
        with svc.store.write() as cur:
            svc.store.add_event(cur, entity_type="agent", entity_id=agent_id,
                                kind="agent.restart_limit",
                                payload={"recent": recent})
        _prev = svc.store.kv_get(f"restart:{agent_id}", {}) or {}
        svc.store.kv_set(f"restart:{agent_id}",
                         {"attempts": int(_prev.get("attempts", 0) or 0),
                          "state": "awaiting_intervention",
                          "fail_count": 0, "last_error":
                          "RESTART_LIMIT_EXCEEDED", "next_allowed_at": None,
                          "started_at": None, "op_id": None})
        return {"restarted": False, "reason": "RESTART_LIMIT_EXCEEDED"}
    st = svc.store.kv_get(f"restart:{agent_id}", {}) or {}
    attempts = int(st.get("attempts", 0) or 0) + 1
    step = backoff[min(attempts - 1, len(backoff) - 1)]
    next_allowed = format_rfc3339(
        parse_rfc3339(now) + datetime.timedelta(seconds=step))
    svc.store.kv_set(f"restart:{agent_id}", {
        "attempts": attempts, "next_allowed_at": next_allowed,
        "state": "scheduled", "fail_count": int(st.get("fail_count", 0) or 0),
        "last_error": None, "started_at": None, "op_id": None})
    with svc.store.write() as cur:
        svc.store.add_event(cur, entity_type="agent", entity_id=agent_id,
                            kind="agent.restart_scheduled",
                            payload={"attempts": attempts, "backoff_s": step,
                                     "next_allowed_at": next_allowed})
    return {"restarted": False, "restart_scheduled": True,
            "attempts": attempts, "backoff_s": step,
            "next_allowed_at": next_allowed}


def _fail_running_ops(cur, agent_id: str):
    cur.execute("UPDATE operations SET state='failed', error=?, ended_at=? WHERE "
                "target=? AND state IN ('accepted','running')",
                (json.dumps({"code": "RUNTIME_OUTCOME_UNKNOWN",
                             "message": "实例崩溃"}), format_rfc3339(now_utc()),
                 f"agent:{agent_id}"))
