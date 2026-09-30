# 给 ZSession 插件施工 Agent 的提示词

你负责升级本地 ZCode 的 ZSession 插件，为 Hive 提供可以实际对话、实时接收事件、取消轮次、恢复连接的标准 ACP 接口。请直接读代码、实现、测试并完成本地联调准备，最终交付可运行实现和报告。

另一位施工 Agent 负责 Hive 后端适配、Hive 前端会话面板和视觉升级。你的工作边界是 ZSession 项目，不修改 Hive 实现。两边通过本文中的接口约定协作。

## 1. 项目位置与阅读顺序

ZSession 项目根目录：

    D:\agent_workspace\projects\zcode-session-manager

插件源码：

    D:\agent_workspace\projects\zcode-session-manager\plugins\session-manager

现有主要文件：

- README.md
- docs/HANDOVER.md
- plugins/session-manager/docs/API.md
- plugins/session-manager/docs/EVENTS.md
- plugins/session-manager/mcp/lib/manager.mjs
- plugins/session-manager/mcp/lib/appserver.mjs
- plugins/session-manager/mcp/lib/ws-client.mjs
- plugins/session-manager/mcp/lib/http.mjs
- plugins/session-manager/mcp/lib/events.mjs
- plugins/session-manager/mcp/daemon.mjs
- plugins/session-manager/bin/zsession.mjs

先读取适用的 AGENTS.md，检查 Git 状态，保护其他 Agent 已有改动。历史文档和当前代码有差异时，以当前代码和实际探测为准，更新相关文档。

Hive 消费方位于：

    D:\agent_workspace\valuable-projects\hive

可只读了解：

- hive_core/harness.py 中的 ZSessionAdapter。
- hive_core/runtime_ops.py 中的实例生命周期。
- FRONTEND_ACP_COSMOS_PROMPT.md 中的消费者要求。

凭据只由现有配置和环境变量读取，不把 token、密钥或完整认证配置写进报告、协议抓包、提示词、提交或示例。

## 2. 交付目标

用户在 Hive 星图中点开某个 Agent，能进入这个 Agent 当前运行实例对应的 ZCode 会话，看到真实的增量回答、工具活动和结束状态，并继续发送消息。

这些功能必须操作同一个受管 ZSession 会话。订阅输出、刷新网页、查看历史不应复制会话或启动第二个 Agent。

当前 ZSession 使用 ZCode 自有 app-server 协议或 WebSocket RPC，并不自动等于标准 ACP。请实现明确的 ACP 适配层：外侧遵守 ACP，内侧复用已有 ZSession 单宿主与 ZCode 事件源。准确标注架构为“ZSession ACP adapter backed by ZCode”，不要把 ZCode 原生协议改个名字当 ACP。

已有 HTTP、CLI、MCP 能力保持可用。增强能力不能成为所有 Hive Harness 的新接入门槛。

## 3. 两边共同的架构约定

链路：

    Hive 浏览器
        ↕ 同源 HTTP + SSE
    Hive 后端会话网关 / ACP client
        ↕ ACP JSON-RPC 2.0，stdio
    ZSession ACP adapter
        ↕ ZSession daemon 与既有 ZCode 单宿主
    当前 ZCode 会话

浏览器不持有 ZSession 管理凭据，也不直连 ZCode 内部 RPC。

ACP adapter 应连接或复用现有 daemon，不额外启动另一个独立 ZCode app-server 宿主。保持项目现有的单宿主架构。

身份各司其职：

- agent_id：Hive 的稳定 Agent 身份。
- instance_id / generation：Hive 当前有效实例及代际。
- sessionId：ZSession 中的真实会话。
- turnId：真实会话轮次；没有原生轮次 ID 时，使用可追溯的桥接 ID并说明来源。
- input_id / request_id：Hive 输入与幂等重试标识。

推荐 ACP sessionId 直接对应真实 ZSession sessionId；如必须映射，持久化映射并明确提供真实 ID。禁止靠名称或根目录猜会话。

