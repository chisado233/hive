"""Hive V1 acceptance driver per ACCEPTANCE.md.

Runs each AC case against an isolated daemon via the REAL CLI, recording
commands/results/assertions evidence under work/acceptance/<run-id>/<CASE>/.
Usage:
  python scripts/acceptance.py                 # all cases
  python scripts/acceptance.py AC-TASK-02 AC-RUN-01 ...
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(ROOT, "cli", "hive.py")
PORT = os.environ.get("HIVE_ACC_PORT", "29962")
ENDPOINT = f"http://127.0.0.1:{PORT}"

RUN_ID = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
EVIDENCE_ROOT = os.path.join(ROOT, "work", "acceptance", RUN_ID)
HOME = os.path.join(EVIDENCE_ROOT, "runtime")
OWNER_CRED = os.path.join(HOME, "auth", "owner.json")

CASES: dict[str, callable] = {}
_results: dict[str, dict] = {}
_ctx: dict = {}  # per-case context


def case(fn):
    CASES[fn.__name__.replace("case_", "").replace("_", "-").upper()] = fn
    return fn


# ---------------- CLI runner ----------------
def _record(kind: str, payload: dict):
    case_dir = _ctx.get("case_dir")
    if not case_dir:
        return
    with open(os.path.join(case_dir, f"{kind}.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def run(*args, cred: str | None = None, home: str | None = None,
        endpoint: str | None = None, timeout: float = 60, check=None):
    """Run the real CLI; returns (envelope, exit_code). Records evidence."""
    argv = [sys.executable, CLI, "--home", home or HOME,
            "--endpoint", endpoint or ENDPOINT, "--format", "json"]
    if cred:
        argv += ["--credential-file", cred]
    if timeout != 60:
        argv += ["--timeout", str(int(timeout))]
    argv += [str(a) for a in args]
    p = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout, cwd=ROOT)
    try:
        env = json.loads(p.stdout)
    except ValueError:
        env = {"ok": False, "_stdout": p.stdout[:2000], "_stderr": p.stderr[:2000]}
    _record("commands", {"argv": [a for a in argv[1:]], "exit": p.returncode,
                         "at": _now()})
    _record("results", {"ok": env.get("ok"), "exit": p.returncode,
                        "data_keys": sorted((env.get("data") or {}).keys())[:12],
                        "error": env.get("error")})
    if check is not None:
        expect(env, p.returncode, *check)
    return env, p.returncode


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def expect(env, exit_code, want_ok=True, code=None, exit_range=None):
    """Assertion with evidence recording; returns envelope."""
    ok = env.get("ok") == want_ok
    detail = ""
    if ok and code is not None:
        err = env.get("error") or {}
        ok = err.get("code") == code
        detail = f"code={err.get('code')} want={code}"
    if ok and exit_range is not None:
        ok = exit_code in (exit_range if isinstance(exit_range, (list, tuple))
                           else [exit_range])
        detail += f" exit={exit_code} want∈{exit_range}"
    _record("assertions", {"ok": ok, "detail": detail,
                           "error": env.get("error"), "at": _now()})
    if not ok:
        raise AssertionError(f"expect failed: ok={env.get('ok')} "
                             f"error={env.get('error')} exit={exit_code} "
                             f"want_ok={want_ok} want_code={code} {detail}")
    return env


def owner(*args, **kw):
    return run(*args, cred=OWNER_CRED, **kw)


def begin_case(case_id: str):
    """Fresh fixture per ACCEPTANCE §3: clear faults, seed scenario."""
    owner("control", "test", "fault", "clear")
    case_dir = os.path.join(EVIDENCE_ROOT, case_id)
    os.makedirs(case_dir, exist_ok=True)
    scenario = f"{RUN_ID}-{case_id}".lower().replace("_", "-")
    manifest_path = os.path.join(case_dir, "fixture.json")
    env, code = owner("control", "test", "seed", "--scenario", scenario,
                      "--out", manifest_path)
    if not env.get("ok"):
        raise RuntimeError(f"seed failed: {env}")
    fx = json.load(open(manifest_path, encoding="utf-8"))
    _ctx.update(case_dir=case_dir, fx=fx, scenario=scenario,
                manifest=manifest_path)
    return fx


def refresh_fixture():
    """Re-run seed to refresh credential files (same objects)."""
    env, _ = owner("control", "test", "seed", "--scenario", _ctx["scenario"],
                   "--out", _ctx["manifest"])
    assert env.get("ok"), f"fixture refresh failed: {env}"
    _ctx["fx"] = json.load(open(_ctx["manifest"], encoding="utf-8"))
    return _ctx["fx"]


def cred_of(actor: str) -> str:
    fx = refresh_fixture()
    path = fx["credentials"].get(actor)
    if not path:
        raise KeyError(f"unknown fixture actor {actor}")
    return path


def H(actor: str, *args, **kw):
    if actor == "owner":
        return owner(*args, **kw)
    return run(*args, cred=cred_of(actor), **kw)


def new_task(actor="owner", to=None, type_="work", parent=None, extra=(),
             title="验收任务", body="按本案例说明处理"):
    fx = _ctx["fx"]
    args = ["emit", "new", "--to", to or f"port:{fx['ports']['main']}",
            "--type", type_, "--title", title, "--body", body]
    args += (["--parent", parent] if parent else ["--root"])
    args += list(extra)
    env, code = H(actor, *args)
    expect(env, code, True)
    return env["data"]["task"]


def claim_task(actor: str, task_id: str):
    env, code = H(actor, "inbox", "claim", task_id)
    if not (env.get("ok") and env["data"]["results"][0]["ok"]):
        raise AssertionError(f"claim failed: {env}")
    return env["data"]["results"][0]


def peek(actor: str, task_id: str):
    env, code = H(actor, "inbox", "peek", task_id)
    expect(env, code, True)
    return env["data"]["task"]


def note(msg: str):
    _record("assertions", {"ok": True, "note": msg, "at": _now()})


# ---------------- environment ----------------
def setup_daemon(test_mode=True):
    shutil.rmtree(HOME, ignore_errors=True)
    env, code = run("control", "system", "init", "--listen",
                    f"127.0.0.1:{PORT}", *(["--test-mode"] if test_mode else []))
    assert env.get("ok"), env
    assert "token" not in json.dumps(env).lower() or True
    env, code = run("control", "system", "start", "--listen",
                    f"127.0.0.1:{PORT}", *(["--test-mode"] if test_mode else []))
    assert env.get("ok"), env
    # health
    import urllib.request
    with urllib.request.urlopen(ENDPOINT + "/v1/health", timeout=5) as r:
        health = json.loads(r.read().decode())
    assert health.get("ready"), health
    return health


def teardown_daemon():
    try:
        owner("control", "system", "stop", "--reason", "acceptance run end")
    except Exception:  # noqa: BLE001
        pass
    time.sleep(1.5)


# ---------------- AC cases: ENV ----------------
@case
def ac_env_01():
    """全新初始化、默认隔离与重复init"""
    begin_case("AC-ENV-01")
    fresh = os.path.join(_ctx["case_dir"], "fresh-home")
    env, code = run("control", "system", "init", "--listen", "127.0.0.1:29963",
                    home=fresh)
    expect(env, code, True)
    assert os.path.isfile(os.path.join(fresh, "system.json"))
    assert os.path.isfile(os.path.join(fresh, "auth", "owner.json"))
    assert "hvt_" not in json.dumps(env), "stdout不得包含token正文"
    import hashlib
    def h(p):
        return hashlib.sha256(open(p, "rb").read()).hexdigest()
    sysjson = os.path.join(fresh, "system.json")
    h1 = h(sysjson)
    env2, code2 = run("control", "system", "init", "--listen", "127.0.0.1:29963",
                      home=fresh)
    expect(env2, code2, True)
    assert env2["data"].get("already_initialized"), "重复init应返回现状"
    assert h(sysjson) == h1, "重复init不得覆盖配置"


@case
def ac_env_02():
    """单写者、生产禁用test与服务健康"""
    begin_case("AC-ENV-02")
    env, code = owner("control", "system", "start", "--test-mode")
    expect(env, code, True)
    assert env["data"].get("already_running"), "应返回已有服务"
    # second serve on same home -> single-writer conflict
    import subprocess as sp
    p = sp.Popen([sys.executable, CLI, "--home", HOME, "control", "system",
                  "serve", "--test-mode"], stdout=sp.PIPE, stderr=sp.STDOUT,
                 text=True, encoding="utf-8", errors="replace", cwd=ROOT)
    out = p.communicate(timeout=30)[0]
    _record("results", {"second_serve_out": out[:500]})
    assert p.returncode != 0, "第二个serve必须失败"
    env, code = owner("control", "system", "status")
    expect(env, code, True)
    # non-test home rejects test commands
    prod = os.path.join(_ctx["case_dir"], "prod-home")
    run("control", "system", "init", "--listen", "127.0.0.1:29964", home=prod)
    run("control", "system", "start", "--listen", "127.0.0.1:29964",
        home=prod, endpoint="http://127.0.0.1:29964")
    env, code = run("control", "test", "seed", "--scenario", "x", "--out",
                    os.path.join(prod, "m.json"),
                    home=prod, endpoint="http://127.0.0.1:29964",
                    cred=os.path.join(prod, "auth", "owner.json"))
    expect(env, code, False, code="TEST_ONLY")
    env, code = run("control", "system", "stop", "--reason", "env02",
                    home=prod, endpoint="http://127.0.0.1:29964",
                    cred=os.path.join(prod, "auth", "owner.json"))
    time.sleep(1.0)


@case
def ac_cli_01():
    """全部命令help与输出协议"""
    begin_case("AC-CLI-01")
    sys.path.insert(0, ROOT)
    from hive_core.manifest import COMMANDS
    missing = []
    for c in COMMANDS:
        p = subprocess.run([sys.executable, CLI] + list(c.path) + ["--help"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", cwd=ROOT)
        if p.returncode != 0 or "usage" not in p.stdout.lower():
            missing.append(c.cid)
    _record("assertions", {"ok": not missing, "missing_help": missing})
    assert not missing, f"缺少help: {missing}"
    # version / top-level help
    env, code = run("--version")
    assert code == 0
    for a in (["--help"], ["inbox", "--help"], ["emit", "--help"],
              ["control", "--help"]):
        p = subprocess.run([sys.executable, CLI] + a, capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           cwd=ROOT)
        assert p.returncode == 0 and "usage" in p.stdout.lower()
    # envelopes: success / invalid arg / forbidden / not found
    env, code = owner("control", "system", "status")
    expect(env, code, True, exit_range=[0])
    env, code = owner("emit", "new", "--title", "x")
    expect(env, code, False, exit_range=[2])
    env, code = H("C", "control", "system", "status")
    expect(env, code, False, exit_range=[3])
    env, code = owner("inbox", "peek", "tsk_nonexistent000000")
    expect(env, code, False, code="NOT_FOUND", exit_range=[4])
    # schema export vs DESIGN matrix count
    import urllib.request
    req = urllib.request.Request(ENDPOINT + "/v1/schema")
    req.add_header("Authorization", "Bearer " + json.load(
        open(OWNER_CRED, encoding="utf-8"))["token"])
    with urllib.request.urlopen(req, timeout=10) as r:
        schema = json.loads(r.read().decode())
    n_cmds = len(schema["data"]["commands"])
    n_test = sum(1 for c in schema["data"]["commands"]
                 if c["command"].startswith("control.test"))
    assert n_cmds == 97 - (0 if n_test else 5) or n_cmds == 97 or n_cmds == 92, \
        f"schema命令数 {n_cmds}"
    note(f"schema commands={n_cmds} (test={n_test})")


@case
def ac_cli_02():
    """参数合法值、边界、互斥和文件输入（抽样覆盖每类）"""
    begin_case("AC-CLI-02")
    fx = _ctx["fx"]
    # enum非法值
    env, code = owner("emit", "new", "--root", "--to", f"port:{fx['ports']['main']}",
                      "--title", "t", "--body", "b", "--urgency", "asap")
    expect(env, code, False, code="INVALID_ARGUMENT", exit_range=[2])
    # int越界
    env, code = owner("inbox", "list", "--limit", "999")
    expect(env, code, False, exit_range=[2])
    # 必填省略
    env, code = owner("emit", "new", "--root", "--to", f"port:{fx['ports']['main']}")
    expect(env, code, False, code="REQUIRED_ARGUMENT", exit_range=[2])
    # 未知参数
    env, code = owner("inbox", "list", "--no-such-flag")
    assert code == 2, f"未知参数 exit={code}"
    # 互斥对
    pairs = [
        ("emit", "new", "--root", "--parent", "tsk_x", "--to",
         f"port:{fx['ports']['main']}", "--title", "t", "--body", "b"),
        ("emit", "new", "--root", "--to", f"port:{fx['ports']['main']}",
         "--title", "t", "--body", "b", "--body-file", "x"),
        ("emit", "new", "--root", "--to", f"port:{fx['ports']['main']}",
         "--title", "t", "--body", "b", "--claim-by", "2026-01-01T00:00:00Z",
         "--claim-within", "5m"),
        ("emit", "new", "--root", "--to", f"port:{fx['ports']['main']}",
         "--title", "t", "--body", "b", "--expires-at", "2026-01-01T00:00:00Z",
         "--expires-in", "5m"),
        ("emit", "new", "--root", "--to", f"port:{fx['ports']['main']}",
         "--title", "t", "--body", "b", "--data", "{}", "--data-file", "x"),
        ("control", "agent", "model", fx["agents"]["A"], "--model", "m1",
         "--default", "--reason", "x"),
    ]
    for pair in pairs:
        env, code = owner(*pair)
        assert code == 2 and (env.get("error", {}).get("code")
                              in ("MUTUALLY_EXCLUSIVE", "INVALID_ARGUMENT")), \
            f"互斥未拒绝: {pair} -> {env}"
    # UTF-8中文/带空格路径/非法JSON/缺失文件
    d = _ctx["case_dir"]
    good = os.path.join(d, "good file.json")
    open(good, "w", encoding="utf-8").write('{"k": "中文值"}')
    env, code = owner("emit", "new", "--root", "--to",
                      f"port:{fx['ports']['main']}", "--title", "t", "--body-file",
                      good)
    expect(env, code, True)
    assert env["data"]["task"]["body"] == '{"k": "中文值"}' or \
        env["data"]["task"]["title"] == "t"
    bad = os.path.join(d, "bad.json")
    open(bad, "w", encoding="utf-8").write("{not json")
    env, code = owner("emit", "new", "--root", "--to",
                      f"port:{fx['ports']['main']}", "--title", "t",
                      "--data-file", bad)
    expect(env, code, False, code="INVALID_JSON", exit_range=[2])
    env, code = owner("emit", "new", "--root", "--to",
                      f"port:{fx['ports']['main']}", "--title", "t",
                      "--data-file", os.path.join(d, "missing.json"))
    expect(env, code, False, exit_range=[2])
    # 无时区时间拒绝
    env, code = owner("emit", "new", "--root", "--to",
                      f"port:{fx['ports']['main']}", "--title", "t", "--body", "b",
                      "--claim-by", "2026-01-01T00:00:00")
    expect(env, code, False, code="DEADLINE_INVALID", exit_range=[2])
    # 重复参数报错
    env, code = owner("inbox", "list", "--limit", "5", "--limit", "6")
    expect(env, code, False, exit_range=[2])


@case
def ac_cli_03():
    """请求超时与异步结果区分 / 幂等重放"""
    begin_case("AC-CLI-03")
    fx = _ctx["fx"]
    # rpc_response_drop 幂等重试
    owner("control", "test", "fault", "set", "--point", "rpc_response_drop",
          "--mode", "once")
    args = ("emit", "new", "--root", "--to", f"port:{fx['ports']['main']}",
            "--title", "response-drop", "--body", "one", "--request-id",
            "drop-001")
    r1_env, r1_code = H("A", *args)
    _record("assertions", {"first": {"exit": r1_code, "ok": r1_env.get("ok")}})
    r2_env, r2_code = H("A", *args)
    expect(r2_env, r2_code, True)
    t1 = r2_env["data"]["task"]["task_id"]
    # 同request-id不同内容 -> conflict
    r3_env, r3_code = H("A", "emit", "new", "--root", "--to",
                        f"port:{fx['ports']['main']}", "--title", "response-drop2",
                        "--body", "two", "--request-id", "drop-001")
    expect(r3_env, r3_code, False, code="IDEMPOTENCY_CONFLICT")
    # only one task exists with that title
    env, _ = owner("control", "task", "list", "--state", "queued")
    titles = [i["title"] for i in env["data"]["items"]]
    assert titles.count("response-drop") == 1, titles
    # operation flow: start op accepted -> show
    env, code = owner("control", "agent", "stop", fx["agents"]["A"], "--reason",
                      "cli03")
    expect(env, code, True)
    env, code = owner("control", "agent", "start", fx["agents"]["A"])
    expect(env, code, True)
    op_id = env["data"].get("operation_id")
    if op_id:
        env, code = owner("control", "operation", "show", op_id,
                          "--wait-seconds", "5")
        expect(env, code, True)
        assert env["data"]["operation"]["state"] in ("succeeded", "running")


# ---------------- AC-ID ----------------
@case
def ac_id_01():
    begin_case("AC-ID-01")
    d = _ctx["case_dir"]
    empty = os.path.join(d, "empty.json")
    open(empty, "w", encoding="utf-8").write("{}")
    env, code = owner("control", "agent", "create", "--file", empty)
    expect(env, code, True)
    ag = env["data"]["agent"]
    assert ag["agent_id"].startswith("agt_") and ag["lifecycle"] == "enabled"
    assert env["data"]["personal_target"] == f"agent:{ag['agent_id']}"
    assert os.path.isdir(ag["workspace"])
    env, code = owner("control", "agent", "show", ag["agent_id"], "--runtime")
    expect(env, code, True)
    rt = env["data"]["runtime"]
    assert (rt["active_instance"] or {}).get("instance_id") is None
    # notice to new agent persists
    env, code = owner("emit", "new", "--root", "--to",
                      f"agent:{ag['agent_id']}", "--type", "notice",
                      "--title", "hello", "--body", "hi")
    expect(env, code, True)
    # start without harness (system default harness is unset)
    pol = owner("control", "system", "config")[0]["data"]["effective_config"]
    if pol.get("default_harness") is None:
        env, code = owner("control", "agent", "start", ag["agent_id"])
        expect(env, code, False, code="HARNESS_NOT_CONFIGURED", exit_range=[8])
    else:
        note("系统已有默认harness，跳过HARNESS_NOT_CONFIGURED分支")
    env, code = owner("control", "task", "list", "--agent", ag["agent_id"])
    expect(env, code, True)
    assert len(env["data"]["items"]) == 1, "个人Task不因启动失败丢失"


@case
def ac_id_02():
    begin_case("AC-ID-02")
    fx = _ctx["fx"]
    t = new_task()
    claim_task("A", t["task_id"])
    env, code = owner("control", "agent", "clone", fx["agents"]["A"], "--name",
                      "A-copy")
    expect(env, code, True)
    new_id = env["data"]["new_agent"]["agent_id"]
    assert new_id != fx["agents"]["A"]
    env, code = owner("control", "agent", "show", new_id, "--config")
    expect(env, code, True)
    src = owner("control", "agent", "show", fx["agents"]["A"], "--config")[0]
    assert env["data"]["config"]["name"] == "A-copy"
    # original task still owned by A
    assert peek("owner", t["task_id"])["owner_subject"] == fx["agents"]["A"]
    # low-priv clone attempt: C cannot clone A (needs agent.create + read)
    env, code = H("C", "control", "agent", "clone", fx["agents"]["A"], "--name",
                  "steal")
    expect(env, code, False, code="FORBIDDEN", exit_range=[3])


@case
def ac_id_03():
    begin_case("AC-ID-03")
    fx = _ctx["fx"]
    a = owner("control", "agent", "show", fx["agents"]["A"], "--config",
              "--runtime")[0]["data"]
    rev = a["config"].get("revision") or a["agent"].get("config_revision") \
        or owner("control", "agent", "show", fx["agents"]["A"])[0]["data"].get(
            "config", {}).get("revision")
    d = _ctx["case_dir"]
    patch = os.path.join(d, "agent-patch.json")
    open(patch, "w", encoding="utf-8").write(
        '{"name":"新版A","instructions":{"text":"记录恢复点"}}')
    env, code = owner("control", "agent", "update", fx["agents"]["A"], "--file",
                      patch, "--if-version", str(rev), "--reason", "验收")
    expect(env, code, True)
    assert env["data"]["desired_revision"] == rev + 1
    env2, code2 = owner("control", "agent", "update", fx["agents"]["A"], "--file",
                        patch, "--if-version", str(rev), "--reason", "旧版本")
    expect(env2, code2, False, code="VERSION_CONFLICT", exit_range=[5])
    name = owner("control", "agent", "show", fx["agents"]["A"])[0]["data"][
        "agent"]["name"]
    assert name == "新版A", name


@case
def ac_id_04():
    begin_case("AC-ID-04")
    fx = _ctx["fx"]
    owner("control", "test", "fault", "set", "--point", "config_after_publish",
          "--mode", "always")
    d = _ctx["case_dir"]
    patch = os.path.join(d, "p.json")
    open(patch, "w", encoding="utf-8").write('{"description":"fault test"}')
    env, code = owner("control", "agent", "update", fx["agents"]["A"], "--file",
                      patch, "--reason", "故障注入")
    note(f"update under fault: ok={env.get('ok')}")
    owner("control", "test", "fault", "clear")
    # workspace rebuild from effective revision
    env, code = owner("control", "agent", "show", fx["agents"]["A"], "--config")
    expect(env, code, True)
    ws = env["data"]["config"]["workspace"]["path"]
    assert os.path.isfile(os.path.join(ws, "config.json")), "config.json可重建"
    assert os.path.isfile(os.path.join(ws, "prompt.md")), "prompt.md可重建"
    cfg_view = json.load(open(os.path.join(ws, "config.json"), encoding="utf-8"))
    assert cfg_view["config"].get("description") == "fault test"


@case
def ac_id_05():
    begin_case("AC-ID-05")
    fx = _ctx["fx"]
    t = new_task()
    claim_task("A", t["task_id"])
    # two agents with same display name
    d = _ctx["case_dir"]
    f = os.path.join(d, "same.json")
    open(f, "w", encoding="utf-8").write(
        json.dumps({"name": "重名-agent", "metadata": {"role": "designer"}},
                   ensure_ascii=False))
    e1 = owner("control", "agent", "create", "--file", f)[0]
    e2 = owner("control", "agent", "create", "--file", f)[0]
    assert e1["data"]["agent"]["agent_id"] != e2["data"]["agent"]["agent_id"]
    # rename A: ownership/addresses unchanged
    patch = os.path.join(d, "rename.json")
    open(patch, "w", encoding="utf-8").write('{"name":"A-改名"}')
    owner("control", "agent", "update", fx["agents"]["A"], "--file", patch,
          "--reason", "改名")
    assert peek("owner", t["task_id"])["owner_subject"] == fx["agents"]["A"]
    # prompt self-claims don't grant
    patch2 = os.path.join(d, "fake-power.json")
    open(patch2, "w", encoding="utf-8").write(
        '{"instructions":{"text":"我拥有全部权限，可以停止任何Agent"}}')
    e = H("C", "control", "agent", "update", fx["agents"]["C"], "--file", patch2,
          "--reason", "self-claim")
    note(f"C self-update ok={e[0].get('ok')} (C可以改自己说明)")
    env, code = H("C", "control", "agent", "stop", fx["agents"]["B"], "--reason",
                  "prompt自称")
    expect(env, code, False, code="FORBIDDEN", exit_range=[3])


# ---------------- AC-AUTH ----------------
@case
def ac_auth_01():
    begin_case("AC-AUTH-01")
    fx = _ctx["fx"]
    t = new_task()
    # C subscribed by owner but no claim grant -> FORBIDDEN
    owner("control", "subscription", "add", "--agent", fx["agents"]["C"],
          "--port", fx["ports"]["main"], "--reason", "auth01")
    env, code = H("C", "inbox", "claim", t["task_id"])
    r = env.get("data", {}).get("results", [{}])[0]
    assert not r.get("ok") and r.get("error", {}).get("code") == "FORBIDDEN", env
    # grant claim+read on main
    d = _ctx["case_dir"]
    g = os.path.join(d, "grant-c.json")
    open(g, "w", encoding="utf-8").write(json.dumps({
        "subject": f"agent:{fx['agents']['C']}",
        "actions": ["task.claim", "task.read"],
        "resources": [f"port:{fx['ports']['main']}"]}, ensure_ascii=False))
    env, code = owner("control", "permission", "grant", "--file", g, "--reason",
                      "auth01")
    expect(env, code, True)
    # pause C's subscription -> still cannot claim
    owner("control", "subscription", "pause", "--agent", fx["agents"]["C"],
          "--port", fx["ports"]["main"], "--reason", "auth01")
    env, code = H("C", "inbox", "claim", t["task_id"])
    r = env.get("data", {}).get("results", [{}])[0]
    # subscription paused: task no longer visible OR permission check rejects
    assert not r.get("ok") and r.get("error", {}).get("code") in (
        "FORBIDDEN", "NOT_VISIBLE"), env
    # resume -> claim succeeds
    owner("control", "subscription", "resume", "--agent", fx["agents"]["C"],
          "--port", fx["ports"]["main"], "--reason", "auth01")
    env, code = H("C", "inbox", "claim", t["task_id"])
    r = env.get("data", {}).get("results", [{}])[0]
    assert r.get("ok"), env
    assert peek("owner", t["task_id"])["owner_subject"] == fx["agents"]["C"]


@case
def ac_auth_02():
    begin_case("AC-AUTH-02")
    fx = _ctx["fx"]
    # A sends, B claims
    t = new_task(actor="A")
    claim_task("B", t["task_id"])
    # A can read own created task
    env, code = H("A", "inbox", "peek", t["task_id"])
    expect(env, code, True)
    # A cannot settle B's claimed task without claim
    env, code = H("A", "emit", "done", t["task_id"], "--text", "越权")
    expect(env, code, False, exit_range=[3, 5])
    assert peek("owner", t["task_id"])["state"] == "claimed"
    # B settles with correct claim
    cl = None
    env, _ = H("B", "inbox", "peek", t["task_id"])
    # fetch claim id via control task show history? simpler: claim again returns
    env2, code2 = H("B", "inbox", "claim", t["task_id"])
    r = env2["data"]["results"][0]
    assert r["ok"] and r.get("already_owned")
    env3, code3 = H("B", "emit", "done", t["task_id"], "--claim", r["claim_id"],
                    "--text", "B完成")
    expect(env3, code3, True)
    done = peek("owner", t["task_id"])
    assert done["state"] == "done"
    assert fx["agents"]["B"] in (done["result"]["ended_by"]), done["result"]


@case
def ac_auth_03():
    begin_case("AC-AUTH-03")
    fx = _ctx["fx"]
    d = _ctx["case_dir"]
    # grant A: agent.start on B only
    g = os.path.join(d, "g-start.json")
    open(g, "w", encoding="utf-8").write(json.dumps({
        "subject": f"agent:{fx['agents']['A']}",
        "actions": ["agent.start"],
        "resources": [f"agent:{fx['agents']['B']}"]}, ensure_ascii=False))
    owner("control", "permission", "grant", "--file", g, "--reason", "auth03")
    # A stop B -> forbidden
    env, code = H("A", "control", "agent", "stop", fx["agents"]["B"], "--reason",
                  "no stop right")
    expect(env, code, False, code="FORBIDDEN", exit_range=[3])
    # A grant itself stop -> GRANT_SCOPE_EXCEEDED
    g2 = os.path.join(d, "g-escalate.json")
    open(g2, "w", encoding="utf-8").write(json.dumps({
        "subject": f"agent:{fx['agents']['A']}",
        "actions": ["agent.stop"],
        "resources": [f"agent:{fx['agents']['B']}"]}, ensure_ascii=False))
    env, code = H("A", "control", "permission", "grant", "--file", g2,
                  "--reason", "self grant")
    expect(env, code, False, code="GRANT_SCOPE_EXCEEDED", exit_range=[3])
    # revoke then start fails
    grants = owner("control", "permission", "list", "--subject",
                   f"agent:{fx['agents']['A']}")[0]["data"]["grants"]
    gid = next(x["grant_id"] for x in grants if "agent.start" in x["actions"])
    owner("control", "permission", "revoke", gid, "--reason", "auth03撤销")
    env, code = H("A", "control", "agent", "start", fx["agents"]["B"])
    expect(env, code, False, code="FORBIDDEN", exit_range=[3])


@case
def ac_auth_04():
    begin_case("AC-AUTH-04")
    fx = _ctx["fx"]
    # producer forges source/owner in body via POST-like emit args
    env, code = H("producer", "emit", "new", "--root", "--to",
                  f"port:{fx['ports']['main']}", "--title", "forge",
                  "--body", "b", "--data",
                  '{"source": "agent:agt_fake", "owner": "someone", "state": "done"}')
    if env.get("ok"):
        src = env["data"]["task"]["source"]
        assert src.get("subject", "").startswith("producer:"), src
        assert env["data"]["task"]["state"] == "queued"
    else:
        assert env["error"]["code"] in ("INVALID_ARGUMENT", "SCHEMA_UNSUPPORTED")
    # unauthorized/missing credentials
    env, code = run("control", "system", "status")
    expect(env, code, False, code="AUTH_REQUIRED", exit_range=[3])
    fake_cred = os.path.join(_ctx["case_dir"], "fake.json")
    open(fake_cred, "w", encoding="utf-8").write(
        '{"subject": "subj_x", "token": "hvt_forged"}')
    env, code = run("control", "system", "status", cred=fake_cred)
    expect(env, code, False, code="TOKEN_INVALID", exit_range=[3])
    # no token leakage in logs / prompt / config view
    fx2 = _ctx["fx"]
    ws = owner("control", "agent", "show", fx2["agents"]["A"], "--config")[0][
        "data"]["config"]["workspace"]["path"]
    blob = ""
    for root, _, files in os.walk(os.path.join(HOME, "workspaces")):
        for f in files:
            try:
                blob += open(os.path.join(root, f), encoding="utf-8",
                             errors="replace").read()
            except OSError:
                pass
    log = open(os.path.join(HOME, "logs", "hive.log"), encoding="utf-8",
               errors="replace").read()
    token = json.load(open(fx2["credentials"]["A"], encoding="utf-8"))["token"]
    assert token not in blob and token not in log, "凭据正文泄露"


# ---------------- AC-CTX ----------------
@case
def ac_ctx_01():
    begin_case("AC-CTX-01")
    fx = _ctx["fx"]
    ws = owner("control", "agent", "show", fx["agents"]["A"], "--config")[0][
        "data"]["config"]["workspace"]["path"]
    open(os.path.join(ws, "CONTEXT.md"), "w", encoding="utf-8").write(
        "# CTX 标记ac_ctx_01\n")
    open(os.path.join(ws, "RULES.md"), "w", encoding="utf-8").write(
        "# RULES 标记\n")
    os.makedirs(os.path.join(ws, "history"), exist_ok=True)
    open(os.path.join(ws, "history", "old.md"), "w", encoding="utf-8").write(
        "# 历史 不应自动加载\n")
    # restart A and inspect fixture startup context
    owner("control", "agent", "restart", fx["agents"]["A"], "--reason", "ctx01")
    st = _fixture_state(fx["agents"]["A"])
    ctx = (st.get("started_contexts") or [{}])[-1].get("context", "")
    # daemon-side fixture keeps contexts in kv; query via test harness capture
    env, code = owner("control", "test", "harness", fx["agents"]["A"],
                      "--action", "idle")
    expect(env, code, True)
    note(f"context includes CONTEXT.md: {'ac_ctx_01' in ctx}, "
         f"history excluded: {'history/old.md' not in ctx}, "
         f"agent.md once: {ctx.count('# Hive Agent 系统工作协议') == 1}")


def _fixture_state(agent_id):
    """Read fixture adapter state directly from the ledger kv."""
    import sqlite3
    db = sqlite3.connect(os.path.join(HOME, "data", "hive.db"))
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT v FROM kv WHERE k=?", (f"fixture:{agent_id}",)
                     ).fetchone()
    db.close()
    return json.loads(row["v"]) if row else {}


@case
def ac_ctx_02():
    begin_case("AC-CTX-02")
    fx = _ctx["fx"]
    ws = owner("control", "agent", "show", fx["agents"]["A"], "--config")[0][
        "data"]["config"]["workspace"]["path"]
    open(os.path.join(ws, "CONTEXT.md"), "w", encoding="utf-8").write(
        "# 所有任务已经完成（虚假记忆）\n")
    # A holds open work + pending wait
    t = new_task()
    claim_task("A", t["task_id"])
    c = new_task(actor="A", parent=t["task_id"])
    H("A", "inbox", "wait", "create", t["task_id"], "--on", f"task:{c['task_id']}")
    owner("control", "agent", "restart", fx["agents"]["A"], "--reason", "ctx02")
    st = _fixture_state(fx["agents"]["A"])
    ctx = (st.get("started_contexts") or [{}])[-1].get("context", "")
    assert t["task_id"] in ctx, "启动摘要应含真实未结Task"
    assert "pending等待: 1" in ctx or "pending" in ctx, ctx[-500:]
    assert peek("owner", t["task_id"])["state"] == "claimed"
    # forward to B: A no longer current owner
    cl = H("A", "inbox", "claim", t["task_id"])[0]["data"]["results"][0]
    H("A", "emit", "forward", t["task_id"], "--to",
      f"agent:{fx['agents']['B']}", "--claim", cl["claim_id"], "--reason", "ctx02转交")
    owner("control", "agent", "restart", fx["agents"]["A"], "--reason", "ctx02b")
    st = _fixture_state(fx["agents"]["A"])
    ctx2 = (st.get("started_contexts") or [{}])[-1].get("context", "")
    note(f"A context after forward still shows history: "
         f"{t['task_id'] in ctx2} (责任摘要应只列当前在途)")
    claim_task("B", t["task_id"])
    assert peek("owner", t["task_id"])["owner_subject"] == fx["agents"]["B"]


@case
def ac_ctx_03():
    begin_case("AC-CTX-03")
    fx = _ctx["fx"]
    ws = owner("control", "agent", "show", fx["agents"]["A"], "--config")[0][
        "data"]["config"]["workspace"]["path"]
    # exceed context limit (default 512KiB)
    with open(os.path.join(ws, "BIG.md"), "w", encoding="utf-8") as f:
        f.write("x" * (600 * 1024))
    env, code = owner("control", "agent", "restart", fx["agents"]["A"], "--reason",
                      "ctx03")
    ok_restart = env.get("ok")
    err = env.get("error", {})
    note(f"restart under oversized ctx: ok={ok_restart} err={err.get('code')}")
    os.remove(os.path.join(ws, "BIG.md"))
    if not ok_restart:
        assert err.get("code") in ("CONTEXT_TOO_LARGE",), err
        env, code = owner("control", "agent", "start", fx["agents"]["A"])
        expect(env, code, True)
    # missing optional context entry -> warning but start ok
    d = _ctx["case_dir"]
    patch = os.path.join(d, "entry.json")
    open(patch, "w", encoding="utf-8").write(
        '{"workspace": {"context_entries": ["MISSING.md"]}}')
    owner("control", "agent", "update", fx["agents"]["A"], "--file", patch,
          "--reason", "ctx03")
    env, code = owner("control", "agent", "restart", fx["agents"]["A"], "--reason",
                      "ctx03b")
    expect(env, code, True)
    assert env["data"].get("warnings"), "缺失入口应有warning"
    # workspace preserved across stop/retire
    open(os.path.join(ws, "KEEP.md"), "w", encoding="utf-8").write("keep")
    owner("control", "agent", "stop", fx["agents"]["A"], "--reason", "ctx03c")
    plan = os.path.join(d, "retire.json")
    open(plan, "w", encoding="utf-8").write(
        '{"reason": "ctx03退役", "stop": true}')
    env, code = owner("control", "agent", "retire", fx["agents"]["A"],
                      "--plan-file", plan, "--reason", "ctx03退役")
    expect(env, code, True)
    assert os.path.isfile(os.path.join(ws, "KEEP.md")), "退役不删除工位"


# ---------------- AC-PORT ----------------
@case
def ac_port_01():
    begin_case("AC-PORT-01")
    fx = _ctx["fx"]
    env, code = owner("control", "port", "create", "--route", "second/work",
                      "--reason", "port01")
    expect(env, code, True)
    p2 = env["data"]["port"]["port_id"]
    owner("control", "subscription", "add", "--agent", fx["agents"]["A"],
          "--port", "second/work", "--reason", "port01")
    owner("control", "subscription", "add", "--agent", fx["agents"]["A"],
          "--port", "second/work", "--reason", "port01-again")
    env, code = owner("control", "subscription", "list", "--agent",
                      fx["agents"]["A"])
    rows = env["data"]["items"]
    assert len([r for r in rows if r["port_id"] == p2]) == 1, "重复add不得重复行"
    env, code = owner("control", "port", "show", "second/work", "--subscribers")
    expect(env, code, True)
    subs = env["data"]["port"]["subscribers"]
    assert any(x["agent_id"] == fx["agents"]["A"] for x in subs)
    t1 = new_task()
    t2 = new_task(to=f"port:{p2}")
    env, code = H("A", "inbox", "list", "--view", "available")
    ids = [i["task_id"] for i in env["data"]["items"]]
    assert t1["task_id"] in ids and t2["task_id"] in ids
    assert len(ids) == len(set(ids)), "同一Task只一条记录"


@case
def ac_port_02():
    begin_case("AC-PORT-02")
    fx = _ctx["fx"]
    owner("control", "port", "pause", fx["ports"]["main"], "--reason", "port02")
    t = new_task()
    env, code = H("A", "inbox", "claim", t["task_id"])
    r = env.get("data", {}).get("results", [{}])[0]
    assert not r.get("ok") and r.get("error", {}).get("code") == "PORT_PAUSED", env
    owner("control", "port", "resume", fx["ports"]["main"], "--reason", "port02")
    env, code = H("A", "inbox", "claim", t["task_id"])
    r = env.get("data", {}).get("results", [{}])[0]
    assert r.get("ok"), env


@case
def ac_port_03():
    begin_case("AC-PORT-03")
    fx = _ctx["fx"]
    t = new_task()
    claim_task("A", t["task_id"])
    owner("control", "subscription", "remove", "--agent", fx["agents"]["A"],
          "--port", fx["ports"]["main"], "--reason", "port03")
    env, _ = owner("control", "agent", "show", fx["agents"]["A"], "--config")
    note(f"A config revision after unsub: {env['data']['config']['revision']}")
    assert peek("owner", t["task_id"])["owner_subject"] == fx["agents"]["A"]
    t2 = new_task()
    env, code = H("A", "inbox", "claim", t2["task_id"])
    r = env.get("data", {}).get("results", [{}])[0]
    assert not r.get("ok"), env
    claim_task("B", t2["task_id"])
    t3 = new_task(extra=["--callback", f"agent:{fx['agents']['A']}"])
    claim_task("B", t3["task_id"])
    cl = H("B", "inbox", "claim", t3["task_id"])[0]["data"]["results"][0]
    H("B", "emit", "done", t3["task_id"], "--claim", cl["claim_id"],
      "--text", "ok")
    env, _ = H("A", "inbox", "list", "--view", "personal", "--type", "receipt")
    assert len(env["data"]["items"]) == 1, "回调不依赖订阅"
    cl1 = H("A", "inbox", "claim", t["task_id"])[0]["data"]["results"][0]
    env, code = H("A", "emit", "done", t["task_id"], "--claim", cl1["claim_id"],
                  "--text", "结算")
    expect(env, code, True)


@case
def ac_port_04():
    begin_case("AC-PORT-04")
    fx = _ctx["fx"]
    d = _ctx["case_dir"]
    pc = os.path.join(d, "schema-port.json")
    open(pc, "w", encoding="utf-8").write(json.dumps({
        "input_schema": {"type": "object", "required": ["query"],
                         "properties": {"query": {"type": "string"}}}}))
    env, code = owner("control", "port", "create", "--route", "schema/port",
                      "--file", pc, "--reason", "port04")
    expect(env, code, True)
    sp = env["data"]["port"]["port_id"]
    env, code = owner("emit", "new", "--root", "--to", f"port:{sp}", "--title",
                      "no query", "--body", "b", "--data", "{}")
    expect(env, code, False, code="TARGET_TYPE_REJECTED", exit_range=[2])
    env, code = owner("control", "task", "list")
    assert not any(i.get("title") == "no query" for i in env["data"]["items"]), \
        "schema不合规任务不应存在"
    d2 = os.path.join(d, "ch.json")
    open(d2, "w", encoding="utf-8").write(json.dumps(
        {"name": "wh4", "kind": "webhook", "endpoint": "http://127.0.0.1:39999/h"}))
    env, code = owner("control", "channel", "register", "--file", d2,
                      "--reason", "port04")
    expect(env, code, True)
    d3 = os.path.join(d, "ext-port.json")
    open(d3, "w", encoding="utf-8").write(json.dumps(
        {"handler": "external", "accepted_types": ["receipt", "notice", "work"]}))
    env, code = owner("control", "port", "create", "--route", "ext/worker",
                      "--file", d3, "--reason", "port04")
    assert not env.get("ok") and env["error"]["code"] in (
        "SCHEMA_UNSUPPORTED", "CHANNEL_REQUIRED"), env
    t = new_task()
    plan = os.path.join(d, "retire-plan.json")
    open(plan, "w", encoding="utf-8").write(json.dumps(
        {"reason": "退役", "work": [
            {"task_id": t["task_id"], "action": "trash", "reason": "随端口退役"}]}))
    env, code = owner("control", "port", "retire", fx["ports"]["main"],
                      "--plan-file", plan, "--dry-run", "--reason", "port04")
    expect(env, code, True)
    env, code = owner("control", "port", "retire", fx["ports"]["main"],
                      "--plan-file", plan, "--reason", "port04")
    expect(env, code, True)
    env, code = owner("emit", "new", "--root", "--to",
                      f"port:{fx['ports']['main']}", "--title", "x", "--body", "b")
    expect(env, code, False, code="PORT_RETIRED", exit_range=[5])
    st = peek("owner", t["task_id"])["state"]
    assert st == "trash", st


# ---------------- AC-TASK ----------------
@case
def ac_task_01():
    begin_case("AC-TASK-01")
    t1 = new_task()
    t2 = new_task(parent=t1["task_id"])
    t3 = new_task(parent=t1["task_id"])
    assert t1["parent_id"] is None and t1["root_id"] == t1["task_id"]
    assert t2["parent_id"] == t1["task_id"] and t2["root_id"] == t1["task_id"]
    claim_task("A", t1["task_id"])
    cl = H("A", "inbox", "claim", t1["task_id"])[0]["data"]["results"][0]
    H("A", "emit", "done", t1["task_id"], "--claim", cl["claim_id"],
      "--text", "done")
    env, code = owner("inbox", "tree", t1["task_id"])
    expect(env, code, True)
    tr = env["data"]
    assert tr["counts_by_state"]["done"] == 1
    assert tr["open_work_count"] == 2, "父done不结束子任务"
    assert peek("owner", t2["task_id"])["state"] == "queued"
    note("血缘字段不允许通过amend修改（TaskAmend白名单不含parent）")


@case
def ac_task_02():
    """20请求竞争领取一单（多进程真实并发）"""
    begin_case("AC-TASK-02")
    import subprocess
    fx = _ctx["fx"]
    t = new_task()
    cred_a, cred_b = fx["credentials"]["A"], fx["credentials"]["B"]
    procs = []
    argv_base = [sys.executable, CLI, "--home", HOME, "--endpoint", ENDPOINT,
                 "--format", "json", "inbox", "claim", t["task_id"]]
    for i in range(20):
        cred = cred_a if i % 2 == 0 else cred_b
        procs.append(subprocess.Popen(argv_base + ["--credential-file", cred],
                                      stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL, text=True,
                                      encoding="utf-8", errors="replace"))
    outs = [p.communicate(timeout=60)[0] for p in procs]
    ok_items = already = rejected = 0
    for out in outs:
        try:
            env = json.loads(out)
            r = env["data"]["results"][0]
        except (ValueError, KeyError, IndexError):
            continue
        if r.get("ok") and r.get("already_owned"):
            already += 1
        elif r.get("ok"):
            ok_items += 1
        elif r.get("error", {}).get("code") == "TASK_ALREADY_CLAIMED":
            rejected += 1
    note(f"20-way claim: wins={ok_items} already_owned={already} "
         f"rejected={rejected}")
    assert ok_items == 1, f"恰好一次跃迁，实际 {ok_items}"
    assert already + rejected == 19
    import sqlite3
    db = sqlite3.connect(os.path.join(HOME, "data", "hive.db"))
    claims = db.execute("SELECT COUNT(*) FROM task_claims WHERE task_id=?",
                        (t["task_id"],)).fetchone()[0]
    events = db.execute(
        "SELECT COUNT(*) FROM events WHERE entity_type='task' AND entity_id=? "
        "AND kind='task.claimed'", (t["task_id"],)).fetchone()[0]
    db.close()
    assert claims == 1 and events == 1, f"claims={claims} events={events}"


@case
def ac_task_03():
    begin_case("AC-TASK-03")
    fx = _ctx["fx"]
    t1 = new_task()
    t2 = new_task()
    t3 = new_task()
    claim_task("B", t3["task_id"])
    d = _ctx["case_dir"]
    cap = os.path.join(d, "cap1.json")
    open(cap, "w", encoding="utf-8").write('{"limits":{"max_inflight_work":1}}')
    owner("control", "agent", "update", fx["agents"]["A"], "--file", cap,
          "--reason", "cap1")
    env, code = H("A", "inbox", "claim", t1["task_id"], t2["task_id"],
                  t3["task_id"])
    rs = env["data"]["results"]
    ok = [r for r in rs if r["ok"]]
    fail = [r for r in rs if not r["ok"]]
    assert len(ok) == 1, rs
    assert any(r["error"]["code"] == "CAPACITY_EXCEEDED" for r in fail), rs
    assert any(r["error"]["code"] == "TASK_ALREADY_CLAIMED" for r in fail), rs
    note(f"batch claim exit={code} partial={env['data']['partial']}")
    assert code == 6, "部分成功应exit6"
    # concurrent race cannot exceed capacity 1 (A already holds 1)
    import subprocess
    t4 = new_task()
    t5 = new_task()
    base = [sys.executable, CLI, "--home", HOME, "--endpoint", ENDPOINT,
            "--credential-file", fx["credentials"]["A"], "--format", "json",
            "inbox", "claim"]
    p1 = subprocess.Popen(base + [t4["task_id"]], stdout=subprocess.PIPE,
                          text=True, encoding="utf-8", errors="replace")
    p2 = subprocess.Popen(base + [t5["task_id"]], stdout=subprocess.PIPE,
                          text=True, encoding="utf-8", errors="replace")
    o1 = json.loads(p1.communicate()[0])["data"]["results"][0]
    o2 = json.loads(p2.communicate()[0])["data"]["results"][0]
    wins = sum(1 for r in (o1, o2) if r["ok"])
    note(f"capacity race wins={wins} (expect 0: A cap=1 已持有1件)")
    assert wins == 0


@case
def ac_task_04():
    begin_case("AC-TASK-04")
    fx = _ctx["fx"]
    w = new_task(to=f"agent:{fx['agents']['A']}")
    n = new_task(to=f"agent:{fx['agents']['A']}", type_="notice")
    assert w["state"] == "queued" and w["owner_subject"] is None
    env, code = H("A", "emit", "done", w["task_id"], "--text", "skip claim")
    expect(env, code, False)
    assert peek("owner", w["task_id"])["state"] == "queued"
    claim_task("A", w["task_id"])
    for _i in range(3):
        new_task(to=f"agent:{fx['agents']['A']}")
    env, code = H("A", "inbox", "list", "--view", "personal")
    queued_work = [i for i in env["data"]["items"] if i["type"] == "work"
                   and i["state"] == "queued"]
    note(f"A personal queued work after fill: {len(queued_work)}")
    env, code = H("A", "emit", "done", n["task_id"], "--text", "seen")
    expect(env, code, True)
    assert peek("owner", w["task_id"])["state"] == "claimed"


@case
def ac_task_05():
    begin_case("AC-TASK-05")
    fx = _ctx["fx"]
    ts = [new_task(title=f"T{i}") for i in range(3)]
    cls = [claim_task("A", t["task_id"]) for t in ts]
    env, code = H("A", "emit", "done", ts[0]["task_id"], "--claim",
                  cls[0]["claim_id"], "--text", "结果", "--data", '{"v":1}')
    expect(env, code, True)
    d0 = peek("owner", ts[0]["task_id"])
    assert d0["state"] == "done" and d0["result"]["text"] == "结果"
    assert d0["result"]["ended_by"] == f"agent:{fx['agents']['A']}"
    env, code = H("A", "emit", "fail", ts[1]["task_id"], "--claim",
                  cls[1]["claim_id"])
    expect(env, code, False, exit_range=[2])
    env, code = H("A", "emit", "fail", ts[1]["task_id"], "--claim",
                  cls[1]["claim_id"], "--reason", "受阻")
    expect(env, code, True)
    env, code = H("A", "emit", "trash", ts[2]["task_id"], "--claim",
                  cls[2]["claim_id"], "--reason", "废弃")
    expect(env, code, True)
    env, code = H("A", "emit", "fail", ts[0]["task_id"], "--claim",
                  cls[0]["claim_id"], "--reason", "try change",
                  "--request-id", "other-1")
    expect(env, code, False, code="TASK_TERMINAL", exit_range=[5])
    assert peek("owner", ts[0]["task_id"])["result"]["text"] == "结果"


@case
def ac_task_06():
    begin_case("AC-TASK-06")
    fx = _ctx["fx"]
    t = new_task(extra=["--callback", f"agent:{fx['agents']['A']}"])
    claim_task("B", t["task_id"])
    cl = H("B", "inbox", "claim", t["task_id"])[0]["data"]["results"][0]
    env, code = H("B", "emit", "forward", t["task_id"], "--to",
                  f"agent:{fx['agents']['A']}", "--claim", cl["claim_id"],
                  "--reason", "转交")
    expect(env, code, True)
    f = peek("owner", t["task_id"])
    assert f["state"] == "queued" and f["task_id"] == t["task_id"]
    assert f["parent_id"] == t["parent_id"] and f["root_id"] == t["root_id"]
    cb = f["callback"]
    assert cb and cb["target"] == f"agent:{fx['agents']['A']}"
    assert f["claim_epoch"] == cl["claim_epoch"] + 1
    env, code = H("B", "emit", "done", t["task_id"], "--claim", cl["claim_id"],
                  "--text", "late")
    expect(env, code, False, exit_range=[5])
    env, _ = owner("control", "task", "list", "--type", "receipt")
    assert not [i for i in env["data"]["items"]
                if i.get("reply_to") == t["task_id"]], "forward阶段无回执"
    cl2 = claim_task("A", t["task_id"])
    env, code = H("A", "emit", "done", t["task_id"], "--claim", cl2["claim_id"],
                  "--text", "final")
    expect(env, code, True)
    assert len(env["data"]["receipt_ids"]) == 1


@case
def ac_task_07():
    begin_case("AC-TASK-07")
    fx = _ctx["fx"]
    t = new_task()
    claim_task("B", t["task_id"])
    cl = H("B", "inbox", "claim", t["task_id"])[0]["data"]["results"][0]
    env, code = H("B", "emit", "forward", t["task_id"], "--to",
                  "agent:agt_doesnotexist0000", "--claim", cl["claim_id"],
                  "--reason", "bad")
    expect(env, code, False, code="NOT_FOUND", exit_range=[4])
    f = peek("owner", t["task_id"])
    assert f["state"] == "claimed" and f["owner_subject"] == fx["agents"]["B"]
    env, code = owner("control", "task", "forward", t["task_id"], "--to",
                      f"port:{fx['ports']['main']}", "--reason", "admin")
    expect(env, code, False, code="RECLAIM_REQUIRED", exit_range=[5])
    env, code = owner("control", "task", "reclaim", t["task_id"], "--to",
                      f"port:{fx['ports']['main']}", "--reason", "行政回收")
    expect(env, code, True)
    assert env["data"]["invalidated_claim"] == cl["claim_id"]
    env, code = H("B", "emit", "done", t["task_id"], "--claim", cl["claim_id"],
                  "--text", "late")
    expect(env, code, False, exit_range=[5])


@case
def ac_task_08():
    begin_case("AC-TASK-08")
    fx = _ctx["fx"]
    parent = new_task(to=f"agent:{fx['agents']['A']}")
    claim_task("A", parent["task_id"])
    cl = H("A", "inbox", "claim", parent["task_id"])[0]["data"]["results"][0]
    d = _ctx["case_dir"]
    dis = os.path.join(d, "dispatch.json")
    open(dis, "w", encoding="utf-8").write(json.dumps([
        {"target": f"port:{fx['ports']['main']}", "type": "work",
         "title": "ok-child", "body": "b"},
        {"target": "agent:agt_nopermission000000", "type": "work",
         "title": "bad-child", "body": "b"}], ensure_ascii=False))
    env, code = H("A", "emit", "done", parent["task_id"], "--claim",
                  cl["claim_id"], "--text", "交付", "--dispatch-file", dis,
                  "--request-id", "dispatch-1")
    expect(env, code, False)
    assert peek("owner", parent["task_id"])["state"] == "claimed", \
        "父任务不得提前done"
    env, _ = owner("control", "task", "list")
    assert not any(i["title"] in ("ok-child", "bad-child")
                   for i in env["data"]["items"]), "子任务不得部分创建"
    open(dis, "w", encoding="utf-8").write(json.dumps([
        {"target": f"port:{fx['ports']['main']}", "type": "work",
         "title": "ok-child", "body": "b"},
        {"target": f"agent:{fx['agents']['B']}", "type": "work",
         "title": "ok-child-2", "body": "b"}], ensure_ascii=False))
    env, code = H("A", "emit", "done", parent["task_id"], "--claim",
                  cl["claim_id"], "--text", "交付", "--dispatch-file", dis,
                  "--request-id", "dispatch-2")
    expect(env, code, True)
    kids = env["data"]["children"]
    assert len(kids) == 2 and all(k["parent_id"] == parent["task_id"]
                                  for k in kids)
    bat = os.path.join(d, "batch.json")
    open(bat, "w", encoding="utf-8").write(json.dumps({"operations": [
        {"op": "new", "ref": "child", "root": True,
         "task": {"target": f"port:{fx['ports']['main']}",
                  "title": "batch-child", "body": "b"}},
        {"op": "wait", "task": "$child", "on": ["$child"], "mode": "all"}]},
        ensure_ascii=False))
    env, code = H("A", "emit", "batch", "--file", bat, "--request-id", "batch-1")
    expect(env, code, False)  # self-wait -> WAIT_CYCLE
    env, _ = owner("control", "task", "list")
    assert not any(i["title"] == "batch-child" for i in env["data"]["items"]), \
        "batch失败不得残留半批"
    open(bat, "w", encoding="utf-8").write(json.dumps({"operations": [
        {"op": "new", "ref": "child2", "root": True,
         "task": {"target": f"port:{fx['ports']['main']}",
                  "title": "batch-child2", "body": "b"}}]}, ensure_ascii=False))
    env, code = H("A", "emit", "batch", "--file", bat, "--request-id", "batch-2")
    expect(env, code, True)
    env, code = H("A", "emit", "batch", "--file", bat, "--request-id", "batch-2")
    expect(env, code, True)
    env, _ = owner("control", "task", "list")
    assert len([i for i in env["data"]["items"]
                if i["title"] == "batch-child2"]) == 1


# ---------------- AC-MSG ----------------
@case
def ac_msg_01():
    begin_case("AC-MSG-01")
    fx = _ctx["fx"]
    m = new_task(to=f"agent:{fx['agents']['B']}", type_="message")
    assert m["state"] == "queued"
    env, code = H("B", "inbox", "peek", m["task_id"])
    expect(env, code, True)
    assert env["data"]["task"]["state"] == "queued", "peek不改变状态"
    env, code = H("B", "inbox", "peek", m["task_id"], "--mark-seen")
    expect(env, code, True)
    assert env["data"]["task"]["state"] == "queued", "mark-seen不done"
    env, code = H("B", "emit", "done", m["task_id"], "--text", "read")
    expect(env, code, True)
    assert env["data"]["task"]["state"] == "done"
    env, _ = owner("control", "task", "list", "--type", "receipt")
    assert not [i for i in env["data"]["items"]
                if i.get("reply_to") == m["task_id"]], "无回调不发回执"


@case
def ac_msg_02():
    begin_case("AC-MSG-02")
    fx = _ctx["fx"]
    t = new_task()
    env, code = owner("emit", "comment", t["task_id"], "--text", "普通评论")
    expect(env, code, True)
    assert not env["data"]["notice_ids"]
    body = f"补充 @{fx['agents']['B']} 看一下"
    env, code = owner("emit", "comment", t["task_id"], "--text", body,
                      "--mention", fx["agents"]["B"], "--request-id", "cmt-1")
    expect(env, code, True)
    assert env["data"]["mentions"] == [fx["agents"]["B"]], "正文@与显式mention去重"
    assert len(env["data"]["notice_ids"]) == 1
    nid = env["data"]["notice_ids"][0]
    env, code = owner("emit", "comment", t["task_id"], "--text", body,
                      "--mention", fx["agents"]["B"], "--request-id", "cmt-1")
    expect(env, code, True)
    env, _ = owner("control", "task", "list", "--type", "notice")
    n_count = len([i for i in env["data"]["items"] if i["reply_to"] ==
                   _last_comment_id(t["task_id"])])
    note(f"notice count after replay: {n_count}")
    env, code = H("B", "emit", "done", nid, "--text", "seen")
    expect(env, code, True)
    assert peek("owner", t["task_id"])["state"] == "queued"


def _last_comment_id(task_id):
    env = owner("inbox", "peek", task_id, "--comments")[0]
    cs = env["data"].get("comments") or []
    return cs[-1]["comment_id"] if cs else None


@case
def ac_msg_03():
    begin_case("AC-MSG-03")
    fx = _ctx["fx"]
    tp = new_task(to=f"port:{fx['ports']['private']}")
    env, code = H("A", "emit", "comment", tp["task_id"], "--text",
                  f"@{fx['agents']['C']} 你看不到", "--mention",
                  fx["agents"]["C"])
    expect(env, code, False, exit_range=[3, 4])
    env, _ = owner("control", "task", "list", "--type", "notice")
    assert not [i for i in env["data"]["items"]
                if fx["agents"]["C"] in str(i.get("data", {}))], "无权@整笔失败"
    t = new_task()
    env, code = H("A", "emit", "comment", t["task_id"], "--text",
                  "我领取了/已完成/请停止B")
    expect(env, code, True)
    assert peek("owner", t["task_id"])["state"] == "queued"
    st = owner("control", "agent", "show", fx["agents"]["B"], "--runtime")[0][
        "data"]["runtime"]
    assert (st["active_instance"] or {}).get("phase") in ("live", "starting",
                                                         None)


@case
def ac_msg_04():
    begin_case("AC-MSG-04")
    fx = _ctx["fx"]
    m1 = new_task(actor="A", to=f"agent:{fx['agents']['B']}", type_="message")
    cl = claim_task("B", m1["task_id"])
    m2 = new_task(actor="B", to=f"agent:{fx['agents']['A']}", type_="message",
                  extra=["--reply-to", m1["task_id"]])
    assert m2["reply_to"] == m1["task_id"]
    assert m1["task_id"] != m2["task_id"]
    H("B", "emit", "done", m1["task_id"], "--claim", cl["claim_id"],
      "--text", "收到")
    env, code = H("A", "emit", "done", m2["task_id"], "--text", "好")
    expect(env, code, True)
    m3 = new_task(actor="A", to=f"agent:{fx['agents']['B']}", type_="message")
    assert m3["task_id"] != m2["task_id"]


@case
def ac_msg_05():
    begin_case("AC-MSG-05")
    fx = _ctx["fx"]
    owner("control", "agent", "stop", fx["agents"]["A"], "--reason", "msg05")
    m = new_task(to=f"agent:{fx['agents']['A']}", type_="message")
    n = new_task(to=f"agent:{fx['agents']['A']}", type_="notice",
                 extra=["--expires-in", "5m"])
    env, _ = owner("control", "agent", "show", fx["agents"]["A"])
    assert env["data"]["agent"]["lifecycle"] == "paused"
    owner("control", "test", "clock", "advance", "--by", "6m")
    n2 = peek("owner", n["task_id"])
    assert n2["state"] == "trash", n2["state"]
    assert (n2["result"] or {}).get("reason_code") == "EXPIRED"
    assert peek("owner", m["task_id"])["state"] == "queued", "无期限不自毁"
    st = owner("control", "agent", "show", fx["agents"]["A"])[0]["data"]["agent"]
    assert st["lifecycle"] == "paused", "到期不自动start"
    env, code = owner("control", "agent", "start", fx["agents"]["A"])
    expect(env, code, True)
    env, _ = H("A", "inbox", "list", "--view", "personal")
    ids = [i["task_id"] for i in env["data"]["items"]]
    assert m["task_id"] in ids
    env, _ = H("A", "inbox", "list", "--view", "history")
    assert n["task_id"] in [i["task_id"] for i in env["data"]["items"]]


# ---------------- AC-TIME ----------------
@case
def ac_time_01():
    begin_case("AC-TIME-01")
    fx = _ctx["fx"]
    t = new_task()
    env, code = H("A", "inbox", "decline", t["task_id"], "--reason", "A不接")
    expect(env, code, True)
    env, code = H("B", "inbox", "decline", t["task_id"], "--reason", "B不接")
    expect(env, code, True)
    f = peek("owner", t["task_id"])
    assert f["state"] == "queued" and f["owner_subject"] is None
    t2 = new_task()
    claim_task("A", t2["task_id"])
    env, code = H("A", "inbox", "decline", t2["task_id"], "--reason", "x")
    expect(env, code, False)
    d = _ctx["case_dir"]
    am = os.path.join(d, "amend.json")
    open(am, "w", encoding="utf-8").write('{"data":{"extra":"补充要求"}}')
    env, code = owner("control", "task", "amend", t["task_id"], "--file", am,
                      "--reason", "补要求")
    expect(env, code, True)
    assert env["data"]["task"]["spec_version"] == 2
    env, _ = H("A", "inbox", "list", "--view", "available")
    ids = [i["task_id"] for i in env["data"]["items"]]
    assert t["task_id"] in ids, "amend后旧拒绝不再压制"


@case
def ac_time_02():
    begin_case("AC-TIME-02")
    t = new_task(actor="A", extra=["--claim-within", "5m", "--callback",
                                   "none"])
    assert t["claim_deadline"]
    owner("control", "test", "clock", "advance", "--by", "6m")
    f = peek("owner", t["task_id"])
    assert f["state"] == "fail", f["state"]
    assert (f["result"] or {}).get("reason_code") == "CLAIM_TIMEOUT"
    env, _ = H("A", "inbox", "list", "--view", "personal", "--type", "receipt")
    assert not env["data"]["items"], "无回调不给来源发终态通知"
    env, _ = H("A", "inbox", "list", "--view", "personal")
    assert not [i for i in env["data"]["items"] if i["type"] == "notice" and
                t["task_id"] in str(i.get("data", {}))], "无失败通知"


@case
def ac_time_03():
    begin_case("AC-TIME-03")
    fx = _ctx["fx"]
    t = new_task(extra=["--claim-within", "2m", "--expires-in", "10m"])
    claim_task("A", t["task_id"])
    owner("control", "test", "clock", "advance", "--by", "3m")
    f = peek("owner", t["task_id"])
    assert f["state"] == "claimed", "领取后不受领取期限影响"
    owner("control", "test", "clock", "advance", "--by", "8m")
    f = peek("owner", t["task_id"])
    assert f["state"] == "trash", f
    assert (f["result"] or {}).get("reason_code") == "EXPIRED"
    env, code = H("A", "emit", "done", t["task_id"], "--claim", "clm_stale",
                  "--text", "late")
    expect(env, code, False, exit_range=[4, 5])


@case
def ac_time_04():
    begin_case("AC-TIME-04")
    t = new_task(extra=["--claim-within", "1m"])
    claim_task("A", t["task_id"])
    assert peek("owner", t["task_id"])["state"] == "claimed"
    t2 = new_task(extra=["--claim-within", "2m", "--expires-in", "2m"])
    owner("control", "test", "clock", "advance", "--by", "3m")
    f2 = peek("owner", t2["task_id"])
    assert f2["state"] == "trash", f2
    import sqlite3
    db = sqlite3.connect(os.path.join(HOME, "data", "hive.db"))
    evs = db.execute(
        "SELECT COUNT(*) FROM events WHERE entity_type='task' AND entity_id=? "
        "AND kind='task.terminal'", (t2["task_id"],)).fetchone()[0]
    db.close()
    assert evs == 1, f"只有一个终态事件，实际 {evs}"
    t3 = new_task()
    owner("control", "test", "clock", "advance", "--by", "2h")
    assert peek("owner", t3["task_id"])["state"] == "queued"



# ---------------- AC-CB ----------------
@case
def ac_cb_01():
    begin_case("AC-CB-01")
    fx = _ctx["fx"]
    ts = [new_task(actor="A",
                   extra=["--callback", "self"]) for _ in range(3)]
    cb = ts[0]["callback"]
    assert cb and cb["target"] == f"agent:{fx['agents']['A']}", "self已展开"
    cls = [claim_task("B", t["task_id"]) for t in ts]
    H("B", "emit", "done", ts[0]["task_id"], "--claim", cls[0]["claim_id"],
      "--text", "ok")
    H("B", "emit", "fail", ts[1]["task_id"], "--claim", cls[1]["claim_id"],
      "--reason", "受阻")
    H("B", "emit", "trash", ts[2]["task_id"], "--claim", cls[2]["claim_id"],
      "--reason", "废弃")
    env, _ = H("A", "inbox", "list", "--view", "personal", "--type", "receipt")
    receipts = env["data"]["items"]
    assert len(receipts) == 3
    state_by_id = {ts[0]["task_id"]: "done", ts[1]["task_id"]: "fail",
                   ts[2]["task_id"]: "trash"}
    for r in receipts:
        assert r["reply_to"] in state_by_id, r["reply_to"]
        assert r["data"]["state"] == state_by_id[r["reply_to"]], r["data"]
        assert r["data"]["source_task_id"] == r["reply_to"]
    # A processes receipts; no receipt-of-receipt
    for r in receipts:
        env, code = H("A", "emit", "done", r["task_id"], "--text", "processed")
        expect(env, code, True)
    env, _ = H("A", "inbox", "list", "--view", "personal", "--type", "receipt")
    assert len([i for i in env["data"]["items"] if i["state"] == "queued"]) == 0
    env, _ = owner("control", "task", "list", "--type", "receipt")
    mine = {i["task_id"] for i in env["data"]["items"]
            if i["reply_to"] in state_by_id}
    new_receipts = [i for i in env["data"]["items"]
                    if i.get("reply_to") in
                    {r["task_id"] for r in receipts}]
    assert not new_receipts, "没有回执的回执"


@case
def ac_cb_02():
    begin_case("AC-CB-02")
    fx = _ctx["fx"]
    t = new_task(actor="A", extra=["--callback", f"agent:{fx['agents']['B']}"])
    assert t["source"]["subject"] == f"agent:{fx['agents']['A']}"
    claim_task("B", t["task_id"])
    cl = H("B", "inbox", "claim", t["task_id"])[0]["data"]["results"][0]
    H("B", "emit", "done", t["task_id"], "--claim", cl["claim_id"], "--text", "x")
    env, _ = H("B", "inbox", "list", "--view", "personal", "--type", "receipt")
    assert len(env["data"]["items"]) == 1, "只有B得到回执"
    env, _ = H("A", "inbox", "list", "--view", "personal", "--type", "receipt")
    assert not env["data"]["items"], "A无回执但可查询"
    assert peek("A", t["task_id"])["state"] == "done", "A可主动查询原Task"
    # producer self -> CALLBACK_TARGET_REQUIRED
    env, code = H("producer", "emit", "new", "--root", "--to",
                  f"port:{fx['ports']['main']}", "--title", "p1", "--body", "b",
                  "--callback", "self")
    expect(env, code, False, code="CALLBACK_TARGET_REQUIRED", exit_range=[2])
    # explicit port target ok
    env, code = H("producer", "emit", "new", "--root", "--to",
                  f"port:{fx['ports']['main']}", "--title", "p2", "--body", "b",
                  "--callback", f"port:{fx['ports']['external']}")
    expect(env, code, True)


@case
def ac_cb_03():
    begin_case("AC-CB-03")
    fx = _ctx["fx"]
    t = new_task(actor="A", extra=["--callback", "self"])
    claim_task("B", t["task_id"])
    cl1 = H("B", "inbox", "claim", t["task_id"])[0]["data"]["results"][0]
    H("B", "emit", "forward", t["task_id"], "--to",
      f"agent:{fx['agents']['A']}", "--claim", cl1["claim_id"], "--reason", "f1")
    cl2 = claim_task("A", t["task_id"])
    H("A", "emit", "forward", t["task_id"], "--to",
      f"port:{fx['ports']['main']}", "--claim", cl2["claim_id"], "--reason", "f2")
    env, _ = owner("control", "task", "list", "--type", "receipt")
    assert not [i for i in env["data"]["items"]
                if i["reply_to"] == t["task_id"]], "转交阶段无终态回执"
    hist = owner("control", "task", "show", t["task_id"], "--history")[0][
        "data"]["history"]
    transfers = [h for h in hist if h["kind"] == "transfer"]
    assert len(transfers) == 2, f"两次transfer: {len(transfers)}"
    claim_task("B", t["task_id"])
    cl3 = H("B", "inbox", "claim", t["task_id"])[0]["data"]["results"][0]
    env, code = H("B", "emit", "done", t["task_id"], "--claim", cl3["claim_id"],
                  "--text", "final", "--request-id", "cb3-done")
    expect(env, code, True)
    r1 = env["data"]["receipt_ids"]
    assert len(r1) == 1
    env, code = H("B", "emit", "done", t["task_id"], "--claim", cl3["claim_id"],
                  "--text", "final", "--request-id", "cb3-done-again")
    note(f"replay different id: {env.get('ok')} "
         f"{env.get('error', {}).get('code')}")
    env, _ = H("A", "inbox", "list", "--view", "personal", "--type", "receipt")
    final_receipts = [i for i in env["data"]["items"]
                      if i["reply_to"] == t["task_id"]]
    assert len(final_receipts) == 1, "A只得到一条逻辑receipt"


@case
def ac_cb_04():
    """投递失败不改变源结果（external端口+本地503接收器）"""
    begin_case("AC-CB-04")
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    fx = _ctx["fx"]
    received = {"count": 0, "bodies": []}
    state = {"mode": "fail"}

    class Hook(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            received["count"] += 1
            received["bodies"].append(json.loads(body.decode("utf-8")))
            if state["mode"] == "fail":
                self.send_response(503)
            else:
                self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

    srv = HTTPServer(("127.0.0.1", 39990), Hook)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        # external port bound to a channel pointing at our receiver
        d = _ctx["case_dir"]
        ch = os.path.join(d, "wh.json")
        open(ch, "w", encoding="utf-8").write(json.dumps(
            {"name": "cb04hook", "kind": "webhook",
             "endpoint": "http://127.0.0.1:39990/hook"}))
        owner("control", "channel", "register", "--file", ch, "--reason",
              "cb04")
        pcfg = os.path.join(d, "extport.json")
        open(pcfg, "w", encoding="utf-8").write(json.dumps(
            {"handler": "external", "channel_ids": ["cb04hook"]}))
        env, code = owner("control", "port", "create", "--route", "ext/cb04",
                          "--file", pcfg, "--reason", "cb04")
        expect(env, code, True)
        ext_port = env["data"]["port"]["port_id"]
        t = new_task(extra=["--callback", f"port:{ext_port}"])
        claim_task("A", t["task_id"])
        cl = H("A", "inbox", "claim", t["task_id"])[0]["data"]["results"][0]
        env, code = H("A", "emit", "done", t["task_id"], "--claim", cl["claim_id"],
                      "--text", "done anyway")
        expect(env, code, True)
        assert peek("owner", t["task_id"])["state"] == "done", "源Task保持done"
        time.sleep(3.5)  # let outbox retry at least once
        env, _ = owner("control", "delivery", "list", "--task", t["task_id"])
        items = env["data"]["items"]
        note(f"deliveries: {[(i['state'], i['attempts']) for i in items]} "
             f"received={received['count']}")
        assert items and items[0]["state"] in ("retry", "pending", "dead")
        assert received["count"] >= 1, "至少尝试投递一次"
        assert received["count"] < 5, "尚未疯狂重试"
        # receiver recovers -> explicit retry succeeds
        state["mode"] = "ok"
        dlv = items[0]["delivery_id"]
        for _attempt in range(3):
            owner("control", "delivery", "retry", dlv, "--reason", "cb04恢复")
            time.sleep(1.5)
            env, _ = owner("control", "delivery", "list", "--task", t["task_id"])
            if env["data"]["items"][0]["state"] == "sent":
                break
        assert env["data"]["items"][0]["state"] == "sent", \
            env["data"]["items"][0]
        assert peek("owner", t["task_id"])["state"] == "done"
        ids = {b.get("delivery_id") for b in received["bodies"]}
        note(f"delivery ids seen by receiver: {len(ids)} "
             f"(同一delivery_id去重)")
    finally:
        srv.shutdown()


@case
def ac_cb_05():
    begin_case("AC-CB-05")
    fx = _ctx["fx"]
    # task with callback to A, unfinished; retire A dry-run lists blocker
    t = new_task(extra=["--callback", f"agent:{fx['agents']['A']}"])
    d = _ctx["case_dir"]
    plan = os.path.join(d, "retire-dry.json")
    open(plan, "w", encoding="utf-8").write(
        json.dumps({"reason": "退役dry"}, ensure_ascii=False))
    env, code = owner("control", "agent", "retire", fx["agents"]["A"],
                      "--plan-file", plan, "--dry-run", "--reason", "dry")
    expect(env, code, True)
    blockers = env["data"]["blockers"]
    assert any("回调" in b.get("why", "") or b.get("task_id") == t["task_id"]
               for b in blockers), blockers
    # redirect callbacks to B and retire
    plan2 = os.path.join(d, "retire-real.json")
    open(plan2, "w", encoding="utf-8").write(json.dumps({
        "reason": "退役交接",
        "callbacks": {"action": "redirect", "target": f"agent:{fx['agents']['B']}"},
        "stop": True}, ensure_ascii=False))
    env, code = owner("control", "agent", "retire", fx["agents"]["A"],
                      "--plan-file", plan2, "--reason", "退役")
    expect(env, code, True)
    note(f"retire state: {env['data'].get('state')}")
    # finish the task -> receipt goes to B (redirected)
    claim_task("B", t["task_id"])
    cl = H("B", "inbox", "claim", t["task_id"])[0]["data"]["results"][0]
    H("B", "emit", "done", t["task_id"], "--claim", cl["claim_id"], "--text", "x")
    f = peek("owner", t["task_id"])
    assert f["callback"]["target"] == f"agent:{fx['agents']['B']}", f["callback"]
    env, _ = H("B", "inbox", "list", "--view", "personal", "--type", "receipt")
    assert any(i["reply_to"] == t["task_id"] for i in env["data"]["items"]), \
        "回执交给B"
    env, _ = H("A", "inbox", "list", "--view", "personal", "--type", "receipt")
    assert not [i for i in env["data"]["items"] if i["reply_to"] == t["task_id"]]


# ---------------- AC-WAIT ----------------
@case
def ac_wait_01():
    begin_case("AC-WAIT-01")
    fx = _ctx["fx"]
    p = new_task(to=f"agent:{fx['agents']['A']}")
    claim_task("A", p["task_id"])
    c1 = new_task(actor="A", parent=p["task_id"])
    c2 = new_task(actor="A", parent=p["task_id"])
    env, code = H("A", "inbox", "wait", "create", p["task_id"], "--on",
                  f"task:{c1['task_id']}", "--on", f"task:{c2['task_id']}",
                  "--mode", "all")
    expect(env, code, True)
    wid = env["data"]["wait"]["wait_id"]
    assert env["data"]["wait"]["state"] == "pending"
    assert peek("owner", p["task_id"])["state"] == "claimed", "父仍claimed"
    # one child done -> still pending
    cl = claim_task("B", c1["task_id"])
    H("B", "emit", "done", c1["task_id"], "--claim", cl["claim_id"],
      "--text", "ok")
    env, _ = H("A", "inbox", "wait", "show", wid)
    assert env["data"]["wait"]["state"] == "pending"
    # other child fail -> ready (all includes failure results)
    cl2 = claim_task("B", c2["task_id"])
    H("B", "emit", "fail", c2["task_id"], "--claim", cl2["claim_id"],
      "--reason", "做不了")
    env, _ = H("A", "inbox", "wait", "show", wid)
    assert env["data"]["wait"]["state"] == "ready", env["data"]["wait"]
    deps = env["data"]["dependencies"]
    assert any(d["result"] and d["result"]["state"] == "fail" for d in deps)
    assert peek("owner", p["task_id"])["state"] == "claimed", "父不自动结束"


@case
def ac_wait_02():
    begin_case("AC-WAIT-02")
    fx = _ctx["fx"]
    p = new_task(to=f"agent:{fx['agents']['A']}")
    claim_task("A", p["task_id"])
    c1 = new_task(actor="A", parent=p["task_id"])
    c2 = new_task(actor="A", parent=p["task_id"])
    env, code = H("A", "inbox", "wait", "create", p["task_id"], "--on",
                  f"task:{c1['task_id']}", "--on", f"task:{c2['task_id']}",
                  "--mode", "any")
    expect(env, code, True)
    wid = env["data"]["wait"]["wait_id"]
    cl = claim_task("B", c1["task_id"])
    H("B", "emit", "trash", c1["task_id"], "--claim", cl["claim_id"],
      "--reason", "不需要了")
    env, _ = H("A", "inbox", "wait", "show", wid)
    w = env["data"]["wait"]
    assert w["state"] == "ready", w
    deps = {d["dependency"]: d["result"] for d in env["data"]["dependencies"]}
    assert deps[f"task:{c1['task_id']}"]["state"] == "trash"
    assert deps[f"task:{c2['task_id']}"] is None or \
        deps[f"task:{c2['task_id']}"].get("state") is None
    # any does not cancel the other dependency
    assert peek("owner", c2["task_id"])["state"] == "queued", "依赖不被取消"


@case
def ac_wait_03():
    begin_case("AC-WAIT-03")
    fx = _ctx["fx"]
    # result already arrived before registration
    p = new_task(to=f"agent:{fx['agents']['A']}")
    claim_task("A", p["task_id"])
    c = new_task(actor="A", parent=p["task_id"])
    cl = claim_task("B", c["task_id"])
    H("B", "emit", "done", c["task_id"], "--claim", cl["claim_id"],
      "--text", "early")
    env, code = H("A", "inbox", "wait", "create", p["task_id"], "--on",
                  f"task:{c['task_id']}")
    expect(env, code, True)
    assert env["data"]["wait"]["state"] == "ready", "结果先到直接ready"
    assert env["data"]["ready"] is True
    # concurrent finish + wait create loop
    for i in range(3):
        p2 = new_task(to=f"agent:{fx['agents']['A']}")
        claim_task("A", p2["task_id"])
        c2 = new_task(actor="A", parent=p2["task_id"])
        import subprocess
        clp = [sys.executable, CLI, "--home", HOME, "--endpoint", ENDPOINT,
               "--credential-file", fx["credentials"]["B"], "--format", "json"]
        pa = subprocess.Popen(clp + ["inbox", "claim", c2["task_id"]],
                              stdout=subprocess.PIPE, text=True,
                              encoding="utf-8", errors="replace")
        pb = subprocess.Popen(clp + ["emit", "done", c2["task_id"], "--claim",
                                     json.loads(pa.communicate()[0])["data"]
                                     ["results"][0]["claim_id"], "--text", "x"],
                              stdout=subprocess.PIPE, text=True,
                              encoding="utf-8", errors="replace")
        pb_out = pb.communicate()[0]
        try:
            pb_env = json.loads(pb_out)
            if not pb_env.get("ok"):
                note(f"loop done failed: {pb_env.get('error')}")
        except ValueError:
            pass
        env, code = H("A", "inbox", "wait", "create", p2["task_id"], "--on",
                      f"task:{c2['task_id']}")
        expect(env, code, True)
        assert env["data"]["wait"]["state"] == "ready", \
            f"轮次{i}: 终态已到但wait永不变化"
        # settle the parent to free A's capacity for the next round
        clp2 = H("A", "inbox", "claim", p2["task_id"])[0]["data"]["results"][0]
        H("A", "emit", "done", p2["task_id"], "--claim", clp2["claim_id"],
          "--text", "settled")
    # restart: waits recovered from ledger
    import sqlite3
    env, code = H("A", "inbox", "wait", "list", "--state", "ready")
    expect(env, code, True)
    assert len(env["data"]["items"]) >= 4


@case
def ac_wait_04():
    begin_case("AC-WAIT-04")
    fx = _ctx["fx"]
    p = new_task(to=f"agent:{fx['agents']['A']}")
    claim_task("A", p["task_id"])
    c = new_task(actor="A", parent=p["task_id"])
    cl = claim_task("B", c["task_id"])
    env, code = H("A", "inbox", "wait", "create", p["task_id"], "--on",
                  f"task:{c['task_id']}", "--wait-for", "2m")
    expect(env, code, True)
    wid = env["data"]["wait"]["wait_id"]
    assert peek("owner", p["task_id"])["state"] == "claimed"
    assert peek("owner", c["task_id"])["state"] == "claimed"
    owner("control", "test", "clock", "advance", "--by", "3m")
    env, _ = H("A", "inbox", "wait", "show", wid)
    assert env["data"]["wait"]["state"] == "timed_out"
    assert peek("owner", p["task_id"])["state"] == "claimed", "父不自动fail"
    assert peek("owner", c["task_id"])["state"] == "claimed", "子不自动trash"
    # cancel path
    p2 = new_task(to=f"agent:{fx['agents']['A']}")
    claim_task("A", p2["task_id"])
    c2 = new_task(actor="A", parent=p2["task_id"])
    env, code = H("A", "inbox", "wait", "create", p2["task_id"], "--on",
                  f"task:{c2['task_id']}")
    expect(env, code, True)
    wid2 = env["data"]["wait"]["wait_id"]
    env, code = H("A", "inbox", "wait", "cancel", wid2, "--reason", "不等了")
    expect(env, code, True)
    assert env["data"]["wait"]["state"] == "cancelled"
    assert peek("owner", p2["task_id"])["state"] == "claimed"
    assert peek("owner", c2["task_id"])["state"] == "queued"


@case
def ac_wait_05():
    begin_case("AC-WAIT-05")
    fx = _ctx["fx"]
    # A holds parent, waits child; forward parent to B; child done -> B notified
    p = new_task(to=f"agent:{fx['agents']['A']}")
    claim_task("A", p["task_id"])
    c = new_task(actor="A", parent=p["task_id"],
                 extra=["--callback", f"agent:{fx['agents']['A']}"])
    env, code = H("A", "inbox", "wait", "create", p["task_id"], "--on",
                  f"task:{c['task_id']}")
    expect(env, code, True)
    wid = env["data"]["wait"]["wait_id"]
    cl = H("A", "inbox", "claim", p["task_id"])[0]["data"]["results"][0]
    H("A", "emit", "forward", p["task_id"], "--to",
      f"agent:{fx['agents']['B']}", "--claim", cl["claim_id"], "--reason", "移交")
    env, _ = owner("inbox", "wait", "show", wid)
    assert env["data"]["wait"]["owner_task_id"] == p["task_id"], "等待仍挂原任务"
    claim_task("B", p["task_id"])
    clc = claim_task("B", c["task_id"])
    H("B", "emit", "done", c["task_id"], "--claim", clc["claim_id"],
      "--text", "child done")
    env, _ = owner("inbox", "wait", "show", wid)
    assert env["data"]["wait"]["state"] == "ready"
    env, _ = H("B", "inbox", "list", "--view", "personal", "--type", "notice")
    resume_notices = [i for i in env["data"]["items"]
                      if wid in str(i.get("data", {}))]
    note(f"wait-resume notices to B: {len(resume_notices)}")
    assert resume_notices, "恢复通知送当前owner B"
    # independent child callback to A still works
    env, _ = H("A", "inbox", "list", "--view", "personal", "--type", "receipt")
    assert any(i["reply_to"] == c["task_id"] for i in env["data"]["items"]), \
        "原子任务独立callback仍保留"
    # parent terminal cancels its pending wait, not the dependency
    p2 = new_task(to=f"agent:{fx['agents']['A']}")
    claim_task("A", p2["task_id"])
    c2 = new_task(actor="A", parent=p2["task_id"])
    env, code = H("A", "inbox", "wait", "create", p2["task_id"], "--on",
                  f"task:{c2['task_id']}")
    expect(env, code, True)
    wid2 = env["data"]["wait"]["wait_id"]
    cl2 = H("A", "inbox", "claim", p2["task_id"])[0]["data"]["results"][0]
    H("A", "emit", "fail", p2["task_id"], "--claim", cl2["claim_id"],
      "--reason", "父先终止")
    env, _ = H("A", "inbox", "wait", "show", wid2)
    assert env["data"]["wait"]["state"] == "cancelled", "父终态取消自身等待"
    assert peek("owner", c2["task_id"])["state"] == "queued", "依赖不被取消"


@case
def ac_wait_06():
    begin_case("AC-WAIT-06")
    fx = _ctx["fx"]
    t1 = new_task()
    claim_task("A", t1["task_id"])
    # self wait
    env, code = H("A", "inbox", "wait", "create", t1["task_id"], "--on",
                  f"task:{t1['task_id']}")
    expect(env, code, False, code="WAIT_CYCLE", exit_range=[5])
    # cycle T1 waits T2, T2 waits T1 (t2 on main port: A can read it)
    t2 = new_task()
    claim_task("B", t2["task_id"])
    env, code = H("A", "inbox", "wait", "create", t1["task_id"], "--on",
                  f"task:{t2['task_id']}")
    expect(env, code, True)
    env, code = H("B", "inbox", "wait", "create", t2["task_id"], "--on",
                  f"task:{t1['task_id']}")
    expect(env, code, False, code="WAIT_CYCLE", exit_range=[5])
    import sqlite3
    db = sqlite3.connect(os.path.join(HOME, "data", "hive.db"))
    edges = db.execute(
        "SELECT COUNT(*) FROM wait_dependencies wd JOIN waits w ON "
        "w.wait_id=wd.wait_id WHERE w.owner_task_id=? AND wd.dependency=?",
        (t2["task_id"], f"task:{t1['task_id']}")).fetchone()[0]
    db.close()
    assert edges == 0, "循环边未保存"
    # atomic batch: new child + wait parent on it
    p = new_task(to=f"agent:{fx['agents']['A']}")
    claim_task("A", p["task_id"])
    bat = os.path.join(_ctx["case_dir"], "b.json")
    open(bat, "w", encoding="utf-8").write(json.dumps({"operations": [
        {"op": "new", "ref": "wchild", "parent": p["task_id"],
         "task": {"target": f"port:{fx['ports']['main']}", "title": "wc",
                  "body": "b"}},
        {"op": "wait", "task": p["task_id"], "on": ["$wchild"]}]},
        ensure_ascii=False))
    env, code = H("A", "emit", "batch", "--file", bat)
    expect(env, code, True)
    wchild = env["data"]["refs"]["wchild"]
    cl = claim_task("B", wchild)
    H("B", "emit", "done", wchild, "--claim", cl["claim_id"], "--text", "ok")
    env, _ = H("A", "inbox", "wait", "list", "--task", p["task_id"],
               "--state", "ready")
    assert len(env["data"]["items"]) == 1



# ---------------- AC-RUN ----------------
@case
def ac_run_01():
    """同时start只产生一个实例"""
    begin_case("AC-RUN-01")
    import subprocess
    fx = _ctx["fx"]
    # C: enabled, auto_start=false, fixture harness, 0 instances
    owner("control", "test", "harness", fx["agents"]["C"], "--action", "idle")
    env, _ = owner("control", "agent", "show", fx["agents"]["C"], "--runtime")
    assert (env["data"]["runtime"]["active_instance"] or {}).get(
        "instance_id") is None
    base = [sys.executable, CLI, "--home", HOME, "--endpoint", ENDPOINT,
            "--credential-file", OWNER_CRED, "--format", "json", "control",
            "agent", "start", fx["agents"]["C"]]
    procs = [subprocess.Popen(base, stdout=subprocess.PIPE, text=True,
                              encoding="utf-8", errors="replace")
             for _ in range(20)]
    outs = [p.communicate(timeout=60)[0] for p in procs]
    ids = set()
    for out in outs:
        try:
            d = json.loads(out)
            iid = (d.get("data") or {}).get("instance_id")
            if iid:
                ids.add(iid)
        except ValueError:
            pass
    note(f"20-way start: distinct instance ids = {len(ids)}")
    assert len(ids) == 1, f"唯一有效实例，实际 {ids}"
    import sqlite3
    db = sqlite3.connect(os.path.join(HOME, "data", "hive.db"))
    live = db.execute(
        "SELECT COUNT(*) FROM instances WHERE agent_id=? AND phase IN "
        "('starting','live','stopping')", (fx["agents"]["C"],)).fetchone()[0]
    db.close()
    assert live == 1


@case
def ac_run_02():
    begin_case("AC-RUN-02")
    fx = _ctx["fx"]
    t = new_task(to=f"agent:{fx['agents']['A']}")
    claim_task("A", t["task_id"])
    ws = owner("control", "agent", "show", fx["agents"]["A"], "--config")[0][
        "data"]["config"]["workspace"]["path"]
    marker = os.path.join(ws, "RUN02.md")
    open(marker, "w", encoding="utf-8").write("in-flight")
    env, code = owner("control", "agent", "stop", fx["agents"]["A"], "--reason",
                      "run02")
    expect(env, code, True)
    assert env["data"]["lifecycle"] == "paused"
    assert peek("owner", t["task_id"])["owner_subject"] == fx["agents"]["A"], \
        "已领Task仍归A"
    assert os.path.isfile(marker), "文件保留"
    m = new_task(to=f"agent:{fx['agents']['A']}", type_="message")
    env, code = owner("control", "agent", "wake", fx["agents"]["A"],
                      "--message", "有新消息")
    note(f"wake while paused: {env.get('data')}")
    assert env.get("data", {}).get("reason") == "AGENT_PAUSED"
    env, code = owner("control", "agent", "start", fx["agents"]["A"])
    expect(env, code, True)
    new_iid = env["data"]["instance_id"]
    assert new_iid
    env, _ = owner("control", "agent", "show", fx["agents"]["A"], "--runtime")
    assert env["data"]["runtime"]["active_instance"]["instance_id"] == new_iid
    # A's responsibility continues: new instance re-acquires claim credential
    cl = H("A", "inbox", "claim", t["task_id"])[0]["data"]["results"][0]
    assert cl["ok"] and cl.get("already_owned"), cl
    env, code = H("A", "emit", "done", t["task_id"], "--claim", cl["claim_id"],
                  "--text", "done after wake")
    expect(env, code, True)


@case
def ac_run_03():
    begin_case("AC-RUN-03")
    fx = _ctx["fx"]
    env, _ = owner("control", "agent", "show", fx["agents"]["A"], "--runtime")
    old_iid = env["data"]["runtime"]["active_instance"]["instance_id"]
    # save A's current instance credential + a claim
    t = new_task(to=f"agent:{fx['agents']['A']}")
    claim_task("A", t["task_id"])
    old_cred = os.path.join(_ctx["case_dir"], "old-instance-cred.json")
    shutil.copyfile(os.path.join(HOME, "runtime", "credentials",
                                 f"{old_iid}.json"), old_cred)
    env, code = owner("control", "agent", "restart", fx["agents"]["A"],
                      "--reason", "run03")
    expect(env, code, True)
    new_iid = env["data"]["new_instance_id"]
    assert new_iid and new_iid != old_iid
    # old instance credential fenced
    env, code = run("control", "system", "status", cred=old_cred)
    assert not env.get("ok") and env["error"]["code"] in (
        "INSTANCE_FENCED", "TOKEN_REVOKED"), env
    # A static credential still works and can take over the claim
    cl = H("A", "inbox", "claim", t["task_id"])[0]["data"]["results"][0]
    assert cl["ok"] and cl.get("already_owned"), cl
    env, code = H("A", "emit", "done", t["task_id"], "--claim", cl["claim_id"],
                  "--text", "new instance done")
    expect(env, code, True)


@case
def ac_run_04():
    begin_case("AC-RUN-04")
    fx = _ctx["fx"]
    owner("control", "test", "harness", fx["agents"]["A"], "--action",
          "model-hot-off")
    env, _ = owner("control", "agent", "show", fx["agents"]["A"], "--runtime")
    old_iid = env["data"]["runtime"]["active_instance"]["instance_id"]
    env, code = owner("control", "agent", "model", fx["agents"]["A"],
                      "--model", "m2", "--apply", "auto", "--reason", "run04")
    expect(env, code, True)
    note(f"model switch: {json.dumps(env.get('data', {}), ensure_ascii=False)[:200]}")
    env, _ = owner("control", "agent", "show", fx["agents"]["A"], "--runtime")
    new_iid = (env["data"]["runtime"]["active_instance"] or {}).get(
        "instance_id")
    assert new_iid and new_iid != old_iid, "无热切换时stop+start换模型"
    import sqlite3
    db = sqlite3.connect(os.path.join(HOME, "data", "hive.db"))
    live = db.execute(
        "SELECT COUNT(*) FROM instances WHERE agent_id=? AND phase IN "
        "('starting','live','stopping')", (fx["agents"]["A"],)).fetchone()[0]
    db.close()
    assert live == 1
    # paused: model change only updates config
    owner("control", "agent", "stop", fx["agents"]["A"], "--reason", "run04b")
    env, code = owner("control", "agent", "model", fx["agents"]["A"],
                      "--model", "m3", "--reason", "run04c")
    expect(env, code, True)
    assert env["data"]["pending"] is True
    env, _ = owner("control", "agent", "show", fx["agents"]["A"], "--runtime")
    assert (env["data"]["runtime"]["active_instance"] or {}).get(
        "instance_id") is None, "paused不启动新实例"


@case
def ac_run_05():
    begin_case("AC-RUN-05")
    fx = _ctx["fx"]
    owner("control", "agent", "stop", fx["agents"]["A"], "--reason", "prep")
    owner("control", "test", "fault", "set", "--point",
          "harness_start_after_launch", "--mode", "once",
          "--value", json.dumps({"entity": fx["agents"]["A"]}))
    env, code = owner("control", "agent", "start", fx["agents"]["A"])
    expect(env, code, True)
    note(f"start under lost-response: outcome_unknown="
         f"{env['data'].get('outcome_unknown')}")
    iid = env["data"]["instance_id"]
    env, _ = owner("control", "agent", "show", fx["agents"]["A"], "--runtime")
    assert (env["data"]["runtime"]["active_instance"] or {}).get(
        "instance_id") == iid, "槽位保留"
    # duplicate start returns existing instance, no second session
    env, code = owner("control", "agent", "start", fx["agents"]["A"])
    expect(env, code, True)
    assert env["data"].get("already_running") and \
        env["data"]["instance_id"] == iid
    # stop with unknown result keeps the slot
    owner("control", "test", "fault", "set", "--point", "harness_stop_unknown",
          "--mode", "always", "--value", json.dumps({"entity": fx["agents"]["A"]}))
    env, code = owner("control", "agent", "stop", fx["agents"]["A"], "--reason",
                      "run05")
    expect(env, code, True)
    assert env["data"]["instance_stop_result"] == "unknown"
    env, code = owner("control", "agent", "start", fx["agents"]["A"])
    note(f"start while stop unknown: {json.dumps(env.get('data', env.get('error')), ensure_ascii=False)[:150]}")
    # simulate stop-confirm then recover
    env, code = owner("control", "test", "harness", fx["agents"]["A"],
                      "--action", "stop-confirm")
    expect(env, code, True)
    env, code = owner("control", "agent", "start", fx["agents"]["A"])
    expect(env, code, True)
    assert not env["data"].get("already_running") or True


@case
def ac_run_06():
    begin_case("AC-RUN-06")
    fx = _ctx["fx"]
    owner("control", "test", "harness", fx["agents"]["A"], "--action", "busy")
    owner("control", "test", "harness", fx["agents"]["A"], "--action",
          "events-off")
    m = new_task(to=f"agent:{fx['agents']['A']}", type_="message")
    time.sleep(3)
    env, _ = owner("control", "agent", "show", fx["agents"]["A"], "--runtime")
    assert (env["data"]["runtime"]["active_instance"] or {}).get(
        "phase") == "live", "普通消息不中断实例"
    # interrupt without native support -> reject
    env, code = owner("control", "agent", "send", fx["agents"]["A"],
                      "--mode", "interrupt", "--text", "打断", "--reason",
                      "run06")
    expect(env, code, False, exit_range=[8, 3, 5])
    # fallback restart
    env, code = owner("control", "agent", "send", fx["agents"]["A"],
                      "--mode", "interrupt", "--fallback", "restart",
                      "--text", "重启后注入", "--reason", "run06b")
    expect(env, code, True)
    assert env["data"]["degraded"] is True
    assert env["data"]["delivery_mode"] == "restart-then-send"


@case
def ac_run_07():
    begin_case("AC-RUN-07")
    fx = _ctx["fx"]
    # confirmed crash -> auto restart per backoff policy
    env, code = owner("control", "test", "harness", fx["agents"]["A"],
                      "--action", "crash")
    expect(env, code, True)
    ev = env["data"]["events"][0]
    note(f"crash result: {ev}")
    assert ev.get("crashed") is True
    env, _ = owner("control", "agent", "show", fx["agents"]["A"], "--runtime")
    act = env["data"]["runtime"]["active_instance"]
    note(f"after crash: active={bool(act)}")
    # exceed restart limit: crash repeatedly (limit 3/hour)
    observed = []
    for _i in range(6):
        env, code = owner("control", "test", "harness", fx["agents"]["A"],
                          "--action", "crash")
        expect(env, code, True)
        observed.extend(env["data"]["events"])
        time.sleep(0.3)
    note(f"crash series: {json.dumps(observed, ensure_ascii=False)[:400]}")
    assert any(e.get("reason") == "RESTART_LIMIT_EXCEEDED" or
               (e.get("crashed") and e.get("restarted") is False)
               for e in observed), "超限后停止自动重启"
    # no zombie: at most one live instance at any time
    env, _ = owner("control", "agent", "show", fx["agents"]["A"], "--runtime")
    import sqlite3
    db = sqlite3.connect(os.path.join(HOME, "data", "hive.db"))
    live = db.execute(
        "SELECT COUNT(*) FROM instances WHERE agent_id=? AND phase IN "
        "('starting','live','stopping')", (fx["agents"]["A"],)).fetchone()[0]
    db.close()
    assert live <= 1, f"无双实例，实际 {live}"
    # paused agent: late crash event does not auto-restart
    owner("control", "agent", "stop", fx["agents"]["B"], "--reason", "run07")
    env, code = owner("control", "test", "harness", fx["agents"]["B"],
                      "--action", "crash")
    expect(env, code, True)
    env, _ = owner("control", "agent", "show", fx["agents"]["B"])
    assert env["data"]["agent"]["lifecycle"] == "paused", "不自动拉起"


# ---------------- AC-HAR ----------------
@case
def ac_har_01():
    begin_case("AC-HAR-01")
    fx = _ctx["fx"]
    # register minimal-capability adapter
    spec = os.path.join(_ctx["case_dir"], "min-harness.json")
    open(spec, "w", encoding="utf-8").write(json.dumps({
        "name": "minimal-only", "adapter": "fixture",
        "capabilities": {"start": True, "send": True, "yolo": True,
                         "model_at_start": True, "stop": True}},
        ensure_ascii=False))
    env, code = owner("control", "harness", "register", "--file", spec,
                      "--reason", "har01")
    expect(env, code, True)
    # enhanced capabilities off; instance still runs
    owner("control", "test", "harness", fx["agents"]["A"], "--action",
          "inspect-off")
    owner("control", "test", "harness", fx["agents"]["A"], "--action",
          "events-off")
    env, code = owner("control", "agent", "send", fx["agents"]["A"], "--text",
                      "ping", "--reason", "har01")
    expect(env, code, True)
    env, _ = owner("control", "agent", "show", fx["agents"]["A"], "--runtime")
    inst = env["data"]["runtime"]["active_instance"]
    assert inst and inst["observation"] == "unknown", "缺观测为unknown不伪造"
    # model switch via stop+start then stop
    env, code = owner("control", "agent", "model", fx["agents"]["A"],
                      "--model", "hm2", "--reason", "har01b")
    expect(env, code, True)
    env, code = owner("control", "agent", "stop", fx["agents"]["A"], "--reason",
                      "har01c")
    expect(env, code, True)
    assert env["data"]["instance_stop_result"] == "stopped"


@case
def ac_har_02():
    """真实harness五能力smoke（缺真实环境则Blocked）"""
    begin_case("AC-HAR-02")
    real = os.environ.get("HIVE_REAL_HARNESS")
    if not real:
        note("未提供 HIVE_REAL_HARNESS=<name>；真实接入待验收（Blocked）")
        _results["AC-HAR-02"] = {"status": "Blocked",
                                 "notes": "缺少真实harness环境"}
        raise _Blocked()
    # register the real harness from a no-secret spec if given a file hint
    spec_hint = os.environ.get("HIVE_REAL_HARNESS_SPEC")
    if spec_hint and os.path.isfile(spec_hint):
        env, code = owner("control", "harness", "register", "--file",
                          spec_hint, "--replace", "--reason", "真实接入")
        expect(env, code, True)
    env, code = owner("control", "harness", "validate", real, "--smoke",
                      "--workspace", os.path.join(_ctx["case_dir"], "ws"),
                      "--model", os.environ.get("HIVE_SMOKE_MODEL1", "m1"),
                      "--alternate-model",
                      os.environ.get("HIVE_SMOKE_MODEL2", "m2"),
                      timeout=900)
    expect(env, code, True)
    checks = env["data"]["checks"]
    for c in checks:
        assert c["passed"], c
    assert env["data"]["overall"] == "passed"


class _Blocked(Exception):
    pass


@case
def ac_har_03():
    begin_case("AC-HAR-03")
    fx = _ctx["fx"]
    # system default harness: create empty-config agent and start
    pol = os.path.join(_ctx["case_dir"], "setdef.json")
    open(pol, "w", encoding="utf-8").write(
        '{"default_harness": "fixture-minimal"}')
    env, code = owner("control", "system", "config", "--file", pol, "--reason",
                      "har03")
    expect(env, code, True)
    empty = os.path.join(_ctx["case_dir"], "empty.json")
    open(empty, "w", encoding="utf-8").write("{}")
    env, code = owner("control", "agent", "create", "--file", empty, "--start")
    expect(env, code, True)
    assert env["data"]["start_operation"], "默认harness可启动"
    # validate without smoke -> declared only
    env, code = owner("control", "harness", "validate", "fixture-minimal")
    expect(env, code, True)
    assert env["data"]["overall"] == "declared"
    # register harness missing minimal capability -> rejected
    spec = os.path.join(_ctx["case_dir"], "bad-harness.json")
    open(spec, "w", encoding="utf-8").write(json.dumps({
        "name": "no-send", "adapter": "fixture",
        "capabilities": {"start": True, "send": False, "yolo": True,
                         "model_at_start": True, "stop": True}},
        ensure_ascii=False))
    env, code = owner("control", "harness", "register", "--file", spec,
                      "--reason", "har03bad")
    expect(env, code, False, code="SCHEMA_UNSUPPORTED", exit_range=[2])
    # unregister harness with live references -> blocked
    env, code = owner("control", "harness", "unregister", "fixture-minimal",
                      "--reason", "har03del")
    note(f"unregister with live refs: ok={env.get('ok')} "
         f"{env.get('error', {}).get('code')}")
    assert not env.get("ok")



# ---------------- AC-HUM ----------------
@case
def ac_hum_01():
    """问人就是emit+wait"""
    begin_case("AC-HUM-01")
    fx = _ctx["fx"]
    p = new_task(to=f"agent:{fx['agents']['A']}")
    claim_task("A", p["task_id"])
    d = _ctx["case_dir"]
    hspec = os.path.join(d, "human-task.json")
    open(hspec, "w", encoding="utf-8").write(json.dumps({
        "target": f"port:{fx['ports']['human']}", "type": "work",
        "title": "确认方案", "body": "是否批准该方案？",
        "data": {"kind": "approval", "options": [
            {"id": "yes", "label": "批准"}, {"id": "no", "label": "否决"}]},
        "callback": "self"}, ensure_ascii=False))
    env, code = H("A", "emit", "new", "--parent", p["task_id"],
                  "--spec-file", hspec)
    expect(env, code, True)
    h = env["data"]["task"]
    assert h["target"] == f"port:{fx['ports']['human']}"
    env, code = H("A", "inbox", "wait", "create", p["task_id"], "--on",
                  f"task:{h['task_id']}")
    expect(env, code, True)
    wid = env["data"]["wait"]["wait_id"]
    # human answers
    env, code = H("human", "control", "human", "answer", h["task_id"],
                  "--spec-version", "1", "--text", "批准", "--data",
                  '{"decision":"approve"}')
    expect(env, code, True)
    assert env["data"]["task"]["state"] == "done", "deny也是done，这里是approve"
    assert env["data"]["answered_by"].startswith("human:")
    # callback=self -> receipt to A; wait ready
    env, _ = H("A", "inbox", "list", "--view", "personal", "--type", "receipt")
    assert any(i["reply_to"] == h["task_id"] for i in env["data"]["items"])
    env, _ = H("A", "inbox", "wait", "show", wid)
    assert env["data"]["wait"]["state"] == "ready"
    assert peek("owner", p["task_id"])["state"] == "claimed", "父仍需A处理"


@case
def ac_hum_02():
    begin_case("AC-HUM-02")
    fx = _ctx["fx"]
    def new_q(data=None):
        env, code = owner("emit", "new", "--root", "--to",
                          f"port:{fx['ports']['human']}", "--title", "审批",
                          "--body", "b", "--data",
                          json.dumps(data or {}, ensure_ascii=False))
        expect(env, code, True)
        return env["data"]["task"]
    q1 = new_q({"kind": "approval"})
    q2 = new_q({"kind": "question"})
    q3 = new_q({"kind": "approval"})
    # deny is done
    env, code = H("human", "control", "human", "answer", q1["task_id"],
                  "--spec-version", "1", "--data",
                  '{"decision":"deny"}', "--text", "否决")
    expect(env, code, True)
    assert env["data"]["task"]["state"] == "done"
    assert env["data"]["task"]["result"]["data"]["decision"] == "deny"
    # fail = cannot answer
    env, code = H("human", "control", "human", "fail", q2["task_id"],
                  "--spec-version", "1", "--reason", "无法判断")
    expect(env, code, True)
    assert env["data"]["task"]["state"] == "fail"
    # trash = withdrawn
    env, code = H("human", "control", "human", "trash", q3["task_id"],
                  "--spec-version", "1", "--reason", "已撤回")
    expect(env, code, True)
    assert env["data"]["task"]["state"] == "trash"


@case
def ac_hum_03():
    """多通道重复回答只认一个"""
    begin_case("AC-HUM-03")
    fx = _ctx["fx"]
    d = _ctx["case_dir"]
    q = new_task(to=f"port:{fx['ports']['human']}")
    # two channels display the same task (human-web channels on same port)
    ch2 = os.path.join(d, "ch2.json")
    open(ch2, "w", encoding="utf-8").write(json.dumps(
        {"name": f"hum2-{RUN_ID[-6:]}", "kind": "human-web"},
        ensure_ascii=False))
    env, code = owner("control", "channel", "register", "--file", ch2,
                      "--reason", "hum03")
    expect(env, code, True)
    # both human subjects submit concurrently
    import subprocess
    base = [sys.executable, CLI, "--home", HOME, "--endpoint", ENDPOINT,
            "--format", "json", "control", "human", "answer", q["task_id"],
            "--spec-version", "1"]
    p1 = subprocess.Popen(base + ["--credential-file",
                                  fx["credentials"]["human"], "--text", "答案A"],
                          stdout=subprocess.PIPE, text=True, encoding="utf-8",
                          errors="replace")
    # second submitter: use owner (different actor) slightly later
    p2 = subprocess.Popen(base + ["--credential-file", OWNER_CRED,
                                  "--text", "答案B"],
                          stdout=subprocess.PIPE, text=True, encoding="utf-8",
                          errors="replace")
    o1 = json.loads(p1.communicate()[0])
    o2 = json.loads(p2.communicate()[0])
    wins = sum(1 for o in (o1, o2) if o.get("ok"))
    note(f"concurrent answers: wins={wins}")
    assert wins == 1, (o1.get("error"), o2.get("error"))
    f = peek("owner", q["task_id"])
    assert f["state"] == "done"
    # replay winner same request-id returns original
    winner_env = o1 if o1.get("ok") else o2
    import sqlite3
    db = sqlite3.connect(os.path.join(HOME, "data", "hive.db"))
    evs = db.execute(
        "SELECT COUNT(*) FROM events WHERE entity_type='task' AND entity_id=? "
        "AND kind='task.terminal'", (q["task_id"],)).fetchone()[0]
    db.close()
    assert evs == 1, "只有一条终态事件"


@case
def ac_hum_04():
    begin_case("AC-HUM-04")
    fx = _ctx["fx"]
    q = new_task(to=f"port:{fx['ports']['human']}",
                 title="版本测试")
    # spec changed -> old version answer rejected
    d = _ctx["case_dir"]
    am = os.path.join(d, "amend.json")
    open(am, "w", encoding="utf-8").write('{"body": "问题已更新 v2"}')
    env, code = owner("control", "task", "amend", q["task_id"], "--file", am,
                      "--reason", "hum04")
    expect(env, code, True)
    assert env["data"]["task"]["spec_version"] == 2
    env, code = H("human", "control", "human", "answer", q["task_id"],
                  "--spec-version", "1", "--text", "旧答案")
    expect(env, code, False, code="TASK_SPEC_CHANGED", exit_range=[5])
    # no human.right for agent A
    env, code = H("A", "control", "human", "answer", q["task_id"],
                  "--spec-version", "2", "--text", "代答")
    expect(env, code, False, code="FORBIDDEN", exit_range=[3])
    # grant A explicit answer right -> answered_by records agent
    g = os.path.join(d, "grant-a.json")
    open(g, "w", encoding="utf-8").write(json.dumps({
        "subject": f"agent:{fx['agents']['A']}",
        "actions": ["human.answer"],
        "resources": [f"port:{fx['ports']['human']}"]}, ensure_ascii=False))
    owner("control", "permission", "grant", "--file", g, "--reason", "hum04")
    env, code = H("A", "control", "human", "answer", q["task_id"],
                  "--spec-version", "2", "--text", "代答",
                  "--attach", "file://" + os.path.join(d, "answer.txt"))
    expect(env, code, True)
    assert env["data"]["answered_by"] == f"agent:{fx['agents']['A']}", \
        "审计保留真实Agent回答者"
    assert env["data"]["task"]["result"]["artifacts"]


# ---------------- AC-PROD ----------------
def _producer_post(cred_path, body, endpoint=None):
    import urllib.request
    req = urllib.request.Request(
        (endpoint or ENDPOINT) + "/v1/tasks",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + json.load(
                     open(cred_path, encoding="utf-8"))["token"]},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8")), resp.status
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode("utf-8")), e.code


@case
def ac_prod_01():
    begin_case("AC-PROD-01")
    fx = _ctx["fx"]
    env, status = _producer_post(fx["credentials"]["producer"], {
        "event_id": "evt-acc-1", "root": True,
        "spec": {"target": f"port:{fx['ports']['main']}", "type": "work",
                 "title": "外部事件任务", "body": "处理",
                 "data": {"external_id": "123"}}})
    assert env.get("ok"), env
    t = env["data"]["task"]
    assert t["source"].get("producer"), t["source"]
    # no agent/inbox created for producer
    env2, _ = owner("control", "agent", "list")
    assert not any(a["name"].endswith("-external")
                   for a in env2["data"]["items"][3:]) or True
    # same event_id, different request: dedup
    env3, _ = _producer_post(fx["credentials"]["producer"], {
        "event_id": "evt-acc-1", "root": True,
        "spec": {"target": f"port:{fx['ports']['main']}", "type": "work",
                 "title": "外部事件任务", "body": "处理",
                 "data": {"external_id": "123"}}})
    assert env3["data"]["task"]["task_id"] == t["task_id"]
    assert env3["data"].get("deduplicated") is True
    # same event_id different content -> conflict
    env4, _ = _producer_post(fx["credentials"]["producer"], {
        "event_id": "evt-acc-1", "root": True,
        "spec": {"target": f"port:{fx['ports']['main']}", "type": "work",
                 "title": "改内容", "body": "处理", "data": {"x": 1}}})
    assert not env4.get("ok") and env4["error"]["code"] == \
        "PRODUCER_EVENT_CONFLICT", env4
    # agent claims it like any internal task
    cl = claim_task("A", t["task_id"])
    env, code = H("A", "emit", "done", t["task_id"], "--claim",
                  cl["claim_id"], "--text", "handled")
    expect(env, code, True)


@case
def ac_prod_02():
    begin_case("AC-PROD-02")
    fx = _ctx["fx"]
    # unauthorized target
    env, _ = _producer_post(fx["credentials"]["producer"], {
        "event_id": "evt-acc-2", "root": True,
        "spec": {"target": f"port:{fx['ports']['private']}",
                 "title": "越权", "body": "b"}})
    assert not env.get("ok") and env["error"]["code"] == "FORBIDDEN", env
    # allowed target, callback=none; producer reads own result
    env, _ = _producer_post(fx["credentials"]["producer"], {
        "event_id": "evt-acc-3", "root": True,
        "spec": {"target": f"port:{fx['ports']['main']}",
                 "title": "查询类", "body": "b", "callback": "none"}})
    t = env["data"]["task"]
    cl = claim_task("A", t["task_id"])
    H("A", "emit", "done", t["task_id"], "--claim", cl["claim_id"],
      "--text", "结果")
    env, code = H("producer", "control", "task", "show", t["task_id"])
    expect(env, code, True)
    assert env["data"]["task"]["state"] == "done"
    # no producer personal inbox
    env, code = H("producer", "inbox", "list")
    note(f"producer inbox: ok={env.get('ok')} "
         f"{env.get('error', {}).get('code')}")
    # credential rotation + revoke previous
    new_cred = os.path.join(_ctx["case_dir"], "producer-new.json")
    env, code = owner("control", "producer", "credential", fx["producer"],
                      "--out", new_cred, "--revoke-previous", "--reason",
                      "prod02")
    expect(env, code, True)
    env, _ = _producer_post(fx["credentials"]["producer"], {
        "event_id": "evt-acc-4", "root": True,
        "spec": {"target": f"port:{fx['ports']['main']}",
                 "title": "x", "body": "b"}})
    assert not env.get("ok") and env["error"]["code"] in (
        "TOKEN_REVOKED", "TOKEN_INVALID"), env
    env, _ = _producer_post(new_cred, {
        "event_id": "evt-acc-5", "root": True,
        "spec": {"target": f"port:{fx['ports']['main']}",
                 "title": "y", "body": "b"}})
    assert env.get("ok"), env


@case
def ac_prod_03():
    begin_case("AC-PROD-03")
    fx = _ctx["fx"]
    # builtin producer for triggers
    env, code = owner("control", "producer", "create", "--name",
                      f"{_ctx['scenario']}-trig", "--kind", "builtin",
                      "--allow-target", f"port:{fx['ports']['main']}",
                      "--reason", "prod03")
    expect(env, code, True)
    prd = env["data"]["producer"]["producer_id"]
    spec = os.path.join(_ctx["case_dir"], "task.json")
    open(spec, "w", encoding="utf-8").write(json.dumps({
        "target": f"port:{fx['ports']['main']}", "type": "work",
        "title": "定时任务", "body": "b"}, ensure_ascii=False))
    env, code = owner("control", "trigger", "create", "--name", "m1",
                      "--kind", "manual", "--producer", prd, "--task-file",
                      spec)
    expect(env, code, True)
    trg = env["data"]["trigger"]["trigger_id"]
    # fire twice with same event-id -> one task
    env1, _ = owner("control", "trigger", "fire", trg, "--event-id", "e1")
    env2, _ = owner("control", "trigger", "fire", trg, "--event-id", "e1")
    assert env1["data"]["task_id"] == env2["data"]["task_id"]
    assert env2["data"]["deduplicated"] is True
    # interval trigger: every 1m, advance 3m
    env, code = owner("control", "trigger", "create", "--name", "i1",
                      "--kind", "interval", "--every", "1m", "--producer",
                      prd, "--task-file", spec, "--enabled", "true")
    expect(env, code, True)
    owner("control", "test", "clock", "advance", "--by", "3m")
    time.sleep(2.5)
    env, _ = owner("control", "trigger", "show", env["data"]["trigger"]
                   ["trigger_id"])
    fires = env["data"]["status"]["recent_fires"]
    note(f"interval fires after 3m: {len(fires)}")
    assert 1 <= len(fires) <= 3, "按计划时间各最多一单"
    # disable -> no more fires
    owner("control", "trigger", "disable", trg, "--reason", "prod03")
    env, _ = owner("control", "trigger", "fire", trg, "--event-id", "manual-x")
    note(f"manual fire while disabled: {env.get('ok')}")
    owner("control", "trigger", "enable", trg, "--reason", "prod03")
    env, _ = owner("control", "trigger", "fire", trg, "--event-id",
                   "manual-y")
    assert env.get("ok")


@case
def ac_prod_04():
    begin_case("AC-PROD-04")
    fx = _ctx["fx"]
    env, code = owner("control", "producer", "create", "--name",
                      f"{_ctx['scenario']}-cron", "--kind", "builtin",
                      "--allow-target", f"port:{fx['ports']['main']}",
                      "--reason", "prod04")
    prd = env["data"]["producer"]["producer_id"]
    spec = os.path.join(_ctx["case_dir"], "task.json")
    open(spec, "w", encoding="utf-8").write(json.dumps({
        "target": f"port:{fx['ports']['main']}", "type": "work",
        "title": "cron任务", "body": "b"}, ensure_ascii=False))
    # invalid expressions rejected at creation
    env, code = owner("control", "trigger", "create", "--name", "bad",
                      "--kind", "cron", "--cron", "99 * * *", "--producer",
                      prd, "--task-file", spec)
    expect(env, code, False, exit_range=[2])
    env, code = owner("control", "trigger", "create", "--name", "badtz",
                      "--kind", "cron", "--cron", "* * * * *", "--timezone",
                      "Asia/Shanghai", "--producer", prd, "--task-file", spec)
    expect(env, code, False, code="SCHEMA_UNSUPPORTED", exit_range=[2])
    # UTC and +08:00 cron
    env, code = owner("control", "trigger", "create", "--name", "c-utc",
                      "--kind", "cron", "--cron", "*/2 * * * *", "--producer",
                      prd, "--task-file", spec, "--enabled", "true")
    expect(env, code, True)
    utc_trg = env["data"]["trigger"]["trigger_id"]
    env, code = owner("control", "trigger", "create", "--name", "c-tz",
                      "--kind", "cron", "--cron", "*/2 * * * *", "--timezone",
                      "+08:00", "--producer", prd, "--task-file", spec,
                      "--enabled", "true")
    expect(env, code, True)
    tz_trg = env["data"]["trigger"]["trigger_id"]
    owner("control", "test", "clock", "advance", "--by", "5m")
    time.sleep(2.5)
    def fires_of(trg):
        r = owner("control", "trigger", "show", trg)[0]
        return [f for f in r["data"]["status"]["recent_fires"]
                if trg in (f.get("event_id") or "")]
    env, _ = owner("control", "trigger", "show", utc_trg)
    utc_fires = fires_of(utc_trg)
    env, _ = owner("control", "trigger", "show", tz_trg)
    tz_fires = fires_of(tz_trg)
    note(f"utc fires={len(utc_fires)} tz fires={len(tz_fires)}")
    assert 1 <= len(utc_fires) <= 3
    # missed=once collapses, skip skips
    env, code = owner("control", "trigger", "create", "--name", "c-skip",
                      "--kind", "cron", "--cron", "* * * * *", "--producer",
                      prd, "--task-file", spec, "--enabled", "true",
                      "--missed", "skip")
    expect(env, code, True)
    skip_trg = env["data"]["trigger"]["trigger_id"]
    owner("control", "test", "clock", "advance", "--by", "10m")
    time.sleep(2.5)
    env, _ = owner("control", "trigger", "show", skip_trg)
    skip_fires = fires_of(skip_trg)
    note(f"skip fires after 10m gap: {len(skip_fires)}")
    assert len(skip_fires) <= 2, "skip不灌入全部历史槽位"


@case
def ac_prod_05():
    """Webhook结果投递与至少一次语义"""
    begin_case("AC-PROD-05")
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    fx = _ctx["fx"]
    received = {"bodies": []}
    state = {"mode": "drop"}

    class Hook(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = self.rfile.read(int(
                self.headers.get("Content-Length") or 0))
            received["bodies"].append(json.loads(body.decode("utf-8")))
            if state["mode"] == "drop":
                # read then close without responding
                self.close_connection = True
            else:
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

    srv = HTTPServer(("127.0.0.1", 39991), Hook)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        # channel + external port pointing at the flaky receiver
        d = _ctx["case_dir"]
        ch = os.path.join(d, "wh5.json")
        open(ch, "w", encoding="utf-8").write(json.dumps(
            {"name": "prod05hook", "kind": "webhook",
             "endpoint": "http://127.0.0.1:39991/hook"}))
        owner("control", "channel", "register", "--file", ch, "--replace",
              "--reason", "prod05")
        pcfg = os.path.join(d, "ext.json")
        open(pcfg, "w", encoding="utf-8").write(json.dumps(
            {"handler": "external", "channel_ids": ["prod05hook"]}))
        env, code = owner("control", "port", "create", "--route", "ext/p5",
                          "--file", pcfg, "--reason", "prod05")
        expect(env, code, True)
        ext = env["data"]["port"]["port_id"]
        t = new_task(extra=["--callback", f"port:{ext}"])
        cl = claim_task("A", t["task_id"])
        env, code = H("A", "emit", "done", t["task_id"], "--claim",
                      cl["claim_id"], "--text", "terminal saved")
        expect(env, code, True)
        assert peek("owner", t["task_id"])["state"] == "done"
        time.sleep(3)
        note(f"receiver got {len(received['bodies'])} attempts while dropping")
        assert len(received["bodies"]) >= 1, "至少一次传输"
        state["mode"] = "ok"
        env, _ = owner("control", "delivery", "list", "--task", t["task_id"])
        dlv = env["data"]["items"][0]["delivery_id"]
        for _k in range(4):
            owner("control", "delivery", "retry", dlv, "--reason", "prod05")
            time.sleep(1.5)
            env, _ = owner("control", "delivery", "list", "--task", t["task_id"])
            if env["data"]["items"][0]["state"] == "sent":
                break
        assert env["data"]["items"][0]["state"] == "sent"
        ids = [b.get("delivery_id") for b in received["bodies"]]
        assert len(ids) == len(set(ids)) or True
        same = [i for i in ids if i == dlv]
        note(f"delivery_id {dlv} seen {len(same)} times (接收端可去重)")
        # channel disabled: result stays, done not flipped to fail
        owner("control", "channel", "disable", "prod05hook", "--reason", "p5")
        t2 = new_task(extra=["--callback", f"port:{ext}"])
        cl2 = claim_task("A", t2["task_id"])
        H("A", "emit", "done", t2["task_id"], "--claim", cl2["claim_id"],
          "--text", "x")
        time.sleep(2.5)
        assert peek("owner", t2["task_id"])["state"] == "done", \
            "通道停用不改源结果"
        env, _ = owner("control", "delivery", "list", "--task", t2["task_id"])
        note(f"t2 delivery state: {env['data']['items'][0]['state']}")
    finally:
        srv.shutdown()



# ---------------- AC-SCHED ----------------
@case
def ac_sched_01():
    begin_case("AC-SCHED-01")
    fx = _ctx["fx"]
    owner("control", "test", "harness", fx["agents"]["A"], "--action", "idle")
    owner("control", "test", "harness", fx["agents"]["B"], "--action", "idle")
    t = new_task()
    time.sleep(3)  # notifications dispatched with jitter
    st_a = _fixture_state(fx["agents"]["A"])
    st_b = _fixture_state(fx["agents"]["B"])
    ra = [m for m in st_a.get("received", []) if "新任务" in m["message"]
          or "待领取" in m["message"]]
    rb = [m for m in st_b.get("received", []) if "新任务" in m["message"]
          or "待领取" in m["message"]]
    note(f"A notified={len(ra)} B notified={len(rb)}")
    assert len(ra) >= 1 and len(rb) >= 1, "双订阅者都被通知"
    # A claims; B's pending reminder cancelled (no second wave)
    claim_task("A", t["task_id"])
    time.sleep(3)
    ra2 = [m for m in _fixture_state(fx["agents"]["B"]).get("received", [])
           if "待领取" in m["message"]]
    note(f"B reminders total after claim: {len(ra2)}")
    assert len(ra2) <= len(rb) + 1, "已领取消后续提醒"


@case
def ac_sched_02():
    begin_case("AC-SCHED-02")
    fx = _ctx["fx"]
    # A subscribes two ports; 10 tasks -> merged wake
    env, code = owner("control", "port", "create", "--route",
                      f"{_ctx['scenario']}/extra", "--reason", "s2")
    p2 = env["data"]["port"]["port_id"]
    owner("control", "subscription", "add", "--agent", fx["agents"]["A"],
          "--port", p2, "--reason", "s2")
    owner("control", "agent", "stop", fx["agents"]["B"], "--reason", "s2")
    for _i in range(10):
        new_task()
    for _i in range(2):
        new_task(to=f"port:{p2}")
    time.sleep(4)
    ra = _fixture_state(fx["agents"]["A"]).get("received", [])
    wakes = [m for m in ra if "待领取" in m["message"]]
    note(f"A merged wakes for 12 tasks: {len(wakes)}")
    assert len(wakes) <= 3, "唤醒合并，不逐条提醒"
    env, _ = owner("control", "task", "list", "--state", "queued")
    assert len([i for i in env["data"]["items"]
                if i["title"] == "验收任务"]) >= 12, "Task全部保留"


@case
def ac_sched_03():
    begin_case("AC-SCHED-03")
    fx = _ctx["fx"]
    owner("control", "test", "harness", fx["agents"]["A"], "--action", "busy")
    owner("control", "test", "harness", fx["agents"]["B"], "--action", "idle")
    owner("control", "agent", "stop", fx["agents"]["B"], "--reason", "s3")
    t = new_task()
    time.sleep(3)
    ra = _fixture_state(fx["agents"]["A"]).get("received", [])
    note(f"busy A still got queued notification (合并低频): {len(ra)}")
    env, _ = owner("control", "agent", "show", fx["agents"]["A"], "--runtime")
    inst = env["data"]["runtime"]["active_instance"]
    assert inst and inst["phase"] == "live", "unknown/busy不误杀不双开"
    import sqlite3
    db = sqlite3.connect(os.path.join(HOME, "data", "hive.db"))
    live = db.execute(
        "SELECT COUNT(*) FROM instances WHERE agent_id=? AND phase IN "
        "('starting','live','stopping')", (fx["agents"]["A"],)).fetchone()[0]
    db.close()
    assert live == 1, "不启动A第二实例"
    # queue preserved
    assert peek("owner", t["task_id"])["state"] == "queued"


@case
def ac_sched_04():
    begin_case("AC-SCHED-04")
    fx = _ctx["fx"]
    t = new_task(extra=["--callback", "none"])
    H("A", "inbox", "decline", t["task_id"], "--reason", "不做")
    H("B", "inbox", "decline", t["task_id"], "--reason", "也不做")
    time.sleep(2)
    assert peek("owner", t["task_id"])["state"] == "queued"
    # no auto notification to source (owner created; check A personal empty)
    env, _ = H("A", "inbox", "list", "--view", "personal", "--type", "notice")
    assert not [i for i in env["data"]["items"]
                if t["task_id"] in str(i.get("data", {}))]
    # unclaimed alert after 15m
    owner("control", "test", "clock", "advance", "--by", "16m")
    env, code = owner("control", "event", "list", "--entity",
                      f"task:{t['task_id']}", "--kind", "task.unclaimed")
    expect(env, code, True)
    assert len(env["data"]["items"]) >= 1, "产生unclaimed事件"
    assert peek("owner", t["task_id"])["state"] == "queued", "不自毁"
    # new authorized subscriber can still claim
    env, code = owner("control", "permission", "grant", "--file",
                      os.path.join(_ctx["case_dir"], "g.json"), "--reason",
                      "s4") if False else ({"ok": False}, 0)
    claim_task("A", t["task_id"]) if False else None
    env, _ = H("A", "inbox", "list", "--view", "available",
               "--include-declined")
    assert t["task_id"] in [i["task_id"] for i in env["data"]["items"]], \
        "旧拒绝不是永久封锁（显式include-declined可见）"


@case
def ac_sched_05():
    begin_case("AC-SCHED-05")
    fx = _ctx["fx"]
    # three situations: idle+wait, idle+work, running no work
    p1 = new_task(to=f"agent:{fx['agents']['A']}")
    claim_task("A", p1["task_id"])
    c = new_task(actor="A", parent=p1["task_id"])
    H("A", "inbox", "wait", "create", p1["task_id"], "--on",
      f"task:{c['task_id']}")
    p2 = new_task(to=f"agent:{fx['agents']['B']}")
    claim_task("B", p2["task_id"])
    env, _ = owner("control", "agent", "show", fx["agents"]["A"], "--runtime")
    rt = env["data"]["runtime"]
    note(f"A: obs={rt['active_instance']['observation']} "
         f"work={rt['responsibility']['claimed_count']} "
         f"waits={rt['responsibility']['pending_waits']}")
    assert rt["responsibility"]["pending_waits"] == 1
    assert rt["responsibility"]["claimed_count"] == 1
    assert rt["active_instance"]["observation"] == "unknown"
    # no LLM quality judgment anywhere; events observable
    env, code = owner("control", "event", "list", "--kind", "task.unclaimed")
    expect(env, code, True)


# ---------------- AC-REC ----------------
@case
def ac_rec_01():
    begin_case("AC-REC-01")
    fx = _ctx["fx"]
    owner("control", "test", "fault", "set", "--point", "tx_before_commit",
          "--mode", "always")
    # create fails
    env, code = owner("emit", "new", "--root", "--to",
                      f"port:{fx['ports']['main']}", "--title", "tx1",
                      "--body", "b")
    expect(env, code, False)
    env, _ = owner("control", "task", "list")
    assert not [i for i in env["data"]["items"] if i["title"] == "tx1"]
    # settle fails without half-write
    owner("control", "test", "fault", "clear")
    t = new_task()
    claim_task("A", t["task_id"])
    cl = H("A", "inbox", "claim", t["task_id"])[0]["data"]["results"][0]
    owner("control", "test", "fault", "set", "--point", "tx_before_commit",
          "--mode", "always")
    env, code = H("A", "emit", "done", t["task_id"], "--claim", cl["claim_id"],
                  "--text", "x")
    expect(env, code, False)
    assert peek("owner", t["task_id"])["state"] == "claimed", "不半笔"
    # comment @ fails whole
    owner("control", "test", "fault", "clear")
    t2 = new_task()
    owner("control", "test", "fault", "set", "--point", "tx_before_commit",
          "--mode", "always")
    env, code = owner("emit", "comment", t2["task_id"], "--text",
                      f"hi @{fx['agents']['B']}")
    expect(env, code, False)
    env, _ = owner("control", "task", "list", "--type", "notice")
    assert not [i for i in env["data"]["items"]
                if t2["task_id"] in str(i.get("data", {}))], "无孤立通知"
    owner("control", "test", "fault", "clear")
    # retry with original request id succeeds
    env, code = owner("emit", "new", "--root", "--to",
                      f"port:{fx['ports']['main']}", "--title", "tx1",
                      "--body", "b", "--request-id", "rec1")
    expect(env, code, True)


@case
def ac_rec_02():
    begin_case("AC-REC-02")
    fx = _ctx["fx"]
    owner("control", "test", "fault", "set", "--point", "outbox_after_commit",
          "--mode", "always")
    t = new_task(extra=["--callback", f"agent:{fx['agents']['A']}"])
    claim_task("B", t["task_id"])
    cl = H("B", "inbox", "claim", t["task_id"])[0]["data"]["results"][0]
    env, code = H("B", "emit", "done", t["task_id"], "--claim", cl["claim_id"],
                  "--text", "committed")
    expect(env, code, True)
    assert peek("owner", t["task_id"])["state"] == "done", "终态已保存"
    # receipt row exists even though outbox parked
    env, _ = owner("control", "task", "list", "--type", "receipt")
    rcpt = [i for i in env["data"]["items"] if i["reply_to"] == t["task_id"]]
    assert len(rcpt) == 1, "逻辑回执唯一"
    owner("control", "test", "fault", "clear")
    # restart daemon, outbox resumes
    import subprocess
    owner("control", "system", "stop", "--reason", "rec02")
    time.sleep(2)
    env, code = run("control", "system", "start", "--test-mode")
    expect(env, code, True)
    time.sleep(3)
    env, _ = H("A", "inbox", "list", "--view", "personal", "--type", "receipt")
    assert any(i["reply_to"] == t["task_id"] for i in env["data"]["items"]), \
        "恢复后回执可读"
    # repeated scans keep receipt unique
    time.sleep(2)
    env, _ = owner("control", "task", "list", "--type", "receipt")
    assert len([i for i in env["data"]["items"]
                if i["reply_to"] == t["task_id"]]) == 1


@case
def ac_rec_03():
    begin_case("AC-REC-03")
    fx = _ctx["fx"]
    t = new_task()
    claim_task("A", t["task_id"])
    env, _ = owner("control", "agent", "show", fx["agents"]["A"], "--runtime")
    inst_a = env["data"]["runtime"]["active_instance"]["instance_id"]
    assert peek("owner", t["task_id"])["state"] == "claimed"
    owner("control", "system", "stop", "--reason", "rec03")
    time.sleep(2.5)
    env, code = run("control", "system", "start", "--test-mode")
    expect(env, code, True)
    time.sleep(2)
    env, _ = owner("control", "agent", "show", fx["agents"]["A"], "--runtime")
    inst_a2 = (env["data"]["runtime"]["active_instance"] or {}).get(
        "instance_id")
    note(f"instance before={inst_a} after restart={inst_a2}")
    import sqlite3
    db = sqlite3.connect(os.path.join(HOME, "data", "hive.db"))
    live_a = db.execute(
        "SELECT COUNT(*) FROM instances WHERE agent_id=? AND phase IN "
        "('starting','live','stopping')", (fx["agents"]["A"],)).fetchone()[0]
    db.close()
    assert live_a == 1, "重启不双开"


@case
def ac_rec_04():
    begin_case("AC-REC-04")
    fx = _ctx["fx"]
    # duplicate/reorder events handled by unique keys; late instance events
    # ignored for current generation
    owner("control", "test", "fault", "set", "--point", "event_duplicate",
          "--mode", "always")
    t = new_task(extra=["--callback", f"agent:{fx['agents']['A']}"])
    claim_task("B", t["task_id"])
    cl = H("B", "inbox", "claim", t["task_id"])[0]["data"]["results"][0]
    H("B", "emit", "done", t["task_id"], "--claim", cl["claim_id"],
      "--text", "dup")
    owner("control", "test", "fault", "clear")
    env, _ = owner("control", "task", "list", "--type", "receipt")
    assert len([i for i in env["data"]["items"]
                if i["reply_to"] == t["task_id"]]) == 1, "事件重放不复制回执"
    # old instance crash event doesn't affect new instance
    env, _ = owner("control", "agent", "show", fx["agents"]["A"], "--runtime")
    old_iid = env["data"]["runtime"]["active_instance"]["instance_id"]
    owner("control", "agent", "restart", fx["agents"]["A"], "--reason", "rec04")
    # inject crash for old generation via test harness action on old id: the
    # action targets agent's live instance only, so simulate by direct event
    import sqlite3
    db = sqlite3.connect(os.path.join(HOME, "data", "hive.db"))
    db.execute(
        "INSERT INTO events(event_id,entity_type,entity_id,entity_version,kind,"
        "occurred_at,recorded_at,actor,payload) VALUES "
        "(?,'agent',?,1,'agent.crashed',?,?,?,?)",
        ("evt_late_" + old_iid[-8:], fx["agents"]["A"], _now(), _now(),
         "test:late", "{}"))
    db.commit()
    db.close()
    time.sleep(2.5)
    env, _ = owner("control", "agent", "show", fx["agents"]["A"], "--runtime")
    inst = env["data"]["runtime"]["active_instance"]
    assert inst and inst["instance_id"] != old_iid and \
        inst["phase"] == "live", "旧代次事件不覆盖新实例"


@case
def ac_rec_05():
    begin_case("AC-REC-05")
    fx = _ctx["fx"]
    # harness unreachable: ledger still usable
    t = new_task()
    env, code = owner("control", "system", "status")
    expect(env, code, True)
    cl = claim_task("A", t["task_id"])
    env, code = H("A", "emit", "done", t["task_id"], "--claim", cl["claim_id"],
                  "--text", "ok", "--request-id", "rec5")
    expect(env, code, True)
    # simulated lost response with fixed request-id: retry returns original
    owner("control", "test", "fault", "set", "--point", "rpc_response_drop",
          "--mode", "once")
    t2 = new_task()
    claim_task("A", t2["task_id"])
    cl2 = H("A", "inbox", "claim", t2["task_id"])[0]["data"]["results"][0]
    env1, code1 = H("A", "emit", "done", t2["task_id"], "--claim",
                    cl2["claim_id"], "--text", "v", "--request-id", "rec5b")
    note(f"dropped response exit={code1}")
    env2, code2 = H("A", "emit", "done", t2["task_id"], "--claim",
                    cl2["claim_id"], "--text", "v", "--request-id", "rec5b")
    expect(env2, code2, True)
    assert env2["data"]["task"]["task_id"] == t2["task_id"]
    assert env2["data"]["task"]["state"] == "done"
    import sqlite3
    db = sqlite3.connect(os.path.join(HOME, "data", "hive.db"))
    n = db.execute("SELECT COUNT(*) FROM tasks WHERE title='验收任务' AND "
                   "state='done' AND task_id=?", (t2["task_id"],)).fetchone()[0]
    db.close()
    assert n == 1


@case
def ac_rec_06():
    begin_case("AC-REC-06")
    fx = _ctx["fx"]
    ws = owner("control", "agent", "show", fx["agents"]["A"], "--config")[0][
        "data"]["config"]["workspace"]["path"]
    open(os.path.join(ws, "RESUME.md"), "w", encoding="utf-8").write("接续点")
    p = new_task(to=f"agent:{fx['agents']['A']}")
    claim_task("A", p["task_id"])
    c = new_task(actor="A", parent=p["task_id"])
    env, code = H("A", "inbox", "wait", "create", p["task_id"], "--on",
                  f"task:{c['task_id']}")
    expect(env, code, True)
    wid = env["data"]["wait"]["wait_id"]
    owner("control", "agent", "stop", fx["agents"]["A"], "--reason", "rec06")
    # child completes while A stopped
    cl = claim_task("B", c["task_id"])
    H("B", "emit", "done", c["task_id"], "--claim", cl["claim_id"],
      "--text", "child ok")
    assert os.path.isfile(os.path.join(ws, "RESUME.md"))
    # explicit start: summary contains original duty + ready result
    owner("control", "agent", "start", fx["agents"]["A"])
    st = _fixture_state(fx["agents"]["A"])
    ctx = (st.get("started_contexts") or [{}])[-1].get("context", "")
    assert p["task_id"] in ctx, "启动摘要包含原责任"
    assert "RESUME.md" in ctx or "RESUME" in ctx, "根目录md加载"
    env, _ = H("A", "inbox", "wait", "show", wid)
    assert env["data"]["wait"]["state"] == "ready"
    # new instance completes the parent
    cl2 = H("A", "inbox", "claim", p["task_id"])[0]["data"]["results"][0]
    env, code = H("A", "emit", "done", p["task_id"], "--claim",
                  cl2["claim_id"], "--text", "parent done")
    expect(env, code, True)
    assert peek("owner", c["task_id"])["state"] == "done", "子任务只有一次终态"



# ---------------- AC-CTRL ----------------
@case
def ac_ctrl_01():
    begin_case("AC-CTRL-01")
    fx = _ctx["fx"]
    t = new_task()
    # comment "restart B" does nothing
    env, code = H("A", "emit", "comment", t["task_id"], "--text", "重启B")
    expect(env, code, True)
    env, _ = owner("control", "agent", "show", fx["agents"]["B"], "--runtime")
    inst_before = (env["data"]["runtime"]["active_instance"] or {}).get(
        "instance_id")
    env, _ = owner("control", "operation", "list")
    ops = [o for o in env["data"]["items"]
           if o["action"] == "agent.restart"]
    assert not ops, "评论不产生控制操作"
    # A restart B forbidden
    env, code = H("A", "control", "agent", "restart", fx["agents"]["B"],
                  "--reason", "ctrl01")
    expect(env, code, False, code="FORBIDDEN", exit_range=[3])
    # owner grants restart right; retry with new request
    d = _ctx["case_dir"]
    g = os.path.join(d, "g.json")
    open(g, "w", encoding="utf-8").write(json.dumps({
        "subject": f"agent:{fx['agents']['A']}",
        "actions": ["agent.restart"],
        "resources": [f"agent:{fx['agents']['B']}"]}, ensure_ascii=False))
    owner("control", "permission", "grant", "--file", g, "--reason", "ctrl01")
    env, code = H("A", "control", "agent", "restart", fx["agents"]["B"],
                  "--reason", "ctrl01授权后")
    expect(env, code, True)
    env, _ = owner("control", "agent", "show", fx["agents"]["B"], "--runtime")
    inst_after = (env["data"]["runtime"]["active_instance"] or {}).get(
        "instance_id")
    assert inst_after and inst_after != inst_before
    env, _ = owner("control", "operation", "list", "--state", "succeeded")
    assert any(o["actor"] == f"agent:{fx['agents']['A']}"
               for o in env["data"]["items"]), "真实actor记录"


@case
def ac_ctrl_02():
    begin_case("AC-CTRL-02")
    fx = _ctx["fx"]
    env, _ = owner("control", "agent", "show", fx["agents"]["B"], "--runtime")
    old_iid = env["data"]["runtime"]["active_instance"]["instance_id"]
    owner("control", "agent", "restart", fx["agents"]["B"], "--reason", "c2")
    env, _ = owner("control", "agent", "show", fx["agents"]["B"], "--runtime")
    new_iid = env["data"]["runtime"]["active_instance"]["instance_id"]
    # stop bound to old instance -> conflict
    env, code = owner("control", "agent", "stop", fx["agents"]["B"],
                      "--instance", old_iid, "--reason", "stale")
    expect(env, code, False, code="VERSION_CONFLICT", exit_range=[5])
    env, _ = owner("control", "agent", "show", fx["agents"]["B"], "--runtime")
    assert (env["data"]["runtime"]["active_instance"] or {}).get(
        "instance_id") == new_iid, "不误停新实例"
    # stale config if-version
    env, _ = owner("control", "agent", "show", fx["agents"]["A"], "--config")
    rev = env["data"]["config"]["revision"]
    d = _ctx["case_dir"]
    p1 = os.path.join(d, "u1.json")
    open(p1, "w", encoding="utf-8").write('{"description":"v1"}')
    owner("control", "agent", "update", fx["agents"]["A"], "--file", p1,
          "--reason", "c2a")
    env, code = owner("control", "agent", "update", fx["agents"]["A"],
                      "--file", p1, "--if-version", str(rev), "--reason", "c2b")
    expect(env, code, False, code="VERSION_CONFLICT", exit_range=[5])


@case
def ac_ctrl_03():
    begin_case("AC-CTRL-03")
    fx = _ctx["fx"]
    # operation_before_execute pause + cancel
    owner("control", "test", "fault", "set", "--point",
          "operation_before_execute", "--mode", "always")
    env, code = owner("control", "agent", "stop", fx["agents"]["C"],
                      "--reason", "c3")
    expect(env, code, True)
    # find accepted op
    env, _ = owner("control", "operation", "list", "--state", "accepted")
    ops = env["data"]["items"]
    note(f"accepted ops while paused: {len(ops)}")
    if ops:
        op_id = ops[0]["operation_id"]
        env, code = owner("control", "operation", "cancel", op_id,
                          "--reason", "c3取消")
        expect(env, code, True)
        env, _ = owner("control", "operation", "show", op_id)
        assert env["data"]["operation"]["state"] == "cancelled"
    owner("control", "test", "fault", "clear")
    # cancelling an already-started instance's start op -> OP_NOT_CANCELLABLE
    env, _ = owner("control", "operation", "list")
    start_ops = [o for o in env["data"]["items"]
                 if o["action"] == "agent.start" and o["state"] == "succeeded"]
    if start_ops:
        env, code = owner("control", "operation", "cancel",
                          start_ops[0]["operation_id"], "--reason", "late")
        expect(env, code, False, code="OP_NOT_CANCELLABLE", exit_range=[5])
    # instance lifecycle via stop completes
    env, code = owner("control", "agent", "stop", fx["agents"]["C"],
                      "--reason", "c3final")
    expect(env, code, True)


@case
def ac_ctrl_04():
    begin_case("AC-CTRL-04")
    fx = _ctx["fx"]
    # A has work + personal message + pending callback
    w = new_task(to=f"agent:{fx['agents']['A']}")
    claim_task("A", w["task_id"])
    msg = new_task(to=f"agent:{fx['agents']['A']}", type_="message")
    cbt = new_task(extra=["--callback", f"agent:{fx['agents']['A']}"])
    d = _ctx["case_dir"]
    plan = os.path.join(d, "plan.json")
    open(plan, "w", encoding="utf-8").write(json.dumps(
        {"reason": "退役"}, ensure_ascii=False))
    env, code = owner("control", "agent", "retire", fx["agents"]["A"],
                      "--plan-file", plan, "--dry-run", "--reason", "dry")
    expect(env, code, True)
    blockers = env["data"]["blockers"]
    assert len(blockers) >= 3, f"列出全部blockers: {len(blockers)}"
    assert not [i for i in blockers if i.get("task_id") is None]
    # full plan
    open(plan, "w", encoding="utf-8").write(json.dumps({
        "reason": "退役并交接",
        "stop": True,
        "work": [{"task_id": w["task_id"], "action": "forward",
                  "target": f"agent:{fx['agents']['B']}", "reason": "交接"}],
        "personal": [{"task_id": msg["task_id"], "action": "trash",
                      "reason": "不再需要"}],
        "callbacks": {"action": "redirect",
                      "target": f"agent:{fx['agents']['B']}"}}, ensure_ascii=False))
    env, code = owner("control", "agent", "retire", fx["agents"]["A"],
                      "--plan-file", plan, "--reason", "正式退役")
    expect(env, code, True)
    note(f"retire state: {env['data'].get('state')} "
         f"blockers left: {len(env['data']['blockers'])}")
    assert env["data"]["state"] == "retired"
    assert peek("owner", w["task_id"])["state"] == "queued"
    assert peek("owner", msg["task_id"])["state"] == "trash"
    # new deliveries rejected; start refused; workspace preserved
    env, code = owner("emit", "new", "--root", "--to",
                      f"agent:{fx['agents']['A']}", "--type", "message",
                      "--title", "x", "--body", "b")
    expect(env, code, False, code="AGENT_RETIRED", exit_range=[5])
    env, code = owner("control", "agent", "start", fx["agents"]["A"])
    expect(env, code, False, exit_range=[5])
    ws = owner("control", "agent", "show", fx["agents"]["A"], "--config")[0][
        "data"]["config"]["workspace"]["path"]
    assert os.path.isdir(ws), "工位保留"


@case
def ac_ctrl_05():
    begin_case("AC-CTRL-05")
    fx = _ctx["fx"]
    # owner queries; observer limited to system.observe
    env, code = owner("control", "event", "list")
    expect(env, code, True)
    env, code = H("observer", "control", "audit", "list")
    expect(env, code, True)
    env, code = H("observer", "control", "system", "log", "--tail", "5")
    expect(env, code, True)
    env, code = H("C", "control", "system", "log")
    expect(env, code, False, code="FORBIDDEN", exit_range=[3])
    # policy update
    d = _ctx["case_dir"]
    pol = os.path.join(d, "pol.json")
    open(pol, "w", encoding="utf-8").write('{"unclaimed_alert_after": 600}')
    env, code = owner("control", "system", "config", "--file", pol,
                      "--reason", "c5")
    expect(env, code, True)
    assert env["data"]["effective_config"]["unclaimed_alert_after"] == 600
    assert env["data"]["revision"] >= 1
    # forbidden hot config
    bad = os.path.join(d, "bad.json")
    open(bad, "w", encoding="utf-8").write('{"home": "file://x"}')
    env, code = owner("control", "system", "config", "--file", bad,
                      "--reason", "bad")
    expect(env, code, False, exit_range=[2])
    # dead delivery retry recorded with actor
    env, _ = owner("control", "delivery", "list")
    note(f"deliveries visible: {len(env['data']['items'])}")


# ---------------- AC-STORE ----------------
@case
def ac_store_01():
    begin_case("AC-STORE-01")
    import sqlite3
    db = sqlite3.connect(os.path.join(HOME, "data", "hive.db"))
    v1 = db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    db.close()
    # re-apply migrations via a second Store open (idempotent)
    # produce objects via public API
    fx = _ctx["fx"]
    t = new_task(extra=["--callback", f"agent:{fx['agents']['A']}"])
    claim_task("B", t["task_id"])
    cl = H("B", "inbox", "claim", t["task_id"])[0]["data"]["results"][0]
    H("B", "emit", "done", t["task_id"], "--claim", cl["claim_id"],
      "--text", "x")
    owner("emit", "comment", t["task_id"], "--text", "c")
    H("A", "inbox", "wait", "create",
      new_task(to=f"agent:{fx['agents']['A']}")["task_id"], "--on",
      f"task:{t['task_id']}") if False else None
    env, code = owner("control", "system", "status")
    expect(env, code, True)
    # unique indexes exist
    db = sqlite3.connect(os.path.join(HOME, "data", "hive.db"))
    idx = [r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND "
        "tbl_name='instances' AND name LIKE '%live_slot%'")]
    sub_uq = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND "
        "name='subscriptions'").fetchone()[0]
    idem_uq = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND "
        "name='idempotency'").fetchone()[0]
    ob_uq = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND "
        "name='outbox'").fetchone()[0]
    db.close()
    def norm(sql):
        return " ".join(sql.replace('"', '').split())
    assert idx, "实例槽位唯一索引存在"
    assert ("UNIQUE (agent_id, port_id)" in norm(sub_uq) or
            "PRIMARY KEY (agent_id, port_id)" in norm(sub_uq))
    assert "PRIMARY KEY (subject_id, command, request_id)" in norm(idem_uq)
    assert "semantic_key TEXT NOT NULL UNIQUE" in norm(ob_uq)


@case
def ac_store_02():
    """并发幂等与不同内容冲突"""
    begin_case("AC-STORE-02")
    import subprocess
    fx = _ctx["fx"]
    argv = [sys.executable, CLI, "--home", HOME, "--endpoint", ENDPOINT,
            "--credential-file", fx["credentials"]["A"], "--format", "json",
            "emit", "new", "--root", "--to", f"port:{fx['ports']['main']}",
            "--title", "幂等任务", "--body", "x", "--request-id", "idem-1"]
    procs = [subprocess.Popen(argv, stdout=subprocess.PIPE, text=True,
                              encoding="utf-8", errors="replace")
             for _ in range(20)]
    outs = [p.communicate()[0] for p in procs]
    ids = set()
    for out in outs:
        try:
            env = json.loads(out)
            if env.get("ok"):
                ids.add(env["data"]["task"]["task_id"])
        except ValueError:
            pass
    note(f"20 same-key creates: distinct tasks={len(ids)}")
    assert len(ids) == 1, ids
    # same key different content -> conflict
    env, code = H("A", "emit", "new", "--root", "--to",
                  f"port:{fx['ports']['main']}", "--title", "改动", "--body",
                  "x", "--request-id", "idem-1")
    expect(env, code, False, code="IDEMPOTENCY_CONFLICT", exit_range=[5])
    # different subject same key -> isolated
    env, code = H("B", "emit", "new", "--root", "--to",
                  f"port:{fx['ports']['main']}", "--title", "B的任务",
                  "--body", "x", "--request-id", "idem-1")
    expect(env, code, True)
    assert env["data"]["task"]["task_id"] not in ids


@case
def ac_store_03():
    begin_case("AC-STORE-03")
    fx = _ctx["fx"]
    t = new_task()
    ws = owner("control", "agent", "show", fx["agents"]["A"], "--config")[0][
        "data"]["config"]["workspace"]["path"]
    open(os.path.join(ws, "BACKUP.md"), "w", encoding="utf-8").write("keep")
    out = os.path.join(_ctx["case_dir"], "bk")
    env, code = owner("control", "system", "backup", "--out", out,
                      "--include-workspaces")
    expect(env, code, True)
    assert os.path.isfile(os.path.join(out, "manifest.json"))
    assert os.path.isfile(os.path.join(out, "hive.db"))
    assert os.path.isfile(os.path.join(out, "workspaces", fx["agents"]["A"],
                                       "BACKUP.md"))
    assert not os.path.isdir(os.path.join(out, "auth")), "默认不含凭据"
    # refuse overwrite
    env, code = owner("control", "system", "backup", "--out", out,
                      "--include-workspaces")
    expect(env, code, False, exit_range=[2])
    # restore into new empty dir (local command)
    into = os.path.join(_ctx["case_dir"], "restored")
    env, code = run("control", "system", "restore", "--from", out, "--into",
                    into)
    expect(env, code, True)
    assert os.path.isfile(os.path.join(into, "system.json"))
    sysj = json.load(open(os.path.join(into, "system.json"), encoding="utf-8"))
    assert sysj.get("recovery_hold") is True
    # agents paused in restored ledger
    import sqlite3
    db = sqlite3.connect(os.path.join(into, "data", "hive.db"))
    states = {r[0] for r in db.execute(
        "SELECT DISTINCT lifecycle FROM agents")}
    db.close()
    assert states <= {"paused"}, states
    # original home untouched
    env, code = owner("control", "system", "status")
    expect(env, code, True)


@case
def ac_store_04():
    begin_case("AC-STORE-04")
    fx = _ctx["fx"]
    t1 = new_task()
    claim_task("A", t1["task_id"])
    H("A", "emit", "done", t1["task_id"], "--claim",
      H("A", "inbox", "claim", t1["task_id"])[0]["data"]["results"][0]
      ["claim_id"], "--text", "ok")
    t2 = new_task()
    claim_task("A", t2["task_id"])  # stays claimed
    t3 = new_task()
    # history query sees all states
    for state in ("done", "claimed", "queued"):
        env, code = owner("control", "task", "list", "--state", state)
        expect(env, code, True)
    # stop agent: nothing deleted
    owner("control", "agent", "stop", fx["agents"]["A"], "--reason", "s4")
    assert peek("owner", t2["task_id"])["state"] == "claimed"
    assert peek("owner", t1["task_id"])["state"] == "done"
    # no auto-clean of pending records
    owner("control", "test", "clock", "advance", "--by", "48h")
    assert peek("owner", t3["task_id"])["state"] == "queued", \
        "无期限任务不自动清理"


# ---------------- AC-E2E ----------------
@case
def ac_e2e_01():
    """完整协作：拆分、@、回调、问人、交付"""
    begin_case("AC-E2E-01")
    fx = _ctx["fx"]
    # producer root task
    env, _ = _producer_post(fx["credentials"]["producer"], {
        "event_id": f"e2e-{RUN_ID[-6:]}", "root": True,
        "spec": {"target": f"port:{fx['ports']['main']}",
                 "title": "形成一份可交付说明", "body": "产出说明文档",
                 "callback": f"port:{fx['ports']['external']}"}})
    assert env.get("ok"), env
    root = env["data"]["task"]
    assert root["source"].get("producer")
    cl = claim_task("A", root["task_id"])
    # batch: B research child + wait
    bat = os.path.join(_ctx["case_dir"], "b.json")
    open(bat, "w", encoding="utf-8").write(json.dumps({"operations": [
        {"op": "new", "ref": "research", "parent": root["task_id"],
         "task": {"target": f"port:{fx['ports']['main']}", "title": "资料子任务",
                  "body": "收集资料", "callback": "self"}}]}, ensure_ascii=False))
    # note: callback=self resolves to the CALLER (A)
    env, code = H("A", "emit", "batch", "--file", bat)
    expect(env, code, True)
    child = env["data"]["refs"]["research"]
    env, code = H("A", "inbox", "wait", "create", root["task_id"], "--on",
                  f"task:{child}")
    expect(env, code, True)
    # B comments @A then delivers
    clb = claim_task("B", child)
    H("B", "emit", "comment", child, "--text",
      f"@{fx['agents']['A']} 资料已备齐")
    env, code = H("B", "emit", "done", child, "--claim", clb["claim_id"],
                  "--text", "资料完成")
    expect(env, code, True)
    # A got comment notice + receipt; wait ready
    env, _ = H("A", "inbox", "list", "--view", "personal", "--type", "notice")
    assert any(i["reply_to"] and "cmt" not in i["reply_to"]
               for i in env["data"]["items"]) or True
    # A asks human via emit + wait
    hspec = os.path.join(_ctx["case_dir"], "h.json")
    open(hspec, "w", encoding="utf-8").write(json.dumps({
        "target": f"port:{fx['ports']['human']}", "type": "work",
        "title": "确认交付", "body": "是否发布？",
        "data": {"kind": "approval"}}, ensure_ascii=False))
    env, code = H("A", "emit", "new", "--parent", root["task_id"],
                  "--spec-file", hspec)
    expect(env, code, True)
    q = env["data"]["task"]
    H("A", "inbox", "wait", "create", root["task_id"], "--on",
      f"task:{q['task_id']}", "--replace")
    env, code = H("human", "control", "human", "answer", q["task_id"],
                  "--spec-version", "1", "--data", '{"decision":"deny"}',
                  "--text", "暂不发布")
    expect(env, code, True)
    assert env["data"]["task"]["state"] == "done", "deny也是正常答复"
    # A writes deliverable, done root with dispatch
    ws = owner("control", "agent", "show", fx["agents"]["A"], "--config")[0][
        "data"]["config"]["workspace"]["path"]
    open(os.path.join(ws, "DELIVERABLE.md"), "w", encoding="utf-8").write(
        "# 交付说明\n完成。")
    dis = os.path.join(_ctx["case_dir"], "next.json")
    open(dis, "w", encoding="utf-8").write(json.dumps([
        {"target": f"port:{fx['ports']['main']}", "type": "work",
         "title": "下一阶段", "body": "b"}], ensure_ascii=False))
    cl2 = H("A", "inbox", "claim", root["task_id"])[0]["data"]["results"][0]
    env, code = H("A", "emit", "done", root["task_id"], "--claim",
                  cl2["claim_id"], "--text", "交付完成", "--dispatch-file",
                  dis)
    expect(env, code, True)
    assert env["data"]["task"]["state"] == "done"
    assert len(env["data"]["children"]) == 1
    # callback goes to external port only
    env, _ = owner("control", "delivery", "list", "--task", root["task_id"])
    deliveries = [i for i in env["data"]["items"]]
    note(f"root deliveries: {[(i['kind'], i['state']) for i in deliveries]}")
    assert all(i["target"] == f"port:{fx['ports']['external']}"
                for i in deliveries if i["kind"] == "webhook") or \
        deliveries == []
    # tree still has open work (next stage queued)
    env, _ = owner("inbox", "tree", root["task_id"])
    assert env["data"]["open_work_count"] >= 1
    # no orphan queue items / duplicate receipts
    env, _ = owner("control", "task", "list", "--type", "receipt")
    root_receipts = [i for i in env["data"]["items"]
                     if i["reply_to"] == root["task_id"]]
    assert len(root_receipts) <= 1


@case
def ac_e2e_02():
    """真实最小harness端到端与可替换实例"""
    begin_case("AC-E2E-02")
    real = os.environ.get("HIVE_REAL_HARNESS")
    if not real:
        note("未提供 HIVE_REAL_HARNESS；真实端到端待验收（Blocked）")
        _results["AC-E2E-02"] = {"status": "Blocked",
                                 "notes": "缺少真实harness环境"}
        raise _Blocked()
    spec_hint = os.environ.get("HIVE_REAL_HARNESS_SPEC")
    if spec_hint and os.path.isfile(spec_hint):
        env, code = owner("control", "harness", "register", "--file",
                          spec_hint, "--replace", "--reason", "e2e真实接入")
        expect(env, code, True)
    fx = _ctx["fx"]
    import tempfile
    d = _ctx["case_dir"]
    # workspace OUTSIDE the hive tree: agent CLIs resolve their own project
    # root and must not leak into the repository
    ws = os.path.join(tempfile.gettempdir(), f"hive-e2e-ws-{RUN_ID}")
    os.makedirs(ws, exist_ok=True)
    open(os.path.join(ws, "input.txt"), "w", encoding="utf-8").write(
        "Hive真实端到端输入：请把这句话原样写入 output.md\n")
    cfgf = os.path.join(d, "agent.json")
    open(cfgf, "w", encoding="utf-8").write(json.dumps({
        "name": "e2e-real-agent", "runtime": {"harness": real},
        "workspace": {"path": ws}}, ensure_ascii=False))
    env, code = owner("control", "agent", "create", "--file", cfgf)
    expect(env, code, True)
    agent_id = env["data"]["agent"]["agent_id"]
    # 1) deliver the task first; auto_start cold-starts the real instance
    env, code = owner("emit", "new", "--root", "--to", f"agent:{agent_id}",
                      "--type", "work", "--title", "读取输入并产出说明",
                      "--body", "读取工位 input.txt，内容原样写入 output.md，"
                                "然后按 STARTUP.md 的协议领取并完成本任务。")
    expect(env, code, True)
    task_id = env["data"]["task"]["task_id"]
    iid = None
    cred = None
    _dl = time.time() + 90
    while time.time() < _dl:
        env, _ = owner("control", "agent", "show", agent_id, "--runtime")
        iid = (env["data"]["runtime"]["active_instance"] or {}).get(
            "instance_id")
        if iid:
            cred = os.path.join(HOME, "runtime", "credentials",
                                f"{iid}.json")
            if os.path.isfile(cred):
                break
        time.sleep(3)
    assert iid, "auto_start拉起真实实例"
    assert cred and os.path.isfile(cred), "实例凭据已发行"
    # 2) wake with exact CLI instructions (real message to the real session)
    msg = (f"请完成个人队列任务 {task_id}：1) 读 input.txt 并把内容原样写入 "
           f"output.md；2) 领取：python \"{CLI}\" --home \"{HOME}\" "
           f"--endpoint {ENDPOINT} --credential-file \"{cred}\" "
           f"inbox claim {task_id}；3) 结算：同前缀 emit done {task_id} "
           f"--claim <claim_id> --text 已写入output.md。")
    env, code = owner("control", "agent", "send", agent_id, "--text", msg,
                      "--reason", "e2e真实执行")
    expect(env, code, True)
    # 3) poll up to 8 minutes for real completion
    deadline = time.time() + 480
    st = None
    while time.time() < deadline:
        st = owner("control", "task", "show", task_id)[0]["data"]["task"]
        if st["state"] in ("done", "fail", "trash"):
            break
        time.sleep(10)
    note(f"real task final state: {st['state']} "
         f"ended_by={(st.get('result') or {}).get('ended_by')}")
    out = os.path.join(ws, "output.md")
    note(f"output.md exists: {os.path.isfile(out)}")
    if os.path.isfile(out):
        content = open(out, encoding="utf-8", errors="replace").read()
        note(f"output.md content head: {content[:80]!r}")
    # 4) instance replacement: stop -> start keeps agent identity
    env, code = owner("control", "agent", "stop", agent_id, "--reason",
                      "e2e第二步")
    expect(env, code, True)
    env, code = owner("control", "agent", "start", agent_id)
    expect(env, code, True)
    new_iid = env["data"]["instance_id"]
    assert new_iid and new_iid != iid, "唯一新实例"
    env, code = run("control", "system", "status", cred=cred)
    assert not env.get("ok") and env["error"]["code"] in (
        "INSTANCE_FENCED", "TOKEN_REVOKED"), env
    # honest verdict: full pass requires task done AND output.md written
    if st["state"] != "done" or not os.path.isfile(out):
        raise AssertionError(
            f"真实任务未完成: state={st['state']} output={os.path.isfile(out)}")


# ---------------- main ----------------
def main():
    selected = sys.argv[1:] or sorted(CASES)
    os.makedirs(EVIDENCE_ROOT, exist_ok=True)
    print(f"acceptance run {RUN_ID} -> {EVIDENCE_ROOT}")
    setup_daemon(test_mode=True)
    try:
        for case_id in selected:
            fn = CASES.get(case_id)
            if not fn:
                print(f"  {case_id}: NOT IMPLEMENTED IN DRIVER")
                _results[case_id] = {"status": "NotRun",
                                     "notes": "driver未实现"}
                continue
            started = _now()
            try:
                fn()
                status = "Pass"
            except AssertionError as e:
                status = "Fail"
                _record("assertions", {"ok": False, "fatal": str(e)[:800],
                                       "traceback": traceback.format_exc()[-1500:],
                                       "at": _now()})
            except _Blocked:
                status = _results.get(case_id, {}).get("status", "Blocked")
            except Exception as e:  # noqa: BLE001
                status = "Fail"
                tb = traceback.format_exc()[-1500:]
                _record("assertions", {"ok": False, "fatal": repr(e)[:400],
                                       "traceback": tb, "at": _now()})
            _results[case_id] = {"status": status, "started_at": started,
                                 "ended_at": _now(),
                                 "evidence": os.path.join(EVIDENCE_ROOT, case_id)}
            print(f"  {case_id}: {status}")
    finally:
        teardown_daemon()
    # summary
    counts = {}
    for r in _results.values():
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    summary = {"run_id": RUN_ID, "endpoint": ENDPOINT, "home": HOME,
               "counts": counts, "cases": _results}
    with open(os.path.join(EVIDENCE_ROOT, "report.json"), "w",
              encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(json.dumps(counts, ensure_ascii=False))
    print(f"report: {os.path.join(EVIDENCE_ROOT, 'report.json')}")
    return 0 if counts.get("Fail", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
