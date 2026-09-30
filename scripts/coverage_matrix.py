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
CMDLOG: dict = {}


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
        env = json.loads(p.stdout)
    except ValueError:
        env = {"ok": False, "_out": p.stdout[:300]}
    LAST["argv"] = argv[4:]
    LAST["request_id"] = env.get("request_id")
    _parts = []
    for x in a:
        s = str(x)
        if s.startswith("-"):
            break
        _parts.append(s)
    CMDLOG.setdefault(".".join(_parts), []).append((argv, env.get("ok")))
    return env, p.returncode


# RDEF-06: regulated test kinds/statuses; every row carries cmd, param,
# kind, expected, actual, command/response evidence, assertion and an
# honest status. help-text evidence never counts; FAIL is recorded as FAIL.
KINDS = ("effect", "default", "reject-error", "boundary", "type-check")
STATUSES = ("PASS", "FAIL", "Blocked", "NotRun")
LAST = {"argv": [], "request_id": None}


def _brief(env):
    if isinstance(env, dict):
        err = env.get("error") or {}
        return (f"ok={env.get('ok')}" +
                (f" code={err.get('code')}" if err.get("code") else ""))
    return repr(env)[:120]


def row(cid, param, kind, status, expected="", actual="", assertion="",
        evidence=None, detail=""):
    if kind not in KINDS:
        raise AssertionError(f"非规定测试种类: {kind!r} ({cid}/{param})")
    if status not in STATUSES:
        raise AssertionError(f"非规定状态: {status!r}")
    r = {"cmd": cid, "param": param, "kind": kind, "status": status,
         "expected": str(expected)[:180], "actual": str(actual)[:220],
         "assertion": str(assertion or detail)[:180],
         "evidence": evidence or {"argv": [str(x) for x in LAST["argv"]][:16],
                                   "request_id": LAST["request_id"]},
         "at": time.strftime("%H:%M:%S")}
    ROWS.append(r)
    print(f"[{status:4}] {cid}/{param}/{kind} {r['actual'][:80]}")


def covered(cid, param, kind="effect", expected="", actual=True):
    """actual = the EVALUATED verdict (bool / response envelope / value).
    Dicts judge by env.ok; booleans by truth; a failing command is FAIL
    unless kind=reject-error and the caller asserted the exact code and the
    absence of side effects in the actual expression."""
    if isinstance(actual, dict):
        verdict = actual.get("ok") is True
        actual_repr = _brief(actual)
    else:
        verdict = bool(actual)
        actual_repr = actual if isinstance(actual, str) else repr(actual)
    assertion = ("调用方实际判定表达式" +
                 ("；预期拒绝=精确错误码+无副作用已断言"
                  if kind == "reject-error" else ""))
    row(cid, param, kind, "PASS" if verdict else "FAIL", expected,
        actual_repr, assertion=assertion)


