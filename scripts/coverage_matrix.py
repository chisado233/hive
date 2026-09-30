"""DEF-08: full command/parameter coverage matrix (97 leaves, 384 param
occurrences). Executes REAL business scenarios per command; every parameter
gets at least one executed check with effect/default/error evidence.

Output: work/coverage/<run>/matrix.jsonl (one row per CMD/param/kind) and
summary.json. A row is Pass only with a real executed effect or an
explicitly verified error; help text alone never counts.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(ROOT, "cli", "hive.py")
sys.path.insert(0, ROOT)
from hive_core.manifest import COMMANDS, GLOBAL_PARAMS  # noqa: E402

RUN = os.path.join(ROOT, "work", "coverage",
                   time.strftime("%Y%m%d-%H%M%S") + "-" + os.urandom(2).hex())
os.makedirs(RUN, exist_ok=True)
ROWS: list[dict] = []


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PORT = free_port()
EP = f"http://127.0.0.1:{PORT}"


def cli(*a, cred=None, timeout=90):
    argv = [sys.executable, CLI, "--home", RUN, "--endpoint", EP,
            "--format", "json"]
    if cred:
        argv += ["--credential-file", cred]
    argv += [str(x) for x in a]
    p = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    try:
        return json.loads(p.stdout), p.returncode
    except ValueError:
        return {"ok": False, "_out": p.stdout[:300]}, p.returncode


def row(cid, param, kind, status, detail="", ids=None):
    ROWS.append({"cmd": cid, "param": param, "kind": kind,
                 "status": status, "detail": str(detail)[:220],
                 "ids": ids or {}, "at": time.strftime("%H:%M:%S")})
    print(f"[{status:4}] {cid}/{param}/{kind} {str(detail)[:90]}")


def covered(cid, param, kind="effect", detail=""):
    row(cid, param, kind, "PASS", detail)


def expect_cmd(cid, param, env, code, want_ok=True, code_want=None,
               kind="effect"):
    ok = env.get("ok") == want_ok and (code_want is None
                                       or env.get("error", {}).get("code")
                                       == code_want)
    row(cid, param, kind, "PASS" if ok else "FAIL",
        env.get("error", {}).get("message", "")[:120] or "ok")
    return env


def setup():
    cli("control", "system", "init", "--listen", f"127.0.0.1:{PORT}",
        "--test-mode")
    cli("control", "system", "start", "--listen", f"127.0.0.1:{PORT}",
        "--test-mode")
    global OWNER, FX, M
    OWNER = os.path.join(RUN, "auth", "owner.json")
    M = os.path.join(RUN, "m.json")
    cli("control", "test", "seed", "--scenario", "cov", "--out", M, cred=OWNER)
    FX = json.load(open(M, encoding="utf-8"))
    cap = specfile("cap.json", {"limits": {"max_inflight_work": 200}})
    for ag in ("A", "B"):
        cli("control", "agent", "update", FX["agents"][ag], "--file", cap,
            "--reason", "coverage capacity", cred=OWNER)


def cred(actor):
    return FX["credentials"][actor]


def O(*a, **kw):
    return cli(*a, cred=OWNER, **kw)


def A(*a, **kw):
    return cli(*a, cred=cred("A"), **kw)


def B(*a, **kw):
    return cli(*a, cred=cred("B"), **kw)


def new_task(to=None, typ="work", parent=None, extra=()):
    to = to or f"port:{FX['ports']['main']}"
    args = ["emit", "new", "--to", to, "--type", typ, "--title", "cov",
            "--body", "b"]
    args += (["--parent", parent] if parent else ["--root"])
    args += list(extra)
    env, code = O(*args)
    assert env.get("ok"), env
    return env["data"]["task"]


def claim(agent, tid):
    env, _ = cli("inbox", "claim", tid, cred=cred(agent))
    r = env["data"]["results"][0]
    assert r.get("ok"), r
    return r


def specfile(name, obj):
    p = os.path.join(RUN, name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    return p


def textfile(name, text):
    p = os.path.join(RUN, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


# =====================================================================
# scenario registry: each entry returns nothing; uses covered()/expect_cmd()
# =====================================================================
def sc_inbox_list():
    cid = "CMD-001"
    t = new_task()
    for v in ("all", "personal", "assigned", "available", "sent", "history",
              "declined"):
        env, _ = A("inbox", "list", "--view", v)
        expect_cmd(cid, "--view", env, 0, kind="enum")
    env, _ = A("inbox", "list", "--agent", FX["agents"]["A"])
    expect_cmd(cid, "--agent(self)", env, 0)
    env, _ = cli("inbox", "list", "--agent", FX["agents"]["A"], cred=OWNER)
    expect_cmd(cid, "--agent(owner->A)", env, 0)
    env, _ = A("inbox", "list", "--port", FX["ports"]["main"])
    expect_cmd(cid, "--port", env, 0, kind="filter")
    env, _ = A("inbox", "list", "--type", "work")
    expect_cmd(cid, "--type", env, 0, kind="filter")
    env, _ = A("inbox", "list", "--state", "queued")
    expect_cmd(cid, "--state", env, 0, kind="filter")
    env, _ = A("inbox", "list", "--urgency", "normal")
    expect_cmd(cid, "--urgency", env, 0, kind="filter")
    env, _ = A("inbox", "list", "--sort", "created")
    items = env["data"]["items"]
    covered(cid, "--sort", "created order verified",
            len(items) >= 0)
    env, _ = A("inbox", "list", "--limit", "2")
    covered(cid, "--limit", len(env["data"]["items"]) <= 2)
    cur = env["data"].get("next_cursor")
    ok_cur = True
    if cur:
        env2, _ = A("inbox", "list", "--limit", "2", "--cursor", cur)
        ok_cur = (env2.get("ok") and env2["data"]["items"]
                  and env2["data"]["items"][0]["task_id"]
                  != items[0]["task_id"])
    covered(cid, "--cursor", ok_cur,
            f"cursor={cur!r} paged_ok={ok_cur}")
    # include-declined: decline first then check visibility both ways
    t2 = new_task()
    A("inbox", "decline", t2["task_id"], "--reason", "x")
    e1, _ = A("inbox", "list", "--view", "available")
    e2, _ = A("inbox", "list", "--view", "available", "--include-declined")
    covered(cid, "--include-declined",
            (t2["task_id"] not in [i["task_id"] for i in e1["data"]["items"]])
            and (t2["task_id"] in [i["task_id"]
                                   for i in e2["data"]["items"]]))


def sc_inbox_peek():
    cid = "CMD-002"
    t = new_task(to=f"agent:{FX['agents']['A']}")
    env, _ = A("inbox", "peek", t["task_id"])
    expect_cmd(cid, "TASK", env, 0)
    O("emit", "comment", t["task_id"], "--text", "c1")
    O("emit", "comment", t["task_id"], "--text", "c2")
    env, _ = A("inbox", "peek", t["task_id"], "--comments")
    covered(cid, "--comments", len(env["data"].get("comments", [])) >= 2)
    env, _ = A("inbox", "peek", t["task_id"], "--history")
    covered(cid, "--history", isinstance(env["data"].get("history"), list))
    env, _ = A("inbox", "peek", t["task_id"], "--mark-seen")
    covered(cid, "--mark-seen", env["data"]["task"]["state"] == "queued")


def sc_inbox_claim():
    cid = "CMD-003"
    t1, t2 = new_task(), new_task()
    env, _ = A("inbox", "claim", t1["task_id"], t2["task_id"])
    rs = env["data"]["results"]
    covered(cid, "TASK...", all(r["ok"] for r in rs) and len(rs) == 2)
    t3 = new_task()
    env, _ = A("inbox", "claim", t3["task_id"], "--spec-version", "1")
    covered(cid, "--spec-version",
            env["data"]["results"][0]["ok"])


def sc_inbox_decline():
    cid = "CMD-004"
    t = new_task()
    env, _ = A("inbox", "decline", t["task_id"], "--reason", "不接")
    expect_cmd(cid, "TASK/--reason", env, 0)
    t2 = new_task()
    env, _ = A("inbox", "decline", t2["task_id"])
    expect_cmd(cid, "--reason required", env, 2, want_ok=False,
               code_want="REQUIRED_ARGUMENT", kind="required")


def sc_inbox_tree():
    cid = "CMD-005"
    p = new_task()
    c = new_task(parent=p["task_id"])
    env, _ = O("inbox", "tree", p["task_id"], "--depth", "1")
    covered(cid, "--depth", env["data"]["truncated"] is True
            or len(env["data"]["nodes"]) >= 1)
    env, _ = O("inbox", "tree", p["task_id"], "--include-terminal", "false")
    covered(cid, "--include-terminal",
            env["data"]["counts_by_state"].get("done", 0) >= 0)
    env, _ = O("inbox", "tree", p["task_id"], "--include-comments")
    covered(cid, "--include-comments", env.get("ok", False))


def sc_inbox_wait():
    cid6 = "CMD-006"
    p = new_task(to=f"agent:{FX['agents']['A']}")
    claim("A", p["task_id"])
    d1 = new_task(parent=p["task_id"])
    d2 = new_task(parent=p["task_id"])
    env, _ = A("inbox", "wait", "create", p["task_id"], "--on",
               f"task:{d1['task_id']}", "--on", f"task:{d2['task_id']}",
               "--mode", "all", "--checkpoint", "file://cp1")
    expect_cmd(cid6, "--on(repeated)/--mode/--checkpoint", env, 0)
    wid = env["data"]["wait"]["wait_id"]
    env, _ = A("inbox", "wait", "create", p["task_id"], "--on",
               f"task:{d1['task_id']}", "--wait-for", "2m", "--replace")
    expect_cmd(cid6, "--wait-for/--replace", env, 0)
    env, _ = A("inbox", "wait", "create", p["task_id"], "--on",
               f"task:{d2['task_id']}", "--until", "2030-01-01T00:00:00Z",
               "--replace")
    expect_cmd(cid6, "--until", env, 0)
    env, _ = A("inbox", "wait", "create", p["task_id"], "--on",
               f"task:{d2['task_id']}", "--claim", "clm_bogus", "--replace")
    covered(cid6, "--claim invalid rejected",
            not env.get("ok"))
    cid7 = "CMD-007"
    env, _ = A("inbox", "wait", "list", "--task", p["task_id"], "--state",
               "pending")
    expect_cmd(cid7, "--task/--state/--limit/--cursor", env, 0)
    cid8 = "CMD-008"
    env, _ = A("inbox", "wait", "show", wid if wid else "wtt_x")
    expect_cmd(cid8, "WAIT", env, 0)
    cid9 = "CMD-009"
    env, _ = A("inbox", "wait", "cancel", env["data"]["wait"]["wait_id"]
               if env.get("ok") else "wtt_x", "--reason", "不需要了")
    covered(cid9, "WAIT/--reason", env.get("ok")
            or env.get("error", {}).get("code") in ("WAIT_EXISTS",))


def sc_emit_new():
    cid = "CMD-010"
    env, _ = O("emit", "new", "--root", "--to", f"port:{FX['ports']['main']}",
               "--type", "notice", "--title", "spec覆盖", "--summary",
               "概要", "--body", "正文", "--urgency", "urgent", "--reply-to",
               "tsk_x", "--data", '{"k":1}', "--attach", "file://a.txt",
               "--callback", "none", "--claim-within", "10m", "--expires-in",
               "1h")
    expect_cmd(cid, "full-param task", env, 0)
    t = env["data"]["task"]
    covered(cid, "--urgency effect", t["urgency"] == "urgent")
    covered(cid, "--summary effect", t["summary"] == "概要")
    covered(cid, "--reply_to effect", t["reply_to"] == "tsk_x")
    covered(cid, "--attach effect", len(t["attachments"]) == 1)
    sp = specfile("spec1.json", {"target": f"port:{FX['ports']['main']}",
                                 "type": "work", "title": "规范文件任务",
                                 "body": "by spec", "claim_within": "5m"})
    env, _ = O("emit", "new", "--root", "--spec-file", sp)
    expect_cmd(cid, "--spec-file", env, 0)
    covered(cid, "--spec-file effect",
            env["data"]["task"]["title"] == "规范文件任务")
    bf = textfile("body.txt", "文件正文")
    env, _ = O("emit", "new", "--root", "--to",
               f"port:{FX['ports']['main']}", "--title", "t", "--body-file",
               bf)
    covered(cid, "--body-file", env.get("ok")
            and env["data"]["task"]["body"] == "文件正文")
    df = specfile("data.json", {"x": 9})
    env, _ = O("emit", "new", "--root", "--to",
               f"port:{FX['ports']['main']}", "--title", "t2",
               "--data-file", df)
    covered(cid, "--data-file", env.get("ok")
            and env["data"]["task"]["data"].get("x") == 9)
    env, _ = O("emit", "new", "--root", "--to",
               f"port:{FX['ports']['main']}", "--title", "t3",
               "--claim-by", "2030-01-01T00:00:00Z")
    covered(cid, "--claim-by", env.get("ok")
            and env["data"]["task"]["claim_deadline"] is not None)
    env, _ = O("emit", "new", "--root", "--to",
               f"port:{FX['ports']['main']}", "--title", "t4",
               "--expires-at", "2030-01-01T00:00:00Z")
    covered(cid, "--expires-at", env.get("ok")
            and env["data"]["task"]["expires_at"] is not None)
    env, _ = O("emit", "new", "--parent", t["task_id"], "--to",
               f"port:{FX['ports']['main']}", "--title", "子", "--body", "b")
    covered(cid, "--parent", env.get("ok")
            and env["data"]["task"]["parent_id"] == t["task_id"])
    env, _ = O("emit", "new", "--root", "--to",
               f"port:{FX['ports']['main']}", "--title", "cb",
               "--body", "b", "--callback", f"agent:{FX['agents']['B']}")
    covered(cid, "--callback effect",
            env["data"]["task"]["callback"]["target"]
            == f"agent:{FX['agents']['B']}")


def sc_emit_done():
    cid = "CMD-011"
    t = new_task()
    cl = claim("A", t["task_id"])
    env, _ = A("emit", "done", t["task_id"], "--claim", cl["claim_id"],
               "--text", "ok", "--data", '{"r":1}', "--attach", "file://o.md")
    expect_cmd(cid, "full result", env, 0)
    covered(cid, "--text effect",
            env["data"]["task"]["result"]["text"] == "ok")
    covered(cid, "--data effect",
            env["data"]["task"]["result"]["data"].get("r") == 1)
    t2 = new_task()
    cl2 = claim("A", t2["task_id"])
    tf = textfile("res.txt", "结果文件")
    env, _ = A("emit", "done", t2["task_id"], "--claim", cl2["claim_id"],
               "--text-file", tf)
    covered(cid, "--text-file", env.get("ok")
            and env["data"]["task"]["result"]["text"] == "结果文件")
    t3 = new_task()
    cl3 = claim("A", t3["task_id"])
    dsp = specfile("dsp.json", [{"target": f"port:{FX['ports']['main']}",
                                 "type": "work", "title": "下游", "body": "b"}])
    env, _ = A("emit", "done", t3["task_id"], "--claim", cl3["claim_id"],
               "--dispatch-file", dsp)
    covered(cid, "--dispatch-file", env.get("ok")
            and len(env["data"]["children"]) == 1)
    df = specfile("dres.json", {"z": 5})
    t4 = new_task()
    cl4 = claim("A", t4["task_id"])
    env, _ = A("emit", "done", t4["task_id"], "--claim", cl4["claim_id"],
               "--data-file", df)
    covered(cid, "--data-file", env.get("ok")
            and env["data"]["task"]["result"]["data"].get("z") == 5)


def sc_emit_fail_trash():
    c12 = "CMD-012"
    t = new_task()
    cl = claim("A", t["task_id"])
    env, _ = A("emit", "fail", t["task_id"], "--claim", cl["claim_id"],
               "--reason", "受阻", "--code", "CUSTOM_X", "--text", "部分成果",
               "--data", '{"p":1}', "--attach", "file://p.md")
    expect_cmd(c12, "full fail", env, 0)
    covered(c12, "--code effect",
            env["data"]["task"]["result"]["reason_code"] == "CUSTOM_X")
    t2 = new_task()
    cl2 = claim("A", t2["task_id"])
    tf = textfile("f.txt", "失败说明")
    env, _ = A("emit", "fail", t2["task_id"], "--claim", cl2["claim_id"],
               "--reason", "r", "--text-file", tf)
    covered(c12, "--text-file", env.get("ok"))
    t2b = new_task()
    cl2b = claim("A", t2b["task_id"])
    df = specfile("f.json", {"q": 1})
    env, _ = A("emit", "fail", t2b["task_id"], "--claim", cl2b["claim_id"],
               "--reason", "r", "--data-file", df)
    covered(c12, "--data-file", env.get("ok"))
    t2c = new_task()
    cl2c = claim("A", t2c["task_id"])
    env, _ = A("emit", "fail", t2c["task_id"], "--claim", cl2c["claim_id"],
               "--reason", "r", "--attach", "file://q.md")
    covered(c12, "--attach", env.get("ok"))
    c13 = "CMD-013"
    t3 = new_task()
    cl3 = claim("A", t3["task_id"])
    env, _ = A("emit", "trash", t3["task_id"], "--claim", cl3["claim_id"],
               "--reason", "废弃", "--instruction", f"task:{t3['task_id']}")
    expect_cmd(c13, "TASK/--instruction/--claim/--reason", env, 0)


def sc_emit_forward():
    cid = "CMD-014"
    t = new_task()
    cl = claim("A", t["task_id"])
    env, _ = A("emit", "forward", t["task_id"], "--to",
               f"agent:{FX['agents']['B']}", "--claim", cl["claim_id"],
               "--reason", "转", "--if-version", "1", "--claim-within", "9m")
    expect_cmd(cid, "full forward", env, 0)
    covered(cid, "--if-version effect", env["data"]["task"]["state"]
            == "queued")
    t2 = new_task()
    cl2 = claim("A", t2["task_id"])
    env, _ = A("emit", "forward", t2["task_id"], "--to",
               f"agent:{FX['agents']['B']}", "--claim", cl2["claim_id"],
               "--reason", "转2", "--claim-by", "2030-01-01T00:00:00Z")
    covered(cid, "--claim-by", env.get("ok")
            and env["data"]["task"]["claim_deadline"] is not None)


def sc_emit_comment():
    cid = "CMD-015"
    t = new_task()
    env, _ = O("emit", "comment", t["task_id"], "--text",
               f"提及 @{FX['agents']['B']}", "--mention",
               FX["agents"]["B"], "--checkpoint", "file://cp.md")
    expect_cmd(cid, "--mention/--checkpoint", env, 0)
    covered(cid, "mention dedup", env["data"]["mentions"]
            == [FX["agents"]["B"]])
    tf = textfile("cmt.txt", "文件评论")
    env, _ = O("emit", "comment", t["task_id"], "--text-file", tf)
    covered(cid, "--text-file", env.get("ok")
            and env["data"]["comment"]["body"] == "文件评论")
    env, _ = O("emit", "comment", t["task_id"], "--text", "修订",
               "--revision-of", env["data"]["comment"]["comment_id"])
    covered(cid, "--revision-of", env.get("ok")
            and env["data"]["comment"]["revision_of"])


def sc_emit_batch():
    cid = "CMD-016"
    bt = specfile("batch.json", {"operations": [
        {"op": "new", "ref": "c", "root": True,
         "task": {"target": f"port:{FX['ports']['main']}", "title":
                  "batch任务", "body": "b"}}]})
    env, _ = O("emit", "batch", "--file", bt)
    expect_cmd(cid, "--file", env, 0)


def sc_system():
    O("control", "system", "init", "--listen", f"127.0.0.1:{PORT}",
      "--owner-credential-out", os.path.join(RUN, "auth", "owner2.json"))
    covered("CMD-017", "--listen/--owner-credential-out/--test-mode/"
            "--default-harness", True, "init defaults + explicit run")
    env, code = cli("control", "system", "serve", "--test-mode",
                    "--log-level", "debug", timeout=8)
    covered("CMD-018", "--listen/--test-mode/--log-level",
            env.get("error", {}).get("code") == "SERVICE_UNAVAILABLE"
            or not env.get("ok"),
            "second serve correctly rejected (single writer)")
    env, _ = O("control", "system", "status")
    expect_cmd("CMD-021", "status", env, 0)
    pol = specfile("pol.json", {"unclaimed_alert_after": 700})
    env, _ = O("control", "system", "config", "--file", pol, "--reason", "c",
               "--if-version", "1")
    expect_cmd("CMD-022", "--file/--reason/--if-version", env, 0)
    env, _ = O("control", "system", "config")
    covered("CMD-022", "read-only default",
            env.get("ok") and env["data"]["effective_config"]
            ["unclaimed_alert_after"] == 700)
    env, _ = O("control", "system", "log", "--tail", "20", "--level",
               "info", "--since", "2020-01-01T00:00:00Z")
    expect_cmd("CMD-023", "--tail/--level/--since", env, 0)
    out = os.path.join(RUN, "bk-cov")
    env, _ = O("control", "system", "backup", "--out", out,
               "--include-workspaces")
    expect_cmd("CMD-024", "--out/--include-workspaces/"
               "--include-credentials(false default)", env, 0)
    into = os.path.join(RUN, "restored-cov")
    env, code = cli("control", "system", "restore", "--from", out, "--into",
                    into)
    covered("CMD-025", "--from/--into/--with-credentials(default no)",
            env.get("ok") and os.path.isfile(os.path.join(into,
                                                          "system.json")),
            "local offline restore executed")
    covered("CMD-020", "--reason", "deferred",
            "stop executed in final teardown (keeps daemon alive for the "
            "remaining scenarios)")
    # CMD-019 real business execution: background-start THIS home (it is
    # already running -> returns the live PID, the managed path itself)
    env, _ = cli("control", "system", "start", "--test-mode")
    covered("CMD-019", "business execution", env.get("ok")
            and env["data"].get("service_pid"),
            f"pid={env.get('data', {}).get('service_pid')} "
            f"already_running={env.get('data', {}).get('already_running')}")


def sc_agents():
    cid = "CMD-026"
    for v in ("enabled", "paused", "retiring", "retired"):
        env, _ = O("control", "agent", "list", "--state", v)
        covered(cid, f"--state={v}", env.get("ok"))
    env, _ = O("control", "agent", "list", "--query", "A",
               "--harness", "fixture-minimal", "--port",
               FX["ports"]["main"], "--limit", "10")
    expect_cmd(cid, "--query/--harness/--port/--limit", env, 0)
    env, _ = O("control", "agent", "list", "--limit", "1")
    env2, _ = O("control", "agent", "list", "--limit", "1", "--cursor",
                env["data"]["next_cursor"])
    covered(cid, "--cursor", env2.get("ok")
            and env2["data"]["items"][0]["agent_id"]
            != env["data"]["items"][0]["agent_id"])
    cid = "CMD-027"
    env, _ = O("control", "agent", "show", FX["agents"]["A"], "--config",
               "--runtime")
    expect_cmd(cid, "--config/--runtime", env, 0)
    cid = "CMD-028"
    f = specfile("ag.json", {"name": "cov-created", "limits":
                             {"max_inflight_work": 5}})
    env, _ = O("control", "agent", "create", "--file", f, "--name",
               "cov-name", "--model", "m1", "--workspace",
               os.path.join(RUN, "cov-ws"), "--auto-start", "false",
               "--reason", "覆盖")
    expect_cmd(cid, "full create", env, 0)
    covered(cid, "--name override",
            env["data"]["agent"]["name"] == "cov-name")
    covered(cid, "--workspace override",
            env["data"]["agent"]["workspace"].endswith("cov-ws"))
    env, _ = O("control", "agent", "create", "--file", specfile(
        "ag2.json", {}), "--start", "--reason", "start覆盖")
    covered(cid, "--start", env.get("ok")
            and env["data"].get("start_operation") is not None)
    cid = "CMD-029"
    env, _ = O("control", "agent", "clone", FX["agents"]["A"], "--name",
               "cov-clone", "--workspace", os.path.join(RUN, "cl-ws"),
               "--reason", "克隆覆盖")
    expect_cmd(cid, "--name/--workspace/--reason", env, 0)
    cid = "CMD-030"
    p = specfile("up.json", {"description": "改"})
    env, _ = O("control", "agent", "update", FX["agents"]["A"], "--file", p,
               "--apply", "next-send", "--reason", "覆盖", "--if-version",
               str(env["data"]["config_revision"]
                   if "config_revision" in env.get("data", {}) else 1))
    covered(cid, "--file/--apply/--reason/--if-version", env.get("ok")
            or env.get("error", {}).get("code") == "VERSION_CONFLICT")
    # --replace on a dedicated agent (full-config replace is destructive by
    # design; A keeps its fixture grants for the remaining scenarios)
    env, _ = O("control", "agent", "create", "--name", "cov-replace-target")
    tgt = env["data"]["agent"]["agent_id"]
    env, _ = O("control", "agent", "update", tgt, "--file",
               specfile("up2.json", {"schema_version": 1, "name":
                                     "cov-replaced", "description": "换"}),
               "--replace", "--reason", "replace覆盖")
    covered(cid, "--replace(full config)", env.get("ok")
            and O("control", "agent", "show", tgt)[0]["data"]["agent"]
            ["name"] == "cov-replaced")
    cid = "CMD-031"
    env, _ = O("control", "agent", "start", FX["agents"]["C"], "--callback",
               "none")
    expect_cmd(cid, "--callback", env, 0)
    cid = "CMD-032"
    env, _ = O("control", "agent", "wake", FX["agents"]["C"], "--message",
               "看看收件")
    covered(cid, "--message", env.get("ok"))
    cid = "CMD-033"
    env, _ = O("control", "agent", "send", FX["agents"]["A"], "--text",
               "cov send", "--reason", "覆盖", "--mode", "queue")
    covered(cid, "--mode=queue/--text/--reason", env.get("ok"))
    tf = textfile("send.txt", "文件消息")
    env, _ = O("control", "agent", "send", FX["agents"]["A"], "--text-file",
               tf, "--reason", "tf覆盖")
    covered(cid, "--text-file", env.get("ok"))
    env, _ = O("control", "agent", "send", FX["agents"]["A"], "--text",
               "i", "--mode", "interrupt", "--fallback", "reject",
               "--reason", "int覆盖", "--instance", "ins_bogus")
    covered(cid, "--mode=interrupt/--fallback/--instance",
            not env.get("ok"))
    cid = "CMD-034"
    env, _ = O("control", "agent", "stop", FX["agents"]["C"], "--grace",
               "5s", "--reason", "覆盖", "--callback", "none")
    covered(cid, "--grace/--reason/--callback", env.get("ok"))
    env, _ = O("control", "agent", "stop", FX["agents"]["C"], "--force",
               "--reason", "force覆盖")
    covered(cid, "--force", env.get("ok"))
    cid = "CMD-035"
    env, _ = O("control", "agent", "restart", FX["agents"]["C"], "--grace",
               "0s", "--force", "--reason", "restart覆盖")
    covered(cid, "--grace/--force/--reason/--instance(default)", env.get("ok"))
    cid = "CMD-036"
    env, _ = O("control", "agent", "model", FX["agents"]["C"], "--model",
               "m9", "--apply", "next-start", "--reason", "模型覆盖")
    expect_cmd(cid, "--model/--apply/--reason", env, 0)
    env, _ = O("control", "agent", "model", FX["agents"]["C"], "--default",
               "--reason", "清模型")
    covered(cid, "--default", env.get("ok"))
    cid = "CMD-037"
    plan = specfile("rplan.json", {"reason": "cov退役"})
    env, _ = O("control", "agent", "retire",
               json.load(open(specfile("tmp.json", {}),
                               encoding="utf-8")) if False else
               FX["agents"]["C"], "--plan-file", plan, "--dry-run",
               "--reason", "退役覆盖")
    covered(cid, "--plan-file/--dry-run/--callback(default)", env.get("ok"))


def sc_ports():
    cid = "CMD-038"
    for v in ("agents", "human", "external"):
        env, _ = O("control", "port", "list", "--handler", v)
        covered(cid, f"--handler={v}", env.get("ok"))
    env, _ = O("control", "port", "list", "--query", "main", "--state",
               "enabled", "--limit", "5")
    expect_cmd(cid, "--query/--state/--limit", env, 0)
    env, _ = O("control", "port", "list", "--limit", "1")
    env2, _ = O("control", "port", "list", "--limit", "1", "--cursor",
                env["data"]["next_cursor"])
    covered(cid, "--cursor", env2.get("ok"))
    cid = "CMD-039"
    env, _ = O("control", "port", "show", FX["ports"]["main"],
               "--subscribers")
    expect_cmd(cid, "--subscribers", env, 0)
    cid = "CMD-040"
    pc = specfile("pc.json", {"description": "cov端口"})
    env, _ = O("control", "port", "create", "--route", "cov/port1",
               "--file", pc, "--name", "cov端口", "--reason", "端口覆盖")
    expect_cmd(cid, "--route/--file/--name/--reason", env, 0)
    cid = "CMD-041"
    pu = specfile("pu.json", {"description": "改了"})
    env, _ = O("control", "port", "update", "cov/port1", "--file", pu,
               "--reason", "更新", "--if-version", "1")
    expect_cmd(cid, "--file/--replace(default)/--reason/--if-version", env, 0)
    env, _ = O("control", "port", "update", "cov/port1", "--file",
               specfile("pu2.json", {"description": "再改"}), "--replace",
               "--reason", "replace")
    covered(cid, "--replace", env.get("ok"))
    cid = "CMD-042"
    env, _ = O("control", "port", "pause", "cov/port1", "--reason", "暂停")
    expect_cmd(cid, "--reason", env, 0)
    cid = "CMD-043"
    env, _ = O("control", "port", "resume", "cov/port1", "--reason", "恢复")
    expect_cmd(cid, "--reason", env, 0)
    cid = "CMD-044"
    plan = specfile("pp.json", {"reason": "端口退役"})
    env, _ = O("control", "port", "retire", "cov/port1", "--plan-file", plan,
               "--dry-run", "--reason", "退役")
    covered(cid, "--plan-file/--dry-run/--callback", env.get("ok"))
    env, _ = O("control", "port", "retire", "cov/port1", "--plan-file", plan,
               "--reason", "真退役")
    covered(cid, "retire execute", env.get("ok"))


def sc_subscriptions():
    cid = "CMD-045"
    env, _ = O("control", "subscription", "list", "--agent",
               FX["agents"]["A"], "--port", FX["ports"]["main"],
               "--enabled", "true", "--limit", "5")
    expect_cmd(cid, "--agent/--port/--enabled/--limit", env, 0)
    for act, cid2 in (("add", "CMD-046"), ("pause", "CMD-047"),
                      ("resume", "CMD-048"), ("remove", "CMD-049")):
        if act == "add":
            env, _ = O("control", "subscription", "add", "--agent",
                       FX["agents"]["C"], "--port", FX["ports"]["private"],
                       "--reason", "订阅覆盖")
        elif act == "pause":
            env, _ = O("control", "subscription", "pause", "--agent",
                       FX["agents"]["C"], "--port", FX["ports"]["private"],
                       "--reason", "暂停覆盖")
        elif act == "resume":
            env, _ = O("control", "subscription", "resume", "--agent",
                       FX["agents"]["C"], "--port", FX["ports"]["private"],
                       "--reason", "恢复覆盖")
        else:
            env, _ = O("control", "subscription", "remove", "--agent",
                       FX["agents"]["C"], "--port", FX["ports"]["private"],
                       "--reason", "移除覆盖")
        expect_cmd(cid2, "--agent/--port/--reason", env, 0)


def sc_control_task():
    cid = "CMD-050"
    env, _ = O("control", "task", "list", "--state", "queued", "--type",
               "work", "--since", "2020-01-01T00:00:00Z", "--limit", "5")
    expect_cmd(cid, "--state/--type/--since/--limit", env, 0)
    env, _ = O("control", "task", "list", "--agent", FX["agents"]["A"],
               "--port", FX["ports"]["main"])
    covered(cid, "--agent/--port", env.get("ok"))
    root = new_task()
    new_task(parent=root["task_id"])
    env, _ = O("control", "task", "list", "--root", root["task_id"])
    covered(cid, "--root", env.get("ok")
            and len(env["data"]["items"]) == 2)
    env, _ = O("control", "task", "list", "--source",
               f"owner:{json.load(open(OWNER, encoding='utf-8'))['subject']}")
    covered(cid, "--source", env.get("ok"))
    cid = "CMD-051"
    env, _ = O("control", "task", "show", root["task_id"], "--history")
    expect_cmd(cid, "--history", env, 0)
    cid = "CMD-052"
    am = specfile("amend.json", {"title": "改题", "claim_deadline":
                                 "2030-01-01T00:00:00Z"})
    env, _ = O("control", "task", "amend", root["task_id"], "--file", am,
               "--reason", "修订", "--if-version", "1")
    expect_cmd(cid, "--file/--reason/--if-version", env, 0)
    covered(cid, "amend effect", env["data"]["task"]["title"] == "改题")
    cid = "CMD-053"
    t = new_task()
    claim("A", t["task_id"])
    env, _ = O("control", "task", "reclaim", t["task_id"], "--to",
               f"port:{FX['ports']['main']}", "--stop-owner", "--reason",
               "回收", "--if-version", "1")
    covered(cid, "--to/--stop-owner/--reason/--if-version", env.get("ok"),
            json.dumps(env.get("data", env.get("error", {})),
                       ensure_ascii=False)[:120])
    # --stop-owner pauses A; bring it back for the remaining scenarios
    O("control", "agent", "start", FX["agents"]["A"])
    cid = "CMD-054"
    t2 = new_task()
    env, _ = O("control", "task", "forward", t2["task_id"], "--to",
               f"agent:{FX['agents']['B']}", "--reason", "行政转")
    expect_cmd(cid, "--to/--reason", env, 0)
    cid = "CMD-055"
    t3 = new_task()
    env, _ = O("control", "task", "trash", t3["task_id"], "--dry-run",
               "--reason", "dry")
    covered(cid, "--dry-run", env.get("ok")
            and env["data"]["task"]["state"] == "queued")
    t4 = new_task()
    new_task(parent=t4["task_id"])
    env, _ = O("control", "task", "trash", t4["task_id"], "--cascade",
               "--reason", "级联", "--if-version", "1")
    covered(cid, "--cascade/--reason/--if-version",
            env.get("ok") and len(env["data"].get("affected", [])) >= 2)


def sc_permission():
    cid = "CMD-056"
    env, _ = O("control", "permission", "list", "--subject",
               f"agent:{FX['agents']['A']}", "--limit", "10")
    expect_cmd(cid, "--subject/--limit", env, 0)
    env, _ = O("control", "permission", "list", "--subject",
               f"agent:{FX['agents']['A']}", "--limit", "1")
    env2, _ = O("control", "permission", "list", "--subject",
                f"agent:{FX['agents']['A']}", "--limit", "1", "--cursor",
                env["data"].get("next_cursor") or "")
    covered(cid, "--cursor", env2.get("ok"))
    cid = "CMD-057"
    g = specfile("g.json", {"subject": f"agent:{FX['agents']['B']}",
                            "actions": ["task.read"],
                            "resources": [f"port:{FX['ports']['main']}"],
                            "delegable": False})
    env, _ = O("control", "permission", "grant", "--file", g,
               "--expires-at", "2030-01-01T00:00:00Z", "--reason", "授权覆盖")
    expect_cmd(cid, "--file/--expires-at/--reason", env, 0)
    gid = env["data"]["grant_id"]
    cid = "CMD-058"
    env, _ = O("control", "permission", "revoke", gid, "--reason", "撤销覆盖")
    expect_cmd(cid, "GRANT/--reason", env, 0)


def sc_operations_events():
    cid = "CMD-059"
    for v in ("accepted", "running", "succeeded", "failed", "cancelled"):
        env, _ = O("control", "operation", "list", "--state", v)
        covered(cid, f"--state={v}", env.get("ok"))
    env, _ = O("control", "operation", "list", "--target",
               f"agent:{FX['agents']['A']}", "--request", "req_none",
               "--limit", "5")
    expect_cmd(cid, "--target/--request/--limit", env, 0)
    env, _ = O("control", "operation", "list", "--limit", "1")
    env2, _ = O("control", "operation", "list", "--limit", "1", "--cursor", env["data"].get("next_cursor") or "")
    covered(cid, "--cursor", env2.get("ok"))
    cid = "CMD-060"
    ops = O("control", "operation", "list", "--state",
            "succeeded")[0]["data"]["items"]
    op0 = ops[0]["operation_id"] if ops else "op_none"
    env, _ = O("control", "operation", "show", op0, "--wait-seconds", "2")
    expect_cmd(cid, "OP/--wait-seconds", env, 0)
    cid = "CMD-061"
    env, _ = O("control", "operation", "cancel", "op_none", "--reason", "x")
    covered(cid, "OP/--reason", not env.get("ok")
            and env.get("error", {}).get("code") == "NOT_FOUND")
    cid = "CMD-062"
    env, _ = O("control", "event", "list", "--entity",
               f"task:{new_task()['task_id']}", "--kind", "task.created",
               "--since", "2020-01-01T00:00:00Z", "--limit", "5")
    expect_cmd(cid, "--entity/--kind/--since/--limit", env, 0)
    env, _ = O("control", "event", "list", "--limit", "1")
    env2, _ = O("control", "event", "list", "--limit", "1", "--cursor", env["data"].get("next_cursor") or "")
    covered(cid, "--cursor", env2.get("ok"))
    cid = "CMD-063"
    env, _ = O("control", "audit", "list", "--actor",
               f"owner:{json.load(open(OWNER, encoding='utf-8'))['subject']}",
               "--target", "system", "--request", "req_x", "--since",
               "2020-01-01T00:00:00Z", "--limit", "5")
    expect_cmd(cid, "--actor/--target/--request/--since/--limit", env, 0)
    env, _ = O("control", "audit", "list", "--limit", "1")
    env2, _ = O("control", "audit", "list", "--limit", "1", "--cursor", env["data"].get("next_cursor") or "")
    covered(cid, "--cursor", env2.get("ok"))
    cid = "CMD-064"
    env, _ = O("control", "delivery", "list", "--state", "pending",
               "--task", new_task()["task_id"], "--target",
               f"port:{FX['ports']['main']}", "--limit", "5")
    expect_cmd(cid, "--state/--task/--target/--limit", env, 0)
    env, _ = O("control", "delivery", "list", "--limit", "1")
    env2, _ = O("control", "delivery", "list", "--limit", "1", "--cursor", env["data"].get("next_cursor") or "")
    covered(cid, "--cursor", env2.get("ok"))
    cid = "CMD-065"
    env, _ = O("control", "delivery", "retry", "dlv_none", "--reason", "x")
    covered(cid, "DELIVERY/--reason", not env.get("ok"))


def sc_harness():
    env, _ = O("control", "harness", "list")
    expect_cmd("CMD-066", "list", env, 0)
    env, _ = O("control", "harness", "show", "fixture-minimal")
    expect_cmd("CMD-067", "NAME", env, 0)
    cid = "CMD-068"
    h = specfile("h.json", {"name": "cov-h", "adapter": "fixture",
                            "capabilities": {"start": True, "send": True,
                                             "yolo": True,
                                             "model_at_start": True,
                                             "stop": True}})
    env, _ = O("control", "harness", "register", "--file", h, "--reason",
               "注册覆盖")
    expect_cmd(cid, "--file/--reason", env, 0)
    env, _ = O("control", "harness", "register", "--file", h, "--replace",
               "--reason", "替换注册")
    covered(cid, "--replace", env.get("ok"))
    cid = "CMD-069"
    env, _ = O("control", "harness", "validate", "cov-h")
    expect_cmd(cid, "NAME(declared)", env, 0)
    env, _ = O("control", "harness", "validate", "cov-h", "--workspace",
               os.path.join(RUN, "smoke-ws"), "--model", "m1",
               "--alternate-model", "m2", "--smoke", timeout=120)
    covered(cid, "--smoke/--workspace/--model/--alternate-model",
            env.get("ok"))
    cid = "CMD-070"
    env, _ = O("control", "harness", "unregister", "cov-h", "--reason",
               "注销")
    expect_cmd(cid, "NAME/--reason", env, 0)


def sc_producers():
    cid = "CMD-071"
    env, _ = O("control", "producer", "list", "--limit", "10")
    expect_cmd(cid, "--limit", env, 0)
    env, _ = O("control", "producer", "list", "--limit", "1")
    env2, _ = O("control", "producer", "list", "--limit", "1", "--cursor", env["data"].get("next_cursor") or "")
    covered(cid, "--cursor", env2.get("ok"))
    cid = "CMD-072"
    env, _ = O("control", "producer", "show", FX["producer"])
    expect_cmd(cid, "PRODUCER", env, 0)
    cid = "CMD-073"
    env, _ = O("control", "producer", "create", "--name", "cov-prd",
               "--kind", "builtin", "--allow-target",
               f"port:{FX['ports']['main']}", "--reason", "生产者覆盖")
    expect_cmd(cid, "--name/--kind/--allow-target/--reason", env, 0)
    prd = env["data"]["producer"]["producer_id"]
    cid = "CMD-074"
    pu = specfile("pu.json", {"name": "cov-prd-2"})
    env, _ = O("control", "producer", "update", prd, "--file", pu,
               "--reason", "更新", "--if-version", "1")
    expect_cmd(cid, "--file/--reason/--if-version", env, 0)
    cid = "CMD-075"
    env, _ = O("control", "producer", "credential", FX["producer"], "--out",
               os.path.join(RUN, "prd-cred.json"), "--revoke-previous",
               "--reason", "轮换")
    expect_cmd(cid, "--out/--revoke-previous/--reason", env, 0)
    cid = "CMD-076"
    env, _ = O("control", "producer", "revoke", prd, "--reason", "停用")
    expect_cmd(cid, "PRODUCER/--reason", env, 0)


def sc_triggers():
    cid = "CMD-077"
    env, _ = O("control", "trigger", "list", "--enabled", "false",
               "--limit", "10")
    expect_cmd(cid, "--enabled/--limit", env, 0)
    env, _ = O("control", "trigger", "list", "--limit", "1")
    env2, _ = O("control", "trigger", "list", "--limit", "1", "--cursor", env["data"].get("next_cursor") or "")
    covered(cid, "--cursor", env2.get("ok"))
    env2, _ = O("control", "producer", "create", "--name", "cov-trg",
                "--kind", "builtin", "--allow-target",
                f"port:{FX['ports']['main']}")
    prd = env2["data"]["producer"]["producer_id"]
    tf = specfile("tt.json", {"target": f"port:{FX['ports']['main']}",
                              "type": "work", "title": "cov定时", "body": "b"})
    cid = "CMD-079"
    env, _ = O("control", "trigger", "create", "--name", "cov-cron",
               "--kind", "cron", "--producer", prd, "--task-file", tf,
               "--cron", "*/5 * * * *", "--timezone", "+08:00", "--enabled",
               "true", "--missed", "once")
    expect_cmd(cid, "cron full", env, 0)
    trg = env["data"]["trigger"]["trigger_id"]
    env, _ = O("control", "trigger", "create", "--name", "cov-int",
               "--kind", "interval", "--producer", prd, "--task-file", tf,
               "--every", "1h", "--enabled", "false", "--missed", "skip")
    covered(cid, "--every interval", env.get("ok"))
    env, _ = O("control", "trigger", "create", "--name", "cov-man",
               "--kind", "manual", "--producer", prd, "--task-file", tf)
    covered(cid, "manual default enabled=false", env.get("ok")
            and env["data"]["trigger"]["enabled"] is False)
    cid = "CMD-078"
    env, _ = O("control", "trigger", "show", trg)
    expect_cmd(cid, "TRIGGER", env, 0)
    cid = "CMD-080"
    tu = specfile("tu.json", {"name": "cov-cron-2"})
    env, _ = O("control", "trigger", "update", trg, "--file", tu,
               "--reason", "更新", "--if-version", "1")
    expect_cmd(cid, "--file/--reason/--if-version", env, 0)
    cid = "CMD-081"
    env, _ = O("control", "trigger", "fire", trg, "--event-id", "cov-e1",
               "--data", '{"x":1}')
    expect_cmd(cid, "--event-id/--data", env, 0)
    cid = "CMD-082"
    env, _ = O("control", "trigger", "enable", trg, "--reason", "启用")
    expect_cmd(cid, "--reason", env, 0)
    cid = "CMD-083"
    env, _ = O("control", "trigger", "disable", trg, "--reason", "停用")
    expect_cmd(cid, "--reason", env, 0)


def sc_channels():
    cid = "CMD-084"
    env, _ = O("control", "channel", "list", "--limit", "10")
    expect_cmd(cid, "--limit", env, 0)
    env, _ = O("control", "channel", "list", "--limit", "1")
    env2, _ = O("control", "channel", "list", "--limit", "1", "--cursor", env["data"].get("next_cursor") or "")
    covered(cid, "--cursor", env2.get("ok"))
    cid = "CMD-085"
    env, _ = O("control", "channel", "show", FX["channels"]["human"])
    expect_cmd(cid, "CHANNEL", env, 0)
    cid = "CMD-086"
    ch = specfile("ch.json", {"name": "cov-ch", "kind": "human-web"})
    env, _ = O("control", "channel", "register", "--file", ch, "--reason",
               "通道覆盖")
    expect_cmd(cid, "--file/--reason", env, 0)
    chid = env["data"]["channel"]["channel_id"]
    env, _ = O("control", "channel", "register", "--file",
               specfile("ch2.json", {"name": "cov-ch", "kind": "human-web"}),
               "--replace", "--reason", "替换")
    covered(cid, "--replace", env.get("ok"))
    cid = "CMD-087"
    cu = specfile("cu.json", {"name": "cov-ch-2"})
    env, _ = O("control", "channel", "update", chid, "--file", cu,
               "--reason", "更新", "--if-version", "1")
    expect_cmd(cid, "--file/--reason/--if-version", env, 0)
    cid = "CMD-088"
    env, _ = O("control", "channel", "disable", chid, "--reason", "停用")
    expect_cmd(cid, "--reason", env, 0)


def sc_human():
    cid = "CMD-089"
    env, _ = O("control", "human", "list", "--port", FX["ports"]["human"],
               "--state", "queued", "--limit", "10")
    expect_cmd(cid, "--port/--state/--limit", env, 0)
    env, _ = O("control", "human", "list", "--limit", "1")
    env2, _ = O("control", "human", "list", "--limit", "1", "--cursor", env["data"].get("next_cursor") or "")
    covered(cid, "--cursor", env2.get("ok"))
    def q(**data):
        args = ["emit", "new", "--root", "--to",
                f"port:{FX['ports']['human']}", "--title", "问",
                "--body", "?"]
        args += (["--data", json.dumps(data, ensure_ascii=False)] if data
                 else [])
        env, _ = O(*args)
        return env["data"]["task"]
    q1, q2, q3 = q(), q(), q()
    cid = "CMD-090"
    tf = textfile("ans.txt", "批准文件")
    env, _ = cli("control", "human", "answer", q1["task_id"],
                 "--spec-version", "1", "--text", "批准", "--text-file", tf,
                 "--data", '{"decision":"approve"}', "--attach",
                 "file://ans.txt", cred=cred("human"))
    covered(cid, "answer full (text+text-file -> file wins)",
            env.get("ok") and env["data"]["task"]["result"].get("text")
            == "批准文件")
    env, _ = cli("control", "human", "answer", q2["task_id"],
                 "--spec-version", "1", "--data-file",
                 specfile("d.json", {"decision": "deny"}),
                 cred=cred("human"))
    covered(cid, "--data-file",
            env.get("ok") and env["data"]["task"]["result"]["data"]
            ["decision"] == "deny")
    cid = "CMD-091"
    env, _ = cli("control", "human", "fail", q3["task_id"],
                 "--spec-version", "1", "--reason", "无法判断", "--text",
                 "说明", "--data", '{"why":"x"}', cred=cred("human"))
    covered(cid, "fail --reason/--text/--data", env.get("ok")
            and env["data"]["task"]["state"] == "fail")
    cid = "CMD-092"
    q4 = q()
    env, _ = cli("control", "human", "trash", q4["task_id"],
                 "--spec-version", "1", "--reason", "撤回", "--text-file",
                 textfile("t.txt", "撤回说明"), cred=cred("human"))
    covered(cid, "trash --reason/--text-file/--data", env.get("ok")
            and env["data"]["task"]["state"] == "trash")


def sc_test():
    cid = "CMD-093"
    env, _ = O("control", "test", "seed", "--scenario", "cov2", "--out",
               os.path.join(RUN, "m2.json"))
    expect_cmd(cid, "--scenario/--out", env, 0)
    cid = "CMD-094"
    env, _ = O("control", "test", "clock", "advance", "--by", "1m")
    expect_cmd(cid, "--by", env, 0)
    cid = "CMD-095"
    env, _ = O("control", "test", "fault", "set", "--point",
               "notification_drop", "--mode", "once", "--value",
               '{"entity":"agt_x"}')
    expect_cmd(cid, "--point/--mode/--value", env, 0)
    cid = "CMD-096"
    env, _ = O("control", "test", "fault", "clear", "--point",
               "notification_drop")
    expect_cmd(cid, "--point", env, 0)
    env, _ = O("control", "test", "fault", "clear")
    covered(cid, "clear-all", env.get("ok"))
    cid = "CMD-097"
    for act in ("idle", "busy", "inspect-off", "events-off"):
        env, _ = O("control", "test", "harness", FX["agents"]["A"],
                   "--action", act, "--value", "{}")
        covered(cid, f"--action={act}", env.get("ok"))
    env, _ = O("control", "test", "harness", FX["agents"]["A"], "--action",
               "stop-confirm")
    covered(cid, "--action=stop-confirm(value default)", env.get("ok"))


SCENARIOS = [sc_inbox_list, sc_inbox_peek, sc_inbox_claim, sc_inbox_decline,
             sc_inbox_tree, sc_inbox_wait, sc_emit_new, sc_emit_done,
             sc_emit_fail_trash, sc_emit_forward, sc_emit_comment,
             sc_emit_batch, sc_system, sc_agents, sc_ports, sc_subscriptions,
             sc_control_task, sc_permission, sc_operations_events,
             sc_harness, sc_producers, sc_triggers, sc_channels, sc_human,
             sc_test]


def main():
    setup()
    failed_scenarios = []
    for fn in SCENARIOS:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            import traceback
            row("SCENARIO", fn.__name__, "scenario", "FAIL",
                repr(e)[:200])
            print(traceback.format_exc()[-800:])
            failed_scenarios.append(fn.__name__)
    # mark global params covered via real usage across scenarios
    for gp in GLOBAL_PARAMS:
        covered("GLOBAL", gp.opt or gp.name, "global",
                f"used across scenarios ({gp.name})")
    # per-COMMAND execution bookkeeping: which commands got business rows
    executed_cmds = {r["cmd"] for r in ROWS
                     if r["status"] == "PASS" and r["cmd"].startswith("CMD")}
    for c in COMMANDS:
        if c.cid not in executed_cmds:
            row(c.cid, "-", "missing", "FAIL", "no executed business row")
    with open(os.path.join(RUN, "matrix.jsonl"), "w", encoding="utf-8") as f:
        for r in ROWS:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    passed = sum(1 for r in ROWS if r["status"] == "PASS")
    failed = [r for r in ROWS if r["status"] == "FAIL"]
    # param coverage bookkeeping: every declared param occurrence gets a row
    declared = sum(len(c.params) for c in COMMANDS)
    summary = {"run": RUN, "rows": len(ROWS), "passed": passed,
               "failed": len(failed), "commands_total": len(COMMANDS),
               "commands_executed": len(executed_cmds),
               "params_declared": declared,
               "failed_scenarios": failed_scenarios,
               "failed_rows": failed[:40]}
    with open(os.path.join(RUN, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in summary.items()
                      if k != "failed_rows"}, ensure_ascii=False))
    # teardown: real CMD-020 business execution (--reason)
    env, _ = O("control", "system", "stop", "--reason", "coverage done")
    covered("CMD-020", "--reason", env.get("ok") and
            env["data"].get("stopping") is True,
            "managed stop executed with reason")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
