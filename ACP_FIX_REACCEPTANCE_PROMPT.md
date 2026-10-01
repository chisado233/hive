# ACP修复轮与静谧天文台：独立复验提示词

你是本轮独立验收Agent。请复验ZSession ACP、Hive会话桥接和“静谧天文台”新版前端，验证上次DEF-01～DEF-20真正关闭，并查本轮修复引入的协议、恢复和跨端风险。

请直接执行代码检查、隔离测试、真实ZCode联调与实际浏览器验收，交付复验报告和剩余缺陷。不要只读报告后评价，不把施工方的fixed、8/8、core-verified当作已验收。

这次是验收，不是修复。允许编写隔离测试脚本/夹具与创建本轮测试资源；不修改被验产品代码、原验收证据、施工报告或原需求来消除失败。失败先留证据；后续修复另行授权、另留版本。

## 1. 本轮输入与原基线

本轮用户指定交付：

    D:\agent_workspace\valuable-projects\hive\UI_REDESIGN.md
    D:\agent_workspace\valuable-projects\hive\HIVE_ACP_FIX_REPORT.md
    D:\agent_workspace\projects\zcode-session-manager\ACP_INTEGRATION.md
    D:\agent_workspace\projects\zcode-session-manager\ACP_FIX_REPORT.md

必须同时读：

- Hive/ACP_INDEPENDENT_ACCEPTANCE_REPORT.md。
- Hive/ACP_INDEPENDENT_ACCEPTANCE_DEFECTS.md。
- Hive/ACP_INDEPENDENT_ACCEPTANCE_PROMPT.md，原55项验收基线。
- Hive/ZSESSION_ACP_FIX_PROMPT.md。
- Hive/HIVE_ACP_FIX_AND_REDESIGN_PROMPT.md。
- 原ZSESSION_ACP_UPGRADE_PROMPT.md、FRONTEND_ACP_COSMOS_PROMPT.md及相关DESIGN。
- 当前UI_API.md、DEPLOYMENT.md、README与真实启动配置。

旧验收证据：

    D:\agent_workspace\tmp\hive-acp-acceptance\acc-20261001-100912

包含protocol帧、API结果、浏览器截图、官方schema-v1.24.1和被验收快照。旧失败不等于当前仍失败；当前fixed也不等于已通过。逐条复现对比。

先读适用AGENTS.md和改动状态，记录代码版本、未提交变更、文件hash与运行服务加载的实际版本。

## 2. 验收范围与责任

ZSession修改范围：

    D:\agent_workspace\projects\zcode-session-manager

Hive修改范围：

    D:\agent_workspace\valuable-projects\hive

分别评估组件，再评估联合接口。失败归属明确，不把上游问题算作已关闭的前端项，也不把前端假状态藏在“插件已修”里。

本轮不得跳过：

- DEF-03取消：Hive报告仍记原生confirmed不稳定。
- DEF-04权限：两份报告仍记pending_integration，必须补真实allow/reject/cancel。
- DEF-14历史：双方声明已提供/接入，要验证全历史、稳定分页与首次绑定。
- 标准模式与权限帧：不能因为方法名已改就宣称完整标准兼容。
- Hive启动恢复：释放dispatched队头不等于已经对账。
- 新版UI的真实操作、窄屏、状态和视觉，不能只核对CSS token。

## 3. 权威协议与文档矛盾

先读取旧验收保存的官方schema/meta，核对本次使用的官方v1文档与SDK版本，记录基线差异。不要强加原需求之外后来新增的所有可选功能；但已声明的能力必须合法。

官方参考：

- https://agentclientprotocol.com/protocol/v1/initialization
- https://agentclientprotocol.com/protocol/v1/session-setup
- https://agentclientprotocol.com/protocol/v1/session-modes
- https://agentclientprotocol.com/protocol/v1/prompt-turn
- https://agentclientprotocol.com/protocol/v1/tool-calls
- https://agentclientprotocol.com/protocol/v1/content
- https://agentclientprotocol.com/protocol/v1/transports

用严格schema/独立标准client校验真实帧，不只JSON解析或施工专用client。

尤其核实：

