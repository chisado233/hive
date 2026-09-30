"""Quick functional test: drives the real CLI through the core flow."""
import json
import os
import subprocess
import time
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(ROOT, "cli", "hive.py")


def _free_port(preferred=29972):
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PORT = os.environ.get("HIVE_QUICK_PORT") or str(_free_port())


def run(home, *args, cred=None, expect_ok=True):
    argv = [sys.executable, CLI, "--home", home, "--endpoint",
            f"http://127.0.0.1:{PORT}"]
    if cred:
        argv += ["--credential-file", cred]
    argv += list(args)
    p = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                       timeout=60)
    try:
        env = json.loads(p.stdout)
    except ValueError:
        env = {"ok": False, "raw": p.stdout, "stderr": p.stderr}
    if expect_ok and not env.get("ok"):
        print("CMD FAILED:", " ".join(args))
        print(p.stdout[:2000], p.stderr[:500])
        sys.exit(1)
    return env, p.returncode


def main():
    # unique per-run home: never wipes a directory another run is using
    home = os.path.join(ROOT, "tmp", f"quicktest-{PORT}")
    if os.path.exists(home) and os.listdir(home):
        home = os.path.join(ROOT, "tmp",
                            f"quicktest-{int(time.time())}-{os.getpid()}")
    import shutil
    shutil.rmtree(home, ignore_errors=True)
    run(home, "control", "system", "init", "--listen", f"127.0.0.1:{PORT}",
        "--test-mode", expect_ok=True)
    run(home, "control", "system", "start", "--listen", f"127.0.0.1:{PORT}",
        "--test-mode")
    cred = os.path.join(home, "auth", "owner.json")
    try:
        # seed fixture
        manifest_path = os.path.join(home, "fixtures", "manifest.json")
        env, _ = run(home, "control", "test", "seed", "--scenario", "quick",
                     "--out", manifest_path, cred=cred)
        fx = json.load(open(manifest_path, encoding="utf-8"))
        A, B = fx["agents"]["A"], fx["agents"]["B"]
        cred_a, cred_b = fx["credentials"]["A"], fx["credentials"]["B"]
        main_port = fx["ports"]["main"]
        print("seeded: A=", A, "port main=", main_port)

        # emit task to port
        env, _ = run(home, "emit", "new", "--root", "--to", f"port:{main_port}",
                     "--title", "T1", "--body", "做点事", cred=cred)
        t1 = env["data"]["task"]["task_id"]
        print("T1 =", t1, env["data"]["task"]["state"])

        # A claims
        env, _ = run(home, "inbox", "claim", t1, cred=cred_a)
        print("claim:", env["data"]["results"][0]["ok"],
              env["data"]["results"][0].get("claim_id"))

        # B cannot claim (per-item result, partial envelope)
        env, code = run(home, "inbox", "claim", t1, cred=cred_b, expect_ok=False)
        r = env.get("data", {}).get("results", [{}])[0]
        print("B claim rejected:", r.get("error", {}).get("code"), "exit", code)

        # A inbox list assigned
        env, _ = run(home, "inbox", "list", "--view", "assigned", cred=cred_a)
        print("A assigned:", len(env["data"]["items"]))

        # done with callback=self via B? A holds it; done with dispatch child
        dispatch = os.path.join(home, "fixtures", "dispatch.json")
        json.dump([{"target": f"port:{main_port}", "type": "work",
                    "title": "T2-child", "body": "child"}], open(dispatch, "w",
                                                                 encoding="utf-8"))
        claim = subprocess.run([sys.executable, CLI, "--home", home, "--endpoint",
                                f"http://127.0.0.1:{PORT}", "--credential-file",
                                cred_a, "inbox", "list", "--view", "assigned"],
                               capture_output=True, text=True, encoding="utf-8")
        env, _ = run(home, "emit", "done", t1, "--claim",
                     run(home, "inbox", "peek", t1, cred=cred_a)[0]["data"]
                     ["task"]["claim_id"],
                     "--text", "完成了", "--dispatch-file", dispatch, cred=cred_a)
        print("done+dispatch: state=", env["data"]["task"]["state"],
              "children=", [c["task_id"] for c in env["data"]["children"]])

        # tree shows open work
        env, _ = run(home, "inbox", "tree", t1, cred=cred)
        print("tree open_work:", env["data"]["open_work_count"],
              "counts:", env["data"]["counts_by_state"])

        # callback test: owner creates with callback=agent:A, B claims + fails
        env, _ = run(home, "emit", "new", "--root", "--to", f"port:{main_port}",
                     "--title", "T3", "--body", "x", "--callback", f"agent:{A}",
                     cred=cred)
        t3 = env["data"]["task"]["task_id"]
        env, _ = run(home, "inbox", "claim", t3, cred=cred_b)
        cl3 = env["data"]["results"][0]["claim_id"]
        env, _ = run(home, "emit", "fail", t3, "--claim", cl3, "--reason",
                     "受阻", cred=cred_b)
        print("T3 fail:", env["data"]["task"]["state"],
              "receipts:", env["data"]["receipt_ids"])

        # A's inbox has receipt
        env, _ = run(home, "inbox", "list", "--view", "personal", "--type",
                     "receipt", cred=cred_a)
        print("A receipts:", len(env["data"]["items"]))

        # comment + mention
        env, _ = run(home, "emit", "comment", t1, "--text",
                     f"补充说明 @{B}", "--mention", B, cred=cred)
        print("comment notices:", env["data"]["notice_ids"])

        # wait: owner creates parent+child; A claims parent, waits child
        env, _ = run(home, "emit", "new", "--root", "--to", f"agent:{A}",
                     "--type", "work", "--title", "P1", "--body", "父任务",
                     cred=cred)
        p1 = env["data"]["task"]["task_id"]
        env, _ = run(home, "inbox", "claim", p1, cred=cred_a)
        env, _ = run(home, "emit", "new", "--parent", p1, "--to",
                     f"port:{main_port}", "--title", "C1", "--body", "子任务",
                     cred=cred)
        c1 = env["data"]["task"]["task_id"]
        env, _ = run(home, "inbox", "wait", "create", p1, "--on", f"task:{c1}",
                     cred=cred_a)
        print("wait:", env["data"]["wait"]["state"])
        # B finishes child
        env, _ = run(home, "inbox", "claim", c1, cred=cred_b)
        env, _ = run(home, "emit", "done", c1, "--claim",
                     env["data"]["results"][0]["claim_id"], "--text", "ok",
                     cred=cred_b)
        env, _ = run(home, "inbox", "wait", "list", "--task", p1, "--state",
                     "ready", cred=cred_a)
        print("wait ready items:", len(env["data"]["items"]))

        print("\nALL QUICK TESTS PASSED")
    finally:
        run(home, "control", "system", "stop", "--reason", "test done", cred=cred,
            expect_ok=False)


if __name__ == "__main__":
    main()
