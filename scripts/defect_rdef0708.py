"""RDEF-07/08 targeted regression: workspace ownership under path aliases
and clone explicit-conflict rejection.

RDEF-07: the same physical directory must not bind two agents regardless of
alias form — case, trailing separator, relative path, `..` segments,
junction. create/update/clone share one resolution rule; concurrent
same-directory binding fails at the DB level (one winner, no derived-file
clobber); a conflicting create/update NEVER overwrites the original
agent's files.

RDEF-08: clone with an EXPLICIT conflicting workspace is rejected
(INVALID_ARGUMENT); inherited/absent workspace still allocates a fresh
default workspace (correct default behavior preserved).
"""
import json
import os
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(ROOT, "cli", "hive.py")
RUN = os.path.join(ROOT, "work", "defects", f"rdef0708-{int(time.time())}")
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


cli("control", "system", "init", "--listen", f"127.0.0.1:{PORT}",
    "--test-mode")
start_daemon()
m = os.path.join(RUN, "m.json")
cli("control", "test", "seed", "--scenario", "rdef0708", "--out", m)
fx = json.load(open(m, encoding="utf-8"))
A = fx["agents"]["A"]


def agent_file(ws, name):
    p = os.path.join(ws, name)
    try:
        return open(p, encoding="utf-8").read()
    except OSError:
        return None


def create(name, ws, extra=None):
    f = os.path.join(RUN, f"cfg-{name}.json")
    cfg = {"name": name, "workspace": {"path": ws}}
    if extra:
        cfg.update(extra)
    open(f, "w", encoding="utf-8").write(json.dumps(cfg, ensure_ascii=False))
    return cli("control", "agent", "create", "--file", f,
               "--reason", "rdef0708")


# base directory with a mixed-case name
WS = os.path.join(RUN, "WS_Case")
os.makedirs(WS, exist_ok=True)
env = create("alias-holder", WS)
check("holder agent created with WS_Case", env.get("ok"),
      json.dumps(env.get("error", {}), ensure_ascii=False)[:120])
HOLDER = env["data"]["agent"]["agent_id"]
orig_agent_md = agent_file(WS, "agent.md")
check("holder derived files exist", orig_agent_md is not None)

# ---------- RDEF-07: alias forms must all clash
alias_cases = [
    ("case", WS[:-4] + "case"),                       #大小写
    ("trailing-sep", WS + "\\"),                      #尾部分隔符
    ("double-sep", WS + "\\\\"),                      #双分隔符
    ("relative", os.path.relpath(WS, RUN)),           #相对路径
    ("dotdot", os.path.join(WS, "sub", "..")),        #.. 段
    ("junction", None),                               #junction 别名
]
JUNCTION = os.path.join(RUN, "WS_Junction")
subprocess.run(["cmd", "/c", "mklink", "/J",
                JUNCTION.replace("/", "\\"), WS.replace("/", "\\")],
               capture_output=True)
alias_cases[-1] = ("junction", JUNCTION)
for label, alias in alias_cases:
    env = create(f"alias-{label}", alias)
    ok = (not env.get("ok")
          and env["error"]["code"] == "INVALID_ARGUMENT"
          and env["error"].get("details", {}).get("bound_to") == HOLDER)
    check(f"alias '{label}' rejected as same physical dir", ok,
          env.get("error", {}).get("code"))
check("holder's agent.md untouched after alias rejections",
      agent_file(WS, "agent.md") == orig_agent_md)

# ---------- RDEF-07: update with an alias of another agent's workspace
env = cli("control", "agent", "show", A, "--config")
B2 = create("update-target", os.path.join(RUN, "WS_Own"))
check("independent agent created (own workspace)", env.get("ok", True)
      and B2.get("ok"))
f = os.path.join(RUN, "upd.json")
open(f, "w", encoding="utf-8").write(json.dumps(
    {"workspace": {"path": WS.lower()}}))
env = cli("control", "agent", "update", B2["data"]["agent"]["agent_id"],
          "--file", f, "--reason", "rdef0708 update alias")
