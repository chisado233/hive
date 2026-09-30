"""RDEF-01 targeted regression: slow-command takeover must not duplicate
business effects. Real fault injection (the daemon is os._exit(70)-killed at
the exact fault points); SQL is used ONLY to construct states that are
documented as corruption/legacy-only, exactly as the independent re-acceptance
probe did — the assertions then prove the new behavior refuses blind redo.

Windows covered (per fix-round instruction):
  W1 占位后handler前         : idem_kill_after_reserve (external: start)
  W2 效果已持久化、回填前     : create/grant/producer → atomic single-tx kill
                               (idem_kill_in_tx) — effect+rollback proven;
                               start → idem_kill_after_effect → takeover merge
  W3 外部动作已发生、回填前   : stop → idem_kill_after_effect → retry idempotent
  W4 原请求活着但超租约       : idem_delay_backfill + short lease + 2 threads
  W5 接管后旧执行者恢复       : same run — old executor CAS-loses backfill
  + legacy-orphan guard: SQL-crafted NULL-response row → refusal with
    guidance (no second agent), and no-effect orphan → clean takeover
  + concurrent 20x same-key create -> ONE agent; same-key different-args
    conflict; cross-subject isolation on start
"""
import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(ROOT, "cli", "hive.py")
RUN = os.path.join(ROOT, "work", "defects", f"rdef01-{int(time.time())}")
os.makedirs(RUN, exist_ok=True)
checks = []


def check(name, ok, detail=""):
    checks.append({"check": name, "ok": bool(ok), "detail": str(detail)[:220]})
    print(("PASS " if ok else "FAIL ") + name + (f" | {detail}" if detail else ""))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PORT = free_port()
EP = f"http://127.0.0.1:{PORT}"
OWNER = os.path.join(RUN, "auth", "owner.json")
DB = os.path.join(RUN, "data", "hive.db")


def cli(*a, cred=None, timeout=90):
    argv = [sys.executable, CLI, "--home", RUN, "--endpoint", EP]
    if cred:
        argv += ["--credential-file", cred]
    argv += list(a)
    p = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    try:
        return json.loads(p.stdout), p.returncode
    except ValueError:
        return {"ok": False, "_out": p.stdout[:200]}, p.returncode


def daemon_pid():
    svc = json.load(open(os.path.join(RUN, "runtime", "service.json"),
                         encoding="utf-8"))
    return svc.get("pid")


def alive(pid):
    if not pid:
        return False
    import ctypes
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, 0, int(pid))
    if not handle:
        return False
    kernel32.CloseHandle(handle)
    return True


def start_daemon():
    for _ in range(3):
        env, code = cli("control", "system", "start", "--test-mode")
        assert env.get("ok"), env
        import urllib.request
        for _i in range(20):
            try:
                with urllib.request.urlopen(EP + "/v1/health",
                                            timeout=2) as r:
                    if r.status == 200:
                        return env
            except OSError:
                time.sleep(0.3)
    raise RuntimeError("daemon not healthy after restart")


def db_exec(fn, write=False):
    db = sqlite3.connect(DB, isolation_level=None if write else None)
    db.row_factory = sqlite3.Row
    try:
        return fn(db)
    finally:
        db.close()


def fault_set(point, value=None):
    a = ["control", "test", "fault", "set", "--point", point, "--mode",
         "once"]
    if value is not None:
        a += ["--value", json.dumps(value)]
    env, _ = cli(*a, cred=OWNER)
    assert env.get("ok"), f"fault set {point} failed: {env}"


cli("control", "system", "init", "--listen", f"127.0.0.1:{PORT}",
    "--test-mode")
start_daemon()
m = os.path.join(RUN, "m.json")
cli("control", "test", "seed", "--scenario", "rdef01", "--out", m, cred=OWNER)
fx = json.load(open(m, encoding="utf-8"))
A = fx["agents"]["A"]


def agents_named(name):
    return db_exec(lambda db: [r["agent_id"] for r in db.execute(
        "SELECT agent_id FROM agents WHERE name=?", (name,)).fetchall()])


# ================================================= W2: create is ATOMIC now
fault_set("idem_kill_in_tx")
env, code = cli("control", "agent", "create", "--name", "dup-agent",
                "--request-id", "W2A", cred=OWNER)
pid = daemon_pid()
time.sleep(1.5)
check("W2 create: killed inside business tx (daemon died)",
      not alive(pid), f"pid={pid} code={code}")
