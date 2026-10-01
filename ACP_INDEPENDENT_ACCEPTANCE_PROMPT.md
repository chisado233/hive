# ZSession ACP 与 Hive 会话舱：独立验收提示词

你是独立验收 Agent。请验收两项交付：ZSession 的标准 ACP 接口，以及 Hive 的真实会话桥接、会话舱与星辰大海前端。先读原需求，建立检查矩阵，再独立检查代码、运行测试、操作实际浏览器，并完成两边联调。

不要把施工报告中的“8/8”“全项已验”“未被触碰”直接当证据。你的结论可以是通过、部分实现或不通过。缺少证据的项目必须标未验证，不能默认通过。

本任务是验收，不是默默修好后替施工 Agent 通过。允许建立隔离测试脚本、测试配置和测试资源；发现产品缺陷，先保存原版本证据并报告，不修改被验收的生产实现、原需求或施工报告来消除失败。若用户另行要求修复，另起修复/复验阶段。

## 1. 交付位置与阅读顺序

ZSession：

    D:\agent_workspace\projects\zcode-session-manager

Hive：

    D:\agent_workspace\valuable-projects\hive

现有业务项目：

    D:\agent_workspace\valuable-projects\video-lab

先读适用的 AGENTS.md和Git状态。记录被验收代码版本与本地未提交变更，保护其他会话的工作。

### 原需求，作为验收基线

- Hive/ZSESSION_ACP_UPGRADE_PROMPT.md。
- Hive/FRONTEND_ACP_COSMOS_PROMPT.md。
- Hive/DESIGN.md中稳定Agent、实例、任务、权限、项目根目录等规则。

### 本轮施工交付

- ZSession/ACP_IMPLEMENTATION_REPORT.md。
- ZSession/ACP_INTEGRATION.md。
- Hive/ACP_FRONTEND_REPORT.md。
- Hive/UI_API.md、DEPLOYMENT.md、README.md。

### 重点实现与测试入口

ZSession：

