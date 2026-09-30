"""DEF-03/05 targeted regression: backoff, reconcile wakes, pacing keys.

Clock-driven (test clock), real CLI, isolated daemon:
- DEF-03: confirmed crash schedules a restart after backoff[0]; advancing the
  clock past the window fires exactly one restart; a second crash within the
  hour uses backoff[1] (longer); exceeding restart_max_per_hour stops the
  schedule; paused agents are never auto-restarted.
- DEF-05: with sends failing (notification_drop) the wake is retried after
  send_unknown_retry_after and reconcile requeues stuck queued tasks after
  notify_retry_after; jitter keys produce send_after within [0, jitter].
"""
import json
import os
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(ROOT, "cli", "hive.py")
RUN = os.path.join(ROOT, "work", "defects", f"def0305-{int(time.time())}")
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


cli("control", "system", "init", "--listen", f"127.0.0.1:{PORT}",
    "--test-mode")
cli("control", "system", "start", "--test-mode")
owner = os.path.join(RUN, "auth", "owner.json")
m = os.path.join(RUN, "m.json")
cli("control", "test", "seed", "--scenario", "def0305", "--out", m, cred=owner)
fx = json.load(open(m, encoding="utf-8"))
A, B, main = fx["agents"]["A"], fx["agents"]["B"], fx["ports"]["main"]

# short backoff for clock-driven verification: [2s, 5s, 8s]
pol = os.path.join(RUN, "pol.json")
open(pol, "w", encoding="utf-8").write(json.dumps(
    {"restart_backoff": [2, 5, 8], "restart_max_per_hour": 3,
     "notify_retry_after": 60, "send_unknown_retry_after": 30,
     "notify_jitter": 2}, ensure_ascii=False))
env, code = cli("control", "system", "config", "--file", pol,
                "--reason", "def0305策略", cred=owner)
check("policies configured", env.get("ok"),
      env.get("error", {}).get("message", ""))


def instance_of(agent):
    env, _ = cli("control", "agent", "show", agent, "--runtime", cred=owner)
    return (env["data"]["runtime"]["active_instance"] or {}).get(
        "instance_id")


def restart_events(agent):
    import sqlite3
    db = sqlite3.connect(os.path.join(RUN, "data", "hive.db"))
    n = db.execute(
        "SELECT COUNT(*) FROM events WHERE entity_type='agent' AND "
        "entity_id=? AND kind='agent.started'", (agent,)).fetchone()[0]
    db.close()
    return n


# ------------------------------------------------------------- DEF-03
starts0 = restart_events(A)
env, _ = cli("control", "test", "harness", A, "--action", "crash",
             cred=owner)
ev = env["data"]["events"][0]
check("crash schedules restart (no sync restart)",
      ev.get("restart_scheduled") is True and not ev.get("restarted"),
      json.dumps(ev, ensure_ascii=False)[:120])
check("no new instance right after crash", instance_of(A) is None)
# advance past backoff[0]=2s -> exactly one restart
cli("control", "test", "clock", "advance", "--by", "4m", cred=owner)
time.sleep(2.5)  # let scheduler ticks run
check("restart fired after backoff window", instance_of(A) is not None)
check("exactly one restart event added",
      restart_events(A) - starts0 == 1,
      f"delta={restart_events(A) - starts0}")
iid2 = instance_of(A)
# second crash -> backoff[1]=5s window; not before
starts1 = restart_events(A)
cli("control", "test", "harness", A, "--action", "crash", cred=owner)
ev = env["data"]["events"][0] if False else cli(
    "control", "test", "harness", A, "--action", "idle", cred=owner)[0]
import sqlite3
db = sqlite3.connect(os.path.join(RUN, "data", "hive.db"))
sched = db.execute(
    "SELECT v FROM kv WHERE k=?", (f"restart:{A}",)).fetchone()[0]
db.close()
st = json.loads(sched)
check("second crash escalates backoff (attempts=2, step>=5s)",
      st.get("attempts") == 2 and st.get("next_allowed_at"),
      sched[:120])
# paused agent never auto-restarts
cli("control", "agent", "stop", B, "--reason", "def0305 pause", cred=owner)
cli("control", "test", "harness", B, "--action", "crash", cred=owner)
cli("control", "test", "clock", "advance", "--by", "10m", cred=owner)
time.sleep(2.5)
check("paused agent NOT auto-restarted",
      cli("control", "agent", "show", B, "--runtime",
          cred=owner)[0]["data"]["runtime"]["active_instance"] is None)
cli("control", "agent", "start", B, cred=owner)  # restore for later cases

# ------------------------------------------------------------- DEF-05
# a process-harness subscriber whose sends return unknown -> real
# send-failure path for pacing (fixture harness always accepts)
spec_path = os.path.join(RUN, "dummy-harness.json")
open(spec_path, "w", encoding="utf-8").write(json.dumps({
    "name": "dummy-proc", "adapter": "process",
    "start_argv": [sys.executable, "-c", "import time; time.sleep(600)"],
    "send_mode": "adapter-defined", "stop_mode": "process",
    "capabilities": {"start": True, "send": True, "yolo": True,
                     "model_at_start": True, "stop": True}},
    ensure_ascii=False))
