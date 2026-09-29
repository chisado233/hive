"""hive CLI — single entry: python cli/hive.py (install wrapper to use `hive`).

Three top-level groups only: inbox / emit / control. All commands, parameters,
defaults and mutex rules come from hive_core.manifest (single contract source).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hive_core import __version__  # noqa: E402
from hive_core.errors import HiveError  # noqa: E402
from hive_core.home import DEFAULT_ENDPOINT, default_endpoint, resolve_home  # noqa: E402
from hive_core.manifest import COMMANDS, GLOBAL_PARAMS  # noqa: E402
from hive_core.timeutil import parse_duration  # noqa: E402

TOP_GROUPS = ["inbox", "emit", "control"]
JSON_FILE_PARAMS = {"data", "spec", "dispatch", "plan", "input", "task", "value"}


def _read_file_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return f.read()
    except OSError as e:
        raise HiveError("INVALID_ARGUMENT", f"无法读取文件 {path}: {e.strerror or e}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hive",
        description="Hive V1 — 稳定Agent与统一Task的通用自动化脚手架。"
                    "顶层入口只有 inbox / emit / control。",
        allow_abbrev=False, exit_on_error=False)

    def _json_error(message):
        print(json.dumps({"ok": False, "request_id": None, "error": {
            "code": "INVALID_ARGUMENT", "message": message,
            "details": {}, "retryable": False}}, ensure_ascii=False))
        raise SystemExit(2)

    parser.error = _json_error  # type: ignore[method-assign]
    parser.add_argument("--version", action="version",
                        version=f"hive {__version__} (protocol 1)")
    _add_globals(parser)
    sub = parser.add_subparsers(dest="group", metavar="GROUP")
    # trie: prefix path -> {leaf_command or None, children: {name: node}}
    trie: dict = {"leaf": None, "children": {}}
    for cmd in COMMANDS:
        node = trie
        for seg in cmd.path:
            node = node["children"].setdefault(seg, {"leaf": None, "children": {}})
        node["leaf"] = cmd

    def add_children(action, node, prefix):
        for name, child in node["children"].items():
            p = action.add_parser(name, help=" ".join(prefix + [name]),
                                  allow_abbrev=False)
            _add_globals(p, inherit=True)
            if child["leaf"]:
                _add_leaf_params(p, child["leaf"])
            if child["children"]:
                sub2 = p.add_subparsers(dest="command", metavar="COMMAND")
                add_children(sub2, child, prefix + [name])

    add_children(sub, trie, [])
    _patch_subparser_errors(parser)
    return parser


def _get_or_create(parser, name: str):
    return parser._name_parser_map.get(name)  # noqa: SLF001


def _patch_subparser_errors(parser):
    """Make every nested parser emit a JSON error envelope on usage errors."""
    def _err(message):
        print(json.dumps({"ok": False, "request_id": None, "error": {
            "code": "INVALID_ARGUMENT", "message": message,
            "details": {}, "retryable": False}}, ensure_ascii=False))
        raise SystemExit(2)
    parser.error = _err  # type: ignore[method-assign]
    action = next((a for a in parser._actions  # noqa: SLF001
                   if isinstance(a, argparse._SubParsersAction)), None)
    if not action:
        return
    for sub in action._name_parser_map.values():  # noqa: SLF001
        _patch_subparser_errors(sub)


def _add_globals(parser: argparse.ArgumentParser, *, inherit: bool = False):
    """Root gets real defaults; subparsers use SUPPRESS so an option given
    before the group is not clobbered by subparser defaults."""
    d = argparse.SUPPRESS if inherit else None
    parser.add_argument("--home", help="数据根（默认HIVE_HOME或项目.hive）",
                        default=d)
    parser.add_argument("--endpoint", help="服务地址（默认HIVE_URL或127.0.0.1:19962）",
                        default=d)
    parser.add_argument("--credential-file", help="本地凭据文件", default=d)
    parser.add_argument("--format", choices=["json", "text"], default=d,
                        help="输出格式，默认json")
    parser.add_argument("--request-id", help="幂等键；查询仅作关联标识", default=d)
    parser.add_argument("--timeout", type=float, default=d,
                        help="网络请求超时秒数")


_GLOBAL_OPTS = {"--home", "--endpoint", "--credential-file", "--format",
                "--request-id", "--timeout", "--help"}


def _add_leaf_params(p: argparse.ArgumentParser, cmd):
    for param in cmd.params:
        kwargs = {"help": f"{param.help}" + (
            f" [默认 {param.default}]" if param.default is not None and
            param.kind != "flag" else "")}
        if param.positional:
            name = param.name
            if param.repeatable:
                kwargs["nargs"] = "+"
            p.add_argument(name, **kwargs)
            continue
        opt = param.opt
        if param.kind == "flag":
            p.add_argument(opt, dest=param.name, action="store_true", **kwargs)
        elif param.kind == "bool":
            kwargs["choices"] = ["true", "false"]
            kwargs["metavar"] = "BOOL"
            p.add_argument(opt, dest=param.name, default=None, **kwargs)
        elif param.kind == "int":
            kwargs["type"] = int
            kwargs["metavar"] = "N"
            p.add_argument(opt, dest=param.name, default=None, **kwargs)
        elif param.kind == "enum":
            kwargs["choices"] = param.choices or []
            if param.repeatable:
                kwargs["action"] = "append"
            p.add_argument(opt, dest=param.name, default=None, **kwargs)
        elif param.repeatable:
            kwargs["action"] = "append"
            p.add_argument(opt, dest=param.name, default=None, **kwargs)
        else:
            kwargs["metavar"] = param.name.upper()
            p.add_argument(opt, dest=param.name, default=None, **kwargs)
    return p


def _cmd_help(cmd) -> str:
    base = {"CMD-001": "聚合查看个人、责任和可领任务",
            "CMD-002": "查看任务详情", "CMD-003": "领取单个或多个任务",
            "CMD-004": "拒绝尚未领取的工作", "CMD-005": "查询任务血缘树",
            "CMD-006": "登记具体任务的等待", "CMD-007": "查看等待记录",
            "CMD-008": "查看等待详情", "CMD-009": "解除等待",
            "CMD-010": "创建并投递Task", "CMD-011": "提交成功结果，可原子创建下游",
            "CMD-012": "结束本次处理并说明失败", "CMD-013": "废弃任务并说明原因",
            "CMD-014": "转交同一任务", "CMD-015": "追加评论并可@通知",
            "CMD-016": "原子提交多个业务操作"}.get(cmd.cid)
    if base:
        return base
    return f"{cmd.cid}"


def _check_duplicate_options(argv: list[str]) -> None:
    """DESIGN §12: 同一个参数重复赋值报错（可重复参数除外）。"""
    counts: dict[str, int] = {}
    for tok in argv:
        if tok.startswith("--"):
            counts[tok] = counts.get(tok, 0) + 1
    repeatable = set()
    for cmd in COMMANDS:
        for p in cmd.params:
            if p.repeatable and p.opt:
                repeatable.add(p.opt)
    for opt, n in counts.items():
        if opt in _GLOBAL_OPTS or opt == "--version":
            continue
        if n > 1 and opt not in repeatable:
            raise HiveError("INVALID_ARGUMENT",
                            f"参数 {opt} 重复提供 {n} 次（重复赋值报错）")


def _inline_files_safe(cmd, ns: dict, argv: list[str]) -> dict:
    """Mutex-check BEFORE reading files; convert client errors to envelopes."""
    groups: dict[str, list] = {}
    for p in cmd.params:
        if p.mutex and p.mutex != "spec":
            groups.setdefault(p.mutex, []).append(p.opt)
    for opts in groups.values():
        present = [o for o in opts if o in argv]
        if len(present) > 1:
            raise HiveError("MUTUALLY_EXCLUSIVE",
                            f"互斥参数不能同时提供: {', '.join(present)}")
    spec_opts = [p.opt for p in cmd.params
                 if p.mutex == "spec" and not p.file_of]
    if "--spec-file" in argv:
        clash = [o for o in spec_opts if o in argv]
        if clash:
            raise HiveError("MUTUALLY_EXCLUSIVE",
                            f"--spec-file 与任务内容参数互斥: {', '.join(clash)}")
    return _inline_files(cmd, ns)


def _inline_files(cmd, args: dict) -> dict:
    out = {k: v for k, v in args.items()
           if v is not None and v is not False}
    for p in cmd.params:
        if not p.file_of:
            continue
        val = out.pop(p.name, None)
        if val is None:
            continue
        text = _read_file_text(val)
        if p.file_of in JSON_FILE_PARAMS:
            try:
                out[p.file_of] = json.loads(text) if text.strip() else {}
            except ValueError:
                raise HiveError("INVALID_JSON",
                                f"文件 {val} 不是合法JSON（参数 {p.opt}）")
        else:
            out[p.file_of] = text
    return out


def _textify(data, indent=0):
    lines = []
    pad = "  " * indent
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, (dict, list)) and v:
                lines.append(f"{pad}{k}:")
                lines.append(_textify(v, indent + 1))
            else:
                lines.append(f"{pad}{k}: {_short(v)}")
    elif isinstance(data, list):
        for i, v in enumerate(data):
            if isinstance(v, (dict, list)):
                lines.append(f"{pad}- [{i}]")
                lines.append(_textify(v, indent + 1))
            else:
                lines.append(f"{pad}- {_short(v)}")
    else:
        lines.append(f"{pad}{_short(data)}")
    return "\n".join(lines)


def _short(v) -> str:
    s = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v)
    return s if len(s) <= 200 else s[:197] + "..."


def _main_impl(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        _check_duplicate_options(argv)
    except HiveError as e:
        print(json.dumps({"ok": False, "request_id": None,
                          "error": e.to_dict()}, ensure_ascii=False))
        return e.exit_code
    parser = build_parser()
    parsed, rest = parser.parse_known_args(argv)
    if rest:
        print(json.dumps({"ok": False, "request_id": None, "error": {
            "code": "INVALID_ARGUMENT", "message": f"无法识别的参数: {rest}",
            "details": {}, "retryable": False}}, ensure_ascii=False))
        return 2
    ns = vars(parsed)

    group = ns.pop("group", None)
    if group is None:
        parser.print_help()
        return 0
    command_name = ns.pop("command", None)
    if command_name is None:
        parser.print_help()
        return 0

    # locate command: longest contiguous path-token match in argv
    tokens = list(argv)
    candidates = []
    for c in COMMANDS:
        n = len(c.path)
        for i in range(len(tokens) - n + 1):
            if tokens[i:i + n] == list(c.path):
                candidates.append(c)
                break
    if not candidates:
        print(json.dumps({"ok": False, "error": {
            "code": "COMMAND_UNKNOWN",
            "message": f"无法识别的命令（tokens={tokens[:6]}）",
            "details": {}, "retryable": False}}, ensure_ascii=False))
        return 2
    cmd = max(candidates, key=lambda c: len(c.path))

    home = resolve_home(ns.pop("home", None))
    explicit_ep = ns.pop("endpoint", None)
    endpoint = (explicit_ep or default_endpoint()).rstrip("/")
    if not explicit_ep and endpoint == DEFAULT_ENDPOINT.rstrip("/"):
        # fall back to the endpoint recorded for this home's last daemon
        try:
            import json as _json
            svc = _json.load(open(os.path.join(home, "runtime", "service.json"),
                                  encoding="utf-8"))
            if svc.get("endpoint"):
                endpoint = svc["endpoint"].rstrip("/")
        except (OSError, ValueError, KeyError):
            pass
    credential_file = ns.pop("credential_file", None) or os.environ.get(
        "HIVE_CREDENTIAL_FILE")
    fmt = ns.pop("format", None) or "json"
    request_id = ns.pop("request_id", None)
    timeout = ns.pop("timeout", None) or 15.0

    args = _inline_files_safe(cmd, ns, argv)

    # local commands
    if cmd.local:
        from hive_core import local
        from hive_core.timeutil import parse_duration as pd
        payload = dict(args)
        payload["home"] = home
        if cmd.dotted == "control.system.init":
            data = local.cmd_init(payload)
        elif cmd.dotted == "control.system.serve":
            sysjson = json.load(open(os.path.join(home, "system.json"),
                                     encoding="utf-8")) \
                if os.path.exists(os.path.join(home, "system.json")) else {}
            listen = args.get("listen") or sysjson.get("listen") or "127.0.0.1:19962"
            test_mode = bool(args.get("test_mode", sysjson.get("test_mode")))
            from hive_core.server import run_daemon
            run_daemon(home, listen, test_mode, args.get("log_level") or "info")
            data = {"stopped": True}
        elif cmd.dotted == "control.system.start":
            data = local.cmd_start(payload)
        elif cmd.dotted == "control.system.restore":
            data = local.cmd_restore_local(payload)
        else:
            raise HiveError("NOT_IMPLEMENTED", f"本地命令未实现: {cmd.dotted}")
        print(json.dumps({"ok": True, "request_id": request_id, "data": data,
                          "warnings": []}, ensure_ascii=False))
        return 0

    # online commands
    token = None
    if credential_file:
        from hive_core.auth import load_credential_file
        cred = load_credential_file(credential_file)
        token = cred["token"]
    body = {"command": cmd.dotted, "arguments": args}
    if request_id:
        body["request_id"] = request_id
    req = urllib.request.Request(
        endpoint + "/v1/commands", data=json.dumps(body, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            envelope = json.loads(resp.read().decode("utf-8"))
            code = 0
    except urllib.error.HTTPError as e:
        try:
            envelope = json.loads(e.read().decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            envelope = {"ok": False, "request_id": request_id, "error": {
                "code": "INTERNAL_ERROR", "message": f"HTTP {e.code}",
                "details": {}, "retryable": False}}
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        envelope = {"ok": False, "request_id": request_id, "error": {
            "code": "TRANSPORT_UNAVAILABLE",
            "message": f"无法连接服务 {endpoint}: {e}",
            "details": {"endpoint": endpoint}, "retryable": True}}
    if envelope.get("ok"):
        if fmt == "text":
            print(_textify(envelope.get("data")))
        else:
            print(json.dumps(envelope, ensure_ascii=False))
        data = envelope.get("data") or {}
        if isinstance(data, dict) and data.get("partial"):
            return 6  # 部分成功（如批量claim逐项结果）
        return 0
    err = envelope.get("error") or {}
    code = HiveError(err.get("code", "INTERNAL_ERROR"), err.get("message", "")
                     ).exit_code
    if fmt == "text":
        print(f"错误 [{err.get('code')}]: {err.get('message')}", file=sys.stderr)
    else:
        print(json.dumps(envelope, ensure_ascii=False))
    return code


def main(argv=None) -> int:
    try:
        return _main_impl(argv)
    except HiveError as e:
        print(json.dumps({"ok": False, "request_id": None,
                          "error": e.to_dict()}, ensure_ascii=False))
        return e.exit_code


if __name__ == "__main__":
    sys.exit(main())