- load/new返回的modes如提供，应是SessionModeState结构，包含currentModeId和availableModes；不能把字符串数组当模式状态。
- session/set_mode响应按对应schema验证，模式变化通过实际状态或更新确认。
- PermissionOption有optionId、name、kind；不能用type和藏在_meta的label替代标准字段。
- request_permission必须有toolCall与真实工具关联。
- 许可响应的outcome判别字段按标准结构，不把{type:"selected"}当标准{outcome:"selected"}。
- 标准session/cancel通知无id，不要求回复；完成以对应原生轮次确认。
- cancelled工具不能仅因附_meta就被UI显示为绿色成功。

以上是复验要点，最终以锁定官方schema和当前真实帧判定。agentInfo等MAY/SHOULD/MUST严格分清；不要重复旧报告中可能过严的描述，但原需求的身份信息仍应提供。

本轮ACP_INTEGRATION.md的开头v0.5变更与后文旧能力表存在明显冲突：历史/权限/set_mode/工具失败等前后描述不同。检查实际实现，另记接口文档缺陷；不能择一段作为自己想要的结论。

## 4. 隔离与证据

创建唯一run_id：

    reacp-<日期时间>-<短标识>

目录：

    D:\agent_workspace\tmp\hive-acp-reacceptance\<run_id>\
        PLAN.md
        resources.json
        versions.json
        defect-closure.json
        case-matrix.json
        protocol/
        api/
        browser/
        timings/
        defects/
        snapshots/

使用新的独立Hive Home、可用端口、测试项目和工位。连接生产ZSession时只创建少量本轮测试会话。

不要复制生产Hive Home并保留live实例句柄启动测试服务：新版可能eager_attach，导致测试服务接管已有生产会话。数据库迁移测试用离线副本或明确脱离真实句柄的隔离夹具。

video-lab23节点只读查看项目归属、身份和布局；不抢它们任务，不取消其轮次，不改其模型、方法、TASTE和授权。

禁止全局kill ZCode、重启生产宿主、清空账本或删除旧证据。权限/取消/重启/崩溃注入均仅对本run资源执行，后台 helper使用隐藏窗口。

凭据安全读取，协议/日志/截图脱敏；不导出完整认证配置。resources记录公共资源ID、用途和归属，不写secret。

每个用例保存：基线需求、当前版本、步骤、预期、实际、verdict、真实agent/instance/generation/session/turn/input/interaction关联、证据路径、fixture或real、时间。

fixture可测不可稳定触发的边界，但真实流式、取消、权限、历史和浏览器必须有真实验证。无法测记BLOCKED/NOT_TESTED，不计通过。

## 5. 判定与关闭规则

状态：

- PASS：实测当前版本满足预期，证据足够。
- FAIL：原缺陷仍在、修复不完整或出现新问题。
- BLOCKED：外部状态阻止本项，写原因与已完成部分。
- NOT_TESTED：未执行。
- N/A：确实不属于原需求且未声明，给依据。

旧DEF关闭分别记：

    CLOSED / PARTIALLY_FIXED / OPEN / NOT_REPRODUCED_WITH_EVIDENCE / BLOCKED

NOT_REPRODUCED不是自动CLOSED；core-verified不是端到端PASS。

完整通过的必要条件：

- 标准ACP帧合法，并能和严格client交互。
- 关键幂等、隔离、恢复和取消不产生假成功或重复执行。
- 非YOLO工具权限完整允许/拒绝/取消。
- 全量历史与UI投影可信、无漏无重复。
- 实际浏览器三态与窄屏可用。
- 重要未测项补完，视觉改进有前后实图。

有限YOLO对话可用、组件代码已实现、真实联调通过、视觉通过分别报告。不能把这些合成一个模糊“全部完成”。

## 6. DEF-01～20关闭矩阵

每项使用旧证据复现步骤并补本轮修复条件。表中列的是最低要求，不代替原55项。

