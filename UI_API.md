# Hive 指挥台同源 UI 接口说明

版本：webui v2（含会话舱）· 2026-10-01 · 与 hive_core/webui.py 一致

## 0. 认证与总约定

- 全部接口同源：`http://127.0.0.1:19962/ui/api/...`
- 登录：`POST /ui/api/session` `{token}` → 服务端校验后签发
  `Set-Cookie: hive_ui=<sid>; Path=/ui; HttpOnly; SameSite=Strict`。
  token 只用于换取会话，不写入浏览器存储/URL。`POST /ui/api/logout` 销毁。
- 变更请求必须带自定义头 `X-Requested-With: hive-ui`（CSRF 防线）；
  浏览器跨站表单无法伪造该头。
- 幂等：所有变更接受 `request_id`；同一 subject 同一 request_id 重放返回首次结果，
  不同内容同 ID 拒绝（IDEMPOTENCY_CONFLICT）。前端随草稿固定 request_id。
- 错误信封：`{"ok": false, "error": {code, message, details, retryable}}`。
  HTTP 200 ≠ 业务完成；`accepted_only`/`operation_id` 表示受理，需轮询 operation。
- Agent 凭据不能登录指挥台（FORBIDDEN）；会话查看/发送/取消按
  `conversation.read/send/cancel` 于 `agent:<id>` 授权，owner/system 全通。

## 1. 星图与项目

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /ui/api/overview?since= | 聚合快照：projects/agents(含 runtime 摘要+实际模型)/ports/subscriptions/flows(在途+15min 内终态)/人工计数/事件增量。分区降级（缺权限的区返回 errors 说明） |
| GET | /ui/api/projects | 注册项目列表（execution_root 规范化分组） |
| GET | /ui/api/project/:key/info | 项目文档（TASK_CONTRACT/BRIEF/TASTE…） |
| GET | /ui/api/project/:key/assets?path= | assets 目录列表（containment） |
| GET | /ui/api/project/:key/file?path=&download= | 项目内文件受权预览/下载（Range） |
| GET | /ui/api/agent/:id | agent.show(config+runtime)+相关任务+等待+实际模型落点 |
| GET | /ui/api/task/:id?mark_seen=1 | task.show(history)+评论+上下文任务+已读登记 |

## 2. 人工通道（裁决）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /ui/api/human/inbox?state=queued\|claimed\|done\|fail\|trash | 人工队列水合列表 |
| POST | /ui/api/human/answer | `{task, spec_version, text?, data?, attachments?, request_id}`；按 `task.data.ui.fields` 服务端复验必填/范围/步长/选项集合。spec_version 不符 → 409 TASK_SPEC_CHANGED（草稿保留） |
| POST | /ui/api/human/fail | `{task, spec_version, reason, request_id}` |
| POST | /ui/api/comment | `{task, text, mentions?, revision_of?, attachments?, request_id}` → emit.comment；**评论不结算任务** |
| POST | /ui/api/upload | 原始字节（?name=&mime=）→ `<home>/webfiles/<file_id>/`，返回 `{ref:"hive-file:…", path}` |
| GET | /ui/api/file/:id | 受权下载/预览，支持 HTTP Range（206） |
| GET | /ui/api/task-file?task=&uri= | 任务附件受权访问：uri 必须属于该任务（attachments/result.artifacts/ui.blocks/checkpoint_refs）且在受管目录内 |

## 3. 会话舱（conversation）

绑定：`conv_bindings`（agent_id+instance_id+generation→session_id，唯一）。
实例更换后携带旧 instance/generation 的写入被 STALE_INSTANCE 拒绝。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /ui/api/agent/:id/conversation | `{instance, binding, bridge, bridge_connected, capabilities, active_input, pending_permission, tail:{messages,turns,current_turn}}`。bridge=fixture 时 UI 显示「演示模式」 |
| GET | …/conversation/history?after=&limit= | 事件分页（≤200/页）；`gap_before:true` 表示请求游标已超出保留范围，需触发快照恢复 |
| GET | …/conversation/stream?after= | SSE。事件帧 `id: <cursor>` + `event: <type>`；15s 心跳；断线用 `?after=<cursor>` 或 Last-Event-ID 恢复。只读通道，关闭不影响 ACP/实例 |
| POST | …/conversation/input | `{text, mode:queue\|interrupt, attachments?, instance_id?, generation?, request_id}` → 持久入队。同 request_id 重放返回既有输入；同 ID 不同内容拒绝 |
| POST | …/conversation/cancel | `{instance_id?, generation?, request_id}` → 取消当前活动轮次（取消中/已取消分态；不自动改业务 Task） |
| GET | …/conversation/inputs | 输入状态与待发送列表 |

输入状态机：`queued → dispatched → running → completed | cancelled | failed | outcome_unknown`。
`dispatched`=已向桥接发送（不等于模型已处理）；`running` 需上游事实确认；
桥断开时未决输入一律 `outcome_unknown`（重连先 `_zsession/get_input_state` 对账，不盲目重放）。

事件类型（conv_events.type）：`user.message / agent.delta / agent.message.final /
agent.thought / tool.call / turn.started / turn.completed / turn.failed /
turn.cancelled / permission.request / system.note`。工具卡按 `tool_call_id` 归并更新；
delta 按 `row_id` 就地合并；`replay=true` 标记重放。

