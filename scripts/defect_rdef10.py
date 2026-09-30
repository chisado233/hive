"""RDEF-10: old-credential rejection codes are distinct and stable.

Matrix (each case uses the REAL client path — the CLI reads the credential
file and presents the token to the server):
  a) no credential file at all          -> AUTH_REQUIRED (缺少凭据)
  b) credential file path missing        -> AUTH_REQUIRED (凭据文件不存在,
                                            client-side, no request sent)
  c) unreadable/corrupt credential file  -> TOKEN_INVALID (格式不合法)
  d) full token of a REVOKED instance
     credential (stop revoked it)        -> TOKEN_REVOKED — this code can
                                            ONLY be answered to a token the
                                            server recognized, which proves
                                            the old credential was actually
                                            presented, not silently dropped
  e) garbage token                       -> TOKEN_INVALID (unknown token)
  f) unrevoked credential whose instance
     ended (fence branch)                -> INSTANCE_FENCED (equivalent
                                            ledger state crafted via SQL:
                                            this branch is defense-in-depth
                                            against unmanaged terminations)
Also: token material never appears in daemon logs or audit (only
credential_id / request association).
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
RUN = os.path.join(ROOT, "work", "defects", f"rdef10-{int(time.time())}")
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


def cli(*a, cred=None, timeout=60):
    argv = [sys.executable, CLI, "--home", RUN, "--endpoint", EP]
    if cred is not None:
        argv += ["--credential-file", cred]
    argv += list(a)
    p = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    return (json.loads(p.stdout) if p.stdout.strip() else {}), p.returncode


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
cli("control", "test", "seed", "--scenario", "rdef10", "--out", m, cred=OWNER)
fx = json.load(open(m, encoding="utf-8"))
A = fx["agents"]["A"]

# fresh instance for this run; its credential file is what we fence/revoke
cli("control", "agent", "start", A, cred=OWNER)
db = sqlite3.connect(DB)
_r = db.execute("SELECT instance_id FROM instances WHERE agent_id=? AND "
                "phase IN ('starting','live') ORDER BY started_at DESC",
                (A,)).fetchone()
iid = _r[0]
db.close()
CRED = os.path.join(RUN, "runtime", "credentials", f"{iid}.json")
check("instance credential file exists", os.path.isfile(CRED), CRED)
token = json.load(open(CRED, encoding="utf-8"))["token"]

# a) no credential at all
env, code = cli("inbox", "list")
check("a) no credential -> AUTH_REQUIRED",
      not env.get("ok") and env["error"]["code"] == "AUTH_REQUIRED",
      env.get("error", {}).get("code"))

# b) missing file
env, code = cli("inbox", "list", cred=os.path.join(RUN, "nope.json"))
check("b) missing credential file -> AUTH_REQUIRED (client-side)",
      not env.get("ok") and env["error"]["code"] == "AUTH_REQUIRED"
      and "不存在" in env["error"]["message"],
      env.get("error", {}).get("message", "")[:60])

# c) corrupt file
badf = os.path.join(RUN, "bad.json")
open(badf, "w", encoding="utf-8").write("{not json")
env, code = cli("inbox", "list", cred=badf)
check("c) unreadable credential file -> TOKEN_INVALID",
      not env.get("ok") and env["error"]["code"] == "TOKEN_INVALID",
      env.get("error", {}).get("code"))

# e) garbage token (before revocation: proves unknown-token branch)
garb = os.path.join(RUN, "garb.json")
open(garb, "w", encoding="utf-8").write(json.dumps(
    {"subject": A, "token": "totally-made-up-token"}))
env, code = cli("inbox", "list", cred=garb)
check("e) unknown token -> TOKEN_INVALID",
      not env.get("ok") and env["error"]["code"] == "TOKEN_INVALID",
      env.get("error", {}).get("code"))

# d) stop revokes the instance credential; the SAME file then presented
cli("control", "agent", "stop", A, "--force", "--reason", "rdef10",
    cred=OWNER)
env, code = cli("inbox", "list", cred=CRED)
err = env.get("error", {})
check("d) revoked instance token (really sent) -> TOKEN_REVOKED",
      not env.get("ok") and err["code"] == "TOKEN_REVOKED",
      f"code={err.get('code')} msg={err.get('message', '')[:40]}")
check("d) error carries credential/request association, not token material",
      err.get("code") == "TOKEN_REVOKED"
      and token[:12] not in json.dumps(env), "token fragment leaked!")

# f) unrevoked credential of an ended instance -> INSTANCE_FENCED
db = sqlite3.connect(DB, isolation_level=None)
db.execute("UPDATE credentials SET revoked_at=NULL WHERE instance_id=?",
           (iid,))
db.close()
env, code = cli("inbox", "list", cred=CRED)
check("f) unrevoked but instance ended -> INSTANCE_FENCED",
      not env.get("ok") and env["error"]["code"] == "INSTANCE_FENCED",
      env.get("error", {}).get("code"))

# stability: repeat d-state twice (same code both times)
db = sqlite3.connect(DB, isolation_level=None)
db.execute("UPDATE credentials SET revoked_at='2026-09-30T00:00:00Z' "
           "WHERE instance_id=?", (iid,))
db.close()
env2, _ = cli("inbox", "list", cred=CRED)
env3, _ = cli("inbox", "list", cred=CRED)
check("stability: revoked token rejected identically on repeat calls",
      env2["error"]["code"] == env3["error"]["code"] == "TOKEN_REVOKED")

# no token material in daemon logs or audit
leaks = []
for logf in ("logs/hive.log", "logs/daemon.out.log", "logs/daemon.err.log"):
    p = os.path.join(RUN, logf)
    if os.path.exists(p):
        body = open(p, encoding="utf-8", errors="replace").read()
        if token[:16] in body or token[-16:] in body:
            leaks.append(logf)
db = sqlite3.connect(DB)
audit_hit = db.execute(
    "SELECT COUNT(*) FROM audit_log WHERE details LIKE ? OR reason LIKE ?",
    (f"%{token[:16]}%", f"%{token[:16]}%")).fetchone()[0]
db.close()
check("token material absent from daemon logs and audit",
      not leaks and audit_hit == 0, f"leaks={leaks} audit={audit_hit}")

cli("control", "system", "stop", "--reason", "rdef10 done", cred=OWNER)
with open(os.path.join(RUN, "checks.json"), "w", encoding="utf-8") as f:
    json.dump(checks, f, ensure_ascii=False, indent=2)
failed = [c for c in checks if not c["ok"]]
print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
sys.exit(1 if failed else 0)
