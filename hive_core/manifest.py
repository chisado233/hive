"""Command manifest: the single source of truth for all 97 public CLI leaves.

Drives CLI parsing/help, server-side argument validation and GET /v1/schema.
Every parameter from DESIGN §14 is declared here; unknown arguments are
rejected, never silently ignored.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .errors import HiveError

LIMIT_HELP = "返回条数 (1..200, 默认50)"
CURSOR_HELP = "使用前页 next_cursor，不手工解析"


@dataclass
class Param:
    name: str                      # snake_case canonical name
    kind: str = "str"              # str/int/bool/flag/enum/json/path/duration/time/target/ref
    opt: str = ""                  # CLI option; empty for positional
    help: str = ""
    default: Any = None
    required: bool = False
    choices: list[str] | None = None
    repeatable: bool = False
    positional: bool = False
    min: float | None = None
    max: float | None = None
    mutex: str | None = None       # mutex group name within the command
    allow_none: bool = False       # duration 'none' accepted
    file_of: str = ""              # path param whose content inlines into file_of key
    hidden_reason_limit: str = ""

    def to_schema(self) -> dict:
        d: dict[str, Any] = {"name": self.name, "kind": self.kind}
        if self.positional:
            d["positional"] = True
        if self.opt:
            d["opt"] = self.opt
        if self.required:
            d["required"] = True
        if self.choices is not None:
            d["choices"] = list(self.choices)
        if self.default is not None or not self.required:
            d["default"] = self.default
        if self.repeatable:
            d["repeatable"] = True
        if self.min is not None:
            d["min"] = self.min
        if self.max is not None:
            d["max"] = self.max
        if self.mutex:
            d["mutex_group"] = self.mutex
        if self.allow_none:
            d["allow_none"] = True
        if self.file_of:
            d["file_of"] = self.file_of
        return d


def P(name: str, **kw) -> Param:
    opt = kw.pop("opt", None)
    if opt is None:
        opt = "--" + name.replace("_", "-")
    positional = kw.get("positional", False)
    return Param(name=name, opt=opt if not positional else "", **kw)


LIMIT = lambda: P("limit", kind="int", default=50, min=1, max=200, help=LIMIT_HELP)
CURSOR = lambda: P("cursor", help=CURSOR_HELP)
REASON_REQ = lambda d="操作原因，最长4096字符，进入审计": P(
    "reason", required=True, help=d)
TEXT = lambda: P("text", help="结果/评论文字；与 --text-file 互斥", mutex="text")
TEXT_FILE = lambda: P("text_file", kind="path", file_of="text",
                      help="客户端读取文字，不把路径交服务端任意读取", mutex="text")
DATA = lambda: P("data", kind="json", default={}, help="结构化数据；与 --data-file 互斥",
                 mutex="data")
DATA_FILE = lambda: P("data_file", kind="path", file_of="data",
                      help="客户端解析对象，非法 JSON 返回参数错误", mutex="data")
ATTACH = lambda: P("attach", kind="ref", repeatable=True, default=[],
                   help="可重复；保存引用，内容访问另外鉴权")
CALLBACK = lambda: P("callback", kind="target", default="none",
                     help="注册终态投递目标；非 Agent 不能用 self")
CLAIM = lambda: P("claim", help="work 必需有效本次领取凭据；管理者请用 control")
IF_VERSION = lambda: P("if_version", kind="int", min=1,
                       help="乐观并发检查，过时返回 VERSION_CONFLICT")
GRACE = lambda: P("grace", kind="duration", default="15s",
                  help="保存提示宽限；强制停止不保证新checkpoint")
FORCE = lambda: P("force", kind="flag", help="跳过宽限，仍须确认旧实例停止")
INSTANCE = lambda: P("instance", help="提供时必须匹配当前实例，避免作用于后来的新实例")
DURATION = lambda n, **kw: P(n, kind="duration", **{"allow_none": True, **kw})
TIMEP = lambda n, **kw: P(n, kind="time", **kw)
DRY_RUN = lambda: P("dry_run", kind="flag", help="只检查，不改变状态")
STATE_LIFECYCLE = ["enabled", "paused", "retiring", "retired"]
TASK_STATES = ["queued", "claimed", "done", "fail", "trash"]


@dataclass
class Command:
    cid: str
    path: tuple[str, ...]
    params: list[Param]
    local: bool = False            # runs on the local host (init/serve/start/stop/restore)
    note: str = ""
    auth_action: str = ""          # dominant permission action, checked in handlers

    @property
    def dotted(self) -> str:
        return ".".join(self.path)

    def to_schema(self) -> dict:
        return {"cmd": self.cid, "command": self.dotted,
                "params": [p.to_schema() for p in self.params],
                "local": self.local}


def C(cid: str, path: str, params: list[Param], **kw) -> Command:
    return Command(cid=cid, path=tuple(path.split()), params=params, **kw)


COMMANDS: list[Command] = [
    # ---------------- inbox ----------------
    C("CMD-001", "inbox list", [
        P("view", kind="enum", choices=["all", "personal", "assigned", "available",
                                        "sent", "history", "declined"], default="all",
          help="all聚合personal/assigned/available并按task_id去重；history/declined仅显式视图"),
        P("agent", help="查看别人必须有授权，不改变调用身份"),
        P("port", help="限定目标或来源端口；按可见性过滤"),
        P("type", kind="enum", choices=["work", "notice", "message", "receipt"]),
        P("state", kind="enum", choices=TASK_STATES),
        P("urgency", kind="enum", choices=["urgent", "normal", "low"]),
        P("include_declined", kind="flag",
          help="available默认排除对当前spec_version的本人拒绝"),
        P("sort", kind="enum", choices=["priority", "created", "deadline"],
          default="priority",
          help="priority=紧急度降序、领取截止升序(null最后)、入队时间、ID"),
        LIMIT(), CURSOR(),
    ]),
    C("CMD-002", "inbox peek", [
        P("task", positional=True, required=True, help="仅能读有权访问的任务"),
        P("comments", kind="flag", help="包含最近50条可见评论及继续游标"),
        P("history", kind="flag", help="包含最近50条流转记录及继续游标"),
        P("mark_seen", kind="flag", help="显式记录当前主体已查看；仍不claim或结束"),
    ]),
    C("CMD-003", "inbox claim", [
        P("task", positional=True, required=True, repeatable=True,
          help="去重后按输入顺序逐件竞争 (1..100)"),
        P("spec_version", kind="int", min=1,
          help="仅单任务可用；任务要求已变则拒绝"),
    ]),
    C("CMD-004", "inbox decline", [
        P("task", positional=True, required=True, help="只支持当前可见 queued work"),
        REASON_REQ(),
    ]),
    C("CMD-005", "inbox tree", [
        P("task", positional=True, required=True, help="查询当前任务子树，需读权限"),
        P("depth", kind="int", default=4, min=0, max=20, help="0只返回本节点"),
        P("include_terminal", kind="bool", default=True, help="false仍保留必要祖先结构"),
        P("include_comments", kind="flag", help="每节点仅摘要，完整评论用peek"),
    ]),
    C("CMD-006", "inbox wait create", [
        P("task", positional=True, required=True, help="当前主体持有的非终态work"),
        P("on", kind="ref", repeatable=True, required=True,
          help="task:ID/operation:ID；每个依赖须有读/订阅结果权限，最多100个"),
        P("mode", kind="enum", choices=["all", "any"], default="all",
          help="结果包括done/fail/trash和succeeded/failed/cancelled"),
        DURATION("wait_for", help="等待期限；与 --until 互斥，不修改被等待任务的期限",
                 mutex="waitdl"),
        TIMEP("until", help="等待截止", mutex="waitdl"),
        P("checkpoint", kind="ref", repeatable=True, default=[], help="恢复资料入口"),
        P("replace", kind="flag", help="替换已有pending wait，旧记录置cancelled"),
        CLAIM(),
    ]),
    C("CMD-007", "inbox wait list", [
        P("task", help="按责任任务过滤"),
        P("state", kind="enum", choices=["pending", "ready", "timed_out", "cancelled"],
          default="pending"),
        LIMIT(), CURSOR(),
    ]),
    C("CMD-008", "inbox wait show", [
        P("wait", positional=True, required=True, help="归属任务必须可读"),
    ]),
    C("CMD-009", "inbox wait cancel", [
        P("wait", positional=True, required=True, help="当前责任人或有管理权限者"),
        REASON_REQ(),
    ]),
    # ---------------- emit ----------------
    C("CMD-010", "emit new", [
        P("root", kind="flag", help="独立根任务", mutex="lineage"),
        P("parent", help="显式血缘；必须有派生权限", mutex="lineage"),
        P("spec_file", kind="path", file_of="spec",
          help="TaskSpec JSON；与任务内容参数互斥，仍需root/parent", mutex="spec"),
        P("to", kind="target", required=True, help="agent:ID/port:ID-or-route", mutex="spec"),
        P("type", kind="enum", choices=["work", "notice", "message"], default="work",
          help="receipt只由系统生成", mutex="spec"),
        P("title", required=True, help="任务卡标题 1..256", mutex="spec"),
        P("summary", help="列表摘要 ≤2048", mutex="spec"),
        P("body", help="任务正文；work至少有body或非空data", mutex="body"),
        P("body_file", kind="path", file_of="body", help="客户端读取任务正文",
          mutex="body"),
        P("urgency", kind="enum", choices=["urgent", "normal", "low"], default="normal",
          help="仅排序与提醒策略", mutex="spec"),
        P("reply_to", help="关联正在回应的任务，不改变血缘", mutex="spec"),
        DATA(), DATA_FILE(),
        ATTACH(),
        P("callback", kind="target", default="none",
          help="none/self/agent:ID/port:ID；非Agent不能用self", mutex="spec"),
        TIMEP("claim_by", help="本轮领取截止", mutex="claimdl"),
        DURATION("claim_within", help="相对接收时间；none显式取消默认期限",
                 mutex="claimdl"),
        TIMEP("expires_at", help="业务有效期", mutex="expires"),
        DURATION("expires_in", help="正时长；none显式无有效期限", mutex="expires"),
    ]),
    C("CMD-011", "emit done", [
        P("task", positional=True, required=True,
          help="work需claim；个人非work可直接处理"),
        P("dispatch_file", kind="path", file_of="dispatch",
          help="TaskSpec数组1..100，parent自动为当前Task，全有或全无"),
        CLAIM(),
        TEXT(), TEXT_FILE(), DATA(), DATA_FILE(), ATTACH(),
    ]),
    C("CMD-012", "emit fail", [
        P("task", positional=True, required=True, help="失败表示本次处理停止"),
        P("code", default="FAILED", help="业务原因码，不覆盖系统错误码"),
        CLAIM(),
        REASON_REQ(),
        TEXT(), TEXT_FILE(), DATA(), DATA_FILE(), ATTACH(),
    ]),
    C("CMD-013", "emit trash", [
        P("task", positional=True, required=True, help="有结束此任务的权限"),
        P("instruction", kind="ref", help="引用使任务失效的上级指示，必须可读"),
        CLAIM(),
        REASON_REQ(),
    ]),
    C("CMD-014", "emit forward", [
        P("task", positional=True, required=True, help="当前持有的任务"),
        P("to", kind="target", required=True, help="校验目标、类型和调用者权限"),
        TIMEP("claim_by", help="新目标默认", mutex="claimdl"),
        DURATION("claim_within", help="新一轮领取期限", mutex="claimdl"),
        CLAIM(),
        REASON_REQ(),
        IF_VERSION(),
    ]),
    C("CMD-015", "emit comment", [
        P("task", positional=True, required=True, help="有读与评论权限"),
        P("mention", kind="ref", repeatable=True, default=[],
          help="与正文规范@解析结果合并去重"),
        P("revision_of", help="创建修订记录，不物理覆盖旧评论"),
        P("checkpoint", kind="ref", repeatable=True, default=[],
          help="最新引用进入恢复摘要"),
        TEXT(), TEXT_FILE(),
    ]),
    C("CMD-016", "emit batch", [
        P("file", kind="path", required=True, file_of="input",
          help="EmitBatch JSON；最多100操作"),
    ]),
    # ---------------- control system ----------------
    C("CMD-017", "control system init", [
        P("listen", default="127.0.0.1:19962", help="写初始system.json"),
        P("default_harness", help="注册名，可稍后配置"),
        P("owner_credential_out", help="私密文件保存凭据，stdout仅返回路径"),
        P("test_mode", kind="flag", help="创建隔离验收profile"),
    ], local=True),
    C("CMD-018", "control system serve", [
        P("listen", help="必须与home实例锁一致"),
        P("test_mode", kind="flag", help="仅已init为test的home有效"),
        P("log_level", kind="enum", choices=["debug", "info", "warning", "error"],
          default="info", help="始终脱敏"),
    ], local=True),
    C("CMD-019", "control system start", [
        P("listen", help="检查监听冲突"),
        P("test_mode", kind="flag", help="只允许test profile"),
    ], local=True),
    C("CMD-020", "control system stop", [
        REASON_REQ(),
    ]),
    C("CMD-021", "control system status", []),
    C("CMD-022", "control system config", [
        P("file", kind="path", file_of="input", help="SystemPolicy JSON patch；无=只读"),
        P("reason", help="更新时必填"),
        IF_VERSION(),
    ]),
    C("CMD-023", "control system log", [
        P("tail", kind="int", default=100, min=1, max=10000, help="有界读取"),
        P("level", kind="enum", choices=["debug", "info", "warning", "error"],
          help="最低级别"),
        TIMEP("since", help="时间筛选"),
    ]),
    C("CMD-024", "control system backup", [
        P("out", required=True, help="目标必须不存在或为空；不覆盖已有备份"),
        P("include_workspaces", kind="flag", help="包含受管工位"),
        P("include_credentials", kind="flag",
          help="默认排除；显式owner才可，产物必须按秘密处理"),
    ]),
    C("CMD-025", "control system restore", [
        P("from_dir", opt="--from", kind="path", required=True,
          help="校验manifest/schema/hash"),
        P("into", kind="path", required=True, help="新空目录；不允许原地覆盖"),
        P("with_credentials", kind="flag", help="仅备份明确包含且owner授权时"),
    ], local=True),
    # ---------------- control agent ----------------
    C("CMD-026", "control agent list", [
        P("query", help="搜名称/简介"),
        P("state", kind="enum", choices=STATE_LIFECYCLE),
        P("harness"),
        P("port", help="订阅关系筛选"),
        LIMIT(), CURSOR(),
    ]),
    C("CMD-027", "control agent show", [
        P("agent", positional=True, required=True, help="精确ID"),
        P("config", kind="flag", help="完整可读配置，凭证字段永不返回正文"),
        P("runtime", kind="flag", help="实例、生效版本、责任摘要与observability"),
    ]),
    C("CMD-028", "control agent create", [
        P("file", kind="path", file_of="input", help="AgentConfig JSON，缺字段按默认补全"),
        P("name", help="覆盖文件对应字段"),
        P("harness", help="注册名，可缺省"),
        P("model", help="宿主默认"),
        P("workspace", help="系统分配，不自动复制旧资料"),
        P("auto_start", kind="bool", default=True, help="收到待办时自动启动策略"),
        P("start", kind="flag", help="注册后请求启动，失败不回滚注册"),
        P("reason", default="create agent", help="审计原因"),
    ]),
    C("CMD-029", "control agent clone", [
        P("agent", positional=True, required=True, help="有读配置及创建权限"),
        P("name", help="新显示名"),
        P("workspace", help="新独立目录"),
        P("start", kind="flag"),
        P("reason", default="clone agent", help="审计原因"),
    ]),
    C("CMD-030", "control agent update", [
        P("agent", positional=True, required=True),
        P("file", kind="path", required=True, file_of="input",
          help="AgentConfig/patch JSON；默认合并补丁"),
        P("replace", kind="flag", help="完整配置替换；ID保持不变"),
        P("apply", kind="enum", choices=["next-send", "restart"], default="next-send",
          help="运行说明下次send更新；restart创建重启操作"),
        REASON_REQ(),
        IF_VERSION(),
    ]),
    C("CMD-031", "control agent start", [
        P("agent", positional=True, required=True, help="retired不能启动"),
        CALLBACK(),
    ]),
    C("CMD-032", "control agent wake", [
        P("agent", positional=True, required=True, help="paused不自动启用"),
        P("message", default="请查看inbox", help="运行信号，不能冒充新业务任务"),
    ]),
    C("CMD-033", "control agent send", [
        P("agent", positional=True, required=True, help="需agent.send，普通交流用emit"),
        P("mode", kind="enum", choices=["queue", "interrupt"], default="queue",
          help="queue由Hive排队；interrupt无原生支持须选择降级策略"),
        P("fallback", kind="enum", choices=["reject", "restart"], default="reject",
          help="仅interrupt有效；restart明确允许重启后送入指令"),
        TEXT(), TEXT_FILE(),
        REASON_REQ(),
        INSTANCE(),
        CALLBACK(),
    ]),
    C("CMD-034", "control agent stop", [
        P("agent", positional=True, required=True),
        GRACE(), FORCE(), REASON_REQ(), INSTANCE(), CALLBACK(),
    ]),
    C("CMD-035", "control agent restart", [
        P("agent", positional=True, required=True, help="身份与责任不变"),
        GRACE(), FORCE(), REASON_REQ(), INSTANCE(), CALLBACK(),
    ]),
    C("CMD-036", "control agent model", [
        P("agent", positional=True, required=True),
        P("model", help="适配器能识别的模型名", mutex="model"),
        P("default", kind="flag", help="清空显式模型，使用宿主默认", mutex="model"),
        P("apply", kind="enum", choices=["auto", "restart", "next-start"], default="auto",
          help="auto优先热切换，否则重启；paused只更新配置"),
        REASON_REQ(),
        IF_VERSION(),
    ]),
    C("CMD-037", "control agent retire", [
        P("agent", positional=True, required=True, help="稳定身份保留"),
        P("plan_file", kind="path", required=True, file_of="plan",
          help="RetirePlan JSON"),
        DRY_RUN(),
        REASON_REQ(),
        CALLBACK(),
    ]),
    # ---------------- control port ----------------
    C("CMD-038", "control port list", [
        P("query", help="查路由名/名称/说明"),
        P("handler", kind="enum", choices=["agents", "human", "external"]),
        P("state", kind="enum", choices=STATE_LIFECYCLE),
        LIMIT(), CURSOR(),
    ]),
    C("CMD-039", "control port show", [
        P("port", positional=True, required=True, help="port ID/route"),
        P("subscribers", kind="flag", help="仅返回有权发现的订阅者"),
    ]),
    C("CMD-040", "control port create", [
        P("route", required=True, help="长度1..128；可用斜杠分业务名"),
        P("file", kind="path", file_of="input",
          help="PortConfig JSON：描述/类型/schema/defaults等"),
        P("name", help="显示名覆盖"),
        P("handler", kind="enum", choices=["agents", "human", "external"],
          default="agents"),
        P("channel", kind="ref", repeatable=True, default=[],
          help="人工/外部时必填；human可多个；external绑定一个webhook"),
        P("reason", default="create port", help="审计原因"),
    ]),
    C("CMD-041", "control port update", [
        P("port", positional=True, required=True, help="路由名变更仍保持port_id"),
        P("file", kind="path", required=True, file_of="input",
          help="PortConfig patch JSON"),
        P("replace", kind="flag", help="完整替换可选配置"),
        REASON_REQ(),
        IF_VERSION(),
    ]),
    C("CMD-042", "control port pause", [
        P("port", positional=True, required=True, help="enabled可暂停"),
        REASON_REQ(),
    ]),
    C("CMD-043", "control port resume", [
        P("port", positional=True, required=True, help="paused转enabled"),
        REASON_REQ(),
    ]),
    C("CMD-044", "control port retire", [
        P("port", positional=True, required=True, help="停新投递"),
        P("plan_file", kind="path", required=True, file_of="plan",
          help="安排queued及claimed责任"),
        DRY_RUN(),
        REASON_REQ(),
        CALLBACK(),
    ]),
    # ---------------- control subscription ----------------
    C("CMD-045", "control subscription list", [
        P("agent", help="至少限定agent或port之一"),
        P("port"),
        P("enabled", kind="bool"),
        LIMIT(), CURSOR(),
    ]),
    C("CMD-046", "control subscription add", [
        P("agent", required=True), P("port", required=True, help="仅handler=agents可订阅"),
        REASON_REQ(),
    ]),
    C("CMD-047", "control subscription pause", [
        P("agent", required=True), P("port", required=True), REASON_REQ(),
    ]),
    C("CMD-048", "control subscription resume", [
        P("agent", required=True), P("port", required=True), REASON_REQ(),
    ]),
    C("CMD-049", "control subscription remove", [
        P("agent", required=True), P("port", required=True), REASON_REQ(),
    ]),
    # ---------------- control task ----------------
    C("CMD-050", "control task list", [
        P("source", help="来源筛选"),
        P("agent", help="当前owner或个人目标筛选"),
        P("port", help="当前目标端口"),
        P("root", help="整棵树内可见任务"),
        P("state", kind="enum", choices=TASK_STATES),
        P("type", kind="enum", choices=["work", "notice", "message", "receipt"]),
        TIMEP("since", help="创建时间起点"),
        LIMIT(), CURSOR(),
    ]),
    C("CMD-051", "control task show", [
        P("task", positional=True, required=True),
        P("history", kind="flag", help="流转、领取代次与审计摘要"),
    ]),
    C("CMD-052", "control task amend", [
        P("task", positional=True, required=True, help="非终态任务"),
        P("file", kind="path", required=True, file_of="input",
          help="TaskAmend patch：仅title/summary/body/data/attachments/urgency/"
               "claim_deadline/expires_at/callback"),
        REASON_REQ(),
        IF_VERSION(),
    ]),
    C("CMD-053", "control task reclaim", [
        P("task", positional=True, required=True, help="必须claimed"),
        P("to", kind="target", required=True, help="回收后的目标，不隐含丢弃"),
        P("stop_owner", kind="flag",
          help="明确请求暂停原Agent全部实例工作；记录影响范围"),
        REASON_REQ(),
        IF_VERSION(),
    ]),
    C("CMD-054", "control task forward", [
        P("task", positional=True, required=True, help="通常queued；claimed要用reclaim"),
        P("to", kind="target", required=True),
        REASON_REQ(),
        IF_VERSION(),
    ]),
    C("CMD-055", "control task trash", [
        P("task", positional=True, required=True, help="queued/claimed均可按授权处理"),
        P("cascade", kind="flag", help="显式逐项处理可管理子树；不是默认递归"),
        DRY_RUN(),
        REASON_REQ(),
        IF_VERSION(),
    ]),
    # ---------------- control permission ----------------
    C("CMD-056", "control permission list", [
        P("subject", required=True, help="agent:ID/producer:ID/human:ID等"),
        LIMIT(), CURSOR(),
    ]),
    C("CMD-057", "control permission grant", [
        P("file", kind="path", required=True, file_of="input",
          help="GrantSpec JSON"),
        TIMEP("expires_at", help="到期不再允许新请求"),
        REASON_REQ(),
    ]),
    C("CMD-058", "control permission revoke", [
        P("grant", positional=True, required=True, help="不能猜ID绕过管理范围"),
        REASON_REQ(),
    ]),
    # ---------------- control operation ----------------
    C("CMD-059", "control operation list", [
        P("state", kind="enum",
          choices=["accepted", "running", "succeeded", "failed", "cancelled"]),
        P("target", help="目标对象"),
        P("request", help="找回超时请求"),
        LIMIT(), CURSOR(),
    ]),
    C("CMD-060", "control operation show", [
        P("op", positional=True, required=True),
        P("wait_seconds", kind="int", default=0, min=0, max=30,
          help="有界等待，不是Agent业务wait"),
    ]),
    C("CMD-061", "control operation cancel", [
        P("op", positional=True, required=True, help="已终态不可改"),
        REASON_REQ(),
    ]),
    # ---------------- control event/audit/delivery ----------------
    C("CMD-062", "control event list", [
        P("entity", help="typed resource reference"),
        P("kind", help="如task.terminal/agent.started"),
        TIMEP("since"),
        LIMIT(), CURSOR(),
    ]),
    C("CMD-063", "control audit list", [
        P("actor"), P("target"), P("request"), TIMEP("since"), LIMIT(), CURSOR(),
    ]),
    C("CMD-064", "control delivery list", [
        P("state", kind="enum", choices=["pending", "sending", "sent", "retry", "dead"]),
        P("task", help="源任务或回执关联"),
        P("target"),
        LIMIT(), CURSOR(),
    ]),
    C("CMD-065", "control delivery retry", [
        P("delivery", positional=True, required=True,
          help="retry/dead状态，sent返回已完成"),
        REASON_REQ(),
    ]),
    # ---------------- control harness ----------------
    C("CMD-066", "control harness list", []),
    C("CMD-067", "control harness show", [
        P("name", positional=True, required=True, help="注册名"),
    ]),
    C("CMD-068", "control harness register", [
        P("file", kind="path", required=True, file_of="input", help="HarnessSpec JSON"),
        P("replace", kind="flag", help="同名存在时必须显式替换"),
        REASON_REQ(),
    ]),
    C("CMD-069", "control harness validate", [
        P("name", positional=True, required=True),
        P("smoke", kind="flag", help="显式执行五能力演练"),
        P("workspace", help="smoke时必填；不使用业务工位"),
        P("model", help="第一模型"),
        P("alternate_model", help="smoke时必填；验证可选择不同模型"),
    ]),
    C("CMD-070", "control harness unregister", [
        P("name", positional=True, required=True, help="不物理删除外部程序"),
        REASON_REQ(),
    ]),
    # ---------------- control producer ----------------
    C("CMD-071", "control producer list", [LIMIT(), CURSOR()]),
    C("CMD-072", "control producer show", [
        P("producer", positional=True, required=True, help="不返回token"),
    ]),
    C("CMD-073", "control producer create", [
        P("name", required=True, help="显示名"),
        P("kind", kind="enum", choices=["builtin", "external"], required=True),
        P("allow_target", kind="ref", repeatable=True, required=True,
          help="允许投递目标，至少一个"),
        P("credential_out", help="external时必填；stdout仅路径"),
        P("reason", default="create producer", help="审计原因"),
    ]),
    C("CMD-074", "control producer update", [
        P("producer", positional=True, required=True),
        P("file", kind="path", required=True, file_of="input",
          help="ProducerConfig patch"),
        REASON_REQ(),
        IF_VERSION(),
    ]),
    C("CMD-075", "control producer credential", [
        P("producer", positional=True, required=True),
        P("out", required=True, help="不打印秘密正文"),
        P("revoke_previous", kind="flag"),
        REASON_REQ(),
    ]),
    C("CMD-076", "control producer revoke", [
        P("producer", positional=True, required=True, help="已创建任务仍然有效"),
        REASON_REQ(),
    ]),
    # ---------------- control trigger ----------------
    C("CMD-077", "control trigger list", [
        P("enabled", kind="bool"), LIMIT(), CURSOR(),
    ]),
    C("CMD-078", "control trigger show", [
        P("trigger", positional=True, required=True),
    ]),
    C("CMD-079", "control trigger create", [
        P("name", required=True),
        P("kind", kind="enum", choices=["manual", "interval", "cron"], required=True),
        P("producer", required=True, help="enabled builtin主体"),
        P("task_file", kind="path", required=True, file_of="task",
          help="TaskSpec JSON；每次产生root Task"),
        DURATION("every", help="interval必填；与cron互斥", mutex="sched"),
        P("cron", help="five-field: 分时日月周，支持*、列表、范围、步长", mutex="sched"),
        P("timezone", default="UTC", help="UTC/±HH:MM固定偏移；其他值拒绝"),
        P("enabled", kind="bool", default=False, help="先注册再显式启用"),
        P("missed", kind="enum", choices=["skip", "once"], default="skip",
          help="错过多个触发点时跳过或合并补发一次"),
    ]),
    C("CMD-080", "control trigger update", [
        P("trigger", positional=True, required=True),
        P("file", kind="path", required=True, file_of="input",
          help="TriggerConfig patch；kind相关字段重新校验"),
        REASON_REQ(),
        IF_VERSION(),
    ]),
    C("CMD-081", "control trigger fire", [
        P("trigger", positional=True, required=True,
          help="manual或有权限的其他规则"),
        P("event_id", required=True, help="同producer/trigger内唯一，重放返回原Task"),
        P("data", kind="json", default={}, help="合并进模板data"),
    ]),
    C("CMD-082", "control trigger enable", [
        P("trigger", positional=True, required=True), REASON_REQ(),
    ]),
    C("CMD-083", "control trigger disable", [
        P("trigger", positional=True, required=True), REASON_REQ(),
    ]),
    # ---------------- control channel ----------------
    C("CMD-084", "control channel list", [LIMIT(), CURSOR()]),
    C("CMD-085", "control channel show", [
        P("channel", positional=True, required=True, help="隐藏凭证正文"),
    ]),
    C("CMD-086", "control channel register", [
        P("file", kind="path", required=True, file_of="input", help="ChannelSpec JSON"),
        P("replace", kind="flag"),
        REASON_REQ(),
    ]),
    C("CMD-087", "control channel update", [
        P("channel", positional=True, required=True),
        P("file", kind="path", required=True, file_of="input"),
        REASON_REQ(),
        IF_VERSION(),
    ]),
    C("CMD-088", "control channel disable", [
        P("channel", positional=True, required=True, help="保留待投递"),
        REASON_REQ(),
    ]),
    # ---------------- control human ----------------
    C("CMD-089", "control human list", [
        P("port", help="human port ID/route；handler必须human"),
        P("state", kind="enum", choices=TASK_STATES, default="queued"),
        LIMIT(), CURSOR(),
    ]),
    C("CMD-090", "control human answer", [
        P("task", positional=True, required=True, help="指向人工端口的work"),
        P("spec_version", kind="int", required=True, min=1, help="防旧页面答新问题"),
        TEXT(), TEXT_FILE(), DATA(), DATA_FILE(), ATTACH(),
    ]),
    C("CMD-091", "control human fail", [
        P("task", positional=True, required=True),
        P("spec_version", kind="int", required=True, min=1),
        REASON_REQ(),
        TEXT(), TEXT_FILE(), DATA(), DATA_FILE(),
    ]),
    C("CMD-092", "control human trash", [
        P("task", positional=True, required=True),
        P("spec_version", kind="int", required=True, min=1),
        REASON_REQ(),
        TEXT(), TEXT_FILE(), DATA(), DATA_FILE(),
    ]),
    # ---------------- control test (test profile only) ----------------
    C("CMD-093", "control test seed", [
        P("scenario", required=True, help="本次case前缀；重复seed刷新凭据不重置数据"),
        P("out", required=True, help="只写ID/凭据文件位置，不写秘密正文"),
    ]),
    C("CMD-094", "control test clock advance", [
        DURATION("by", allow_none=False, required=True, help="推进隔离时钟"),
    ]),
    C("CMD-095", "control test fault set", [
        P("point", kind="enum", required=True, choices=[
            "tx_before_commit", "rpc_response_drop", "outbox_after_commit",
            "notification_drop", "harness_start_after_launch", "harness_stop_unknown",
            "config_after_publish", "event_duplicate", "event_reorder",
            "operation_before_execute", "idem_kill_in_tx",
            "idem_kill_after_reserve"]),
        P("mode", kind="enum", choices=["once", "always"], default="once"),
        P("value", kind="json", default={}),
    ]),
    C("CMD-096", "control test fault clear", [
        P("point", kind="enum", choices=[
            "tx_before_commit", "rpc_response_drop", "outbox_after_commit",
            "notification_drop", "harness_start_after_launch", "harness_stop_unknown",
            "config_after_publish", "event_duplicate", "event_reorder",
            "operation_before_execute", "idem_kill_in_tx",
            "idem_kill_after_reserve"]),
    ]),
    C("CMD-097", "control test harness", [
        P("agent", positional=True, required=True, help="仅fixture adapter"),
        P("action", kind="enum", required=True, choices=[
            "idle", "busy", "crash", "stop-confirm", "inspect-off", "events-off",
            "model-hot-off"]),
        P("value", kind="json", default={}),
    ]),
]

BY_PATH: dict[str, Command] = {c.dotted: c for c in COMMANDS}
BY_CID: dict[str, Command] = {c.cid: c for c in COMMANDS}

GLOBAL_PARAMS = [
    P("home", kind="path", help="数据根；不是用户home（默认HIVE_HOME或项目.hive）"),
    P("endpoint", help="在线服务地址（默认HIVE_URL或127.0.0.1:19962）"),
    P("credential_file", kind="path", help="本地凭据文件（默认HIVE_CREDENTIAL_FILE）"),
    P("format", kind="enum", choices=["json", "text"], default="json"),
    P("request_id", help="变更的幂等键；查询仅作关联标识"),
    P("timeout", kind="int", default=15, help="网络请求超时秒数；不改变业务期限"),
]

# file params: canonical content key -> param name mapping for documentation
FILE_PARAM_MAP = {p.name: p.file_of for c in COMMANDS for p in c.params if p.file_of}


def validate_arguments(cmd: Command, args: dict) -> dict:
    """Validate and normalize arguments server-side. Raises HiveError on bad input.

    File-style params (text_file etc.) are rejected here: content must be inlined
    by the client (CLI reads files locally); the server never reads caller paths.
    """
    known = {p.name for p in cmd.params}
    file_of_keys = {p.file_of: p for p in cmd.params if p.file_of}
    out: dict[str, Any] = {}
    for key, value in (args or {}).items():
        if key in file_of_keys:
            continue  # canonical content key of an inlined file param
        if key not in known:
            raise HiveError("INVALID_ARGUMENT",
                            f"未知参数 {key!r}（命令 {cmd.dotted} 不接受未定义字段）",
                            {"command": cmd.dotted, "param": key})
    for p in cmd.params:
        if p.file_of:
            if p.name in args:
                raise HiveError(
                    "INVALID_ARGUMENT",
                    f"参数 {p.opt} 需由客户端读取文件后以 {p.file_of} 内容提交；"
                    f"服务端不读取调用者路径")
            continue
        value = args.get(p.name)
        present = p.name in args and value is not None
        if not present:
            if p.required:
                if p.name == "title" and args.get("spec") is not None:
                    continue  # title comes from TaskSpec
                if p.name == "to" and args.get("spec") is not None:
                    continue
                raise HiveError("REQUIRED_ARGUMENT", f"缺少必填参数 {p.opt or p.name}",
                                {"command": cmd.dotted, "param": p.name})
            out[p.name] = p.default
            continue
        if p.repeatable:
            if isinstance(value, str):
                value = [value]
            if not isinstance(value, list):
                raise HiveError("INVALID_ARGUMENT", f"参数 {p.name} 需为可重复列表")
            value = list(dict.fromkeys(value)) if p.kind == "ref" else list(value)
            if p.min is not None and len(value) < p.min:
                raise HiveError("REQUIRED_ARGUMENT", f"参数 {p.name} 至少 {p.min} 项")
        if p.kind == "enum" and p.choices and (not p.repeatable):
            if value not in p.choices:
                raise HiveError("INVALID_ARGUMENT",
                                f"参数 {p.name}={value!r} 不在允许值 {p.choices}",
                                {"param": p.name, "choices": p.choices})
        if p.kind == "int" and not p.repeatable:
            try:
                iv = int(value)
            except (TypeError, ValueError):
                raise HiveError("INVALID_ARGUMENT", f"参数 {p.name} 需为整数")
            if p.min is not None and iv < p.min:
                raise HiveError("INVALID_ARGUMENT",
                                f"参数 {p.name}={iv} 低于下限 {p.min}")
            if p.max is not None and iv > p.max:
                raise HiveError("INVALID_ARGUMENT",
                                f"参数 {p.name}={iv} 超过上限 {p.max}")
            value = iv
        if p.kind == "bool" and not p.repeatable and p.name in args:
            if isinstance(value, str):
                if value.lower() not in ("true", "false"):
                    raise HiveError("INVALID_ARGUMENT",
                                    f"参数 {p.name} 需为 true/false")
                value = value.lower() == "true"
            value = bool(value)
        if p.kind == "json" and not p.repeatable:
            if isinstance(value, str):
                try:
                    import json
                    value = json.loads(value)
                except ValueError:
                    raise HiveError("INVALID_JSON", f"参数 {p.name} 不是合法JSON")
        if p.name == "reason" and p.required and (not isinstance(value, str)
                                                   or not value.strip()
                                                   or len(value) > 4096):
            raise HiveError("INVALID_ARGUMENT", "reason 必须为非空文本且≤4096字符")
        if p.name == "title" and not (isinstance(value, str) and 1 <= len(value) <= 256):
            raise HiveError("INVALID_ARGUMENT", "title 必须 1..256 字符")
        if p.name == "summary" and value and len(str(value)) > 2048:
            raise HiveError("INVALID_ARGUMENT", "summary ≤2048 字符")
        out[p.name] = value
    # mutex groups: members conflict pairwise, except group 'spec' whose
    # members only conflict with the inlined spec itself
    groups: dict[str, list[str]] = {}
    for p in cmd.params:
        if p.mutex and p.mutex != "spec":
            groups.setdefault(p.mutex, []).append(p.name)
    for group, names in groups.items():
        provided = [n for n in names
                    if args.get(n) is not None and n in args]
        if len(provided) > 1:
            opts = ", ".join(BY_PATH[cmd.dotted] and
                             (next(p.opt for p in cmd.params if p.name == n))
                             for n in provided)
            raise HiveError("MUTUALLY_EXCLUSIVE",
                            f"互斥参数不能同时提供: {opts}",
                            {"command": cmd.dotted, "params": provided})
    # spec-file conflicts with every content param
    if "spec" in args and args.get("spec") is not None:
        content = [p.name for p in cmd.params if p.mutex == "spec"
                   and args.get(p.name) is not None]
        if content:
            raise HiveError("MUTUALLY_EXCLUSIVE",
                            f"--spec-file 与任务内容参数互斥: {content}",
                            {"command": cmd.dotted, "params": content})
    # pass through inlined file content under its canonical key — only for
    # keys that are not regular params (data/text/task etc. are regular)
    for key, param in file_of_keys.items():
        if key in args and key not in known:
            out[key] = args[key]
    return out


def schema_export(test_mode: bool = False) -> dict:
    cmds = [c.to_schema() for c in COMMANDS if test_mode
            or c.path[1] != "test"]
    return {
        "protocol_version": "1",
        "commands": cmds,
        "global_params": [p.to_schema() for p in GLOBAL_PARAMS],
        "file_params": FILE_PARAM_MAP,
    }