## 4. 桥接（bridge）

- `fixture`：协议夹具（test/dev）。`source:'fixture'`，UI 明确标注。 zero 上游依赖。
- `acp`：`hive_core/convbridge.py AcpBridge` 经 stdio ndjson JSON-RPC 连接
  `node …/plugins/session-manager/bin/zsession.mjs acp --stdio`。initialize 协商能力；
  `_meta.zsession` 携带 requestId/inputId/agentId/instanceId/generation；
  session/request_permission 反向请求登记为待决权限卡。
  启用方式：daemon 启动时设置 `HIVE_ACP_ADAPTER` 环境变量为该命令行（缺省不启用，
  自动回退 fixture 并在 UI 标注「上游未就绪」）。

## 5. 与既有命令网关的关系

`/ui/api/command` 白名单转发 41 条只读/受控命令（见 webui.py UI_COMMAND_WHITELIST）。
control test *、system 管理类、permission grant/revoke 等不在白名单。
聊天不生成 work：发正式任务走 emit.new（「发任务」按钮打开既有表单）。

## 6. 第三轮修订（SDEF 修复后）

- **有效表单 schema 单一真源**：`GET /ui/api/task/:id` 返回 `eval_fields`
  （显式 ui.fields 或双评价兜底 schema，服务端权威）+ `eval_schema_version`；
  前端渲染消费同一份。POST /ui/api/human/answer 对两种答复表示
  （data.answers.X / 顶层 X）归一为唯一规范值后按同一 schema 服务端复验：
  冲突/越界/类型/步长/选项/必填违规 → 400 field_errors，不改任务终态；
  合法结果经 normalize 后入账（未评分=null 语义保持）。
- **turn.started 事实驱动**：由上游 input_state=started 通知产生，DB 级
  幂等（event_id=turn-started-{input_id}）；busy 重试不产生重复 started。
- **writerToken**：每 agent+generation 高熵凭据（服务端文件保存，不下发），
  ensure/load 与 prompt 按合同携带；运行时启用 HIVE_ACP_WRITER_KEY=1
  （当前默认关：上游 adapter 丢弃写凭据头 acp.mjs:50，绑定 key 会阻断
  adapter 自身写入——上游修复后开启）。弱模式 ensure 前自动释放本方
  残留 claim，防旧凭据锁死。
- **登录竞态**：认证请求代次守卫 + enterApp 幂等 + auth-lost 双确认；
  HttpOnly cookie 语义不变。

## 7. 第四轮修订（TDEF 修复后）

- **答复表示归一（显式与兜底同规则）**：归一字段集 = effective_fields
  声明的全部字段 ID（显式表单的 name/rating/date 等顶层表示同样参与，
  不再仅双评白名单）；同键两处同值合并、异值 400 冲突。
- **结构化结果白名单**：result 只含 schema 声明字段 + 契约键
  （explicit_autonomy_grant）+ 跟踪 metadata（video_notes/image_notes/
  ui_attachments）；未声明键（grant/authorize/_verified 等）不回填。
  普通自由 data 任务不受影响。显式非双评表单保持 {answers:{…}} 包装。
- **强 writer 模式**：HIVE_ACP_WRITER_KEY=1 启用（上游四修后 adapter
  正确转发 X-ZS-Writer-Key）。每 agent+generation 高熵 key（服务端文件，
  Windows 无 POSIX 0600——实际保护为 NTFS ACL+本机边界）；claim 绑定
  sha256 核实；无 key/错 key 409，owner 直通保留；重启同代际接续。
  弱模式（默认）ensure 前自动释放本方残留 claim（兼容路径，强模式不触发）。
- **busy 合同**：-32011 → 同 requestId 回队（10s 退避）；新合同
  retryable_busy 非终态，不触发派生 ID；不假 turn.started、不自动 interrupt。

## 8. 第五轮修订（FDEF 修复后）

- **unknown 自动收口（FDEF-01）**：周期对账（30s，桥活+有 unknown 时）三层：
  ① 上游登记终态按事实收口；② started/unknown+会话空闲 → 原生归属对账
  （history 精确匹配本输入全文 user 回显 + 其后 assistant 终稿 → completed，
  证据 attribution/registry_state/native_input_id）；③ 无强证据保持 unknown。
  会话 working 时不收口（合法长轮不受影响）。settle 事件 DB 幂等。
- **人工对账兜底**：`POST /ui/api/agent/:id/conversation/reconcile`
  `{action:"recheck"|"confirm", input_id, request_id, outcome?, native_ref?, reason?}`。
  recheck=只读重试（conversation.read）；confirm=人工收口（仅 owner/system，
  必须 outcome+native_ref+reason，audit_log 记录，非 unknown 幂等返回 already）。
  观察者只读不可改账；聊天/评论不改账。
- **会话面板状态（FDEF-02）**：`GET …/conversation` 新增
  `upstream: {bridge_alive, unknown_inputs}`（网页/SSE 与上游桥状态分离）；
  前端增量归并（不清空 Pane，保留草稿/滚动/折叠态）+ 上游状态条 +
  重新对账按钮。未知输入在队列条显示「未知 + 对账」入口。