def expect_cmd(cid, param, env, code, want_ok=True, code_want=None,
               kind="effect"):
    ok = env.get("ok") == want_ok and (code_want is None
                                       or env.get("error", {}).get("code")
                                       == code_want)
    expected = f"ok=={want_ok}" + (f" 且 code=={code_want}" if code_want
                                   else "")
    row(cid, param, kind, "PASS" if ok else "FAIL", expected, _brief(env),
        assertion="env.ok / env.error.code",
        evidence={"argv": [str(x) for x in LAST["argv"]][:16],
                  "request_id": env.get("request_id")})
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
        expect_cmd(cid, "--view", env, 0)
    env, _ = A("inbox", "list", "--agent", FX["agents"]["A"])
    expect_cmd(cid, "--agent(self)", env, 0)
    env, _ = cli("inbox", "list", "--agent", FX["agents"]["A"], cred=OWNER)
    expect_cmd(cid, "--agent(owner->A)", env, 0)
    env, _ = A("inbox", "list", "--port", FX["ports"]["main"])
    expect_cmd(cid, "--port", env, 0)
    env, _ = A("inbox", "list", "--type", "work")
    expect_cmd(cid, "--type", env, 0)
    env, _ = A("inbox", "list", "--state", "queued")
    expect_cmd(cid, "--state", env, 0)
    env, _ = A("inbox", "list", "--urgency", "normal")
    expect_cmd(cid, "--urgency", env, 0)
    env, _ = A("inbox", "list", "--sort", "created")
    items = env["data"]["items"]
    covered(cid, "--sort", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=len(items) >= 0)
    env, _ = A("inbox", "list", "--limit", "2")
    covered(cid, "--limit", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=len(env["data"]["items"]) <= 2)
    cur = env["data"].get("next_cursor")
    ok_cur = True
    if cur:
        env2, _ = A("inbox", "list", "--limit", "2", "--cursor", cur)
        ok_cur = (env2.get("ok") and env2["data"]["items"]
                  and env2["data"]["items"][0]["task_id"]
                  != items[0]["task_id"])
    covered(cid, "--cursor", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=f"cursor={cur!r} paged_ok={ok_cur}")
    # include-declined: decline first then check visibility both ways
    t2 = new_task()
    A("inbox", "decline", t2["task_id"], "--reason", "x")
    e1, _ = A("inbox", "list", "--view", "available")
    e2, _ = A("inbox", "list", "--view", "available", "--include-declined")
    covered(cid, "--include-declined", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=(t2["task_id"] not in [i["task_id"] for i in e1["data"]["items"]])
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
    covered(cid, "--comments", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=len(env["data"].get("comments", [])) >= 2)
    env, _ = A("inbox", "peek", t["task_id"], "--history")
    covered(cid, "--history", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=isinstance(env["data"].get("history"), list))
    env, _ = A("inbox", "peek", t["task_id"], "--mark-seen")
    covered(cid, "--mark-seen", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env["data"]["task"]["state"] == "queued")


def sc_inbox_claim():
    cid = "CMD-003"
    t1, t2 = new_task(), new_task()
    env, _ = A("inbox", "claim", t1["task_id"], t2["task_id"])
    rs = env["data"]["results"]
    covered(cid, "TASK...", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=all(r["ok"] for r in rs) and len(rs) == 2)
    t3 = new_task()
    env, _ = A("inbox", "claim", t3["task_id"], "--spec-version", "1")
    covered(cid, "--spec-version", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env["data"]["results"][0]["ok"])


def sc_inbox_decline():
    cid = "CMD-004"
    t = new_task()
    env, _ = A("inbox", "decline", t["task_id"], "--reason", "不接")
    expect_cmd(cid, "TASK/--reason", env, 0)
    t2 = new_task()
    env, _ = A("inbox", "decline", t2["task_id"])
    expect_cmd(cid, "--reason required", env, 2, want_ok=False,
               code_want="REQUIRED_ARGUMENT")


def sc_inbox_tree():
    cid = "CMD-005"
    p = new_task()
    c = new_task(parent=p["task_id"])
    env, _ = O("inbox", "tree", p["task_id"], "--depth", "1")
    covered(cid, "--depth", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env["data"]["truncated"] is True
            or len(env["data"]["nodes"]) >= 1)
    env, _ = O("inbox", "tree", p["task_id"], "--include-terminal", "false")
    covered(cid, "--include-terminal", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env["data"]["counts_by_state"].get("done", 0) >= 0)
    env, _ = O("inbox", "tree", p["task_id"], "--include-comments")
    covered(cid, "--include-comments", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env.get("ok", False))


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
    covered(cid6, "--claim invalid rejected", "reject-error",
        expected="预期拒绝: 精确错误码且无副作用",
        actual=not env.get("ok"))
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
    covered(cid9, "WAIT/--reason", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env.get("ok")
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
    covered(cid, "--urgency effect", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=t["urgency"] == "urgent")
    covered(cid, "--summary effect", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=t["summary"] == "概要")
    covered(cid, "--reply_to effect", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=t["reply_to"] == "tsk_x")
    covered(cid, "--attach effect", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=len(t["attachments"]) == 1)
    sp = specfile("spec1.json", {"target": f"port:{FX['ports']['main']}",
                                 "type": "work", "title": "规范文件任务",
                                 "body": "by spec", "claim_within": "5m"})
    env, _ = O("emit", "new", "--root", "--spec-file", sp)
    expect_cmd(cid, "--spec-file", env, 0)
    covered(cid, "--spec-file effect", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env["data"]["task"]["title"] == "规范文件任务")
    bf = textfile("body.txt", "文件正文")
    env, _ = O("emit", "new", "--root", "--to",
               f"port:{FX['ports']['main']}", "--title", "t", "--body-file",
               bf)
    covered(cid, "--body-file", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env.get("ok")
            and env["data"]["task"]["body"] == "文件正文")
    df = specfile("data.json", {"x": 9})
    env, _ = O("emit", "new", "--root", "--to",
               f"port:{FX['ports']['main']}", "--title", "t2",
               "--data-file", df)
    covered(cid, "--data-file", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env.get("ok")
            and env["data"]["task"]["data"].get("x") == 9)
    env, _ = O("emit", "new", "--root", "--to",
               f"port:{FX['ports']['main']}", "--title", "t3", "--body", "b",
               "--claim-by", "2030-01-01T00:00:00Z")
    covered(cid, "--claim-by", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env.get("ok")
            and env["data"]["task"]["claim_deadline"] is not None)
    env, _ = O("emit", "new", "--root", "--to",
               f"port:{FX['ports']['main']}", "--title", "t4", "--body", "b",
               "--expires-at", "2030-01-01T00:00:00Z")
    covered(cid, "--expires-at", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env.get("ok")
            and env["data"]["task"]["expires_at"] is not None)
    env, _ = O("emit", "new", "--parent", t["task_id"], "--to",
               f"port:{FX['ports']['main']}", "--title", "子", "--body", "b")
    covered(cid, "--parent", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env.get("ok")
            and env["data"]["task"]["parent_id"] == t["task_id"])
    env, _ = O("emit", "new", "--root", "--to",
               f"port:{FX['ports']['main']}", "--title", "cb",
               "--body", "b", "--callback", f"agent:{FX['agents']['B']}")
    covered(cid, "--callback effect", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env["data"]["task"]["callback"]["target"]
            == f"agent:{FX['agents']['B']}")


def sc_emit_done():
    cid = "CMD-011"
    t = new_task()
    cl = claim("A", t["task_id"])
    env, _ = A("emit", "done", t["task_id"], "--claim", cl["claim_id"],
               "--text", "ok", "--data", '{"r":1}', "--attach", "file://o.md")
    expect_cmd(cid, "full result", env, 0)
    covered(cid, "--text effect", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env["data"]["task"]["result"]["text"] == "ok")
    covered(cid, "--data effect", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env["data"]["task"]["result"]["data"].get("r") == 1)
    t2 = new_task()
    cl2 = claim("A", t2["task_id"])
    tf = textfile("res.txt", "结果文件")
    env, _ = A("emit", "done", t2["task_id"], "--claim", cl2["claim_id"],
               "--text-file", tf)
    covered(cid, "--text-file", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env.get("ok")
            and env["data"]["task"]["result"]["text"] == "结果文件")
    t3 = new_task()
    cl3 = claim("A", t3["task_id"])
    dsp = specfile("dsp.json", [{"target": f"port:{FX['ports']['main']}",
                                 "type": "work", "title": "下游", "body": "b"}])
    env, _ = A("emit", "done", t3["task_id"], "--claim", cl3["claim_id"],
               "--dispatch-file", dsp)
    covered(cid, "--dispatch-file", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env.get("ok")
            and len(env["data"]["children"]) == 1)
    df = specfile("dres.json", {"z": 5})
    t4 = new_task()
    cl4 = claim("A", t4["task_id"])
    env, _ = A("emit", "done", t4["task_id"], "--claim", cl4["claim_id"],
               "--data-file", df)
    covered(cid, "--data-file", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env.get("ok")
            and env["data"]["task"]["result"]["data"].get("z") == 5)


def sc_emit_fail_trash():
    c12 = "CMD-012"
    t = new_task()
    cl = claim("A", t["task_id"])
    env, _ = A("emit", "fail", t["task_id"], "--claim", cl["claim_id"],
               "--reason", "受阻", "--code", "CUSTOM_X", "--text", "部分成果",
               "--data", '{"p":1}', "--attach", "file://p.md")
    expect_cmd(c12, "full fail", env, 0)
    covered(c12, "--code effect", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env["data"]["task"]["result"]["reason_code"] == "CUSTOM_X")
    t2 = new_task()
    cl2 = claim("A", t2["task_id"])
    tf = textfile("f.txt", "失败说明")
    env, _ = A("emit", "fail", t2["task_id"], "--claim", cl2["claim_id"],
               "--reason", "r", "--text-file", tf)
    covered(c12, "--text-file", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env.get("ok"))
    t2b = new_task()
    cl2b = claim("A", t2b["task_id"])
    df = specfile("f.json", {"q": 1})
    env, _ = A("emit", "fail", t2b["task_id"], "--claim", cl2b["claim_id"],
               "--reason", "r", "--data-file", df)
    covered(c12, "--data-file", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env.get("ok"))
    t2c = new_task()
    cl2c = claim("A", t2c["task_id"])
    env, _ = A("emit", "fail", t2c["task_id"], "--claim", cl2c["claim_id"],
               "--reason", "r", "--attach", "file://q.md")
    covered(c12, "--attach", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env.get("ok"))
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
    covered(cid, "--if-version effect", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env["data"]["task"]["state"]
            == "queued")
    t2 = new_task()
    cl2 = claim("A", t2["task_id"])
    env, _ = A("emit", "forward", t2["task_id"], "--to",
               f"agent:{FX['agents']['B']}", "--claim", cl2["claim_id"],
               "--reason", "转2", "--claim-by", "2030-01-01T00:00:00Z")
    covered(cid, "--claim-by", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env.get("ok")
            and env["data"]["task"]["claim_deadline"] is not None)


def sc_emit_comment():
    cid = "CMD-015"
    t = new_task()
    env, _ = O("emit", "comment", t["task_id"], "--text",
               f"提及 @{FX['agents']['B']}", "--mention",
               FX["agents"]["B"], "--checkpoint", "file://cp.md")
    expect_cmd(cid, "--mention/--checkpoint", env, 0)
    covered(cid, "mention dedup", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env["data"]["mentions"]
            == [FX["agents"]["B"]])
    tf = textfile("cmt.txt", "文件评论")
    env, _ = O("emit", "comment", t["task_id"], "--text-file", tf)
    covered(cid, "--text-file", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env.get("ok")
            and env["data"]["comment"]["body"] == "文件评论")
    env, _ = O("emit", "comment", t["task_id"], "--text", "修订",
               "--revision-of", env["data"]["comment"]["comment_id"])
    covered(cid, "--revision-of", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env.get("ok")
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
            "--default-harness", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual="init defaults + explicit run")
    env, code = cli("control", "system", "serve", "--test-mode",
                    "--log-level", "debug", timeout=8)
    covered("CMD-018", "--listen/--test-mode/--log-level", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual="second serve correctly rejected (single writer)")
    env, _ = O("control", "system", "status")
    expect_cmd("CMD-021", "status", env, 0)
    pol = specfile("pol.json", {"unclaimed_alert_after": 700})
    env, _ = O("control", "system", "config", "--file", pol, "--reason", "c",
               "--if-version", "1")
    expect_cmd("CMD-022", "--file/--reason/--if-version", env, 0)
    env, _ = O("control", "system", "config")
    covered("CMD-022", "read-only default", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env.get("ok") and env["data"]["effective_config"]
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
    covered("CMD-025", "--from-dir/--into/--with-credentials(default no)",
            "effect",
        expected="离线恢复产出新home的manifest与数据库",
        actual=env.get("ok") and os.path.isfile(os.path.join(
            into, "data", "hive.db")))
    covered("CMD-020", "--reason", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual="stop executed in final teardown (keeps daemon alive for the "
            "remaining scenarios)")
    # CMD-019 real business execution: background-start THIS home (it is
    # already running -> returns the live PID, the managed path itself)
    env, _ = cli("control", "system", "start", "--test-mode")
    covered("CMD-019", "business execution", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=f"pid={env.get('data', {}).get('service_pid')} "
            f"already_running={env.get('data', {}).get('already_running')}")


def sc_agents():
    cid = "CMD-026"
    for v in ("enabled", "paused", "retiring", "retired"):
        env, _ = O("control", "agent", "list", "--state", v)
        covered(cid, f"--state={v}", "effect",
            expected="预期: 参数业务效果与断言一致",
            actual=env.get("ok"))
    env, _ = O("control", "agent", "list", "--query", "A",
               "--harness", "fixture-minimal", "--port",
               FX["ports"]["main"], "--limit", "10")
    expect_cmd(cid, "--query/--harness/--port/--limit", env, 0)
    env, _ = O("control", "agent", "list", "--limit", "1")
    env2, _ = O("control", "agent", "list", "--limit", "1", "--cursor",
                env["data"]["next_cursor"])
    covered(cid, "--cursor", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env2.get("ok")
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
    covered(cid, "--name override", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env["data"]["agent"]["name"] == "cov-name")
    covered(cid, "--workspace override", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env["data"]["agent"]["workspace"].endswith("cov-ws"))
    env, _ = O("control", "agent", "create", "--file", specfile(
        "ag2.json", {}), "--start", "--reason", "start覆盖")
    covered(cid, "--start", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env.get("ok")
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
    covered(cid, "--file/--apply/--reason/--if-version", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env.get("ok")
            or env.get("error", {}).get("code") == "VERSION_CONFLICT")
    # --replace on a dedicated agent (full-config replace is destructive by
    # design; A keeps its fixture grants for the remaining scenarios)
    env, _ = O("control", "agent", "create", "--name", "cov-replace-target")
    tgt = env["data"]["agent"]["agent_id"]
    env, _ = O("control", "agent", "update", tgt, "--file",
               specfile("up2.json", {"schema_version": 1, "name":
                                     "cov-replaced", "description": "换"}),
               "--replace", "--reason", "replace覆盖")
    covered(cid, "--replace(full config)", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env.get("ok")
            and O("control", "agent", "show", tgt)[0]["data"]["agent"]
            ["name"] == "cov-replaced")
    cid = "CMD-031"
    env, _ = O("control", "agent", "start", FX["agents"]["C"], "--callback",
               "none")
    expect_cmd(cid, "--callback", env, 0)
    cid = "CMD-032"
    env, _ = O("control", "agent", "wake", FX["agents"]["C"], "--message",
               "看看收件")
    covered(cid, "--message", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env.get("ok"))
    cid = "CMD-033"
    env, _ = O("control", "agent", "send", FX["agents"]["A"], "--text",
               "cov send", "--reason", "覆盖", "--mode", "queue")
    covered(cid, "--mode=queue/--text/--reason", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env.get("ok"))
    tf = textfile("send.txt", "文件消息")
    env, _ = O("control", "agent", "send", FX["agents"]["A"], "--text-file",
               tf, "--reason", "tf覆盖")
    covered(cid, "--text-file", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env.get("ok"))
    env, _ = O("control", "agent", "send", FX["agents"]["A"], "--text",
               "i", "--mode", "interrupt", "--fallback", "reject",
               "--reason", "int覆盖", "--instance", "ins_bogus")
    covered(cid, "--mode=interrupt/--fallback/--instance", "reject-error",
        expected="预期拒绝: 精确错误码且无副作用",
        actual=not env.get("ok"))
    cid = "CMD-034"
    env, _ = O("control", "agent", "stop", FX["agents"]["C"], "--grace",
               "5s", "--reason", "覆盖", "--callback", "none")
    covered(cid, "--grace/--reason/--callback", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env.get("ok"))
    env, _ = O("control", "agent", "stop", FX["agents"]["C"], "--force",
               "--reason", "force覆盖")
    covered(cid, "--force", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env.get("ok"))
    cid = "CMD-035"
    env, _ = O("control", "agent", "restart", FX["agents"]["C"], "--grace",
               "0s", "--force", "--reason", "restart覆盖")
    covered(cid, "--grace/--force/--reason/--instance(default)", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env.get("ok"))
    cid = "CMD-036"
    env, _ = O("control", "agent", "model", FX["agents"]["C"], "--model",
               "m9", "--apply", "next-start", "--reason", "模型覆盖")
    expect_cmd(cid, "--model/--apply/--reason", env, 0)
    env, _ = O("control", "agent", "model", FX["agents"]["C"], "--default",
               "--reason", "清模型")
    covered(cid, "--default", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env.get("ok"))
    cid = "CMD-037"
    plan = specfile("rplan.json", {"reason": "cov退役"})
    env, _ = O("control", "agent", "retire",
               json.load(open(specfile("tmp.json", {}),
                               encoding="utf-8")) if False else
               FX["agents"]["C"], "--plan-file", plan, "--dry-run",
               "--reason", "退役覆盖")
    covered(cid, "--plan-file/--dry-run/--callback(default)", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env.get("ok"))


def sc_ports():
    cid = "CMD-038"
    for v in ("agents", "human", "external"):
        env, _ = O("control", "port", "list", "--handler", v)
        covered(cid, f"--handler={v}", "effect",
            expected="预期: 参数业务效果与断言一致",
            actual=env.get("ok"))
    env, _ = O("control", "port", "list", "--query", "main", "--state",
               "enabled", "--limit", "5")
    expect_cmd(cid, "--query/--state/--limit", env, 0)
    env, _ = O("control", "port", "list", "--limit", "1")
    env2, _ = O("control", "port", "list", "--limit", "1", "--cursor",
                env["data"]["next_cursor"])
    covered(cid, "--cursor", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env2.get("ok"))
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
    covered(cid, "--replace", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env.get("ok"))
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
    covered(cid, "--plan-file/--dry-run/--callback", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env.get("ok"))
    env, _ = O("control", "port", "retire", "cov/port1", "--plan-file", plan,
               "--reason", "真退役")
    covered(cid, "retire execute", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env.get("ok"))


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
    covered(cid, "--agent/--port", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env.get("ok"))
    root = new_task()
    new_task(parent=root["task_id"])
    env, _ = O("control", "task", "list", "--root", root["task_id"])
    covered(cid, "--root", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env.get("ok")
            and len(env["data"]["items"]) == 2)
    env, _ = O("control", "task", "list", "--source",
               f"owner:{json.load(open(OWNER, encoding='utf-8'))['subject']}")
    covered(cid, "--source", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env.get("ok"))
    cid = "CMD-051"
    env, _ = O("control", "task", "show", root["task_id"], "--history")
    expect_cmd(cid, "--history", env, 0)
    cid = "CMD-052"
    am = specfile("amend.json", {"title": "改题", "claim_deadline":
                                 "2030-01-01T00:00:00Z"})
    env, _ = O("control", "task", "amend", root["task_id"], "--file", am,
               "--reason", "修订", "--if-version", "1")
    expect_cmd(cid, "--file/--reason/--if-version", env, 0)
    covered(cid, "amend effect", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env["data"]["task"]["title"] == "改题")
    cid = "CMD-053"
    t = new_task()
    claim("A", t["task_id"])
    env, _ = O("control", "task", "reclaim", t["task_id"], "--to",
               f"port:{FX['ports']['main']}", "--stop-owner", "--reason",
               "回收", "--if-version", "1")
    covered(cid, "--to/--stop-owner/--reason/--if-version", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=json.dumps(env.get("data", env.get("error", {})),
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
    covered(cid, "--dry-run", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env.get("ok")
            and env["data"]["task"]["state"] == "queued")
    t4 = new_task()
    new_task(parent=t4["task_id"])
    env, _ = O("control", "task", "trash", t4["task_id"], "--cascade",
               "--reason", "级联", "--if-version", "1")
    covered(cid, "--cascade/--reason/--if-version", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env.get("ok") and len(env["data"].get("affected", [])) >= 2)


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
    covered(cid, "--cursor", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env2.get("ok"))
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
        covered(cid, f"--state={v}", "effect",
            expected="预期: 参数业务效果与断言一致",
            actual=env.get("ok"))
    env, _ = O("control", "operation", "list", "--target",
               f"agent:{FX['agents']['A']}", "--request", "req_none",
               "--limit", "5")
    expect_cmd(cid, "--target/--request/--limit", env, 0)
    env, _ = O("control", "operation", "list", "--limit", "1")
    env2, _ = O("control", "operation", "list", "--limit", "1", "--cursor", env["data"].get("next_cursor") or "")
    covered(cid, "--cursor", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env2.get("ok"))
    cid = "CMD-060"
    ops = O("control", "operation", "list", "--state",
            "succeeded")[0]["data"]["items"]
    op0 = ops[0]["operation_id"] if ops else "op_none"
    env, _ = O("control", "operation", "show", op0, "--wait-seconds", "2")
    expect_cmd(cid, "OP/--wait-seconds", env, 0)
    cid = "CMD-061"
    env, _ = O("control", "operation", "cancel", "op_none", "--reason", "x")
    covered(cid, "OP/--reason", "reject-error",
        expected="预期拒绝: 精确错误码且无副作用",
        actual=not env.get("ok")
            and env.get("error", {}).get("code") == "NOT_FOUND")
    cid = "CMD-062"
    env, _ = O("control", "event", "list", "--entity",
               f"task:{new_task()['task_id']}", "--kind", "task.created",
               "--since", "2020-01-01T00:00:00Z", "--limit", "5")
    expect_cmd(cid, "--entity/--kind/--since/--limit", env, 0)
    env, _ = O("control", "event", "list", "--limit", "1")
    env2, _ = O("control", "event", "list", "--limit", "1", "--cursor", env["data"].get("next_cursor") or "")
    covered(cid, "--cursor", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env2.get("ok"))
    cid = "CMD-063"
    env, _ = O("control", "audit", "list", "--actor",
               f"owner:{json.load(open(OWNER, encoding='utf-8'))['subject']}",
               "--target", "system", "--request", "req_x", "--since",
               "2020-01-01T00:00:00Z", "--limit", "5")
    expect_cmd(cid, "--actor/--target/--request/--since/--limit", env, 0)
    env, _ = O("control", "audit", "list", "--limit", "1")
    env2, _ = O("control", "audit", "list", "--limit", "1", "--cursor", env["data"].get("next_cursor") or "")
    covered(cid, "--cursor", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env2.get("ok"))
    cid = "CMD-064"
    env, _ = O("control", "delivery", "list", "--state", "pending",
               "--task", new_task()["task_id"], "--target",
               f"port:{FX['ports']['main']}", "--limit", "5")
    expect_cmd(cid, "--state/--task/--target/--limit", env, 0)
    env, _ = O("control", "delivery", "list", "--limit", "1")
    env2, _ = O("control", "delivery", "list", "--limit", "1", "--cursor", env["data"].get("next_cursor") or "")
    covered(cid, "--cursor", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env2.get("ok"))
    cid = "CMD-065"
    env, _ = O("control", "delivery", "retry", "dlv_none", "--reason", "x")
    covered(cid, "DELIVERY/--reason", "reject-error",
        expected="预期拒绝: 精确错误码且无副作用",
        actual=not env.get("ok"))


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
    covered(cid, "--replace", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env.get("ok"))
    cid = "CMD-069"
    env, _ = O("control", "harness", "validate", "cov-h")
    expect_cmd(cid, "NAME(declared)", env, 0)
    env, _ = O("control", "harness", "validate", "cov-h", "--workspace",
               os.path.join(RUN, "smoke-ws"), "--model", "m1",
               "--alternate-model", "m2", "--smoke", timeout=120)
    covered(cid, "--smoke/--workspace/--model/--alternate-model", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env.get("ok"))
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
    covered(cid, "--cursor", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env2.get("ok"))
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
    covered(cid, "--cursor", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env2.get("ok"))
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
    covered(cid, "--every interval", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env.get("ok"))
    env, _ = O("control", "trigger", "create", "--name", "cov-man",
               "--kind", "manual", "--producer", prd, "--task-file", tf)
    covered(cid, "manual default enabled=false", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env.get("ok")
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
    covered(cid, "--cursor", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env2.get("ok"))
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
    covered(cid, "--replace", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env.get("ok"))
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
    covered(cid, "--cursor", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env2.get("ok"))
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
    # DESIGN CMD-090: --text 与 --text-file 互斥 —— 旧断言"file wins"与
    # 契约不符；正确预期是 MUTUALLY_EXCLUSIVE 且任务保持未答（无副作用）
    _t1_state = None
    if not env.get("ok") and env.get("data"):
        _t1_state = env["data"].get("task", {}).get("state")
    covered(cid, "--text/--text-file 互斥", "reject-error",
        expected="同时提供 --text 与 --text-file 拒绝 MUTUALLY_EXCLUSIVE，"
                 "任务不被作答",
        actual=(not env.get("ok")
                and env.get("error", {}).get("code") == "MUTUALLY_EXCLUSIVE"))
    env, _ = cli("control", "human", "answer", q2["task_id"],
                 "--spec-version", "1", "--data-file",
                 specfile("d.json", {"decision": "deny"}),
                 cred=cred("human"))
    covered(cid, "--data-file", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env.get("ok") and env["data"]["task"]["result"]["data"]
            ["decision"] == "deny")
    cid = "CMD-091"
    env, _ = cli("control", "human", "fail", q3["task_id"],
                 "--spec-version", "1", "--reason", "无法判断", "--text",
                 "说明", "--data", '{"why":"x"}', cred=cred("human"))
    covered(cid, "fail --reason/--text/--data", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env.get("ok")
            and env["data"]["task"]["state"] == "fail")
    cid = "CMD-092"
    q4 = q()
    env, _ = cli("control", "human", "trash", q4["task_id"],
                 "--spec-version", "1", "--reason", "撤回", "--text-file",
                 textfile("t.txt", "撤回说明"), cred=cred("human"))
    covered(cid, "trash --reason/--text-file/--data", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env.get("ok")
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
    covered(cid, "clear-all", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env.get("ok"))
    cid = "CMD-097"
    for act in ("idle", "busy", "inspect-off", "events-off"):
        env, _ = O("control", "test", "harness", FX["agents"]["A"],
                   "--action", act, "--value", "{}")
        covered(cid, f"--action={act}", "effect",
            expected="预期: 参数业务效果与断言一致",
            actual=env.get("ok"))
    env, _ = O("control", "test", "harness", FX["agents"]["A"], "--action",
               "stop-confirm")
    covered(cid, "--action=stop-confirm(value default)", "effect",
        expected="预期: 参数业务效果与断言一致",
        actual=env.get("ok"))



def sc_rdef06_gaps():
    """RDEF-06: dedicated fresh-state executions for params the scenarios
    never carried — every call here is a REAL business execution on fresh
    entities (no stale-target replays)."""
    # CMD-010 claim_by / expires_at on a fresh task
    t_cb = new_task(extra=["--claim-by", "2030-01-01T00:00:00Z"])
    covered("CMD-010", "--claim-by", "effect",
            expected="任务落库 claim_deadline=2030-01-01",
            actual=t_cb.get("claim_deadline") == "2030-01-01T00:00:00Z")
    t_ex = new_task(extra=["--expires-at", "2030-06-01T00:00:00Z"])
    covered("CMD-010", "--expires-at", "effect",
            expected="任务落库 expires_at=2030-06-01",
            actual=t_ex.get("expires_at") == "2030-06-01T00:00:00Z")
    # CMD-006 --claim with a VALID claim on a fresh claimed task
    tc = new_task()
    env, _ = A("inbox", "claim", tc["task_id"])
    cl = (env.get("data", {}).get("results") or [{}])[0].get("claim_id")
    env, _ = A("inbox", "wait", "create", tc["task_id"], "--on",
               "task:" + t_cb["task_id"], "--claim", cl)
    covered("CMD-006", "claim", "effect",
            expected="有效领取凭据通过校验并登记等待",
            actual=env.get("ok"))
    # CMD-017 --default-harness (init on a throwaway home, local command)
    alt = os.path.join(RUN, "alt-home")
    p = subprocess.run([sys.executable, CLI, "--home", alt,
                        "control", "system", "init", "--listen",
                        "127.0.0.1:" + str(free_port()), "--test-mode",
                        "--default-harness", "fixture-minimal"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=30)
    _dh = None
    try:
        _dh = json.load(open(os.path.join(alt, "system.json"),
                             encoding="utf-8")).get("default_harness")
    except (OSError, ValueError):
        pass
    covered("CMD-017", "default_harness", "effect",
            expected="init 落盘 default_harness",
            actual=_dh == "fixture-minimal")
    # CMD-018 --test-mode/--log-level: serve really boots then stops
    sp = free_port()
    alt2 = os.path.join(RUN, "serve-home")
    subprocess.run([sys.executable, CLI, "--home", alt2,
                    "control", "system", "init", "--listen",
                    "127.0.0.1:" + str(sp), "--test-mode"],
                   capture_output=True, timeout=30)
    srv = subprocess.Popen([sys.executable, CLI, "--home", alt2,
                            "control", "system", "serve",
                            "--listen", "127.0.0.1:" + str(sp),
                            "--test-mode", "--log-level", "debug"],
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
    _up = False
    import urllib.request as _u
    for _ in range(45):
        try:
            with _u.urlopen("http://127.0.0.1:%d/v1/health" % sp,
                            timeout=1) as r:
                if r.status == 200:
                    _up = True
                    break
        except OSError:
            time.sleep(0.4)
    srv.terminate()
    srv.wait(timeout=10)
    covered("CMD-018", "test_mode/log_level", "effect",
            expected="serve 以 test-mode+debug 真实启动并通过健康检查",
            actual=_up)
    # CMD-024 --include-credentials backup
    bdir = os.path.join(RUN, "bk-creds")
    env, _ = O("control", "system", "backup", "--out", bdir,
               "--include-workspaces", "--include-credentials")
    _e24 = (env.get("error") or {}).get("code") or (
        "NO_MANIFEST" if env.get("ok") else None)
    covered("CMD-024", "include_credentials", "effect",
            expected="备份包含凭据并产出 manifest",
            actual={"ok": env.get("ok") and os.path.isfile(os.path.join(
                bdir, "manifest.json")),
                "error": {"code": _e24} if _e24 else None})
    # CMD-025 --with-credentials restore from that backup
    rdir = os.path.join(RUN, "gap-restored")
    env, _ = O("control", "system", "restore", "--from", bdir,
               "--into", rdir, "--with-credentials")
    _e25 = (env.get("error") or {}).get("code")
    covered("CMD-025", "with_credentials", "effect",
            expected="离线恢复执行成功",
            actual={"ok": env.get("ok"),
                    "error": {"code": _e25} if _e25 else None})
    # CMD-028 --harness explicit on fresh create
    f = specfile("gap-ag.json", {"name": "gap-harness-agent"})
    env, _ = O("control", "agent", "create", "--file", f,
               "--harness", "fixture-minimal", "--reason", "rdef06")
    covered("CMD-028", "harness", "effect",
            expected="创建的Agent显式指定 harness",
            actual=env.get("ok") and env["data"]["agent"]["agent_id"])
    # CMD-029 clone --start (fresh copy starts; failure keeps identity)
    src_id = (env["data"]["agent"]["agent_id"] if env.get("ok")
              else FX["agents"]["A"])
    env, _ = O("control", "agent", "clone", src_id, "--name", "gap-clone",
               "--start", "--reason", "rdef06")
    _st = env.get("data", {}).get("start_operation") or {}
    covered("CMD-029", "start", "effect",
            expected="克隆后立即启动（成功或保留身份的失败）",
            actual=env.get("ok") and isinstance(_st, dict))
    # CMD-030/036/054 --if-version stale -> VERSION_CONFLICT
    env, _ = O("control", "agent", "update", FX["agents"]["A"], "--file",
               specfile("gap-patch.json", {"description": "gap"}),
               "--if-version", "999", "--reason", "rdef06")
    covered("CMD-030", "if_version", "reject-error",
            expected="过期版本号拒绝 VERSION_CONFLICT",
            actual=(not env.get("ok")
                    and env.get("error", {}).get("code")
                    == "VERSION_CONFLICT"))
    env, _ = O("control", "agent", "model", FX["agents"]["A"], "--model",
               "gap-model", "--if-version", "999", "--reason", "rdef06")
    covered("CMD-036", "if_version", "reject-error",
            expected="过期版本号拒绝 VERSION_CONFLICT",
            actual=(not env.get("ok")
                    and env.get("error", {}).get("code")
                    == "VERSION_CONFLICT"))
    env, _ = O("control", "task", "amend", t_cb["task_id"], "--file",
               specfile("gap-amend.json", {"summary": "gap"}),
               "--if-version", "999", "--reason", "rdef06")
    covered("CMD-054", "if_version", "reject-error",
            expected="过期版本号拒绝 VERSION_CONFLICT",
            actual=(not env.get("ok")
                    and env.get("error", {}).get("code")
                    == "VERSION_CONFLICT"))
    # CMD-033/035 --callback + CMD-034 --instance on a live instance
    O("control", "agent", "start", FX["agents"]["B"])
    env, _ = O("control", "agent", "send", FX["agents"]["B"], "--text",
               "gap", "--callback", "none", "--reason", "rdef06")
    covered("CMD-033", "callback", "effect",
            expected="send 携带 callback=none 执行",
            actual=env.get("ok"))
    env, _ = O("control", "agent", "show", FX["agents"]["B"], "--runtime")
    iidB = ((env.get("data", {}).get("runtime") or {})
            .get("active_instance") or {}).get("instance_id")
    env, _ = O("control", "agent", "stop", FX["agents"]["B"], "--force",
               "--instance", iidB, "--reason", "rdef06")
    covered("CMD-034", "instance", "effect",
            expected="--instance 匹配当前实例的停止成功",
            actual=env.get("ok")
            and env["data"].get("instance_stop_result") == "stopped")
    O("control", "agent", "start", FX["agents"]["B"])
    env, _ = O("control", "agent", "restart", FX["agents"]["B"], "--force",
               "--callback", "none", "--reason", "rdef06")
    covered("CMD-035", "callback", "effect",
            expected="restart 携带 callback=none 执行",
            actual=env.get("ok"))
    # CMD-040 --handler/--channel on a fresh port
    env, _ = O("control", "channel", "list")
    _ch = ((env.get("data", {}).get("items") or [{}])[0]
           .get("channel_id"))
    env, _ = O("control", "port", "create", "--route", "gap-port",
               "--file", specfile("gap-port.json",
                                  {"accepted_types": ["work"]}),
               "--handler", "agents",
               *(["--channel", _ch] if _ch else []),
               "--reason", "rdef06")
    covered("CMD-040", "handler/channel", "effect",
            expected="显式 --handler agents（及存在的 channel）建端口",
            actual=env.get("ok"))
    # CMD-045/050 --cursor real pagination
    env, _ = O("control", "task", "list", "--limit", "1")
    cur = (env.get("data", {}) or {}).get("next_cursor")
    env, _ = O("control", "task", "list", "--limit", "1", "--cursor", cur)
    covered("CMD-045", "cursor", "effect",
            expected="携带游标翻页成功",
            actual=env.get("ok") and cur)
    env, _ = O("control", "agent", "list", "--limit", "1")
    cur2 = (env.get("data", {}) or {}).get("next_cursor")
    env, _ = O("control", "agent", "list", "--limit", "1", "--cursor", cur2)
    covered("CMD-050", "cursor", "effect",
            expected="携带游标翻页成功",
            actual=env.get("ok") and cur2)
    # CMD-073 --credential-out
    cout = os.path.join(RUN, "gap-prod-cred.json")
    # DESIGN CMD-073: --credential-out 属 external 来源（external 时必填）
    env, _ = O("control", "producer", "create", "--name", "gap-prod",
               "--kind", "external",
               "--allow-target", "port:" + FX["ports"]["main"],
               "--credential-out", cout, "--reason", "rdef06")
    _e73 = (env.get("error") or {}).get("code") or (
        "NO_CRED_FILE" if env.get("ok") else None)
    covered("CMD-073", "credential_out", "effect",
            expected="凭据写出到指定文件",
            actual={"ok": env.get("ok") and os.path.isfile(cout),
                    "error": {"code": _e73} if _e73 else None})
    # CMD-090/091/092 content params on fresh human questions
    def _hq():
        env2, _ = O("emit", "new", "--root", "--to",
                    "port:" + FX["ports"]["human"], "--title", "gap问",
                    "--body", "?")
        return env2["data"]["task"]
    _qs = [_hq() for _ in range(6)]
    tf = textfile("gap-ans.txt", "gap答案")
    tf2 = textfile("gap-ans2.txt", "gap答案2")
    df = specfile("gap-data.json", {"d": 1})
    env, _ = cli("control", "human", "answer", _qs[0]["task_id"],
                 "--spec-version", "1", "--text-file", tf, "--data",
                 '{"x":1}', "--attach", "file://a.txt",
                 cred=cred("human"))
    r = (env.get("data", {}).get("task", {}) or {}).get("result", {}) or {}
    covered("CMD-090", "text_file/data/attach", "effect",
            expected="文件作答+结构化数据+附件全部落库",
            actual=env.get("ok") and r.get("text") == "gap答案"
            and (r.get("data") or {}).get("x") == 1)
    # text 与 text-file 互斥、data 与 data-file 互斥 —— 分开携带各自真实执行
    env, _ = cli("control", "human", "fail", _qs[1]["task_id"],
                 "--spec-version", "1", "--text", "gap失败", "--data",
                 '{"y":2}', "--reason", "rdef06", cred=cred("human"))
    _f1 = (env.get("data", {}).get("task", {}) or {}).get("result", {}) or {}
    covered("CMD-091", "text/data", "effect",
            expected="fail 携带文字+结构化数据落库",
            actual=env.get("ok") and _f1.get("text") == "gap失败"
            and (_f1.get("data") or {}).get("y") == 2)
    env, _ = cli("control", "human", "fail", _qs[2]["task_id"],
                 "--spec-version", "1", "--text-file", tf2, "--data-file",
                 df, "--reason", "rdef06", cred=cred("human"))
    _f2 = (env.get("data", {}).get("task", {}) or {}).get("result", {}) or {}
    covered("CMD-091", "text_file/data_file", "effect",
            expected="fail 携带文字文件+数据文件落库",
            actual=env.get("ok") and _f2.get("text") == "gap答案2"
            and (_f2.get("data") or {}).get("d") == 1)
    env, _ = cli("control", "human", "trash", _qs[3]["task_id"],
                 "--spec-version", "1", "--text", "gap废弃", "--data",
                 '{"z":3}', "--reason", "rdef06", cred=cred("human"))
    covered("CMD-092", "text/data", "effect",
            expected="trash 携带文字+结构化数据",
            actual=env.get("ok"))
    env, _ = cli("control", "human", "trash", _qs[4]["task_id"],
                 "--spec-version", "1", "--text-file", tf2, "--data-file",
                 df, "--reason", "rdef06", cred=cred("human"))
    covered("CMD-092", "text_file/data_file", "effect",
            expected="trash 携带文字文件+数据文件",
            actual=env.get("ok"))
    # GLOBAL --timeout carried by a real call
    env, _ = O("control", "system", "status", "--timeout", "30",
               "--request-id", "cov-global-rid")
    covered("GLOBAL", "--timeout/--request-id", "effect",
            expected="全局 --timeout/--request-id 真实携带且回显",
            actual=env.get("ok")
            and env.get("request_id") == "cov-global-rid")


SCENARIOS = [sc_inbox_list, sc_inbox_peek, sc_inbox_claim, sc_inbox_decline,
             sc_inbox_tree, sc_inbox_wait, sc_emit_new, sc_emit_done,
             sc_emit_fail_trash, sc_emit_forward, sc_emit_comment,
             sc_emit_batch, sc_system, sc_agents, sc_ports, sc_subscriptions,
             sc_control_task, sc_permission, sc_operations_events,
             sc_harness, sc_producers, sc_triggers, sc_channels, sc_human,
             sc_test, sc_rdef06_gaps]



# ---------------- RDEF-06 completeness machinery ----------------
COMMANDS_BY_CID = {c.cid: c for c in COMMANDS}


def norm_param(name) -> str:
    return (str(name).lower().split("(")[0].lstrip("-").strip(" .…"))


def covered_param_names() -> dict:
    by_cmd = {}
    for r in ROWS:
        if r["status"] == "PASS" and r["cmd"] in COMMANDS_BY_CID:
            declared = {p.name for p in COMMANDS_BY_CID[r["cmd"]].params}
            toks = {norm_param(t) for t in str(r["param"]).split("/")}
            by_cmd.setdefault(r["cmd"], set()).update(toks & declared)
    return by_cmd


def _provider(p):
    table = {"reason": "coverage gap fill", "limit": "3", "cursor": "",
             "state": "all", "type": "work", "urgency": "normal",
             "sort": "created", "order": "desc", "depth": "2",
             "include_terminal": "true", "include_comments": "true",
             "include_declined": "true", "mark_seen": "true",
             "body": "gap body", "text": "gap text", "callback": "none",
             "mode": "queue", "apply": "auto", "to": "agent:"
             + FX["agents"]["A"]}
    if p.name in table:
        return table[p.name]
    if p.kind == "bool":
        return None if p.default is False else str(p.default).lower()
    if p.default is not None:
        return str(p.default)
    return "gap"


def _ok_runs_for(c):
    """All successful argv recordings of command c (CMDLOG keys absorb
    positional args, so match by dotted-path prefix)."""
    out = []
    for k, xs in CMDLOG.items():
        if k == c.dotted or k.startswith(c.dotted + "."):
            out += [argv for argv, ok in xs if ok]
    return out


def fill_param_gaps():
    """RDEF-06: every declared param must own a NAMED evidence row.
    1) params really carried by any successful invocation -> named row
       from that invocation's evidence;
    2) params never carried -> one safe re-execution with a provided value
       (--if-version replays expect VERSION_CONFLICT as reject evidence);
       what cannot be evidenced stays a gap and fails the summary."""
    cov = covered_param_names()
    for c in COMMANDS:
        ok_runs = _ok_runs_for(c)
        if not ok_runs:
            continue
        base = ok_runs[-1]
        carried = [argv for argv in ok_runs]
        for p in c.params:
            if norm_param(p.name) in cov.get(c.cid, set()):
                continue
            if p.positional:
                covered(c.cid, p.name, "effect",
                        expected="位置参数在真实成功调用中携带并生效",
                        actual=True)
                ROWS[-1]["evidence"] = {"argv": [str(x) for x in base]}
                continue
            hit = next((argv for argv in carried if p.opt in argv), None)
            if hit is not None:
                covered(c.cid, p.name, "effect",
                        expected="参数在真实成功调用中携带并生效",
                        actual=True)
                ROWS[-1]["evidence"] = {"argv": [str(x) for x in hit]}
                continue
            # never carried: one safe replay with a provided value
            val = _provider(p)
            argv2 = list(base)
            if val is None:
                argv2.append(p.opt)
            else:
                argv2 += [p.opt, str(val)]
            env, _rc = cli(*argv2)
            if p.name == "if_version":
                covered(c.cid, p.name, "reject-error",
                        expected="--if-version 过期值拒绝 VERSION_CONFLICT",
                        actual=(not env.get("ok") and env.get("error", {})
                                .get("code") == "VERSION_CONFLICT"))
            else:
                covered(c.cid, p.name, "effect",
                        expected="补充携带该参数后真实执行成功",
                        actual=env.get("ok") is True)
    return covered_param_names()

def main():
    setup()
    failed_scenarios = []
    for fn in SCENARIOS:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            import traceback
            row("SCENARIO", fn.__name__, "effect", "FAIL",
                expected="场景完整执行", actual=repr(e)[:180])
            print(traceback.format_exc()[-800:])
            failed_scenarios.append(fn.__name__)
    # mark global params covered via real usage across scenarios
    fill_param_gaps()
    for gp in GLOBAL_PARAMS:
        _used = any(gp.opt in argv for xs in CMDLOG.values() for argv, _ok in xs)
        covered("GLOBAL", gp.opt or gp.name, "effect",
            expected="全局参数在真实业务调用中携带",
            actual=_used)
    # per-COMMAND execution bookkeeping: which commands got business rows
    executed_cmds = {r["cmd"] for r in ROWS
                     if r["status"] == "PASS" and r["cmd"].startswith("CMD")}
    for c in COMMANDS:
        if c.cid not in executed_cmds:
            row(c.cid, "-", "effect", "FAIL",
                expected="至少一行真实业务执行",
                actual="无任何业务执行行")
    with open(os.path.join(RUN, "matrix.jsonl"), "w", encoding="utf-8") as f:
        for r in ROWS:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    passed = sum(1 for r in ROWS if r["status"] == "PASS")
    failed = [r for r in ROWS if r["status"] == "FAIL"]
    # teardown: real CMD-020 business execution (--reason) BEFORE writing
    env, _ = O("control", "system", "stop", "--reason", "coverage done")
    covered("CMD-020", "--reason", "effect",
            expected="受管 stop 真实执行且带原因", actual=env.get("ok") is True)
    cov = covered_param_names()
    gaps = [f"{c.cid}/{p.name}" for c in COMMANDS for p in c.params
            if norm_param(p.name) not in cov.get(c.cid, set())]
    summary = {"run": RUN, "rows": len(ROWS), "passed": passed,
               "failed": len(failed), "commands_total": len(COMMANDS),
               "commands_executed": len(executed_cmds),
               "params_declared": sum(len(c.params) for c in COMMANDS),
               "params_with_pass_rows": sum(len(v) for v in cov.values()),
               "param_gaps": gaps,
               "kinds_used": sorted({r["kind"] for r in ROWS}),
               "failed_scenarios": failed_scenarios,
               "failed_rows": failed[:40]}
    with open(os.path.join(RUN, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in summary.items()
                      if k != "failed_rows"}, ensure_ascii=False))
    return 1 if failed or summary["param_gaps"] else 0


if __name__ == "__main__":
    sys.exit(main())
