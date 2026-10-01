# ZSession 独立修复提示词

你负责修复 ZSession ACP插件的验收缺陷。Hive后端/前端由另一位Agent修复，两者分开施工。请直接复现、修复、测试并交付更新后的接口文档与修复报告；不要只回复方案。

## 1. 范围、依据与交接

你的修改范围：

    D:\agent_workspace\projects\zcode-session-manager

Hive只读参考：

    D:\agent_workspace\valuable-projects\hive

必读：

- 适用的AGENTS.md、当前代码与改动状态。
- Hive/ACP_INDEPENDENT_ACCEPTANCE_DEFECTS.md。
- Hive/ACP_INDEPENDENT_ACCEPTANCE_REPORT.md。
- Hive/ZSESSION_ACP_UPGRADE_PROMPT.md。
- 本项目ACP_INTEGRATION.md、ACP_IMPLEMENTATION_REPORT.md。

验收证据：

    D:\agent_workspace\tmp\hive-acp-acceptance\acc-20261001-100912

该目录保留了protocol、api、browser及被验收快照，先利用现有证据缩短复现。它是历史版本证据；核对当前代码，已修复项独立复验，不盲目重复改动。

归属：

- 你主修：DEF-01、02、03、04、05、06、07、15、17。
- DEF-14为联合项：你提供可靠的会话历史/分页/完整性接口；Hive Agent接入和处理历史缺口。
- Hive Agent主修：DEF-08、09、10、11、12、13、16、18、19、20。

不修改Hive源码、UI和它的数据库。发现消费者问题，写进接口交接与联调报告，不替对方跨目录施工。

ZSession目前可能不是Git仓库。保护现有代码，修复前保存关键文件快照/hash；不为此次任务擅自迁移整个仓库或删除目录。

## 2. 修复原则

- 复用现有ZSession daemon和ZCode单宿主，不另起一个独立app-server。
- 保留既有CLI/HTTP/MCP的未受管会话能力。
- 标准ACP与_zsession扩展分开，文档和实际能力声明一致。
- Hive队列负责受管会话的正常工作输入；插件负责真实执行、权限、事件、取消和对账。
- 不用降低能力声明、隐藏失败或把“尚未实现”改写成“暂时不需要”来完成原需求。
- 无法确认的输入不能自动重发；但确认已执行的输入必须跨adapter连接可靠去重。
- 不注入新的伪用户消息实现取消，不把失败工具包装为成功。
- 测试用独立会话，禁止全局kill ZCode、重启全部宿主或清空生产数据。
- 凭据安全读取，报告/帧/示例脱敏；退出adapter不终止上游会话。

## 3. DEF-01：会话级幂等隔离

输入登记至少以sessionId + requestId定位，必要时加实际客户端/受管写入身份。只改Map的键不够：读写、重试合并、等待链、状态查询和完成回写都要使用同一范围。

要求：

- A/B会话可以各自使用同一requestId，输入都实际执行。
- 同会话同ID同内容只执行一次；同ID异内容明确拒绝。
- get_input_state必须验证请求session与记录匹配，不泄露另一个会话的状态。
- fingerprint覆盖完整语义输入，包括ContentBlock/必要附件引用与配置条件，不能只比较提取出的text。
- 并发重复请求原子合并，不能先读不存在、两边同时发送。

复验：两个会话同ID同文本与同ID不同文本；原生输入数、轮次和副作用计数正确。相同会话重试无第二次副作用。

## 4. DEF-02：跨adapter重启的幂等与对账

将输入登记移到daemon持久存储，或建立同等可靠的上游input/turn对账机制。adapter内存可缓存，但不能是真源。

设计清楚：

- requestId、sessionId、fingerprint、输入状态、原生inputId/turnId、结果、时间及记录版本。
- 发送前的持久预约与发送后确认之间的崩溃窗口。
- 多adapter同时重试同一输入时的原子竞争。
- 已确认完成/取消/拒绝记录跨连接可查询，返回原结果而不再次调用模型。
- 只有能确认尚未发送的输入才允许首次派发；已经发送但原生结果不明时标unknown并明确拒绝不安全重试，不能把查不到等同未执行。
- 记录保留/轮转策略，过期状态不能暗中变成允许重执行。