| 用例 | 原DEF | 必做操作与通过条件 |
| --- | --- | --- |
| D01 | 01 | 两会话同requestId同/异内容分别真实执行；get_input_state严格对应本session，无串号或吞单 |
| D02 | 02 | adapter与daemon重启、多adapter并发重试；真实文件追加/原生输入计数只一次；终态持久且不被reserved重写 |
| D03 | 03 | 无id cancel长轮：原生停止确认，零伪用户消息、零额外模型轮；同session继续；不把requested当confirmed |
| D04 | 04 | 全新非YOLO会话分别允许、拒绝、取消、超时；合法反向请求→Hive卡→标准答复→原生执行/拒绝；无无限挂起 |
| D05 | 05 | 已有多轮及工具历史的标准load；完整replay先于响应；模式/响应schema合法；不存在会话是错误 |
| D06 | 06 | 严格initialize、能力/认证/agentInfo结构；未初始化写操作被拒绝；版本/非法帧可恢复 |
| D07 | 07 | 标准session/set_mode，合法参数与响应、实际模式变化、current_mode_update/配置一致；旧别名不冒充标准 |
| D08 | 08 | 用不回cancel响应的严格agent测试Hive，通知立即受理且不等60秒；原生确认独立 |
| D09 | 09 | 真实流式及工具后，队列chip、会话头、思考/正文终态无需刷新更新；事件关联完整 |
| D10 | 10 | queued/dispatched/running/cancelling各窗口重启Hive；真实对账，无永久卡死、双派发或提前抢占 |
| D11 | 11 | 权限请求存在/未知/重复/过期/越权答复；只有真发送成功才能成功信封；实际原生结果对应选择 |
| D12 | 12 | 同秒和并发入队至少20条隔离输入，比较事务入队seq与实际dispatch；FIFO、迁移兼容 |
| D13 | 13 | 旧instance/generation写/取消409+STALE_INSTANCE；前端保留草稿且不自动改投新实例 |
| D14 | 14 | 早期目标会话历史被其他超过500条事件挤出后仍能取完整分页；Hive首次绑定、重放、实时交接正确 |
| D15 | 15 | 受管期间HTTP/CLI/MCP/automation/第二adapter写入严格约束；旧owner/代际与release校验；未受管功能回归 |
| D16 | 16 | SSE断网、心跳停、认证过期/撤销、重新登录；明显提示、游标恢复、旧订阅清理，长时无静默冻结 |
| D17 | 17 | 真工具成功/失败/取消：合法ToolCallContent、pending/running/failed正确；取消不渲染成功；历史与实时一致 |
| D18 | 18 | cancel失败、断线、自然完成竞态：cancelling正确回滚/未知/终态，不假cancelled、不覆写已确认终态 |
| D19 | 19 | 默认遮蔽/显示切换、提交/退出/重登清空；DOM无残留secret，日志截图不漏；不夸称password是新安全边界 |
| D20 | 20 | 390/320顶部无逐字竖排，导航可发现；无裁切/低对比/other裸英文；idle与思考不矛盾，未分类文本不删除 |

D12批量顺序测试可以主要使用严格fixture免额外20轮模型调用，再用少量真实输入复核同一调度；标明两类证据。

D03的turnCount依据原生实际计数定义判断，不机械固定before+1；真正条件是取消不新建输入/轮次，以及对应原轮终止。

D04不能用“第二轮没触发权限”跳过。用独立新会话/必要的独立工作区，确认受限模式和原生待决条件；不修改全局用户权限策略或变YOLO来完成测试。

## 7. 新修复风险用例

这些风险必须独立测试，不因旧DEF被标fixed就省略。

