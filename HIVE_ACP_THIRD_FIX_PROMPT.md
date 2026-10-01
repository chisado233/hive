# Hive第三轮修复：恢复派发、服务端评分与writer接入

你负责Hive第三轮修复。最新独立验收已经证明ZSession协议核心通过，本轮集中修Hive，不重写已经通过的上游能力，也不再以代码路径/旧记录清点替代真实派发。

请直接修复、测试并联调，恢复真实会话链路后完成上轮全部被SDEF-01阻断的关键用例。最终交付可运行代码、真实证据与新报告。

## 1. 范围与依据

你修改：

    D:\agent_workspace\valuable-projects\hive

ZSession只读：

    D:\agent_workspace\projects\zcode-session-manager

读适用AGENTS.md、当前Git状态与：

- ACP_SECOND_FIX_ACCEPTANCE_REPORT.md。
- ACP_SECOND_FIX_ACCEPTANCE_DEFECTS.md。
- ACP_SECOND_FIX_ACCEPTANCE_PROMPT.md。
- HIVE_ACP_SECOND_FIX_PROMPT.md、HIVE_ACP_SECOND_FIX_REPORT.md。
- UI_API.md、UI_REDESIGN.md、DESIGN.md。
- 上游当前ACP_INTEGRATION.md v0.6与ACP_SECOND_FIX_REPORT.md。

证据根：

    D:\agent_workspace\tmp\hive-acp-second-acceptance\2acc-20261001-200745-v1

主修SDEF-01、02、03、04、07。SDEF-05由ZSession另做小范围合同补齐；SDEF-06是验收工具记录问题，不冒充产品缺陷。

已有通过项保留：上游输入真相/幂等、标准模式、完整回放、writer机制，以及Hive单调FIFO、陈旧实例拒绝、审批既有校验等。

不要改ZSession源码、删除失败记录、重建video-lab23节点网络、修改其审美/方法/授权。

本轮使用既有已配置YOLO业务路径，不新增工具权限实验；已有用户直接裁定由你本会话沿用并同步范围说明。人类Task审批与Hive control权限继续保留。

## 2. SDEF-01：修属性归属，并恢复真实事件语义

验收版本96b3c1a：

- AcpBridge.__init__初始化_started_inputs。
- ConversationService._dispatch_one却使用self._started_inputs。
- 首次派发即AttributeError，任何联合消息都不能执行。

先修正确类/职责归属，移除无用错位状态。检查所有新增实例字段的初始化、重连、重启与销毁路径，避免只修这一个同名属性又漏其他路径。

不能只补一行后宣布通过：

- 首条输入必须真实通过ConversationService→AcpBridge→ZSession→原生轮次。
- 不依赖手工修数据库、不临时绕过dispatcher、不回退fixture。
- Generic Exception正确落到本条input_id，后续队列仍能运行。
- busy重试保持同一业务身份，不重复副作用。
- turn.started需要真实对应输入的活动事实，派发尝试不是原生开始。
- started去重不能仅依赖重启丢失的内存set；跨重启事件投影/终态仍正确。
- queued/dispatched/running/cancelling/最终态在UI与原生一致。

实际验证“turn.started一次”：先busy至少一次，再空闲后真实接收。不得在第一次被busy拒绝时就写started，然后用set阻止真正开始的事实。

写有意义的回归测试触发真实dispatcher：严格fake ACP服务可覆盖状态时序，但至少一条真实ZCode轮次和工具必须跑通。

## 3. SDEF-02：兜底评分必须在服务端复验

目前无ui.fields的dual_evaluation任务由前端生成表单，_human_answer却不校验。验收直接POST artifact_score=999成功入账。

建立统一“有效表单”解析规则：

1. 显式合法ui.fields优先。
2. 无显式fields且符合已定义dual_evaluation契约，使用同一版本的兜底schema。
3. 普通任务不误推断为双评分。
4. 后端和前端取得相同字段、类型、范围、步长、必填、选项和默认值。

优先由服务器提供有效schema，前端消费；或共享可版本化的声明，避免Python与JS各自复制两套很快分叉。

保持项目原有“未评分=null，0是真实分值”的语义。必填/可选依照任务与有效schema，不为修999漏洞擅自把全部可选评分改必填。

检查全部答复表示：

- data.answers内结构化字段。
- normalizeDualAnswers产生的顶层artifact_score/review_score/decision等兼容字段。
- 嵌套与顶层同时出现且冲突。
- 缺answers却带非法顶层分值。

不能只校验answers=80，然后仍把顶层artifact_score=999写入结果。解析成唯一规范值；冲突明确拒绝；校验后再生成必要兼容字段。

服务端拒绝：

- 越界999、101、-1。
- 非法类型、布尔冒充数字、无法解析值。
- 与schema不符的步长/选项。
- 缺必填判决或字段。
- 未授权/未知的自主授权内容。
- 旧spec_version。

错误返回400/相应业务冲突及明确field_errors，不能改变任务终态、生成成功回执或部分写入非法结果。

验证所有同语义公开答复入口是否一致；避免网页端补好后另一API仍能绕过。若有明确的不同合同，文档说明并按该合同验证，不能靠客户端校验保护数据。

用独立测试Task覆盖：

- request_type=dual_evaluation无ui.fields。
- 契约键无ui.fields。
- human_eval+相关intent无ui.fields。
- 显式ui.fields。
- 普通任务与相似但不匹配标记。

合法0/100、未评分null、两个独立分数、正式decision/Task done/callback都验证。测试评分不得写入video-lab正式人评、TASTE或授权。