不要声称对任意外部工具实现了物理“恰好一次”。本次必须保证相同业务输入的安全重试，并说明不可消除的上游结果未知窗口。

复验：发送前、发送后未响应、活动轮、结束后四个窗口分别退出adapter；新adapter查询/同ID重试，已执行副作用不增加；多adapter并发也只派发一次。

## 5. DEF-03：真正取消，不注入文本

替换当前send(text="cancel requested by client", mode=interrupt)路径。使用原生stop/取消命令，只取消现有活动轮次。

要求：

- session/cancel是无id通知，不依赖响应。
- 不新增userInput、不触发模型回复“已取消”、不额外消耗一轮。
- 取消请求、正在停止和原生确认分开。
- 以对应真实轮次的终态确认prompt cancelled；不能只凭本地cancelRequested和idle就判断成功。
- 原轮自然结束与取消竞态不误取消下一轮。
- 取消未完成工具要如实表示，已经完成的工具不回写成未执行。
- 取消之后仍可在同一session继续对话。

复验：真实长工具任务和模型轮次；比对取消前后native输入、轮次和marker。按上游turnCount的实际计数定义判断，没有额外轮次，不机械套验收文档中before+1的表达。

若需要受控_zsession取消预检/轮次guard，作为协商扩展提供；标准session/cancel本身保持标准通知语义。

## 6. DEF-04：非YOLO工具权限贯通

从原生待决interaction提取实际工具与允许/拒绝选项，向ACP client发session/request_permission，并把其合法答复送回对应原生请求。

要求：

- 使用真实permission request/interaction ID；不能另造一张与原生不相连的卡片。
- prompt长时间等待时仍能接收权限答复、取消和其他通知。
- 允许、拒绝、取消、断线、过期和重复回答有明确状态。
- 不答复时可以真实waiting_permission，但不能无限挂起且没有出口；取消可以解除待决。
- YOLO沿用配置授权策略，非YOLO不能默默allow。
- pendingApproval正确表示pending，不表示工具已经running。
- 旧实例/旧轮次/过期请求不能被新回答授权。

Hive会实现answer_permission与网页答复接口，你需要给它标准反向请求、合法optionId和响应关联信息。不要规定浏览器持有daemon token。

## 7. DEF-05 与 DEF-14上游：标准load与完整历史

真正实现session/load的历史回放：

1. 权限与cwd/会话有效性检查。
2. 读取该session的完整已保存会话；按顺序回放用户、助手、公开思考及可用工具历史。
3. 用合法session/update提供回放，稳定message/tool ID与replay元数据。
4. 回放完成后才响应。
5. 历史与实时订阅交接无漏事件/重复段。

不存在/不允许接入/归档不可加载的会话返回标准错误，不用成功信封loaded:false让标准client误判。

如果提供无回放resume/attach，应使用正确标准能力或命名明确的扩展，不能替代load原需求。

同时修复历史数据来源：

- 解决已有会话messages端点的Zod/RPC兼容问题，或在插件内部封装可靠的只读会话存储接口。
- 公开会话级过滤与稳定分页/快照水位；不能以全daemon最后500条事件代替会话历史。
- 如扩展/events，先session过滤再分页，游标和缺口有明确语义。
- 返回真实完整性、next_cursor、epoch等；拿不到的历史明确标缺口。
- Hive只消费公开接口，不需要直接读取ZCode私有数据库。

复验：A会话历史被其他会话产生的超过500条事件挤出后，仍能回放A早期消息/工具；新client标准load及Hive首次绑定前历史均正确。

## 8. DEF-06 / DEF-07：标准握手、字段与模式

对照验收保存的官方schema-v1.24.1、meta.json及当前官方文档。验收报告也需按权威schema核实，不能把SHOULD误写MUST；但请补齐可信agentInfo元数据并保持能力合法。