## 4. 固定的 ACP 接入入口

新增 CLI 子命令：

    node plugins/session-manager/bin/zsession.mjs acp --stdio

现有 mycli 透传应能运行：

    mycli zsession native acp --stdio

参数要求：

- acp：启动 ACP stdio adapter。
- --stdio：启用 ACP 的标准输入输出传输。
- --endpoint <url>：可选；使用指定 ZSession daemon 地址，否则沿用既有配置。
- --token-env <name>：可选；从指定环境变量读取认证信息，否则沿用已有安全配置读取方式。
- --help：说明用法、依赖、如何配置 Hive；帮助文本不得输出任何凭据。

禁止要求把 token 直接放在命令行参数里。

stdout 只能输出合法 ACP JSON-RPC 帧；日志走 stderr。正确处理 UTF-8、中文、多帧合并、跨分片帧和合理的帧大小限制。stdin 中一条长时间运行的 session/prompt 不能阻塞取消通知、其他会话消息或 Agent 反向权限请求的处理。

进程退出或 stdin 关闭默认只断开桥接连接，不终止所有受管会话。终止会话与退出客户端是不同操作。

## 5. ACP 协议能力

实施前对照官方当前 v1 文档与 schema，优先使用官方 SDK或 schema校验；依赖选择保持项目可运行，不凭记忆虚构字段或方法。

官方依据：

- https://agentclientprotocol.com/protocol/v1/overview
- https://agentclientprotocol.com/protocol/v1/initialization
- https://agentclientprotocol.com/protocol/v1/session-setup
- https://agentclientprotocol.com/protocol/v1/prompt-turn
- https://agentclientprotocol.com/protocol/v1/transports
- https://agentclientprotocol.com/protocol/v1/content
- https://agentclientprotocol.com/protocol/v1/tool-calls

实现要求：

1. initialize：协商协议版本，返回真实 agentInfo 和实际能力。
2. authenticate：如果 adapter 的认证方式需要此流程，按协议提供；本机配置已有认证时也应正确说明，不制造多余登录流程。
3. session/new：在指定绝对 cwd 创建真实 ZSession 会话，返回 sessionId。创建失败不得返回虚构 ID。
4. session/prompt：将 ContentBlock 输入交给真实会话，持续发送更新；对应请求在本轮结束后返回真实 stopReason，不能刚 accepted 就返回 end_turn。
5. session/update：转换原生事件，保留可用的消息、工具与轮次关联。
6. session/cancel：取消当前活动轮次；取消确认以原生结果为依据，不能只收到请求就宣布取消完成。
7. session/load：本次重点实现已受管会话的重新接入和可用历史回放。若存在无法支持的会话类型，明确拒绝该类型，不另开新会话冒充恢复。
8. session/request_permission：在确实需要交互授权时桥接，能将选择返回原生待决请求。
9. 模型与模式：通过实际支持的 ACP 配置/模式接口提供；现有 model / mode HTTP 操作可作为兼容入口。报告期望值、确认值和生效时机，不冒充底层兜底路由的实际模型。

若 session/load 尚不满足协议规定的历史回放行为，不得提前宣告 loadSession=true。先完善实现；无法完善的具体限制要保留在报告中，不能用假历史补齐。

## 6. 约定的扩展元数据与观测能力

标准 ACP payload 保持标准结构。Hive 附加信息放在 _meta.zsession 中，作为文档化扩展：

    requestId
    inputId
    agentId
    instanceId
    generation
    source
    taskId（可选）

原生 ACP 请求 ID仅用于当前连接的请求响应匹配；重连后的幂等去重使用 requestId，不把二者混为一谈。

请声明并实现一个小而明确的扩展命名空间，方法名以 _zsession/ 开头：

- _zsession/get_session_state：只读查询真实 sessionId、当前轮次、忙闲或未知状态、模型/模式确认值、连接状态及可用能力。
- _zsession/get_input_state：按 sessionId + requestId 查询某条输入是否已接收、开始、完成、取消、拒绝或结果未知，并返回已知轮次与结束结果。