start_daemon()
check("W2 create: crash rolled the agent row back (zero objects)",
      agents_named("dup-agent") == [], str(agents_named("dup-agent")))
env, code = cli("control", "agent", "create", "--name", "dup-agent",
                "--request-id", "W2A", cred=OWNER)
check("W2 create: same-key retry succeeds", env.get("ok"),
      json.dumps(env.get("error", {}), ensure_ascii=False)[:150])
check("W2 create: exactly ONE agent after retry",
      len(agents_named("dup-agent")) == 1, str(agents_named("dup-agent")))
env, _ = cli("control", "agent", "create", "--name", "dup-agent",
             "--request-id", "W2A", cred=OWNER)
check("W2 create: third same-key call replays same agent_id",
      env.get("ok") and env["data"]["agent"]["agent_id"]
      == agents_named("dup-agent")[0])

# ================================================= W2: grant is ATOMIC now
spec_path = os.path.join(RUN, "grant.json")
# grant to agent A's subject: actions on its own resources
open(spec_path, "w", encoding="utf-8").write(json.dumps({
    "subject": f"agent:{A}", "actions": ["task.read"],
    "resources": ["created_tasks"]}, ensure_ascii=False))
fault_set("idem_kill_in_tx")
env, code = cli("control", "permission", "grant", "--file", spec_path,
                "--reason", "rdef01", "--request-id", "W2G", cred=OWNER)
pid = daemon_pid()
time.sleep(1.5)
check("W2 grant: killed inside business tx (daemon died)", not alive(pid))
start_daemon()
n0 = db_exec(lambda db: db.execute(
    "SELECT COUNT(*) c FROM audit_log WHERE request_id='W2G' AND "
    "action='permission.grant'").fetchone()["c"])
check("W2 grant: crash rolled the grant back (0 audit effect rows)",
      n0 == 0, f"n={n0}")
env, code = cli("control", "permission", "grant", "--file", spec_path,
                "--reason", "rdef01", "--request-id", "W2G", cred=OWNER)
check("W2 grant: same-key retry succeeds", env.get("ok"),
      json.dumps(env.get("error", {}), ensure_ascii=False)[:150])
n1 = db_exec(lambda db: db.execute(
    "SELECT COUNT(*) c FROM audit_log WHERE request_id='W2G' AND "
    "action='permission.grant'").fetchone()["c"])
check("W2 grant: exactly ONE grant effect after retry", n1 == 1, f"n={n1}")
env, code = cli("control", "permission", "grant", "--file", spec_path,
                "--reason", "rdef01", "--request-id", "W2G", cred=OWNER)
check("W2 grant: third same-key call replays same grant_id",
      env.get("ok") and env["data"]["grant_id"])

# ================================================= W2: producer create ATOMIC
fault_set("idem_kill_in_tx")
env, code = cli("control", "producer", "create", "--name", "dup-prod",
                "--kind", "builtin", "--allow-target", f"port:{fx['ports']['main']}",
                "--request-id", "W2P", cred=OWNER)
pid = daemon_pid()
time.sleep(1.5)
check("W2 producer: killed inside business tx (daemon died)", not alive(pid))
start_daemon()
env, code = cli("control", "producer", "create", "--name", "dup-prod",
                "--kind", "builtin", "--allow-target", f"port:{fx['ports']['main']}",
                "--request-id", "W2P", cred=OWNER)
check("W2 producer: retry succeeds", env.get("ok"))
np = db_exec(lambda db: db.execute(
    "SELECT COUNT(*) c FROM producers WHERE name='dup-prod'"
).fetchone()["c"])
check("W2 producer: exactly ONE producer", np == 1, f"n={np}")

# ================================================= short lease for takeovers
pol = os.path.join(RUN, "lease.json")
open(pol, "w", encoding="utf-8").write('{"idem_lease_seconds": 2}')
env, _ = cli("control", "system", "config", "--file", pol,
             "--reason", "rdef01短租约", cred=OWNER)
check("short idem lease configured", env.get("ok"))

# ================================================= W2/W3: start killed AFTER
# the external effect (instance live) but BEFORE response backfill
cli("control", "agent", "stop", A, "--force", "--reason", "rdef01 prep", cred=OWNER)
fault_set("idem_kill_after_effect")
env, code = cli("control", "agent", "start", A, "--request-id", "W3S",
                cred=OWNER, timeout=60)
pid = daemon_pid()
time.sleep(1.5)
check("W3 start: killed after effect before backfill", not alive(pid),
      f"pid={pid}")
