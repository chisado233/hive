"""DEF-01/02 targeted regression: workspace split-brain & STARTUP.md loop.

Drives the real CLI against an isolated daemon:
- explicit workspace gets agent.md/config.json/prompt.md (DEF-01)
- startup brief contains protocol + individual prompt exactly once,
  current responsibility summary, no stale instance credential paths (DEF-02)
- 3 consecutive restarts: brief size does not grow unboundedly, protocol
  count stays 1 per brief, no recursion of previous briefs
- default workspace path still works
- shared-workspace guard: second agent with same path is rejected
"""
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(ROOT, "cli", "hive.py")
import socket


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PORT = free_port()
EP = f"http://127.0.0.1:{PORT}"
RUN = os.path.join(ROOT, "work", "defects", f"def0102-{int(time.time())}")
os.makedirs(RUN, exist_ok=True)
checks = []


def check(name, ok, detail=""):
    checks.append({"check": name, "ok": bool(ok), "detail": str(detail)[:200]})
    print(("PASS " if ok else "FAIL ") + name + (f" | {detail}" if detail else ""))


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
        return {"ok": False, "_out": p.stdout[:300]}, p.returncode


def fixture(agent_id):
    """Read this agent's latest startup brief from runtime/startups."""
    d = os.path.join(RUN, "runtime", "startups", agent_id)
    files = sorted((os.path.join(d, f) for f in os.listdir(d)),
                   key=os.path.getmtime)
    with open(files[-1], encoding="utf-8") as f:
        return f.read(), os.path.basename(files[-1])


# setup
cli("control", "system", "init", "--listen", f"127.0.0.1:{PORT}",
    "--test-mode")
cli("control", "system", "start", "--listen", f"127.0.0.1:{PORT}",
    "--test-mode")
owner = os.path.join(RUN, "auth", "owner.json")
m = os.path.join(RUN, "m.json")
cli("control", "test", "seed", "--scenario", "def0102", "--out", m, cred=owner)
fx = json.load(open(m, encoding="utf-8"))
A = fx["agents"]["A"]

# --- register a REAL process-harness channel (local dummy process) so the
# startup-brief path is the same one opencode/zsession walk
spec_path = os.path.join(RUN, "dummy-harness.json")
open(spec_path, "w", encoding="utf-8").write(json.dumps({
    "name": "dummy-proc", "adapter": "process",
    "start_argv": [sys.executable, "-c", "import time; time.sleep(600)"],
    "send_mode": "adapter-defined", "stop_mode": "process",
    "capabilities": {"start": True, "send": True, "yolo": True,
                     "model_at_start": True, "stop": True}},
    ensure_ascii=False))
env, code = cli("control", "harness", "register", "--file", spec_path,
                "--reason", "def0102", cred=owner)
check("register dummy process harness", env.get("ok"))

explicit_ws = os.path.join(RUN, "explicit-ws")
os.makedirs(explicit_ws, exist_ok=True)
open(os.path.join(explicit_ws, "CONTEXT.md"), "w", encoding="utf-8").write(
    "# 业务上下文标记 DEF0102\n")
cfg = os.path.join(RUN, "agent-explicit.json")
open(cfg, "w", encoding="utf-8").write(json.dumps({
    "name": "explicit-ws-agent", "runtime": {"harness": "dummy-proc"},
    "workspace": {"path": explicit_ws}}, ensure_ascii=False))
env, code = cli("control", "agent", "create", "--file", cfg, "--start",
                cred=owner)
check("create+start explicit-workspace agent", env.get("ok"),
      env.get("error", {}).get("message", ""))
E = env["data"]["agent"]["agent_id"]

# DEF-01: three files exist in the EXPLICIT workspace (not the default root)
for fname in ("agent.md", "config.json", "prompt.md"):
    check(f"explicit ws has {fname}",
          os.path.isfile(os.path.join(explicit_ws, fname)))
default_ws = os.path.join(RUN, "workspaces", E)
check("default root NOT used for explicit agent",
      not os.path.isfile(os.path.join(default_ws, "agent.md")))