这两个查询用于桥接断线后的对账，不能触发新一轮模型调用。

输入状态变化可使用 _zsession/input_state 通知；原生事件中的 seq / epoch / inputId / turnId 等可通过 _meta.zsession 携带。能力名称、字段、枚举和错误需在 ACP_INTEGRATION.md 固化，并提供合法 JSON 示例。

这些是 ZSession 扩展，不声称属于 ACP 标准。Hive 消费者必须在 initialize 后检查声明。

客户端未提供 requestId 时，标准 ACP 对话仍可正常运行；Hive 生产链路必须提供 requestId，以支持可靠重试。

## 7. 真实流式数据

请从原生会话订阅链获得增量事件。重点检查现有 manager 的事件处理是否只保留 started/completed/failed，而丢掉了对话和工具增量；检查 WebSocket RPC 的 V4 会话帧和 stdio session/event 的实际结构。

至少实现实际存在的：

- 用户消息与 Agent 文本增量。
- 工具调用创建、状态变化和结果。
- 轮次开始与结束。
- 取消确认、原生错误与连接丢失。

计划、公开思考片段、usage、文件变化、命令、模型/模式变化按原生能力提供。没有的内容报告未支持，不自行编造。

重要规则：

- 不从最终全文拆成若干字符，制造“实时输出”。
- 不解析终端画面或日志来假装 ACP 工具调用。
- 不把轮询完整消息称为原生增量。
- 明确区分 delta、snapshot、历史 replay、replace 和最终正文，防止重复追加。
- 工具调用用稳定 toolCallId 关联更新；消息使用原生 messageId，或可重复计算的适配 ID。
- 重连补发时提供可去重依据。原生序号重置后不能与旧事件混在同一个未区分的序列里。
- stderr 的诊断日志不作为 Agent 回答。
- 会话存储保留真实内容，但审计和诊断继续脱敏；读取历史遵守会话权限。

## 8. 调度、幂等和队列归属

Hive 管理的会话，正常工作输入由 Hive 的持久输入队列统一调度。Hive 每个会话同时最多发出一个活动 session/prompt；下一条在上一轮结束后发送。

ZSession 负责执行、真实事件和输入对账，不再暗中维护一份会自动把同一条 Hive 输入重复送达的队列。

ZSession 原有未被 Hive 管理的会话继续保留原生 HTTP/CLI/MCP 队列能力。对已由 Hive 管理的会话，其他写入口必须经过同一受管写入约束，或者明确返回会话受管/忙碌冲突；只读订阅可以并存。请在接入文档写清如何登记受管关系、如何释放以及如何恢复。不要让插件自动化或其他客户端绕过约束启动并行轮次。

使用 requestId 对 session/prompt 的业务输入去重。相同 ID相同内容返回既有状态；相同 ID不同内容拒绝。断线后已确认执行的输入不可重发；结果未知时先查询，不盲目重新执行。

busy、已接收、开始执行、完成必须区分。接收成功不等于模型已处理。

queue 不能在超时后自动变成 interrupt。当前 Hive 适配器存在等待约 20秒后切换 interrupt 的代码，新接入链路必须移除该行为。

真正的“中断并发送”由 Hive 执行：

    标记本轮取消中
    → session/cancel
    → 确认本轮结束或取消
    → session/prompt 发送新内容

正常情况下保留同一个 sessionId。仅停止本轮不归档、不 kill、不创建新会话。

队列等待超时、连接断线和取消都不能自行改变 Hive Task 的 done/fail/trash 状态。

## 9. 运行与权限

项目执行根目录：

    D:\agent_workspace\valuable-projects\video-lab

这是接入示例，不是插件中硬编码的唯一工作区。ACP session/new 的 cwd 由消费者传入绝对路径。

Hive Agent 工位、项目执行根目录和 Hive Home 是不同概念。插件不能把 cwd 擅自切换到某个 Agent 工位。