- plugins/session-manager/mcp/lib/acp.mjs。
- plugins/session-manager/mcp/lib/manager.mjs、ws-client.mjs、appserver.mjs。
- plugins/session-manager/bin/zsession.mjs。
- tests/acp/*.mjs。

Hive：

- hive_core/convbridge.py、conversation.py、runtime_ops.py、scheduler.py。
- hive_core/harness.py、webui.py、server.py、store.py、权限/manifest相关实现。
- hive_web/index.html、styles.css、js下会话/星图/背景/审批相关模块。
- scripts/acp_e2e.py、scripts/quicktest.py及其他相关测试。
- work/acp-e2e/e2e-evidence.json与施工报告引用的截图，若文件真实存在。

不要仅沿用施工脚本的断言。先检查它到底测试了哪些条件、是否忽略解析错误、是否只测试成功状态、是否会归档或终止传入的会话。

## 2. 官方 ACP 基线

实施本次验收时读取官方当前 v1文档/schema，记录来源和版本。原需求并未要求实现后来新增的所有可选能力；但已经声明的能力和标准方法必须符合其协议。

官方入口：

- https://agentclientprotocol.com/protocol/v1/overview
- https://agentclientprotocol.com/protocol/v1/initialization
- https://agentclientprotocol.com/protocol/v1/session-setup
- https://agentclientprotocol.com/protocol/v1/prompt-turn
- https://agentclientprotocol.com/protocol/v1/tool-calls
- https://agentclientprotocol.com/protocol/v1/content
- https://agentclientprotocol.com/protocol/v1/transports
- 从官方文档定位当前schema/SDK和兼容性测试工具。

特别检查：

1. session/load：若声明loadSession=true，必须按标准回放会话，再完成load响应。仅从当前水位续流不能当作标准load的完整实现。
2. 无历史回放的resume/自定义attach：只有正确协商、命名和消费者调用时才可作为另一项能力；不能把自定义live-forward塞进标准load并宣称兼容。
3. session/cancel是通知，不要求JSON-RPC响应。独立客户端应发送无id的通知；Hive不应依赖非标准cancel响应才能继续。
4. 工具completed代表成功，失败必须正确反映为failed；等待权限不能当已经执行。
5. ToolCallContent与普通ContentBlock结构不同，检查实际输出是否符合schema，不只检查JSON可解析。
6. 反向session/request_permission需要正确答复选项或取消结果；YOLO自动策略不证明非YOLO交互桥接完成。
7. 标准方法、响应、能力字段和扩展分别处理；扩展使用约定命名空间，不用错误的标准字段位置掩盖未实现能力。

用官方schema/SDK或独立严格校验器检查所有抽到的ACP帧。施工方专用client能运行，不代表标准client一定兼容。

## 3. 根据报告必须核实的风险

以下只是待证伪/确认的重点，不代替你自己的最终结论：

- ZSession报告说session/load只live-forward，同时initialize声明loadSession=true。
- 历史读取“经消息查询/状态扩展”与“消息端点有兼容问题”并存：确认是否真能取到历史全文、工具结果和正确顺序。
- 输入登记在adapter内存，重启后unknown；这与原要求的跨连接幂等/输入对账差距要单独评估。
- 报告称工具error/cancelled映射completed：检查实际帧与界面是否把失败显示为成功。
- 两份报告都注明非YOLO权限桥接未实现，前端却把验收项16等归入“全项已验”。
- 报告所列协议8项是否足以覆盖原前端24项：不能拿一个脚本代替浏览器交互、授权、审批和性能。
- 默认未设HIVE_ACP_ADAPTER时会回退fixture：确认真实ZSession节点不会被模拟回答冒充已连通。
- 会话轮次结束是否只由working变成非working加固定flush延迟推定；检查失败、待输入、断线与迟到尾部。
- 同requestId是否按session/调用主体正确隔离，而不是一个全局Map串会话。
- UI输入队列、control.agent.send、scheduler通知与插件CLI/MCP/automation是否仍是不同写入路径。
- cancel是否只凭本地cancelRequested和idle推断成功，是否真正停止原生轮次/工具。
- 前端截图是否有真实文件路径、版本和内容；不能凭“见ZCode工件目录”算视觉验收。

如施工期间已经修复，应检查当前版本并实测，不因旧报告中的限制预判当前失败；同时记录报告与当前版本差异。

## 4. 安全、隔离与证据

沿用已授权的本地验收，不逐条请求确认，也不把普通测试变成发布流程。

新建唯一run_id与目录：

    D:\agent_workspace\tmp\hive-acp-acceptance\<run_id>\
        PLAN.md
        resources.json
        case-matrix.json
        protocol/
        api/
        browser/
        logs/
        defects/
        timings/

resources只记录测试会话/Agent/端口/进程等非秘密ID和用途。凭据只安全读取或由CLI使用，脱敏后才存证据；不要输出认证配置全文。

使用独立测试Hive Home、可用测试端口、项目根目录和工位。连接现有ZSession服务时只新建少量独立验收会话，不改video-lab23节点的业务责任。

正式23节点可只读核对身份、项目归属和会话状态。会话取消、重启、权限拒绝、断线注入、未知输入重放等破坏性场景只对你创建的测试资源执行。

禁止全局kill ZCode、杀全部宿主、停止生产Hive、清空生产数据库或删除其他Agent工位。后台测试进程使用隐藏窗口；不要调用会自动停止整套生产服务的ops脚本。

每个用例保存：

    用例ID / 需求来源 / 测试版本 / 操作 / 原始结果
    预期 / 实际 / verdict / Agent-instance-session-turn-input关联
    证据路径 / fixture或real标记 / 耗时 / 清理或保留状态

协议采集保留请求、通知、响应与单调时间戳。不要保存secret；正文使用无敏感内容的测试输入。

允许fixture验证乱序、长内容、非法帧或断线等边界，但fixture结果不能替代真实ZCode联调。真实流式与媒体审批至少要有实际浏览器证据。

先建立最小可复现失败，避免无必要重复大量模型调用。默认2个测试Agent/会话已足够大多数隔离测试。

## 5. 通过标准与判定

用例状态：

- PASS：当前版本按要求实测，证据完整。
- FAIL：可复现行为不符原需求或标准；报告限制不自动免除。
- BLOCKED：环境/外部状态使本项无法执行，写明原因与已完成检查。
- NOT_TESTED：尚未测试，不能计入通过。
- N/A：明确不在原需求且未声明的可选能力，给出依据。

分别给出：

1. ZSession原需求完成度。
2. ACP标准兼容性。
3. Hive后端与前端原需求完成度。
4. 真实两端联调可用性。
5. 星辰大海视觉与性能。

“有限YOLO文字实时对话可用”与“原需求完整通过”是不同结论。不能因核心对话能跑就豁免历史、权限、审批、模型、恢复与隔离项。

### 直接阻止完整通过的情形

- 自定义行为冒充标准能力，严格标准client无法互通。
- 真实生产会话展示fixture回答，或输入没真正送达却显示完成。
- 失败工具显示成功，取消没有停止/误取消下一轮。
- 跨Agent/会话/身份泄露、越权、旧实例误投、重复执行。
- 队列被其他写入口绕过，输入丢失或偷偷interrupt。
- 原需求中的重要功能未实现，却以“暂不支持/后续接”列为通过。
- 关键恢复或浏览器用例没有实际证据。

缺陷按阻断程度排序，别用大量细节装饰掩盖核心错误。标准合规缺陷、产品缺口与环境阻塞分开写。

## 6. ZSession协议与真实会话用例

每行执行步骤与预期如下。已有测试仅是辅助，必要时另写严格client。

| ID | 操作 | 预期与取证 |
| --- | --- | --- |
| Z01 | 用node实际启动acp --stdio，读help及可选参数；观察宿主 | stdout仅协议；stderr日志；无新增第二个ZCode宿主；凭据不回显 |
| Z02 | initialize支持/不支持版本；schema校验响应 | 协商准确，标准能力/认证结构合法；不能用错误字段声明能力 |
| Z03 | 未初始化操作、非法方法/参数、碎帧/多帧/分片中文 | 合法错误处理；UTF-8不损坏；进程可继续；不能悄悄吞掉协议错误 |
| Z04 | session/new绝对cwd、模型/模式，再做首轮检查 | 真sessionId；实际cwd正确；配置先应用；不额外注入bootstrap人格 |
| Z05 | 连续两轮新暗号对话，并与原生会话查对 | 同session上下文保持；原生轮次实际发生；不靠adapter复述伪造 |
| Z06 | 生成足够长的分段中文回答，逐帧记录接收时间 | 在最终prompt响应前收到真实增量；native→ACP正文完整一致，无重复尾部 |
| Z07 | 执行真实成功工具任务，产生测试目录marker | 稳定toolCallId、合法Content结构、生命周期与实际文件一致 |
| Z08 | 执行故意失败工具任务，产生可查错误 | 原生失败→ACP failed→UI失败；不能映射completed冒充成功 |
| Z09 | 对长轮次发送无id session/cancel通知 | 不依赖cancel响应；旧轮原生结束确认后prompt返回cancelled；无悬挂 |
| Z10 | 对即将结束轮次制造取消竞态，再开下一轮 | 不误杀新轮；自然结束和取消分别准确；中断工具不会继续产生取消后marker |
| Z11 | 有历史的会话关闭adapter，用严格新client session/load | 声明loadSession=true时完整历史通过update回放后响应；live-forward单列，不冒充通过 |
| Z12 | load不存在/归档会话、错cwd、未授权对象 | 明确合法错误、不新建替身、不把loaded:false成功信封当标准成功；无泄露 |
| Z13 | 同会话同requestId重试；异内容冲突 | 只执行一次；原生文件追加计数一次；冲突真实拒绝 |
| Z14 | 两会话同requestId，分别相同/不同文本；交叉get_input_state | session范围隔离；查询不返回另一会话状态；合法各自输入不被全局Map误合并 |
| Z15 | 接收/执行中/执行后分别断开adapter，新adapter对账与同ID重试 | 区分可确认与unknown；原要求跨连接幂等实际评估；不能重复副作用 |
| Z16 | 非YOLO真实权限待决，允许/拒绝/取消 | session/request_permission贯通；原生执行结果对应选择；未实现记FAIL原需求项 |
| Z17 | 使用标准模式/配置方法，而非仅施工自定义set_mode | 命名与能力声明匹配，确认模型/模式/生效时机真实 |
| Z18 | 给真实会话通过HTTP/CLI/MCP/automation另投输入，与ACP并发 | 受管写入约束生效或明确冲突；不能“ACP内串行、其他入口自由并发” |
| Z19 | stdin关闭、长prompt时只读、桥接连接失效 | adapter不终止上游；未决调用正确收口/标未知；会话能继续使用 |
| Z20 | 真实上游失败/等待输入/事件延迟及最终尾部 | 不仅凭非working推end_turn；尾部不丢、不串到下轮；错误与待决真实 |

Z15的重复副作用测试只在本run目录进行：让模型执行一次向计数文件追加唯一标识，查实际次数、原生用户消息数与轮次。不能只数adapter回显证明幂等。

对未知输入，禁止在生产会话试“显式重发”可能造成的副作用。测试报告要说明新request_id重发与恢复原输入不是同一保证。

官方不要求所有Agent必须主动发工具权限请求；但本项目原需求明确要非YOLO权限桥接，所以该原需求项不能因当前默认YOLO而写N/A。

## 7. Hive后端、队列和恢复用例

使用独立Hive Home和两个测试Agent，保留真实Harness。先验证配置确实启用了AcpBridge。

| ID | 操作 | 预期与取证 |
| --- | --- | --- |
| H01 | 核对agent→instance/generation→session绑定，打开读取端点 | 真实句柄一致；阅读不新建实例或复制会话 |
| H02 | UI/API发输入，再查原生会话和全部关联记录 | 受理快速返回；queued/dispatched/running/completed有真实依据；非accepted即running |
| H03 | 长轮次时排两条不同输入 | 有限FIFO、无丢单；20秒后不偷改interrupt；不同文本不被同dedupe键合并 |
| H04 | UI输入期间同时control.agent.send与inbox唤醒 | 同一会话统一调度；源标明；单活动轮；真实系统任务仍可被Agent处理 |
| H05 | 同用户同ID重试、异内容同ID、不同用户/Agent复用ID | 按契约正确隔离；不误复用别人的输入状态/结果；副作用一次 |
| H06 | 停止本轮、再对话、中断并发送 | 取消中→原生确认→下一输入；同session；Task状态未被自动修改 |
| H07 | 取消请求带旧turn，旧轮刚结束且下一轮已开始 | 不误取消下一轮；服务端核对预期轮次/实例；仅UI防重不够 |
| H08 | 独立测试Agent换实例，旧页面/旧请求发送或取消 | stale-instance/fencing拒绝；缺预期字段行为明确；不误投新实例 |
| H09 | 浏览器刷新、SSE断开，后台继续输出，再补历史 | 无漏事件/重复消息；首次history与stream交接竞态被处理 |
| H10 | Last-Event-ID、after游标、过旧游标、慢消费者 | 正确恢复/标缺口；实际快照恢复可用；非只显示gap_before就算恢复 |
| H11 | 只停止本run的adapter，活动输入仍在上游执行 | outcome_unknown真实；重连先对账，不重跑；后续已知结果正确关联 |
| H12 | 独立Hive进程重启：覆盖queued/dispatched/running输入 | 持久队列恢复；同实例/session绑定恢复；无丢失或双派发 |
| H13 | 上游断连但网页打开；关闭网页但上游运行 | 两种连接状态独立；不自动fixture、不把disconnect当idle或completed |
| H14 | 实例创建到首次打开面板之前已经产出消息/工具 | 历史/初期事件可查；不是仅发第一条UI消息后才开始订阅 |
| H15 | 用非YOLO原生权限请求驱动待决卡并回答 | 真实反向请求回应；HTTP答复接口可用；不是只有卡片投影没有答复路径 |
| H16 | ACP ContentBlock附件、模型/模式改变与错误 | 输入能力真实；不默默丢非文本块；期望值/确认值/生效时机准确 |
| H17 | 缺失/配置错误HIVE_ACP_ADAPTER；真实Harness请求聊天 | 生产会话明确不可用或按明确模式处理；fixture不得成为真实回答 |
| H18 | 读取/发送/取消权限、cookie撤销、跨项目与未授权文件 | 服务端逐接口授权；撤销后持续SSE也处理；无token/任意路径泄露 |
| H19 | CSRF、缺自定义头、跨来源、未知/过期permission | 写入被拒绝；只读SSE不绕过认证；过期授权不能生效 |
| H20 | delta/snapshot/replace/replay/工具更新交错，迟到旧实例事件 | 正文和卡片正确投影；generation/session/turn隔离；不污染当前会话 |

H16检测附件真实传入内容，不仅检测上传列表。

原始业务Task的done/fail/trash、callback、等待和审批仍通过原有系统；不得把会话end_turn或cancelled映射为Task终态。创建独立业务Task实测这一点。

## 8. 浏览器功能、审批与视觉验收

使用可用的真实浏览器控制工具。按其技能/工具规则操作；没有浏览器能力就记录该阶段BLOCKED，API通过不能替代它。截图须保存绝对路径并实际查看。

| ID | 操作 | 预期与取证 |
| --- | --- | --- |
| B01 | 登录、选实际项目、点击节点进入会话 | 正确Agent与session；实际历史和实时输出；无新增实例 |
| B02 | 在UI发送中文多行、使用中文输入法与快捷键 | 不被组合输入误发；排队/中断按钮明确；输入受理和结果分态 |
| B03 | 真实长回答/工具失败、代码块、复制/折叠 | 增量在轮结束前可见；失败清楚；Markdown/代码无破坏或重复 |
| B04 | 向上读，继续生成，再点新输出 | 不强制滚底；返回跟随；阅读位置正确 |
| B05 | 切A/B Agent，保留各自草稿/附件/阅读位置 | 完全隔离；回到A仍是A内容，不串草稿与工具 |
| B06 | 排队两条、查看队列、停止本轮、中断并发送 | UI动作与H03/H06事实一致；无假取消确认 |
| B07 | 从审批卡打开讨论，聊“通过”，回到审批 | 草稿保留；聊天不自动结算、代评分或授予自主权 |
| B08 | 独立人工测试Task含Markdown、图片、视频、两项评分与选项 | 真预览/可播放；artifact_score与review_score独立；正式提交后真实callback |
| B09 | 少填/越界/步长不符/未知选项/旧spec_version提交 | 服务端拒绝，草稿可恢复；按钮隐藏不是校验 |
| B10 | 在UI改模型/模式，查看不支持的图片/音频/视频输入 | 实际控件和错误可用；准确能力提示；不能“显示附件”冒充模型理解 |
| B11 | 当前实例换代、历史会话、无实例、断线重连 | 当前/历史明确；旧页不可误写；无实例仅浏览不启动；重连状态真实 |
| B12 | 星图拖动/缩放/项目切换/刷新，少节点及23节点 | 按execution_root分图；稳定布局；圆Agent/方资产/人形人类，非过程节点 |
| B13 | 检查星云、恒星光晕、航道流星、会话舱展开 | 精致统一、中文清楚；业务动效只由真实事件触发，不伪造忙碌 |
| B14 | reduced-motion、隐藏标签页、键盘导航、窄屏 | 动效减少/暂停；焦点可见；功能仍可用，不只是媒体查询存在 |
| B15 | 1440×900、1920×1080、约390宽；23/50节点和长历史 | 截图与性能trace；无严重卡顿/遮挡；分页/短批流更新有效 |

B08使用你创建的人工通道/测试身份答复，必须标为测试判决，不写进video-lab TASTE、active、authorizations或正式人评。UI卡片的真实性以真实Task、提交和callback验证，不要求真人替你填写工程测试分数。

保留一项测试评论证明comment不结单；另一次正式answer证明Task done与业务decision的区别。字段内容采用技术测试值，绝不声称用户审美分。

长历史/50节点用隔离测试数据，并明确source=fixture；这类性能测试可以fixture，但B01/B03的真实会话与B08实际媒体/审批链不能假造。

视觉报告至少附：

- 全景星图。
- 真实ACP会话、工具成功与失败卡片。
- 审批台，图片/视频与双评分。
- 窄屏/专注会话视图。
- 减少动效模式与性能证据。

对“星辰大海”给清楚评价和可复现问题：视觉层级、文字可读性、布局、动效克制与实际性能。不要只有“好看”“流畅”两句，也不要把个人偏好当协议阻断。

## 9. 施工报告可信度与测试充分性

做一张“施工声称 → 实际覆盖”的表：

    施工条目
    引用的脚本/证据
    脚本真正断言了什么
    你新增的独立验证
    当前结论

特别回答：

- scripts/acp_e2e.py是否真正驱动浏览器，还是只运行adapter协议？
- chunk数≥1加最终文本非空，是否足够证明结束前真实流式？
- 只得到cancelled字符串，是否证明原生工具已停止且没有误取消？
- 内存同ID去重，是否证明跨adapter/Hive重启不会再执行？
- 报告中权限未实现的项，如何被归入24项通过？
- 只跑quicktest，是否实际覆盖前端输入、评分、媒体、授权和性能？
- “未被触碰”是代码改动说明，还是有HTTP/CLI/MCP回归实测？
- 历史get_session_state返回状态，不返回全文时，如何证明历史可恢复？

已有脚本复跑结果记为“施工脚本回归”，独立测试记为另一列。文件不存在、截图无法定位或原始证据不含必要断言，记录证据缺口。

## 10. 联调最小真实主线

在独立资源上完成并给出逐事件时间线：

    标准client initialize/new
      → 真实ZSession会话
      → Hive绑定当前Agent实例
      → 浏览器发消息
      → 原生模型/工具活动
      → ACP增量/工具结果
      → Hive持久事件
      → SSE
      → 浏览器渲染
      → 排队第二条
      → 原轮结束后下一轮
      → 取消长轮次并继续
      → 刷新/桥重连/对账
      → 真实测试审批与callback

直接创建的ACP会话与Hive绑定会话若走不同建会话路径，分别说明并分别验证，不能把协议new成功替代Hive实例启动/接入验证。

共同最小通过证据必须包含实际sessionId、instance/generation、输入requestId、真实轮次/工具ID、先后时间和浏览器截图。各层能通过唯一标识串起来。

如果标准协议测试暴露失败，仍可以继续验证同厂专用桥的有限可用性，但不得因此把标准兼容性改成PASS。

## 11. 输出报告与交接

在Hive项目根目录交付：

- ACP_INDEPENDENT_ACCEPTANCE_REPORT.md：完整结论、分项矩阵、测试环境/版本、真实证据、视觉评价、范围与未测项。
- ACP_INDEPENDENT_ACCEPTANCE_DEFECTS.md：可给施工方直接复现的缺陷列表。

将独立测试脚本和原始证据保存在本run目录，报告链接到绝对路径。必要时补一份只读的ZSession组件结果摘要到其项目目录；不要覆盖施工报告或修改其PASS声明。

缺陷记录至少包含：

    编号与严重性
    归属：ZSession / Hive / 联调 / 文档证据
    对应原需求与官方来源
    文件+当前真实行号
    最短复现步骤/合法协议帧
    预期与实际
    可见影响
    原始证据
    建议修复方向
    复验条件

最终报告开头给明确结论，例如：

    ZSession原需求：通过/不通过/部分实现
    ACP标准兼容：通过/不通过/未完成验证
    Hive后端与前端：通过/不通过/部分实现
    真实有限对话链：可用/不可用/受限
    视觉与性能：通过/需改进/未完成验证
    本次用例：PASS / FAIL / BLOCKED / NOT_TESTED / N/A数量
    最重要的阻断项

完整通过必须满足原始范围，而不是由施工报告自行缩小范围。已知未实现重要能力仍是未完成；如实披露可作为文档优点，不是功能通过。

收尾仅停止你创建的测试活动、保存会话与日志供复现；不清理其他资源，不删除证据，不全局停生产服务。最终给用户报告入口、主要问题和实际已验证的可用范围。

现在开始：读原需求与交付，锁定版本和隔离资源，建立矩阵，然后按标准协议、真实后端和实际浏览器三层独立验收。