| 用例 | 场景 | 预期 |
| --- | --- | --- |
| R01 | 同一requestId已经终态，模式/模型切换后以同一原业务输入重试 | 对原输入/锁定条件有明确语义；不会因当前配置变化误执行或回假结果 |
| R02 | reserved后已真正发上游、accepted持久写前崩溃；120秒后会话空闲再重试 | 年龄+idle不能证明未执行；不重复副作用，需原生对账或明确unknown |
| R03 | 上游running期间重启Hive，queued后续仍存在 | 启动sweep标unknown不等于上游idle；后续不抢占/重复；真实活动结束后按序继续 |
| R04 | 真实cancel confirmed=false、null、超时和异常 | 没有肯定确认不能cancelled；不把“非false”当true；UI与原生对应 |
| R05 | cancel工具终态带_meta.cancelled但status=completed | UI不显示成功；标准状态/元数据处置有正确依据；工具与轮次取消分开 |
| R06 | 权限options/工具关联与selected/cancelled响应逐帧schema验证 | toolCall存在，optionId/name/kind合法，outcome标准；非标准type/隐藏label不能冒充 |
| R07 | load/new/set_mode响应有modes数组/结构 | 真实帧符合SessionModeState/对应响应schema；方法名正确不代表payload正确 |
| R08 | 受管写入仅靠X-ZS-Writer:acp；不同owner/旧generation/第二adapter也带该头 | 标识头不是owner/代际证据；旧或未获准writer不可绕过；只有被授权override例外 |
| R09 | Hive load未传ownerId/generation或用默认acp-client/1；实例更换后再attach | 受管绑定与实际instance/generation对应，非所有会话共享默认owner/代际 |
| R10 | failed/rejected输入跨重启同ID重试 | 复用真实失败/拒绝结果，不默认返回成功end_turn、不再执行 |
| R11 | adapter复接load全量replay，Hive已有历史/实时事件，第二会话同时load | 无重复消息/工具，无串会话；replay不触发业务动作/假turn；历史/实时无缺口 |
| R12 | busy或发送异常时本会话还有后续queued输入 | 失败处理精确定位当前input_id，不把next_queued误标failed/回队；其余输入无损 |
| R13 | 单工具失败、非end_turn stopReason、上游断连/waiting状态 | 不统一当completed；状态、UI、持久记录和真实执行对应 |
| R14 | 保留范围4000条/7天：隔离存储触发GC，旧ID再查询/重试 | 过期合同明确；旧已执行输入不被当新请求静默重跑；不得在生产改时间/批量跑模型 |

R02测试应使用可控失效注入/代理/夹具重建崩溃窗口，并有少量真实侧效应验证。你不修改产品实现来让测试通过；测试hook若必须改动，以临时独立副本/代理实施并说明。

R08只用本run资源和测试凭据，不尝试控制生产其他用户会话。目标是原需求中的单有效writer与代际约束，不是无边界安全扫描。

本轮新增的“Session is not active自动resume重试一次”也要检查：明确未接收才可重试；已接收但返回不明不能悄悄再send。

## 8. 非YOLO真实链路的专项步骤

不能将两边各自“链路就绪”当联合通过。必须：

1. 新建隔离真实非YOLO会话，确认模式。
2. 发会触发实际工具许可的有限操作，例如写本run目录的marker。
3. 捕获原生interaction、ACP反向请求，严格schema验证。
4. Hive登记到正确agent/session/instance/turn，浏览器显示原生工具与选项。
5. 在网页选择允许，记录标准响应帧→daemon resolveInteraction→原生工具执行→marker与工具终态→prompt结果。
6. 新会话或新待决分别测试拒绝与取消，不能复用缓存授权造成不触发。
7. 测试未知optionId、过期、重复、旧实例与未授权身份，都不能生效。
8. 无答复超时/桥断线时有明确处置，不能假完成或留下无限待决。

确认120秒自动cancelled的范围：仅取消许可还是整轮？是否通知client/Hive清理卡片？实际结果与文档必须一致。

测试结果至少有一条完整allow、一条reject、一条cancel的真实联合证据。core-only/fixture-only不关闭DEF-04/11。

## 9. 恢复、历史和受管写入专项

### 恢复

覆盖两种进程：adapter与Hive daemon；必要时用隔离daemon验证ZSession持久记录。

窗口：尚queued、reserved、已发送未确认、running、cancelling、已终态未返网页。

分别核对：

- 原生输入/工具侧效应实际次数。
- Hive绑定与代际。
- 输入状态从真实对账得到还是统一扫成unknown。
- 后续队列顺序与原生活动，没有副作用、消息或工具双派发。
- 恢复状态及时可见；合法长轮不因30秒指标被强行终止。

### 历史

不只数“365条深历史”或“6个回放chunk”。核对内容与顺序：

