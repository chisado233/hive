"""DEF-04 targeted regression: idempotency reservation crash recovery.

Real crash paths (the daemon process is KILLED, no SQL state editing):
1. idem_kill_in_tx (fast/atomic path): emit.new killed after the task row is
   written but BEFORE the transaction commits -> restart -> same request-id
   retry executes cleanly -> exactly ONE task, no orphan reservation.
2. idem_kill_after_reserve (slow path): control.agent.start killed after the
   reservation commits, before the handler runs -> restart -> same
   request-id retry initially waits (lease valid) -> after lease expiry
   (idem_lease_seconds=3 in this run) takes over -> starts exactly once.
3. Same-key different-content still IDEMPOTENCY_CONFLICT; cross-subject
   isolation intact; concurrent 20x same-key still one task.
"""
import json
import os
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(ROOT, "cli", "hive.py")
RUN = os.path.join(ROOT, "work", "defects", f"def04-{int(time.time())}")
os.makedirs(RUN, exist_ok=True)
checks = []


def check(name, ok, detail=""):
    checks.append({"check": name, "ok": bool(ok), "detail": str(detail)[:200]})
    print(("PASS " if ok else "FAIL ") + name + (f" | {detail}" if detail else ""))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PORT = free_port()
EP = f"http://127.0.0.1:{PORT}"


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
    """Windows-safe liveness: ctypes OpenProcess (avoids tasklist encoding)."""
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
        # wait until health really answers (RST right after restart)
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


cli("control", "system", "init", "--listen", f"127.0.0.1:{PORT}",
    "--test-mode")
owner = os.path.join(RUN, "auth", "owner.json")
start_daemon()
m = os.path.join(RUN, "m.json")
cli("control", "test", "seed", "--scenario", "def04", "--out", m, cred=owner)
fx = json.load(open(m, encoding="utf-8"))
A, main = fx["agents"]["A"], fx["ports"]["main"]

# ---------------------------------------------------------------- case 1
# fast path killed inside the business transaction
cli("control", "test", "fault", "set", "--point", "idem_kill_in_tx",
    "--mode", "once", cred=owner)
env, code = cli("emit", "new", "--root", "--to", f"port:{main}",
                "--title", "kill-in-tx", "--body", "b",
                "--request-id", "def04-k1",
                "--credential-file", fx["credentials"]["A"])
check("killed request returns transport/unknown error",
      not env.get("ok") and code in (1, 7),
      f"code={code}")
pid = daemon_pid()
time.sleep(1.5)  # allow OS teardown to complete before liveness probe
check("daemon process actually died", not alive(pid), f"pid={pid}")
start_daemon()
# retry same request-id -> executes exactly once
env, code = cli("emit", "new", "--root", "--to", f"port:{main}",
                "--title", "kill-in-tx", "--body", "b",
                "--request-id", "def04-k1",
                "--credential-file", fx["credentials"]["A"])
check("same-key retry succeeds after crash-rollback", env.get("ok"),
      json.dumps(env.get("error", {}), ensure_ascii=False)[:150])
t_k1 = env["data"]["task"]["task_id"]
env, _ = cli("control", "task", "list", "--type", "work", "--limit", "200",
             cred=owner)
n_k1 = len([i for i in env["data"]["items"] if i["title"] == "kill-in-tx"])
check("exactly ONE task after rollback+retry", n_k1 == 1, f"n={n_k1}")
env, _ = cli("emit", "new", "--root", "--to", f"port:{main}",
             "--title", "kill-in-tx", "--body", "b",
             "--request-id", "def04-k1",
             "--credential-file", fx["credentials"]["A"])
check("third same-key call replays same task",
      env["data"]["task"]["task_id"] == t_k1)

# ---------------------------------------------------------------- case 2
# slow path killed after reservation, before handler
cli("control", "system", "config", "--file", "-", cred=owner) if False else None
pol = os.path.join(RUN, "lease.json")
open(pol, "w", encoding="utf-8").write('{"idem_lease_seconds": 3}')
env, code = cli("control", "system", "config", "--file", pol,
                "--reason", "def04短租约", cred=owner)
check("short idem lease configured", env.get("ok"))
# precondition: no live instance for A (seed leaves fixture instances up)
cli("control", "agent", "stop", A, "--reason", "def04 prep", cred=owner)
env, _ = cli("control", "agent", "show", A, "--runtime", cred=owner)
assert (env["data"]["runtime"]["active_instance"] or {}).get(
    "instance_id") is None, "A must have no live instance before case 2"
