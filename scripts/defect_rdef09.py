"""RDEF-09 targeted regression: --grace actually happens.

For every stop/restart with a grace window the sequence must be REAL and
observable: instance goes stopping (no new wake delivery; claims/progress
still authorized), a save/handoff hint is delivered to the instance, the
full grace duration elapses, then the harness terminate is called and
confirmed; credentials are revoked only at termination (old instance fenced
afterwards); force skips the window explicitly. Accepted asynchronously —
the control request returns immediately, business writes continue, daemon
restarts mid-grace are recovered from persistent plans.
"""
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(ROOT, "cli", "hive.py")
RUN = os.path.join(ROOT, "work", "defects", f"rdef09-{int(time.time())}")
os.makedirs(RUN, exist_ok=True)
checks = []
DB = os.path.join(RUN, "data", "hive.db")


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


def cli(*a, timeout=90):
    p = subprocess.run([sys.executable, CLI, "--home", RUN, "--endpoint", EP,
                        "--credential-file", OWNER] + list(a),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    return json.loads(p.stdout) if p.stdout.strip() else {}


def start_daemon():
    for _ in range(3):
        cli("control", "system", "start", "--test-mode")
        import urllib.request
        for _i in range(20):
            try:
                with urllib.request.urlopen(EP + "/v1/health",
                                            timeout=2) as r:
                    if r.status == 200:
                        return
            except OSError:
                time.sleep(0.3)
    raise RuntimeError("daemon not healthy")


def db1(sql, *p):
    db = sqlite3.connect(DB)
    try:
        return db.execute(sql, p).fetchone()
    finally:
        db.close()


def phase_of(agent):
    r = db1("SELECT instance_id, phase FROM instances WHERE agent_id=? AND "
            "phase IN ('starting','live','stopping') ORDER BY started_at "
            "DESC", agent)
    return (r[0], r[1]) if r else (None, None)


def op_state(op_id):
    r = db1("SELECT state, outcome_unknown FROM operations WHERE "
            "operation_id=?", op_id)
    return (r[0], r[1]) if r else (None, None)


def fixture_msgs(agent):
    db = sqlite3.connect(DB)
    try:
        row = db.execute("SELECT v FROM kv WHERE k=?",
                         (f"fixture:{agent}",)).fetchone()
        return json.loads(row[0]).get("received", []) if row else []
    finally:
        db.close()


cli("control", "system", "init", "--listen", f"127.0.0.1:{PORT}",
    "--test-mode")
start_daemon()
m = os.path.join(RUN, "m.json")
cli("control", "test", "seed", "--scenario", "rdef09", "--out", m)
fx = json.load(open(m, encoding="utf-8"))
A, B = fx["agents"]["A"], fx["agents"]["B"]
main = fx["ports"]["main"]

# ---------- 1: grace=4s — async accept, hint, real elapsed time, fencing
iid0, _ = phase_of(A)
t0 = time.time()
env = cli("control", "agent", "stop", A, "--grace", "4s",
          "--reason", "rdef09 grace")
accept_t = time.time() - t0
check("grace stop accepted asynchronously (returns in <2s)",
      env.get("ok") and env["data"]["instance_stop_result"] == "grace"
      and accept_t < 2, f"t={accept_t:.1f}s res="
      f"{env.get('data', {}).get('instance_stop_result')}")
msgs = fixture_msgs(A)
check("save/handoff hint delivered to the instance",
      any("停止" in x.get("message", "") for x in msgs),
      str([x.get("message", "")[:40] for x in msgs[-3:]]))
check("instance marked stopping during grace (no new wake delivery)",
      phase_of(A) == (iid0, "stopping"), str(phase_of(A)))
# claims still authorized during grace (credentials not yet revoked)
env2 = cli("emit", "new", "--root", "--to", f"agent:{A}",
           "--title", "g-w", "--body", "b")
gw = env2["data"]["task"]["task_id"]
inst_cred = os.path.join(RUN, "runtime", "credentials", f"{iid0}.json")
p = subprocess.run([sys.executable, CLI, "--home", RUN, "--endpoint", EP,
                    "--credential-file", inst_cred, "inbox", "claim", gw],
                   capture_output=True, text=True, encoding="utf-8",
                   timeout=30)
claim_env = json.loads(p.stdout) if p.stdout.strip() else {}
check("in-flight claim works DURING grace (credentials still valid)",
      claim_env.get("ok")
      or (claim_env.get("data", {}).get("results") or [{}])[0].get("ok"),
      json.dumps(claim_env.get("error", {}),
                 ensure_ascii=False)[:80])
# a wake for a queued task must NOT be sent to a stopping instance
db = sqlite3.connect(DB, isolation_level=None)
db.execute("INSERT OR IGNORE INTO notifications(notify_id,agent_id,"
           "dedupe_key,message,state,created_at) VALUES (?,?,?,"
           "'wake probe','pending',?)", ("view_probe1", A, "probe:1",
                                          time.strftime(
                                              "%Y-%m-%dT%H:%M:%SZ",
                                              time.gmtime())))
db.close()
time.sleep(3)
sent = db1("SELECT COUNT(*) FROM notifications WHERE notify_id='view_probe1'"
           " AND state='sent'")[0]
check("no wake delivered to a stopping instance during grace", sent == 0)
# wait past the deadline for the real termination
while time.time() - t0 < 12 and phase_of(A)[1] not in (None, "stopped"):
    time.sleep(0.5)
elapsed = time.time() - t0
check("termination happened AFTER the full grace window (>=4s)",
      phase_of(A)[0] is None and elapsed >= 4,
      f"elapsed={elapsed:.1f}s phase={phase_of(A)}")
env = cli("control", "operation", "show", env["data"]["operation_id"])
op = env.get("data", {}).get("operation") or env.get("data", {})
check("stop op succeeded after grace", op.get("state") == "succeeded",
      str(op.get("state")))
check("wake not silently lost during grace (still pending or sent)",
      db1("SELECT state FROM notifications WHERE notify_id='view_probe1'"
          )[0] in ("pending", "sent"))

# old instance credential now fenced
p = subprocess.run([sys.executable, CLI, "--home", RUN, "--endpoint", EP,
                    "--credential-file", inst_cred, "inbox", "list"],
                   capture_output=True, text=True, encoding="utf-8",
                   timeout=30)
fenced = json.loads(p.stdout) if p.stdout.strip() else {}
check("old instance credentials revoked at termination (fenced)",
      not fenced.get("ok") and fenced.get("error", {}).get("code") in
      ("TOKEN_REVOKED", "INSTANCE_FENCED", "AUTH_REQUIRED"),
      fenced.get("error", {}).get("code"))

# ---------- 2: force skips the grace window explicitly
cli("control", "agent", "start", A)
time.sleep(1)
iidF, _ = phase_of(A)
t0 = time.time()
env = cli("control", "agent", "stop", A, "--grace", "30s", "--force",
          "--reason", "rdef09 force")
el = time.time() - t0
check("force stop skips grace (immediate stopped)", env.get("ok")
      and env["data"]["instance_stop_result"] == "stopped" and el < 3,
      f"t={el:.1f}s")

# ---------- 3: busy instance still gets its window
cli("control", "agent", "start", A)
time.sleep(1)
cli("control", "test", "harness", A, "--action", "busy")
t0 = time.time()
env = cli("control", "agent", "stop", A, "--grace", "3s",
          "--reason", "rdef09 busy")
check("busy instance: grace accepted", env.get("ok")
      and env["data"]["instance_stop_result"] == "grace")
while time.time() - t0 < 10 and phase_of(A)[1] not in (None, "stopped"):
    time.sleep(0.5)
check("busy instance terminated after its window",
      phase_of(A)[0] is None and time.time() - t0 >= 3,
      f"t={time.time()-t0:.1f}s")

# ---------- 4: stop result unknown at deadline — slot retained
cli("control", "agent", "start", A)
time.sleep(1)
cli("control", "test", "fault", "set", "--point", "harness_stop_unknown",
    "--value", '{"entity": "%s"}' % A)
t0 = time.time()
env = cli("control", "agent", "stop", A, "--grace", "3s",
          "--reason", "rdef09 unknown")
op_id = env["data"]["operation_id"]
while time.time() - t0 < 10 and op_state(op_id)[0] == "running" \
        and not op_state(op_id)[1]:
    time.sleep(0.5)
st, unk = op_state(op_id)
check("grace deadline + unknown stop: op running outcome_unknown, slot kept",
      st == "running" and unk == 1 and phase_of(A)[1] == "stopping",
      f"op={st}/{unk} phase={phase_of(A)}")
env = cli("control", "test", "harness", A, "--action", "stop-confirm")
_rel = [e for e in (env.get("data", {}).get("events") or [])
        if e.get("released")]
check("confirm_stop releases the unknown slot", env.get("ok") and _rel,
      json.dumps(env.get("data", {}).get("events"), ensure_ascii=False)[:100])
cli("control", "test", "fault", "clear",
    "--point", "harness_stop_unknown")

# ---------- 5: stop failure at deadline — explicit failure, observable
cli("control", "agent", "start", A)
time.sleep(1)
cli("control", "test", "fault", "set", "--point", "harness_stop_fail",
    "--value", '{"entity": "%s"}' % A)
t0 = time.time()
env = cli("control", "agent", "stop", A, "--grace", "3s",
          "--reason", "rdef09 stopfail")
op_id = env["data"]["operation_id"]
while time.time() - t0 < 10 and op_state(op_id)[0] == "running":
    time.sleep(0.5)
st, _ = op_state(op_id)
check("grace stop failure recorded (op failed, no silent loss)",
      st == "failed", f"op={st}")

# ---------- 6: single-instance constraint during grace
cli("control", "agent", "start", A)
time.sleep(1)
iidS, _ = phase_of(A)
cli("control", "agent", "stop", A, "--grace", "5s",
    "--reason", "rdef09 single")
env = cli("control", "agent", "start", A)
check("start during grace merges into the stopping instance (no double)",
      env.get("data", {}).get("already_running") is True
      and db1("SELECT COUNT(*) FROM instances WHERE agent_id=? AND phase IN "
              "('starting','live','stopping')", A)[0] == 1,
      json.dumps(env.get("data", {}), ensure_ascii=False)[:100])
_t6 = time.time()
while time.time() - _t6 < 15 and phase_of(A)[0] is not None:
    time.sleep(0.5)

# ---------- 7: restart with grace replaces the instance after the window
cli("control", "agent", "start", A)
time.sleep(1)
iidR, _ = phase_of(A)
t0 = time.time()
env = cli("control", "agent", "restart", A, "--grace", "3s",
          "--reason", "rdef09 graceful restart")
check("graceful restart accepted async (replacement deferred)",
      env.get("ok") and env["data"].get("grace_pending")
      and not env["data"].get("new_instance_id"),
      json.dumps(env.get("data", {}), ensure_ascii=False)[:110])
while time.time() - t0 < 15:
    cur_iid, cur_phase = phase_of(A)
    if cur_iid and cur_iid != iidR and cur_phase == "live":
        break
    time.sleep(0.5)
cur_iid, cur_phase = phase_of(A)
check("replacement instance started after grace termination",
      cur_iid and cur_iid != iidR and cur_phase == "live",
      f"old={iidR} new={cur_iid}")

# ---------- 8: daemon restart mid-grace recovers the plan
cli("control", "agent", "stop", A, "--grace", "8s",
    "--reason", "rdef09 daemon restart")
iidD, _ = phase_of(A)
cli("control", "system", "stop", "--reason", "rdef09 mid-grace")
time.sleep(1.5)
start_daemon()
check("grace plan survived the daemon restart", phase_of(A) ==
      (iidD, "stopping"), str(phase_of(A)))
t0 = time.time()
while time.time() - t0 < 15 and phase_of(A)[0] is not None:
    time.sleep(0.5)
check("termination completed after daemon restart",
      phase_of(A)[0] is None)

cli("control", "system", "stop", "--reason", "rdef09 done")
with open(os.path.join(RUN, "checks.json"), "w", encoding="utf-8") as f:
    json.dump(checks, f, ensure_ascii=False, indent=2)
failed = [c for c in checks if not c["ok"]]
print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
sys.exit(1 if failed else 0)