- 多页包含用户、助手、公开思考、成功/失败/取消工具。
- before/oldestSeq/newestSeq/page边界稳定，没有缺页或重复。
- 超过500个其他会话事件仍能取得目标早期记录。
- 首次打开绑定前历史可读；重新load和新消息无重复。
- live期间load与历史页交接有快照水位或同等可靠机制。
- 过去会话只读不恢复成当前有效实例。

### 合流

同时经UI、control.agent.send、系统inbox唤醒投同一测试Agent，检查只有一个受管输入调度。

插件HTTP/CLI/MCP/automation的约束与Hive当前实现要联合验证。eager_attach/load导致会话受管之后，原任务唤醒和控制仍能工作；不能“聊天好了，自动工作被409全部挡住”。

只读查看可以共享；多adapter/旧owner不能成为多writer。释放和再绑定的行为按文档实际验证。

## 10. 静谧天文台视觉与浏览器验收

按用户认可方向和HIVE_ACP_FIX_AND_REDESIGN_PROMPT评审；UI_REDESIGN是施工说明，不是自行放宽视觉目标的依据。

设计参考：

    C:\Users\Administrator\Documents\Codex\2026-09-29\hello\work\hive-observatory.html

参考是设计示例，不是实际业务或评分；不要求逐像素照搬，重点是空间、层级、安静、阅读和三态统一。

| 用例 | 操作 | 预期与证据 |
| --- | --- | --- |
| V01 | 实际项目星图全景，无选中节点 | 76px导航/抽屉可用，空详情不常驻挤图；项目标题与图层有层级，不是统一小字 |
| V02 | 点节点、展开/关闭会话、专注阅读 | 选中保留上下文，面板不遮内容/挤坏图；运行技术信息低噪声且可查看 |
| V03 | 真实长回答、中文输入法、代码换行/复制、工具展开 | 正文舒适，复制正确，错误与参数分清，无重复/遗留思考状态 |
| V04 | 审批全文/图片/视频、双评分、判决、评论、授权 | 真预览/播放，评分对象独立，null与0不同；正式提交/评论/自主授权意义清楚 |
| V05 | 1920/1440/1280/1024 | 全景/会话/审批三态完整截图，无标签/首图标/按钮/提示裁切 |
| V06 | 768/390/320 | 导航入口可见，顶部不逐字竖排，底抽屉/输入/评分可操作；局部代码溢出不拖坏全页 |
| V07 | 星图拖动/缩放/项目切换/刷新，23节点、50节点 | 稳定布局、字清楚；不同execution_root分图；圆Agent/方资产/人形人类/端口区别清楚 |
| V08 | 有/无真实业务事件，busy/idle/unknown，reduced-motion和隐藏页 | 业务动效真实，空闲不伪造忙；背景克制；减少动效不损失状态，隐藏暂停 |
| V09 | SSE断线、认证失败、重新登录、多个节点来回切 | 明确状态和安全恢复；草稿按身份/Agent隔离；订阅不泄漏、不静默冻结 |
| V10 | 权限卡、旧实例、队列/停止/中断实际点击 | 状态与原生对应，取消中不中途假完成；操作有明确反馈，不误投 |
| V11 | 星图/会话/审批前后对比与键盘访问 | 留白、字号、对比、线条与图标统一；焦点可见、Esc关闭与恢复；不靠厚光晕美化 |
| V12 | 23/50节点、长历史、流式期间录性能与资源变化 | 有环境/trace；无明显卡顿、每token全量DOM重建、持续订阅/计时器泄漏 |

23/50节点压力与长历史可以隔离fixture，明确标记。真实对话、状态、权限和审批至少使用真实链路；不能把fixture页面截图作真实完成证据。

尤其审查：

- UI_REDESIGN提到正文15px但其它文字偏小：实际读起来是否仍是“所有字都小”，不能只核对数值。
- 会话面板58%且最高880px：中屏是否挤掉星图上下文，专注视图是否舒展。
- 头部仍堆技术徽章、emoji/图标混用、发送按钮与决策金使用是否一致。
- 只有虚构项目/两节点截图，不能证明video-lab完整布局优雅。
- 模型assistantText里未分类英文片段保持真实，明确呈现；不得正则裁掉造成回答内容损失。