cli("control", "test", "fault", "set", "--point", "idem_kill_after_reserve",
    "--mode", "once", cred=owner)
env, code = cli("control", "agent", "start", A, "--request-id", "def04-s1",
                cred=owner, timeout=30)
pid = daemon_pid()
check("slow-path kill: daemon died", not alive(pid), f"pid={pid}")
time.sleep(1.0)
start_daemon()
env, _ = cli("control", "agent", "show", A, "--runtime", cred=owner)
inst_before = (env["data"]["runtime"]["active_instance"] or {}).get(
    "instance_id")
check("no instance started (handler never ran)", inst_before is None)
# retry while lease still valid -> waits then either replay or STORAGE tell
t0 = time.time()
env, code = cli("control", "agent", "start", A, "--request-id", "def04-s1",
                cred=owner, timeout=60)
waited = time.time() - t0
check("same-key retry after lease expiry takes over and starts once",
      env.get("ok") and env["data"].get("instance_id"),
      f"waited={waited:.1f}s err={env.get('error', {}).get('code')}")
iid = env.get("data", {}).get("instance_id")
op_id = env.get("data", {}).get("operation_id")
# retry again -> must REPLAY the original response verbatim (same instance,
# same operation) — a re-execution would mint a new instance/operation
env, code = cli("control", "agent", "start", A, "--request-id", "def04-s1",
                cred=owner, timeout=60)
check("retry replays original response (same instance+operation)",
      env.get("ok") and env["data"].get("instance_id") == iid
      and env["data"].get("operation_id") == op_id,
      f"first={iid}/{op_id} replay="
      f"{env.get('data', {}).get('instance_id')}/"
      f"{env.get('data', {}).get('operation_id')}")
import sqlite3
db = sqlite3.connect(os.path.join(RUN, "data", "hive.db"))
live = db.execute(
    "SELECT COUNT(*) FROM instances WHERE agent_id=? AND phase IN "
    "('starting','live','stopping')", (A,)).fetchone()[0]
db.close()
check("exactly one live slot after takeover", live == 1, f"live={live}")

# ---------------------------------------------------------------- case 3
# invariants that must survive the redesign
env, code = cli("emit", "new", "--root", "--to", f"port:{main}",
                "--title", "other", "--body", "b",
                "--request-id", "def04-k1",
                "--credential-file", fx["credentials"]["A"])
check("same-key different-content still conflict",
      not env.get("ok") and env["error"]["code"] == "IDEMPOTENCY_CONFLICT")
env, code = cli("emit", "new", "--root", "--to", f"port:{main}",
                "--title", "B own", "--body", "b",
                "--request-id", "def04-k1",
                "--credential-file", fx["credentials"]["B"])
check("cross-subject same-key isolated", env.get("ok")
      and env["data"]["task"]["task_id"] != t_k1)

# concurrent 20x same-key still exactly one task
argv = ["emit", "new", "--root", "--to", f"port:{main}", "--title",
        "conc-def04", "--body", "b", "--request-id", "def04-c1"]
procs = []
for _ in range(20):
    procs.append(subprocess.Popen(
        [sys.executable, CLI, "--home", RUN, "--endpoint", EP,
         "--credential-file", fx["credentials"]["A"], "--format", "json"]
        + argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        encoding="utf-8", errors="replace"))
outs = [p.communicate()[0] for p in procs]
ids = set()
for out in outs:
    try:
        e = json.loads(out)
        if e.get("ok"):
            ids.add(e["data"]["task"]["task_id"])
    except ValueError:
        pass
check("20x concurrent same-key -> one task", len(ids) == 1, f"ids={len(ids)}")

# no orphan reservations remain
db = sqlite3.connect(os.path.join(RUN, "data", "hive.db"))
orphans = db.execute("SELECT COUNT(*) FROM idempotency WHERE response IS "
                     "NULL").fetchone()[0]
db.close()
check("no orphan reservations at end", orphans == 0, f"orphans={orphans}")

cli("control", "agent", "stop", A, "--reason", "def04 teardown", cred=owner)
cli("control", "system", "stop", "--reason", "def04 done", cred=owner)
with open(os.path.join(RUN, "checks.json"), "w", encoding="utf-8") as f:
    json.dump(checks, f, ensure_ascii=False, indent=2)
failed = [c for c in checks if not c["ok"]]
print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
sys.exit(1 if failed else 0)