## 4. SDEF-03：真实writerToken接入

上游v0.6已实测支持writerToken及daemon hash校验，但Hive只传ownerId，导致实际会话未绑key。

实现真实派发身份：

- 为受管范围生成足够随机的服务端writerToken。
- 与ownerId、session/Agent、instance/generation关系明确。
- 安全保存并跨Hive/adapter重连恢复，不在浏览器、普通元数据、日志、截图或协议证据中回显。
- ensure/load按合同传writerToken，adapter正常写入自动携带key。
- 登记后核实writerKeyBound=true，不把“claim存在”当凭据已启用。
- 发送、取消、重连、实例换代、释放、受控轮换都正常。
- 旧key/旧代际不能继续写新实例。

使用现有Hive受保护凭据保存机制或同等清楚的安全位置，不把token放进公开conv_bindings/result/prompt/agent.md。

不要用无头owner直通绕过缺key来让测试成功。显式主人override可以存在，普通UI/后台派发必须使用受管writer。

control.agent.send、系统inbox唤醒和网页消息仍统一调度；启用key不能让业务通知全部409，也不能让它们偷偷沿owner旧HTTP入口绕开队列。

真实测试矩阵：

- Hive首次绑定claims和writerKeyBound。
- 正确key派发工具一次。
- 无key/错key409。
- adapter与Hive分别重启后合法接续。
- 旧generation/key写入/取消/释放拒绝。
- 同会话第二writer与多会话隔离。

凭据测试只在本run身份/会话；不扫描生产其他用户token。

## 5. SDEF-04：登录与启动竞态

验收两次出现登录已成功而app仍hidden，二次reload才恢复。

查boot/session检查、用户点击登录、进入应用、auth-lost和异步旧请求的竞争：

- 使用清楚的认证状态机/请求代次，迟到的未登录检查不能覆盖较新的登录成功。
- 登录后确认session并驱动进入应用、关闭登录卡/清空凭据。
- enterApp多次调用不重复注册polling、SSE、事件监听。
- logout/认证撤销及时停止旧订阅，并保护按身份隔离的草稿。
- 刷新恢复登录态与已有页面状态一致，不能必须再reload一次。
- 保持HttpOnly/同源凭据，不用localStorage明文token“解决”。

测试：冷启动立即登录、登录中旧session查询返回、快速刷新/重登、网络延迟/断线、退出后迟到登录响应。

模拟浏览器页面时序与真实IAB均验证，不因“环境竞态”而不处理可控状态。

## 6. SDEF-07：320px、吸顶与评分可读性

保留静谧天文台方向及已修390布局，精修：

- 320px退出入口放可发现的图标/溢出菜单，完整可点、有标签。
- 顶栏项目/审批/身份的优先级清楚，不挤出控件或逐字竖排。
- 审批卡第一行标题不被吸顶栏遮挡；滚动定位使用合理scroll-padding/offset。
- 滑块刻度、范围、未评分和当前值有足够对比；不把弱文字继续压暗。
- 下抽屉、输入区、关闭控件和底导航同时出现时无遮挡。
- 320/390/768/1440/1920实图检查，代码块局部滚动不拖坏全页。

不能只修改CSS值而不看真实表单和页面。

## 7. 修完阻断后必须补完的联合验证

SDEF-01导致旧验收大量BLOCKED，本轮必须实际跑这些，不能引用旧单侧PASS替代：

1. 网页中文输入→原生真实增量→工具结果→正确completed。
2. bootstrap/长轮busy→同requestId等待→真实执行一次，不派生ID制造重复。
3. rejected/failed/unknown按原状态，协议receipt可疑不假完成。
4. 重启queued/dispatched/running/cancelling，精准对账与FIFO继续。
5. load期间真实live，replay/history/SSE不重不丢。
6. writer启用后的control/UI/系统唤醒合流。
7. 取消请求/确认/失败分态与expected turn守卫；中断不误杀下一轮。
8. SSE断网、半开、heartbeat活但业务停流，watchdog补齐，无新模型调用或订阅泄漏。
9. 兜底双评分合法/非法直接API与浏览器，实际callback。
10. 会话头、队列、思考区与实际原生一致；321附近窄屏变化也完整。

取消能力仍受宿主限制时，诚实显示受限/未确认，不能为了关闭本轮把自然end_turn写成cancelled。本次不要求改ZSession源码，但必须验证Hive正确处理真实结果。

## 8. 测试顺序与完成条件

顺序：

1. 锁定版本与独立Home/端口/测试资源。
2. 修SDEF-01，立即跑一条真实派发，证明主链恢复。
3. 服务端兜底schema和writer接入。
4. 补全部被阻断的联合场景。
5. 登录、窄屏、可读性。
6. 针对性回归，真实浏览器截图与报告。

不要一开始重跑原55项耗掉大量调用；先恢复主链再扩大必要覆盖。

隔离资源操作，禁止全局kill、清库或删除旧证据。源码变更保护他人工作；当前已修项复验不重复改。

交付：

- HIVE_ACP_THIRD_FIX_REPORT.md。
- 更新UI_API/部署/Hive skill相关说明。
- 测试脚本、完整命令、fresh run原始证据和截图绝对路径。
- SDEF-01/02/03/04/07逐条根因、修改、实测与剩余依赖。
- 被旧SDEF-01阻断用例的最新结果矩阵。

不覆盖二修报告。主链必须有本轮真实派发证据，不再用生产旧completed清点作“实测”。