- initialize正确协商版本；拒绝初始化前的会话写操作。
- promptCapabilities只声明真实支持的ContentBlock能力，loadSession放正确位置。
- authMethods、响应结构和扩展元数据合法，不虚构认证形状。
- 实现标准session/set_mode及modeId，回报实际当前模式。
- 旧set_mode如保留只能作为明确兼容入口，不冒充标准。
- 相对cwd、非法/碎帧、多帧、UTF-8边界、未知方法正确处理。

用严格标准client/schema检查，不只验证JSON能解析。

## 9. DEF-15：受管会话全入口单写入

在daemon登记Hive受管关系与有效写入owner/代际，并在HTTP、CLI、MCP、automation和ACP入口共同检查。

- 只读订阅不排他。
- 正常工作写入由Hive统一队列派发，其他写入口明确返回受管/忙碌冲突，或经同一经授权调度。
- 允许ACP adapter服务的合法派发，不通过关闭所有旧入口来避开检查。
- 受管关系可跨adapter重连恢复，旧owner不能在新代际继续写。
- 接管、释放与失联处置文档化；不因adapter瞬时掉线立即放开其他写入口。
- 并发受管认领和发送有原子约束。

复验：ACP活动期间经HTTP/CLI/MCP/automation写同会话不得偷偷插队；两个ACP连接不能并行开轮；未受管旧会话功能继续可用。

## 10. DEF-17：工具失败与Content结构

本项虽被旧清单列为低，但失败工具被显示成功会影响真实判断，作为核心修复。

- success→completed；error→failed。
- pendingApproval→pending；running→in_progress。
- cancelled没有对应标准成功语义，按官方schema和轮次取消/元数据明确表示，不当作成功completed。
- ToolCallContent使用合法content包装或diff/terminal结构。
- 输出、错误、位置/命令信息按真实数据提供；截断须标明并有完整内容访问途径。
- 稳定toolCallId，终态不可被迟到旧状态覆写。

不要把未分类assistantText中的英文段落按字符串规则删掉来美化。提供真实channel/row类别与来源元数据，已知reasoning分流；无法稳定分类的片段保持原文并标记供UI处理。

## 11. 给Hive的修复版接口合同

尽早更新ACP_INTEGRATION.md，定版：

- 标准方法与真实能力。
- 受管登记/owner/代际与sessionId关系。
- 标准取消与可选轮次预检扩展。
- requestId/fingerprint的范围、状态枚举、持久对账、unknown处置。
- session-scoped历史/分页、replay水位和完整性字段。
- session/request_permission选项、合法响应、过期/取消处理。
- 工具终态与Content结构。
- 标准session/set_mode/模型配置、生效时机。
- 合法JSON示例、错误与兼容迁移方式。

这是双方交接依据，不能仅在你自己会话里口头说已改。外侧保持stdio入口和安全凭据读取方式，避免要求Hive新增第三套接口才能正常聊天。

## 12. 测试、报告与完成条件

新建独立fix run，保留旧验收证据不覆盖。每条DEF记录复现、根因、修改文件、真实重测和证据。

至少完成：

- 严格协议/schema回归。
- 两会话同requestId与多adapter重试。
- 四个重启窗口的副作用对账。
- 标准load完整历史及超过500条事件后的历史。
- 取消零伪消息、零新增轮次、继续对话。
- 非YOLO允许/拒绝/取消。
- 工具成功/失败/取消与合法Content。
- 受管全入口冲突，以及未受管HTTP/CLI/MCP回归。

允许fixture覆盖乱序与崩溃边界，真实模型/工具/权限/load至少各有真实验证；不要把某个测试未报错当所有DEF关闭。

交付：

- 本项目ACP_FIX_REPORT.md。
- 更新ACP_INTEGRATION.md、必要的CLI/API/EVENTS文档。
- 可复跑测试和完整证据路径。
- 每条DEF的fixed / pending / not_reproduced状态与依据。

不覆盖原施工报告的历史PASS结论；新报告明确修复版本。与Hive联调尚未执行时标pending_integration，不声称两边整体通过。

完成核心接口后，Hive Agent按文档接入；最终复验使用原ACP_INDEPENDENT_ACCEPTANCE_PROMPT.md，并补本次新增边界用例。