check("update to another agent's workspace (lowercase alias) rejected",
      not env.get("ok") and env["error"]["code"] == "INVALID_ARGUMENT",
      env.get("error", {}).get("code"))
check("original holder files still intact after failed update",
      agent_file(WS, "agent.md") == orig_agent_md)

# ---------- RDEF-07: concurrent binding — exactly one winner
CONC = os.path.join(RUN, "WS_Conc")
os.makedirs(CONC, exist_ok=True)
procs = []
for i in range(8):
    f = os.path.join(RUN, f"conc-{i}.json")
    open(f, "w", encoding="utf-8").write(json.dumps(
        {"name": f"conc-{i}", "workspace": {"path": CONC}}))
    procs.append(subprocess.Popen(
        [sys.executable, CLI, "--home", RUN, "--endpoint", EP,
         "--credential-file", OWNER, "control", "agent", "create",
         "--file", f, "--reason", "rdef0708 conc"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        encoding="utf-8", errors="replace"))
outs = [p.communicate()[0] for p in procs]
oks = 0
for out in outs:
    try:
        if json.loads(out).get("ok"):
            oks += 1
    except ValueError:
        pass
check("8x concurrent same-directory create -> exactly ONE winner",
      oks == 1, f"ok={oks}")
check("winner's derived files single-set intact",
      agent_file(CONC, "agent.md") is not None
      and agent_file(CONC, "config.json") is not None)

# ---------- RDEF-08: clone explicit conflict vs inherited default
f = os.path.join(RUN, "clone1.json")
env = cli("control", "agent", "clone", HOLDER, "--name", "clone-explicit",
          "--workspace", WS[:-4] + "case",
          "--reason", "rdef0708 explicit conflict", )
check("clone with EXPLICIT conflicting workspace rejected",
      not env.get("ok") and env["error"]["code"] == "INVALID_ARGUMENT",
      json.dumps(env.get("error", {}), ensure_ascii=False)[:120])
env = cli("control", "agent", "clone", HOLDER, "--name", "clone-inherit",
          "--reason", "rdef0708 inherited")
ok = env.get("ok")
new_aid = env.get("data", {}).get("new_agent", {}).get("agent_id", "")
import sqlite3 as _sq
db = _sq.connect(os.path.join(RUN, "data", "hive.db"))
row = db.execute("SELECT workspace_path FROM agents WHERE agent_id=?",
                 (new_aid,)).fetchone() if ok and new_aid else None
inherited_ws = row[0] if row else ""
db.close()
check("clone WITHOUT explicit workspace allocates its own default",
      ok and inherited_ws and inherited_ws.lower() != WS.lower()
      and "workspaces" in inherited_ws.replace("/", "\\").lower(),
      f"ws={inherited_ws}")
check("clone explicit-conflict left holder files untouched",
      agent_file(WS, "agent.md") == orig_agent_md)

# ---------- RDEF-07: 8.3 short-name alias (this volume generates them)
import ctypes
short_buf = ctypes.create_unicode_buffer(512)
n_ = ctypes.windll.kernel32.GetShortPathNameW(WS, short_buf, len(short_buf))
SHORT_WS = short_buf.value if n_ else None
if SHORT_WS and SHORT_WS != WS:
    env = create("alias-short", SHORT_WS)
    ok = (not env.get("ok")
          and env["error"]["code"] == "INVALID_ARGUMENT"
          and env["error"].get("details", {}).get("bound_to") == HOLDER)
    check("8.3 short-name alias rejected as same physical dir", ok,
          env.get("error", {}).get("code"))
else:
    check("8.3 short-name alias rejected as same physical dir", False,
          f"volume did not generate a short alias for {WS}")

cli("control", "system", "stop", "--reason", "rdef0708 done")
with open(os.path.join(RUN, "checks.json"), "w", encoding="utf-8") as f:
    json.dump(checks, f, ensure_ascii=False, indent=2)
failed = [c for c in checks if not c["ok"]]
print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
sys.exit(1 if failed else 0)