模型和 yolo 由 Hive 配置决定。先设置确认模型/模式，再发送启动上下文；创建会话不得抢先注入另一个默认人格或消耗一轮错误模型。兼容原有 spawn bootstrap 行为，但 ACP new 路径需可以创建后完成配置再开始首轮。

yolo 下依照现有授权策略自动处理工具权限，保持可观察的真实记录；非 yolo 的权限请求转交 ACP client。工具权限回答不是 Hive 人类审批任务的审批结果。

权限待决、取消、断开连接、超时都要有明确的处置，不能无限挂起或 silently allow。

禁止为完成联调执行“杀全部 ZCode.exe”“重启所有宿主”或全局清空会话。使用独立测试会话，保护用户其他正在运行的 Agent。

## 10. 实施顺序

1. 阅读当前架构，确认活动连接采用哪种模式；不要因为旧文档写了 stdio 就恢复已经淘汰的双宿主架构。
2. 用独立测试会话记录脱敏后的原生事件结构，定位增量消息、工具事件、结束和取消的真实来源。
3. 实现共享事件投影、标识和对账记录。
4. 实现 ACP stdio adapter，先跑通 initialize / new / prompt / update / cancel。
5. 完善已受管会话的 load / replay 与幂等扩展。
6. 接上模型、模式、权限及受管写入约束。
7. 回归既有 HTTP / CLI / MCP 行为，更新文档与示例。
8. 产出接口文档和真实验证报告，给 Hive 消费方可直接运行的命令。

允许过程中先建立接口文档和协议测试夹具，但不可将 fixture 通过当成真实 ZCode 联调完成。

## 11. 验收用例与预期

每项记录具体命令、结果和证据；实测与仅单测分开。

| 场景 | 预期 |
| --- | --- |
| 启动 adapter 并 initialize | stdout 全部可解析；版本与能力正确 |
| new 指定 cwd、模型、模式 | 返回真实会话；首轮设置正确；无额外宿主 |
| 连续两轮中文对话 | 第二轮保留第一轮上下文，sessionId 不变 |
| 流式回答 | 本轮结束前收到真实增量，不是完成后拆字 |
| 至少一次真实工具执行 | 同一 toolCallId 的开始/更新/结果正确关联 |
| 活动轮次 cancel | 本轮取消被原生确认；原会话仍能继续对话 |
| 等待中的输入 | 不因等待时间长自动中断正在执行的工作 |
| 同 requestId 重试 | 只执行一次；异内容冲突被拒绝 |
| 断开桥接并重新接入 | 不 kill 上游；历史可回放；不重执行输入 |
| 上游断开/恢复 | 标注真实断连和结果未知，不伪装为 idle/done |
| 历史与实时拼接 | 消息不重复、工具不重复、顺序明确 |
| 非 yolo 权限请求 | 能回答原生请求；取消后不残留悬挂权限 |
| 模型/模式切换 | 确认值、生效时机和失败状态真实 |
| 两个独立测试会话 | 输出与输入严格隔离 |
| 被其他宿主占用 | 返回明确冲突，不复制会话或强抢 |
| 既有 HTTP/CLI/MCP 回归 | 未受管会话的既有功能可用 |

取消真实轮次时，若执行不可瞬间停止，请报告取消中和后续确认；不要声称物理撤销已经发生的外部副作用。

## 12. 最终产物

在 ZSession 项目根目录交付：

- ACP_INTEGRATION.md：启动入口、标准能力、扩展、生命周期、受管写入、幂等、恢复、权限、错误、JSON 示例与 Hive 接入方式。
- ACP_IMPLEMENTATION_REPORT.md：改动文件、架构、真实用例证据、回归结果、限制、启动命令。
- 相关代码、协议测试与必要配置示例。

Hive 侧将通过实际 ACP client 读取该文档和真实接口，请把文档与实现一并完成。报告说明已实现/未实现/未实测，不能写“后续接前端即可”来掩盖尚未取得原生增量数据。
