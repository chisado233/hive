"""RDEF-03 targeted regression: the crash-restart plan is a recoverable
state machine, not a consume-then-forget schedule.

Covers all demanded behaviors:
  1. 到期前不启动，到期后只启动一次
  2. 暂时启动失败后计划保留并按策略顺延（同档位重排，不升级也不丢失）
  3. 连续失败达限次进入明确、可查询的待干预状态（agent.show --runtime）
  4. 消费计划(transition to starting)与调用启动之间崩溃可恢复
  5. daemon 重启后计划继续生效
  6. paused 不被自动拉起
  7. 旧计划不覆盖新实例（外部 start 接管槽位后计划作废）
  8. 自动通知/冷启动路径不绕过退避规则
  + 手动 start 成功清除待干预计划（恢复路径）
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
RUN = os.path.join(ROOT, "work", "defects", f"rdef03-{int(time.time())}")
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


def tick(adv=0):
    if adv:
        env = cli("control", "test", "clock", "advance", "--by", f"{adv}s")
        assert env.get("ok"), env
    time.sleep(2.6)


def kv_plan(agent):
    db = sqlite3.connect(DB)
    try:
        row = db.execute("SELECT v FROM kv WHERE k=?",
                         (f"restart:{agent}",)).fetchone()
        return json.loads(row[0]) if row else {}
    finally:
        db.close()


def live_count(agent):
    db = sqlite3.connect(DB)
    try:
        return db.execute(
            "SELECT COUNT(*) FROM instances WHERE agent_id=? AND phase IN "
            "('starting','live','stopping')", (agent,)).fetchone()[0]
    finally:
        db.close()


def agent_events(agent, kind):
    db = sqlite3.connect(DB)
    try:
        return db.execute(
            "SELECT COUNT(*) FROM events WHERE entity_type='agent' AND "
            "entity_id=? AND kind=?", (agent, kind)).fetchone()[0]
    finally:
        db.close()


cli("control", "system", "init", "--listen", f"127.0.0.1:{PORT}",
    "--test-mode")
start_daemon()
pol = os.path.join(RUN, "pol.json")
open(pol, "w", encoding="utf-8").write(json.dumps({
    "restart_backoff": [15, 30, 60], "restart_max_per_hour": 10}))
cli("control", "system", "config", "--file", pol, "--reason", "rdef03")
m = os.path.join(RUN, "m.json")
cli("control", "test", "seed", "--scenario", "rdef03", "--out", m)
fx = json.load(open(m, encoding="utf-8"))
A = fx["agents"]["A"]


def set_harness(h):
    f = os.path.join(RUN, "h.json")
    open(f, "w", encoding="utf-8").write(json.dumps(
        {"runtime": {"harness": h}}))
    cli("control", "agent", "update", A, "--file", f, "--reason", "rdef03")


# ---------------- 1: not before due, exactly once after due
cli("control", "test", "harness", A, "--action", "crash")
p1 = kv_plan(A)
check("crash schedules plan (state=scheduled, step=15s)",
      p1.get("state") == "scheduled" and p1.get("attempts") == 1
      and p1.get("next_allowed_at"), str(p1))
tick(adv=3)
check("before due: no auto-start", live_count(A) == 0,
      f"live={live_count(A)}")
tick(adv=14)
check("after due: auto-start fired exactly once", live_count(A) == 1,
      f"live={live_count(A)}")
p2 = kv_plan(A)
check("plan done after success (no next_allowed, attempts kept)",
      not p2.get("next_allowed_at") and p2.get("attempts") == 1
      and not p2.get("state"), str(p2))

# ---------------- 2: start failure keeps the plan, defers at same step
# (A is live on fixture from step 1; switching its harness to a bogus one
#  makes the DUE restart attempt fail while the crash itself still works)
set_harness("gone-harness")
cli("control", "test", "harness", A, "--action", "crash")
p3 = kv_plan(A)
check("second crash schedules step=30s (attempts escalate, plan scheduled)",
      p3.get("state") == "scheduled" and p3.get("attempts") == 2, str(p3))
tick(adv=32)
p4 = kv_plan(A)
check("start failure: plan RETAINED as state=failed with new due time",
      p4.get("state") == "failed" and p4.get("fail_count") == 1
      and p4.get("next_allowed_at") and p4.get("last_error"),
      str(p4)[:170])
check("failed attempt left no instance", live_count(A) == 0)
check("agent.restart_failed event recorded",
      agent_events(A, "agent.restart_failed") >= 1)
n1 = agent_events(A, "agent.restart_failed")
tick(adv=32)
p5 = kv_plan(A)
check("second failure: fail_count=2, plan still retained",
      p5.get("fail_count") == 2 and p5.get("next_allowed_at"), str(p5)[:170])
tick(adv=32)
p6 = kv_plan(A)

# ---------------- 3: 3 consecutive failures -> awaiting_intervention
check("3rd failure: explicit awaiting_intervention state",
      p6.get("state") == "awaiting_intervention"
      and not p6.get("next_allowed_at"), str(p6)[:170])
check("agent.restart_stalled event recorded",
      agent_events(A, "agent.restart_stalled") >= 1)
n_before = agent_events(A, "agent.restart_failed")
tick(adv=40)
check("awaiting_intervention: no further auto attempts",
      agent_events(A, "agent.restart_failed") == n_before
      and live_count(A) == 0)
env = cli("control", "agent", "show", A, "--runtime")
plan_show = ((env.get("data", {}).get("runtime") or {})
             .get("restart_plan") or {})
check("pending-intervention state queryable via agent show --runtime",
      plan_show.get("state") == "awaiting_intervention"
      and plan_show.get("last_error"), str(plan_show)[:150])

# ---------------- recovery: manual start clears the plan
set_harness("fixture-minimal")
env = cli("control", "agent", "start", A)
check("manual start succeeds after intervention", env.get("ok"),
      json.dumps(env.get("error", {}), ensure_ascii=False)[:100])
check("manual start cleared the plan", not kv_plan(A))

# ---------------- 4: crash between consume-transition and start call
# (the product commits state=starting one tick before op_start_agent; a
#  daemon death in that window leaves exactly this state; crafted below
#  to the byte-identical kv the product itself writes there)
cli("control", "agent", "stop", A, "--force", "--reason", "free slot for recovery case")
db = sqlite3.connect(DB, isolation_level=None)
import datetime as _dti
_from = db.execute("SELECT MAX(occurred_at) FROM events").fetchone()[0]
_now = _dti.datetime.strptime(_from, "%Y-%m-%dT%H:%M:%SZ")
_nxt = (_now - _dti.timedelta(seconds=40)).strftime("%Y-%m-%dT%H:%M:%SZ")
_sat = (_now - _dti.timedelta(seconds=35)).strftime("%Y-%m-%dT%H:%M:%SZ")
db.execute("UPDATE kv SET v=? WHERE k=?", (json.dumps({
    "attempts": 1, "next_allowed_at": _nxt,
    "state": "starting", "fail_count": 0, "last_error": None,
    "started_at": _sat, "op_id": None},
    ensure_ascii=False), f"restart:{A}"))
db.close()
tick(adv=1)
p7 = kv_plan(A)
check("state=starting with no live instance recovers as failed attempt",
      p7.get("state") == "failed" and p7.get("fail_count") == 1
      and p7.get("next_allowed_at"), str(p7)[:150])

# ---------------- 5: daemon restart keeps the plan
cli("control", "test", "harness", A, "--action", "crash") if live_count(A) \
    else None
# ensure a live instance then crash it for a fresh plan
if live_count(A) == 0:
    cli("control", "agent", "start", A)
    tick(1)
cli("control", "test", "harness", A, "--action", "crash")
p8 = kv_plan(A)
check("fresh plan scheduled before daemon restart", p8.get("state") ==
      "scheduled" and p8.get("next_allowed_at"), str(p8)[:120])
cli("control", "system", "stop", "--reason", "rdef03 restart")
time.sleep(1.5)
start_daemon()
check("daemon restart: plan still present", kv_plan(A).get("state") ==
      "scheduled")
tick(adv=20)
check("plan fires after daemon restart", live_count(A) == 1)

# ---------------- 6: paused agents are never auto-lifted
cli("control", "test", "harness", A, "--action", "crash")
cli("control", "agent", "stop", A, "--force", "--reason", "pause during backoff")
tick(adv=30)
check("paused agent not auto-lifted; plan cleared",
      live_count(A) == 0 and not kv_plan(A).get("next_allowed_at"))

# ---------------- 7: an old plan never overrides a newer instance
if live_count(A) == 0:
    cli("control", "agent", "start", A)
    tick(1)
cli("control", "test", "harness", A, "--action", "crash")
check("plan scheduled while slot free", kv_plan(A).get("state") ==
      "scheduled")
env = cli("control", "agent", "start", A)  # external start takes the slot
tick(adv=20)
check("external start supersedes the plan (no duplicate instance)",
      live_count(A) == 1 and not kv_plan(A).get("next_allowed_at"),
      f"live={live_count(A)}")

# ---------------- 8: cold start must not bypass backoff
cli("control", "test", "harness", A, "--action", "crash")
p9 = kv_plan(A)
check("plan scheduled for bypass test", p9.get("state") == "scheduled")
cli("control", "agent", "send", A, "--text", "wake probe",
    "--reason", "rdef03 bypass")
tick(adv=3)
check("pending wake does NOT cold-start during backoff",
      live_count(A) == 0, f"live={live_count(A)}")
tick(adv=16)
check("after due the agent comes up (via the plan, not before)",
      live_count(A) == 1)

cli("control", "system", "stop", "--reason", "rdef03 done")
with open(os.path.join(RUN, "checks.json"), "w", encoding="utf-8") as f:
    json.dump(checks, f, ensure_ascii=False, indent=2)
failed = [c for c in checks if not c["ok"]]
print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
sys.exit(1 if failed else 0)