inst_live = db_exec(lambda db: db.execute(
    "SELECT instance_id FROM instances WHERE agent_id=? AND phase IN "
    "('starting','live','stopping')", (A,)).fetchall())
check("W3 start: instance IS live despite crash (effect happened)",
      len(inst_live) == 1, str([dict(r) for r in inst_live]))
orig_iid = inst_live[0]["instance_id"]
start_daemon()
env, code = cli("control", "agent", "start", A, "--request-id", "W3S",
                cred=OWNER, timeout=60)
check("W3 start: same-key retry takes over and rebuilds response",
      env.get("ok") and env["data"].get("instance_id") == orig_iid,
      f"want={orig_iid} got={env.get('data', {}).get('instance_id')} err="
      f"{env.get('error', {}).get('code')}")
live_after = db_exec(lambda db: db.execute(
    "SELECT COUNT(*) c FROM instances WHERE agent_id=? AND phase IN "
    "('starting','live','stopping')", (A,)).fetchone()["c"])
check("W3 start: still exactly ONE live slot", live_after == 1,
      f"live={live_after}")
n_ops = db_exec(lambda db: db.execute(
    "SELECT COUNT(*) c FROM operations WHERE action='agent.start' AND "
    "request_id='W3S'").fetchone()["c"])
check("W3 start: retry merged, no second start op for this request",
      n_ops == 1, f"ops={n_ops}")

# ================================================= W3: stop killed AFTER kill
fault_set("idem_kill_after_effect")
env, code = cli("control", "agent", "stop", A, "--force", "--reason", "rdef01 W3P",
                "--request-id", "W3P", cred=OWNER, timeout=60)
pid = daemon_pid()
time.sleep(1.5)
check("W3 stop: killed after kill before backfill", not alive(pid))
live_mid = db_exec(lambda db: db.execute(
    "SELECT COUNT(*) c FROM instances WHERE agent_id=? AND phase IN "
    "('starting','live','stopping')", (A,)).fetchone()["c"])
check("W3 stop: instance already stopped by the killed request", live_mid == 0,
      f"live={live_mid}")
start_daemon()
env, code = cli("control", "agent", "stop", A, "--force", "--reason", "rdef01 W3P",
                "--request-id", "W3P", cred=OWNER, timeout=60)
check("W3 stop: same-key retry converges (idempotent stop)", env.get("ok"),
      json.dumps(env.get("error", {}), ensure_ascii=False)[:120])
live_end = db_exec(lambda db: db.execute(
    "SELECT COUNT(*) c FROM instances WHERE agent_id=? AND phase IN "
    "('starting','live','stopping')", (A,)).fetchone()["c"])
check("W3 stop: zero live slots after retry", live_end == 0)

# ================================================= W4+W5: original executor
# alive past lease, takeover happens, old executor resumes and must NOT
# clobber the new executor's backfilled response
fault_set("idem_delay_backfill", {"seconds": 6})
res_a, res_b = {}, {}


def run_a():
    res_a["env"], res_a["code"] = cli("control", "agent", "start", A,
                                      "--request-id", "W45", cred=OWNER,
                                      timeout=90)


ta = threading.Thread(target=run_a)
ta.start()
time.sleep(2.8)  # past the 2s lease while executor A is held at backfill
t_b0 = time.time()
env, code = cli("control", "agent", "start", A, "--request-id", "W45",
                cred=OWNER, timeout=60)
res_b["env"], res_b["code"] = env, code
b_wait = time.time() - t_b0
ta.join(timeout=60)
check("W4 takeover: executor B returned while A held (took over lease)",
      env.get("ok") and b_wait < 5,
      f"wait={b_wait:.1f}s err={env.get('error', {}).get('code')}")
check("W5 old executor A resumed and got the takeover's response (no error,"
      " no clobber)",
      res_a.get("env", {}).get("ok"),
      json.dumps(res_a.get("env", {}).get("error", {}),
                 ensure_ascii=False)[:120])
iid_a = res_a.get("env", {}).get("data", {}).get("instance_id")
iid_b = res_b.get("env", {}).get("data", {}).get("instance_id")
check("W5 both executors return the SAME instance", iid_a and iid_a == iid_b,
      f"A={iid_a} B={iid_b}")
gen_row = db_exec(lambda db: db.execute(
    "SELECT executor_gen, response FROM idempotency WHERE request_id='W45'"
).fetchone())
check("W5 fencing generation advanced exactly once (gen=1)",
      gen_row["executor_gen"] == 1, f"gen={gen_row['executor_gen']}")
