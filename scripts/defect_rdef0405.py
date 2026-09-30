"""RDEF-04/05 targeted regression: reconcile wake rounds and per-candidate
suppression.

RDEF-04: round counting is PERSISTENT (kv wakeround:<task>) — deleting
notification rows can never reset it; rounds advance :1 :2 :3 monotonically;
notify_max_rounds is a visible+configurable policy (default 12, 1..1000);
cap => exactly one task.notify_exhausted event + queryable state, and the
task is NOT silently forgotten; recovery paths: spec amend / candidate-set
change / raising the cap.

RDEF-05: a stuck pending from one bad subscriber (bogus harness, cold start
failing forever) does NOT suppress other candidates' reconcile rounds;
pending freshness judged per candidate; rounds paced by notify_retry_after
(no per-tick re-send); wake retries create no tasks/receipts/@notices and
push nothing to a callback-less source.

Gates: declined / capacity-full / paused candidates excluded; unauthorized
claim still fenced at claim time (wake is not claim).
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
RUN = os.path.join(ROOT, "work", "defects", f"rdef0405-{int(time.time())}")
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
                        return
            except OSError:
                time.sleep(0.3)
    raise RuntimeError("daemon not healthy")


def db_rows(sql, *params):
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    try:
        return db.execute(sql, params).fetchall()
    finally:
        db.close()


def db_write(sql, *params):
    db = sqlite3.connect(DB, isolation_level=None)
    try:
        db.execute(sql, params)
    finally:
        db.close()


def tick(n=1, adv=0):
    """Let the scheduler tick (2s period) in real time; optionally advance
    the daemon clock first (deterministic pacing)."""
    for _ in range(n):
        if adv:
            env, _c = cli("control", "test", "clock", "advance", "--by",
                          str(adv) + "s", cred=OWNER)
            assert env.get("ok"), f"clock advance failed: {env}"
        time.sleep(2.6)


def dedupe_rows(task_id):
    return {r["dedupe_key"] for r in db_rows(
        "SELECT dedupe_key FROM notifications WHERE dedupe_key LIKE ?",
        f"reconcile:{task_id}:%")}


def agent_rows(task_id, agent_id):
    return {r["dedupe_key"] for r in db_rows(
        "SELECT dedupe_key FROM notifications WHERE agent_id=? AND "
        "dedupe_key LIKE ?", agent_id, f"reconcile:{task_id}:%")}


cli("control", "system", "init", "--listen", f"127.0.0.1:{PORT}",
    "--test-mode")
start_daemon()

# -------- notify_max_rounds: visible, configurable, range-checked (RDEF-04)
env, _ = cli("control", "system", "config", cred=OWNER)
check("notify_max_rounds visible in effective config (default 12)",
      env["data"]["effective_config"].get("notify_max_rounds") == 12)
pol = os.path.join(RUN, "pol.json")
open(pol, "w", encoding="utf-8").write(json.dumps({
    "notify_retry_after": 4, "notify_max_rounds": 3, "notify_jitter": 0,
    "send_unknown_retry_after": 2, "cold_start_batch": 3}))
env, _ = cli("control", "system", "config", "--file", pol,
             "--reason", "rdef0405", cred=OWNER)
check("notify_max_rounds configurable (set 3)", env.get("ok")
      and env["data"]["effective_config"]["notify_max_rounds"] == 3)
open(pol, "w", encoding="utf-8").write('{"notify_max_rounds": 0}')
env, _ = cli("control", "system", "config", "--file", pol,
             "--reason", "bad", cred=OWNER)
check("notify_max_rounds range-checked (0 rejected)",
      not env.get("ok") and env["error"]["code"] == "INVALID_ARGUMENT")
open(pol, "w", encoding="utf-8").write('{"notify_max_rounds": "x"}')
env, _ = cli("control", "system", "config", "--file", pol,
             "--reason", "bad", cred=OWNER)
check("notify_max_rounds type-checked (string rejected)",
      not env.get("ok") and env["error"]["code"] == "INVALID_ARGUMENT")
open(pol, "w", encoding="utf-8").write(json.dumps({
    "notify_retry_after": 4, "notify_max_rounds": 3, "notify_jitter": 0,
    "send_unknown_retry_after": 2, "cold_start_batch": 3}))
cli("control", "system", "config", "--file", pol, "--reason", "rdef0405b",
    cred=OWNER)

# -------- stage: custom port P1; H healthy-but-not-started, BAD stuck
portf = os.path.join(RUN, "p1.json")
open(portf, "w", encoding="utf-8").write(json.dumps({
    "handler": "agents", "accepted_types": ["work"]}, ensure_ascii=False))
env, _ = cli("control", "port", "create", "--route", "p1x", "--file", portf,
             "--reason", "rdef0405", cred=OWNER)
check("port P1 created", env.get("ok"), json.dumps(
    env.get("error", {}), ensure_ascii=False)[:120])
P1 = env["data"]["port"]["port_id"]


def agent_cfg(ws_name, harness, auto_start):
    f = os.path.join(RUN, f"{ws_name}.json")
    open(f, "w", encoding="utf-8").write(json.dumps({
        "name": ws_name,
        "runtime": {"harness": harness, "auto_start": auto_start},
        "subscriptions": [{"port": P1}],
    }, ensure_ascii=False))
    return f


env, _ = cli("control", "agent", "create", "--file", agent_cfg(
    "r04H", "fixture-minimal", False), "--reason", "rdef0405", cred=OWNER)
H = env["data"]["agent"]["agent_id"]
env, _ = cli("control", "agent", "create", "--file", agent_cfg(
    "r04BAD", "no-such-harness", True), "--reason", "rdef0405", cred=OWNER)
BAD = env["data"]["agent"]["agent_id"]
check("H (healthy, not auto-started) + BAD (bogus harness) created",
      bool(H) and bool(BAD))

env, _ = cli("emit", "new", "--root", "--to", f"port:{P1}",
             "--title", "T1", "--body", "b",
             "--credential-file", OWNER)
T1 = env["data"]["task"]["task_id"]
tick(1)
initial = db_rows("SELECT agent_id, state FROM notifications WHERE "
                  "dedupe_key LIKE 'port:%'")
check("initial notify queued for both candidates",
      {r["agent_id"] for r in initial} == {H, BAD},
      str([dict(r) for r in initial]))
check("BAD cold start failing is observable (stuck pending persists)",
      any(r["agent_id"] == BAD and r["state"] == "pending" for r in initial))

# -------- RDEF-05 core: BAD's stuck pending must not suppress rounds
tick(1, adv=5)
r1 = dedupe_rows(T1)
check("round :1 fired despite BAD's stuck pending (both candidates)",
      r1 == {f"reconcile:{T1}:1"}, str(r1))
check("healthy H targeted in round 1",
      f"reconcile:{T1}:1" in agent_rows(T1, H))

# -------- RDEF-04 persistent counting: delete rows, rounds keep advancing
db_write("DELETE FROM notifications WHERE dedupe_key LIKE ?",
         f"reconcile:{T1}:%")
tick(1, adv=5)
r2 = dedupe_rows(T1)
check("rows deleted -> next round :2 (count persisted, not reset to :1)",
      f"reconcile:{T1}:2" in r2 and f"reconcile:{T1}:1" not in r2, str(r2))

# -------- pacing: consecutive rounds are spaced >= notify_retry_after
# (the daemon clock is wall+offset, so both advances and real time move it;
#  the invariant under test is the inter-round interval, asserted from the
#  effective schedule records themselves, not from absence heuristics)
def _round_times():
    return {r["dedupe_key"].rsplit(":", 1)[1]: r["created_at"]
            for r in db_rows(
                "SELECT dedupe_key, created_at FROM notifications WHERE "
                "dedupe_key LIKE ?", f"reconcile:{T1}:%")}
for _ in range(2):
    tick(1, adv=1)
times = sorted(_round_times().items(), key=lambda kv: int(kv[0]))
check("rounds kept advancing (:3 reached)",
      any(k == "3" for k, _ in times), str([k for k, _ in times]))
from datetime import datetime as _dt
def _p(s):
    return _dt.strptime(s.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z")
gaps_ok = all(
    (_p(times[i + 1][1]) - _p(times[i][1])).total_seconds() >= 4
    for i in range(len(times) - 1))
check("pacing: every consecutive round gap >= notify_retry_after (4s)",
      gaps_ok, str(times)[:180])

# -------- cap: exactly one task.notify_exhausted, no :4, task NOT forgotten
tick(2, adv=6)
check("cap reached: no round :4 after notify_max_rounds",
      not any(k.endswith(":4") for k in dedupe_rows(T1)), str(dedupe_rows(T1)))
ev = db_rows("SELECT payload FROM events WHERE entity_type='task' AND "
             "entity_id=? AND kind='task.notify_exhausted'", T1)
check("exactly ONE task.notify_exhausted event", len(ev) == 1, f"n={len(ev)}")
st = db_rows("SELECT v FROM kv WHERE k=?", f"wakeround:{T1}")
stv = json.loads(st[0]["v"]) if st else {}
check("round state queryable (exhausted flag + totals)",
      stv.get("exhausted") is True and stv.get("total") == 3
      and stv.get("offset") == 0, str(stv)[:150])
task_state = db_rows("SELECT state FROM tasks WHERE task_id=?", T1)[0]["state"]
check("task still queued after cap (not silently dropped)",
      task_state == "queued")

# -------- recovery 1: raise the cap via config
open(pol, "w", encoding="utf-8").write('{"notify_max_rounds": 6}')
cli("control", "system", "config", "--file", pol, "--reason", "raise cap",
    cred=OWNER)
tick(2, adv=6)
check("recovery via raised cap: round :4 fires",
      any(k.endswith(":4") for k in dedupe_rows(T1)), str(dedupe_rows(T1)))

# -------- recovery 2: spec amend resets the cap counter
amend = os.path.join(RUN, "amend.json")
open(amend, "w", encoding="utf-8").write('{"title": "T1-amended"}')
env, _ = cli("control", "task", "amend", T1, "--file", amend,
             "--reason", "rdef0405 reset", cred=OWNER)
check("task amended (spec_version bump)", env.get("ok"))
tick(2, adv=6)
stv2 = json.loads(db_rows("SELECT v FROM kv WHERE k=?",
                          f"wakeround:{T1}")[0]["v"])
check("amend reset cap counter (offset advanced)",
      stv2.get("offset", 0) >= 4 and not stv2.get("exhausted"),
      str(stv2)[:150])

# -------- recovery 3: new candidate joins -> candidate set reset + notified
env, _ = cli("control", "agent", "create", "--file", agent_cfg(
    "r04H2", "fixture-minimal", False), "--reason", "rdef0405", cred=OWNER)
H2 = env["data"]["agent"]["agent_id"]
tick(2, adv=6)
check("new candidate H2 receives reconcile wake after joining",
      len(agent_rows(T1, H2)) >= 1, str(agent_rows(T1, H2)))

# -------- paused candidate excluded
cli("control", "agent", "stop", BAD, "--force", "--reason", "pause bad", cred=OWNER)
tick(2, adv=6)
latest_round = max(int(k.rsplit(":", 1)[1]) for k in dedupe_rows(T1))
bad_latest = max((int(k.rsplit(":", 1)[1]) for k in agent_rows(T1, BAD)),
                 default=0)
check("paused BAD excluded from later rounds (its last round is older)",
      bad_latest < latest_round,
      f"bad_last={bad_latest} latest={latest_round}")

# -------- side-effect fences: no tasks/receipts/@notices; no callback push
n_tasks = db_rows("SELECT COUNT(*) c FROM tasks")[0]["c"]
n_outbox = db_rows("SELECT COUNT(*) c FROM outbox WHERE state IN "
                   "('pending','retry')")[0]["c"]
check("reconcile rounds created NO new tasks (only T1 + fixtures)",
      n_tasks == 1, f"tasks={n_tasks}")
check("reconcile rounds pushed NO callbacks (outbox empty)",
      n_outbox == 0, f"outbox={n_outbox}")

# -------- gates with live seeded agents: decline / capacity / permission
m = os.path.join(RUN, "m.json")
cli("control", "test", "seed", "--scenario", "r0405", "--out", m, cred=OWNER)
fx = json.load(open(m, encoding="utf-8"))
sA, sB, mainp = fx["agents"]["A"], fx["agents"]["B"], fx["ports"]["main"]
env, _ = cli("emit", "new", "--root", "--to", f"port:{mainp}",
             "--title", "T2", "--body", "b",
             "--credential-file", OWNER)
T2 = env["data"]["task"]["task_id"]
cli("inbox", "decline", T2, "--reason", "not mine",
    "--credential-file", fx["credentials"]["A"])
tick(2, adv=6)
check("declined A gets NO reconcile rows for T2",
      not agent_rows(T2, sA), str(agent_rows(T2, sA)))
check("non-declined B DOES get reconcile rows for T2",
      agent_rows(T2, sB), str(agent_rows(T2, sB)))

# capacity: cap B at 1 inflight, B claims T2 -> full -> excluded from T3
lim = os.path.join(RUN, "lim.json")
open(lim, "w", encoding="utf-8").write('{"limits": {"max_inflight_work": 1}}')
cli("control", "agent", "update", sB, "--file", lim, "--reason", "cap1",
    cred=OWNER)
env, _ = cli("inbox", "claim", T2, "--credential-file", fx["credentials"]["B"])
check("B claims T2 (fills its capacity)", env.get("ok"))
_d = env.get("data", {})
T2_CLAIM = None
for _r in (_d.get("results") or []):
    if _r.get("task_id") == T2 and _r.get("claim_id"):
        T2_CLAIM = _r["claim_id"]
assert T2_CLAIM, f"claim id missing: {json.dumps(_d)[:200]}"
env, _ = cli("emit", "new", "--root", "--to", f"port:{mainp}",
             "--title", "T3", "--body", "b",
             "--credential-file", OWNER)
T3 = env["data"]["task"]["task_id"]
tick(2, adv=6)
check("capacity-full B excluded from T3 reconcile candidates",
      not agent_rows(T3, sB), str(agent_rows(T3, sB)))
check("capacity-free A still targeted for T3",
      agent_rows(T3, sA), str(agent_rows(T3, sA)))

# permission fence: B frees capacity, gets WOKEN for T4 (wake is not claim),
# then its claim grant is revoked and the claim itself is fenced
env, _ = cli("emit", "done", T2, "--claim", T2_CLAIM,
             "--text", "T2 finished",
             "--credential-file", fx["credentials"]["B"])
check("B finishes T2 (frees capacity)", env.get("ok"),
      json.dumps(env.get("error", {}), ensure_ascii=False)[:160])
env, _ = cli("emit", "new", "--root", "--to", f"port:{mainp}",
             "--title", "T4", "--body", "b",
             "--credential-file", OWNER)
T4 = env["data"]["task"]["task_id"]
cli("inbox", "decline", T4, "--reason", "fence", "--credential-file",
    fx["credentials"]["A"])
_t0 = time.time()
while time.time() - _t0 < 10 and not agent_rows(T4, sB):
    tick(1, adv=3)
check("agent woken for T4 (wake delivery is not claim authorization)",
      bool(agent_rows(T4, sB)), str(agent_rows(T4, sB)))
env, _ = cli("control", "permission", "list", "--subject", f"agent:{sB}",
             cred=OWNER)
grants = env["data"].get("grants") or env["data"].get("items") or []
claim_grants = [g["grant_id"] for g in grants
                if "task.claim" in json.dumps(g)]
for gid in claim_grants:
    cli("control", "permission", "revoke", gid,
        "--reason", "rdef0405 fence test", cred=OWNER)
check("all task.claim grants revoked", len(claim_grants) >= 1,
      f"n={len(claim_grants)}")
env, code = cli("inbox", "claim", T4,
                "--credential-file", fx["credentials"]["B"])
_res = (env.get("data", {}).get("results") or [{}])[0]
check("woken but revoked agent cannot claim (permission fence intact)",
      (not env.get("ok")) or (_res.get("ok") is False
                              and _res["error"]["code"] == "FORBIDDEN"),
      json.dumps(_res, ensure_ascii=False)[:140])

cli("control", "system", "stop", "--reason", "rdef0405 done", cred=OWNER)
with open(os.path.join(RUN, "checks.json"), "w", encoding="utf-8") as f:
    json.dump(checks, f, ensure_ascii=False, indent=2)
failed = [c for c in checks if not c["ok"]]
print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
sys.exit(1 if failed else 0)