# DEF-02: brief composition
brief1, bfile1 = fixture(E)
check("brief has protocol header once",
      brief1.count("# Hive Agent 系统工作协议") == 1,
      f"count={brief1.count('# Hive Agent 系统工作协议')}")
check("brief has individual prompt once",
      brief1.count("个体工作说明") == 1)
check("brief has business md", "DEF0102" in brief1)
check("brief has responsibility summary", "当前责任摘要" in brief1)
check("brief not written into business workspace",
      not os.path.isfile(os.path.join(explicit_ws, "STARTUP.md")))

# --- restart x3: no unbounded growth, no recursion, no stale creds
sizes = [len(brief1.encode("utf-8"))]
creds_seen = set()
import re
for i in range(3):
    env, code = cli("control", "agent", "restart", E, "--force", "--reason",
                    f"def0102 r{i}", cred=owner)
    if not env.get("ok"):
        check(f"restart {i}", False, env.get("error"))
        break
    brief, bfile = fixture(E)
    size = len(brief.encode("utf-8"))
    sizes.append(size)
    check(f"r{i}: protocol exactly once",
          brief.count("# Hive Agent 系统工作协议") == 1)
    check(f"r{i}: no nested previous brief",
          brief.count("--- 工位文件 STARTUP.md") == 0)
    for m2 in re.findall(r"ins_[0-9a-f]+\.json", brief):
        creds_seen.add(m2)
check("brief size stable across restarts",
      max(sizes) - min(sizes) < 200, f"sizes={sizes}")
check("only current instance credential referenced",
      len(creds_seen) <= len(sizes), f"creds={sorted(creds_seen)}")
# the LAST brief must reference only the LAST instance
env, _ = cli("control", "agent", "show", E, "--runtime", cred=owner)
last_iid = (env["data"]["runtime"]["active_instance"] or {}).get(
    "instance_id")
brief_last, _ = fixture(E)
refs = re.findall(r"ins_[0-9a-f]+\.json", brief_last)
check("last brief references only last instance",
      last_iid and refs and set(refs) == {f"{last_iid}.json"},
      refs)

# --- default workspace agent still works
env, code = cli("control", "agent", "create", "--name", "default-ws",
                "--harness", "dummy-proc", "--start", cred=owner)
check("default-workspace agent create+start", env.get("ok"))
D = env["data"]["agent"]["agent_id"]
check("default ws has agent.md",
      os.path.isfile(os.path.join(RUN, "workspaces", D, "agent.md")))

# --- shared workspace guard
cfg2 = os.path.join(RUN, "agent-clash.json")
open(cfg2, "w", encoding="utf-8").write(json.dumps({
    "name": "clash-agent", "workspace": {"path": explicit_ws}},
    ensure_ascii=False))
env, code = cli("control", "agent", "create", "--file", cfg2, cred=owner)
check("shared workspace rejected", not env.get("ok")
      and env.get("error", {}).get("code") == "INVALID_ARGUMENT",
      env.get("error", {}).get("message", "")[:80])

# --- config update: derived files follow the effective revision in the
#     EXPLICIT workspace
patch = os.path.join(RUN, "patch.json")
open(patch, "w", encoding="utf-8").write(
    '{"instructions":{"text":"DEF0102 新指令标记"}}')
env, code = cli("control", "agent", "update", E, "--file", patch,
                "--reason", "def0102", cred=owner)
check("config update ok", env.get("ok"))
cli("control", "agent", "restart", E, "--force", "--reason", "after update", cred=owner)
brief_u, _ = fixture(E)
check("updated instruction in brief", "DEF0102 新指令标记" in brief_u)

# cleanup (managed): stop managed agents first, then daemon
for ag in (E, D):
    cli("control", "agent", "stop", ag, "--force", "--reason", "def0102 teardown",
        cred=owner)
cli("control", "system", "stop", "--reason", "def0102 done", cred=owner)

with open(os.path.join(RUN, "checks.json"), "w", encoding="utf-8") as f:
    json.dump(checks, f, ensure_ascii=False, indent=2)
failed = [c for c in checks if not c["ok"]]
print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
sys.exit(1 if failed else 0)