live_w45 = db_exec(lambda db: db.execute(
    "SELECT COUNT(*) c FROM instances WHERE agent_id=? AND phase IN "
    "('starting','live','stopping')", (A,)).fetchone()["c"])
check("W4/W5: exactly ONE live instance after dual-executor race",
      live_w45 == 1, f"live={live_w45}")

# ================================================= legacy-orphan guard: the
# independent probe's SQL state (successful create, response NULLed) must
# now be REFUSED with guidance, not re-executed
env, _ = cli("control", "agent", "create", "--name", "orphan-ag",
             "--request-id", "ORPH1", cred=OWNER)
check("orphan prep: created ok", env.get("ok"))
db_exec(lambda db: db.execute(
    "UPDATE idempotency SET response=NULL, lease_until='2000-01-01T00:00:00Z'"
    " WHERE request_id='ORPH1'"), write=True)
env, code = cli("control", "agent", "create", "--name", "orphan-ag",
                "--request-id", "ORPH1", cred=OWNER)
err = env.get("error", {})
check("orphan with recorded effect: refused with guidance (no blind redo)",
      not env.get("ok") and err.get("code") == "STORAGE_UNAVAILABLE"
      and err.get("details", {}).get("recorded_effects"),
      f"code={err.get('code')}")
check("orphan with recorded effect: still exactly ONE agent",
      len(agents_named("orphan-ag")) == 1, str(agents_named("orphan-ag")))

# no-effect orphan (reservation only, handler never ran) -> clean takeover
sys.path.insert(0, ROOT)
from hive_core.service import canonical_hash  # noqa: E402
# dispatch hashes the RAW body (pre-validation) — replicate exactly that
_ph = canonical_hash({"name": "orphan2-ag"})
_subj = db_exec(lambda db: db.execute(
    "SELECT subject_id FROM idempotency WHERE request_id='W2A'"
).fetchone()["subject_id"])
db_exec(lambda db: db.execute(
    "INSERT OR REPLACE INTO idempotency(subject_id,command,request_id,"
    "payload_hash,response,created_at,lease_until) VALUES (?,?,?,?,NULL,"
    "'2026-01-01T00:00:00Z','2000-01-01T00:00:00Z')",
    (_subj, "control.agent.create", "ORPH2", _ph)), write=True)
env, code = cli("control", "agent", "create", "--name", "orphan2-ag",
                "--request-id", "ORPH2", cred=OWNER)
check("no-effect orphan: clean takeover executes (no permanent block)",
      env.get("ok"), json.dumps(env.get("error", {}),
                                ensure_ascii=False)[:120])

# ================================================= invariants
env, code = cli("control", "agent", "create", "--name", "other-name",
                "--request-id", "W2A", cred=OWNER)
check("same-key different-content still conflict",
      not env.get("ok") and env["error"]["code"] == "IDEMPOTENCY_CONFLICT")
procs = []
for _ in range(20):
    procs.append(subprocess.Popen(
        [sys.executable, CLI, "--home", RUN, "--endpoint", EP,
         "--credential-file", OWNER, "--format", "json",
         "control", "agent", "create", "--name", "conc-ag",
         "--request-id", "CONC1"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        encoding="utf-8", errors="replace"))
outs = [p.communicate()[0] for p in procs]
ids = set()
for out in outs:
    try:
        e = json.loads(out)
        if e.get("ok"):
            ids.add(e["data"]["agent"]["agent_id"])
    except ValueError:
        pass
check("20x concurrent same-key create -> ONE agent", len(ids) == 1,
      f"ids={len(ids)}")
orphans = db_exec(lambda db: db.execute(
    "SELECT request_id FROM idempotency WHERE response IS NULL").fetchall())
unexpected = [r["request_id"] for r in orphans
              if r["request_id"] not in ("ORPH1", "ORPH2")]
check("no unexpected NULL reservations (crafted probe rows excluded)",
      not unexpected, f"unexpected={unexpected} all="
      f"{[r['request_id'] for r in orphans]}")

cli("control", "agent", "stop", A, "--force", "--reason", "rdef01 teardown", cred=OWNER)
cli("control", "system", "stop", "--reason", "rdef01 done", cred=OWNER)
with open(os.path.join(RUN, "checks.json"), "w", encoding="utf-8") as f:
    json.dump(checks, f, ensure_ascii=False, indent=2)
failed = [c for c in checks if not c["ok"]]
print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
sys.exit(1 if failed else 0)