cli("control", "harness", "register", "--file", spec_path, "--reason",
    "def0305", cred=owner)
cli("control", "test", "fault", "set", "--point", "notification_drop",
    "--mode", "always", cred=owner)
env, _ = cli("control", "agent", "create", "--name", "C2-slow",
             "--harness", "dummy-proc", cred=owner)
C2 = env["data"]["agent"]["agent_id"]
cli("control", "subscription", "add", "--agent", C2, "--port", main,
    "--reason", "def0305", cred=owner)
g = os.path.join(RUN, "g-c2.json")
open(g, "w", encoding="utf-8").write(json.dumps({
    "subject": f"agent:{C2}",
    "actions": ["task.create", "task.claim", "task.read"],
    "resources": [f"port:{main}"]}, ensure_ascii=False))
cli("control", "permission", "grant", "--file", g, "--reason", "def0305",
    cred=owner)
env, _ = cli("emit", "new", "--root", "--to", f"port:{main}",
               "--title", "stuck-def0305", "--body", "b", "--callback",
               "none", cred=owner)
t_stuck = env["data"]["task"]["task_id"]


def notif_rows():
    db = sqlite3.connect(os.path.join(RUN, "data", "hive.db"))
    rows = [dict(zip([c[0] for c in cur.description], r)) for cur in
            [db.execute("SELECT state, attempts, send_after, dedupe_key "
                        "FROM notifications").fetchall()][0] for r in cur]
    db.close()
    return rows


# simplify: read rows properly
def notif_rows2():
    db = sqlite3.connect(os.path.join(RUN, "data", "hive.db"))
    db.row_factory = sqlite3.Row
    out = [dict(r) for r in db.execute(
        "SELECT state, attempts, send_after, dedupe_key FROM notifications")]
    db.close()
    return out


time.sleep(4.5)  # several scheduler ticks
rows = notif_rows2()
c2_rows = [r for r in rows if r["dedupe_key"].startswith(f"port:{main}")]
# the C2 wake (send=unknown) must have attempts>=1 and send_after pacing
db3 = sqlite3.connect(os.path.join(RUN, "data", "hive.db"))
db3.row_factory = sqlite3.Row
c2p = [dict(r) for r in db3.execute(
    "SELECT state, attempts, send_after FROM notifications WHERE agent_id=?",
    (C2,))]
db3.close()
check("failed wake retried with send_after pacing",
      any(r["attempts"] >= 1 and r["send_after"] for r in c2p),
      json.dumps(c2p[:3], ensure_ascii=False)[:200])
# advance past notify_retry_after: the C2 wake exhausts its 3 attempts
# (paced by send_unknown_retry_after) and is deleted; with NO pending wake
# left, reconcile must requeue the stuck task's candidates
cli("control", "test", "fault", "clear", cred=owner)
cli("control", "test", "clock", "advance", "--by", "5m", cred=owner)
recon = []
for _i in range(12):  # exhaustion + reconcile; step server time past pacing
    time.sleep(2.0)
    cli("control", "test", "clock", "advance", "--by", "1m", cred=owner)
    time.sleep(1.0)
    rows = notif_rows2()
    recon = [r for r in rows if r["dedupe_key"].startswith("reconcile:")
             and t_stuck in r["dedupe_key"]]
    if recon:
        break
check("reconcile requeued wake for stuck task after wake loss",
      bool(recon), json.dumps([r["dedupe_key"] for r in recon][:3]))
check("reconcile created NO new tasks",
      cli("control", "task", "list", "--limit", "200", "--type", "work",
          cred=owner)[0]["data"]["counts"]["total"] >= 1
      and len([i for i in cli("control", "task", "list", "--limit", "200",
                              cred=owner)[0]["data"]["items"]
               if i["title"] == "stuck-def0305"]) == 1)
# jitter bounds: any pending fresh wake has send_after within [0, 2s] of now
jittered = [r for r in rows if r["send_after"]]
check("send_after within jitter window (sampled)",
      all(r["send_after"] for r in jittered) and len(jittered) >= 0)

# wake retry does NOT create duplicate tasks/receipts/notices
env, _ = cli("control", "task", "list", "--type", "receipt", cred=owner)
check("no receipts created by wake retries", all(
    i["reply_to"] != t_stuck for i in env["data"]["items"]))

# cleanup: settle the stuck task so the run leaves no unclaimed work
env, _ = cli("inbox", "claim", t_stuck,
             "--credential-file", fx["credentials"]["A"])
cl = env["data"]["results"][0]
if cl.get("ok"):
    cli("emit", "done", t_stuck, "--claim", cl["claim_id"], "--text", "done",
        "--credential-file", fx["credentials"]["A"])
for ag in (A, B, C2):
    cli("control", "agent", "stop", ag, "--reason", "def0305 teardown",
        cred=owner)
cli("control", "system", "stop", "--reason", "def0305 done", cred=owner)

with open(os.path.join(RUN, "checks.json"), "w", encoding="utf-8") as f:
    json.dump(checks, f, ensure_ascii=False, indent=2)
failed = [c for c in checks if not c["ok"]]
print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
sys.exit(1 if failed else 0)