视觉结论给具体问题和图片定位，不把你的审美分写进项目TASTE；也不因纯偏好差异把已通过协议项否决。

每种关键状态保存绝对路径截图并实际查看：全景、真实会话/工具、真实非YOLO权限、审批、390/320、专注/抽屉、断线/恢复。

## 11. 原55项回归与补齐未测项

保留原ACP_INDEPENDENT_ACCEPTANCE_PROMPT的完整矩阵，逐项标：

    本轮独立复测 / 可信沿用旧PASS（注明版本变化影响） / FAIL / BLOCKED / NOT_TESTED

受修改影响的旧PASS必须复测；不必机械重复毫无影响的昂贵对话，但给沿用依据。

必须补上旧轮未测/部分项：

- 模型/模式UI实际切换。
- 真实附件ContentBlock传入与输入能力提示。
- 审批卡到发起Agent会话讨论，再返回保留草稿/评分，不自动结案。
- 拖拽/缩放真实操作证据。
- 多身份/跨项目读取、发送、取消与附件权限。
- 23/50节点、长历史性能trace。
- 实际允许/拒绝/取消权限答复。
- 浏览器丢cookie/SSE心跳缺失的恢复与长时观察。

至少复核已通过的审批双评分、五项字段校验、comment不结单、业务decision与Task done分离、聊天不生成work、Task不因会话cancel/end_turn改变。

quicktest只证明它真实覆盖的部分；acp_e2e8步不驱动浏览器，不用它们替整个矩阵。

## 12. 执行顺序、预算与中断接续

1. 锁定版本/环境与隔离资源，生成矩阵。
2. 严格schema和本轮接口文档对照，低成本先查核心帧。
3. 复测幂等、取消、权限、load与受管写入。
4. Hive队列、重启与恢复，确保不会触发重复工具。
5. 真浏览器三态、响应式、视觉和长时会话。
6. 补旧未测项与有影响的回归。
7. 汇总关闭矩阵、新缺陷、联合结论。

任一阻断失败留证据后继续独立可测项目，不因一个缺陷停止全部验收，也不重复无意义调用。

默认2个测试Agent与少量独立会话，按需增加非YOLO新会话。性能/GC/大量边界用隔离fixture，不跑4000轮真实模型。

持续更新本run的STATUS与资源表，压缩/重启后按Task/会话ID接续，避免重复创建同名测试资源。

## 13. 最终产物

在Hive根目录新写：

- ACP_FIX_REACCEPTANCE_REPORT.md。
- ACP_FIX_REACCEPTANCE_DEFECTS.md。

不覆盖旧独立验收报告或修复报告。

报告开头明确给出：

    ZSession标准ACP：通过/不通过/部分实现/未验证
    ZSession原需求：通过/不通过/部分实现
    Hive后端与恢复：通过/不通过/部分实现
    真实两端联调：通过/受限/不通过
    非YOLO权限：完整通过/部分/未完成
    静谧天文台视觉：通过/需改进/未测
    旧20项：关闭/部分/未关闭/阻塞数量
    新风险用例：PASS/FAIL/BLOCKED/NOT_TESTED
    原55项：本轮覆盖、沿用依据、剩余项
    最重要阻断及影响

每条旧DEF记录：

    旧问题 → 本轮施工声称 → 当前复现/步骤 → 原始证据
        → 实际关闭状态 → 关联新问题 → 下一次复验条件

新缺陷使用RDEF-01等新编号，不重排旧DEF。至少有：归属、严重性、当前文件/行号、需求/官方schema依据、最短复现、预期/实际、影响、原始证据、修复建议。

把文档前后冲突与代码/运行失败分开；若标准帧非法，即便专用两端能互通也不得写“标准兼容通过”。权限或取消仍pending时不能完整通过。

最终提供真实完整主线：浏览器输入→ACP原生轮次/工具→SSE→队列完成→取消/恢复→非YOLO授权→审批callback，以ID和时序串起各层。

收尾只停止本run创建的测试活动，保留证据与可复现会话；不改生产网络、不删除其他资源。向用户给报告入口、主要结论与可用范围。

现在开始独立复验。
