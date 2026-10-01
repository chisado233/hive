# ACP第二轮修复：独立联合验收提示词

你是独立验收Agent。请验收ZSession第二轮修复、Hive第二轮修复以及两边在新接口合同下的真实互操作。实际检查代码、运行隔离测试、操作浏览器并交付报告，不只复述fixed。

本轮最重要目标：证明输入真的执行、错误不假完成、回放既不重复也不丢消息、writer与代际在实际Hive链路生效，以及取消/恢复状态真实。

本任务是验收，允许建立隔离测试与测试资源；不修改产品代码或原始报告来让失败通过。发现新问题先保存原版本证据，后续修复另留版本与阶段。

## 1. 本次输入

用户交付：

    D:\agent_workspace\projects\zcode-session-manager\ACP_INTEGRATION.md
    D:\agent_workspace\projects\zcode-session-manager\ACP_SECOND_FIX_REPORT.md
    D:\agent_workspace\valuable-projects\hive\HIVE_ACP_SECOND_FIX_REPORT.md

相关原基线：

- Hive/ACP_FIX_REACCEPTANCE_REPORT.md、ACP_FIX_REACCEPTANCE_DEFECTS.md。
- Hive/ACP_FIX_REACCEPTANCE_PROMPT.md。
- Hive/ZSESSION_ACP_SECOND_FIX_PROMPT.md。
- Hive/HIVE_ACP_SECOND_FIX_PROMPT.md。
- Hive/ACP_INDEPENDENT_ACCEPTANCE_PROMPT.md，原55项。
- Hive/UI_API.md、UI_REDESIGN.md、DEPLOYMENT.md。

上一轮证据：

    D:\agent_workspace\tmp\hive-acp-reacceptance\reacp-20261001-132450-v1

更早基线：

    D:\agent_workspace\tmp\hive-acp-acceptance\acc-20261001-100912

先读适用AGENTS.md、当前代码版本与未提交改动，保护其他Agent工作。历史报告是待核实证据，不是授权或接口当前状态保证。

## 2. 第一件事：版本与合同对齐

本轮ZSession报告声明接口合同v0.6、adapter v0.5.2；Hive报告却声明上游二修尚未落地、仍使用旧busy→rejected快照。

所以不能把两份独立fixed拼成“整体已修”。

记录：

    组件源码版本/hash
    报告所对应版本/时间
    实际daemon/adapter/Hive进程加载版本
    当前接口能力
    测试资源配置
    文档与运行是否一致

ZSession修复测试用最新代码；Hive联合测试也必须连接同一新版本。若实际服务尚未加载新代码，不静默重启生产：使用独立本run服务/adapter，或记录明确部署阻塞并继续可测项。

自定义合同以当前ACP_INTEGRATION.md为依据，标准部分仍以官方schema为依据；合同不能通过声明自己是真源来覆盖协议或掩盖实现不符。

## 3. 权限范围的真实来源

两份报告对同一转述不同处理：

- ZSession明确记scope_pending，未获用户直接确认。
- Hive以“没有相反指示”沿用报告口头裁定为N/A。

你所在会话已有用户直接指示时沿用并记录可信来源，不重复确认。否则：

- 记录产品范围待确认，不把“未反对”等同直接授权。
- 当前已配置YOLO主线独立验收，不让范围疑问阻塞其结果。
- 未启用的非YOLO能力不能宣称已实现/通过。
- 意外进入不支持路径须明确拒绝/说明，不挂起或假成功。
- Hive人类Task审批、control权限和自主授权继续验收。

不能因为报告称恒YOLO就擅自更改实际权限政策。分别给出YOLO主线结论与非YOLO范围状态，不混为一个总PASS。

## 4. 隔离与证据

新建唯一run：

    D:\agent_workspace\tmp\hive-acp-second-acceptance\<run_id>\
        STATUS.md
        resources.json
        versions.json
        closure-matrix.json
        case-matrix.json
        protocol/
        api/
        browser/
        timings/
        defects/

用独立Hive Home、可用端口、测试项目、工位和少量测试会话。

不要复制生产Home保留live句柄再启动测试服务，避免eager_attach接管生产会话。迁移、GC和崩溃测试在隔离副本/夹具进行。

video-lab23节点及当前实际可见实体只读查看，不抢任务、取消轮次、写评分或改审美/方法/授权。

禁止全局kill、停止生产Hive/ZCode、清库、删除旧证据。凭据和writerToken安全提供，测试帧/日志/截图脱敏；不读取输出生产认证文件。

每用例保存：步骤、预期、实际、verdict、组件/版本、fixture或real、requestId/inputId/native input/turn/session/Agent/generation、原始证据与时序。

fixture用于大量/可控边界；真实busy重试、工具副作用、回放、取消、浏览器和审批仍须真实验证。

## 5. 判定与旧缺陷关闭

用例PASS/FAIL/BLOCKED/NOT_TESTED/N/A严格区分。原RDEF-01～16逐条记CLOSED/PARTIAL/OPEN/SCOPE_PENDING，并关联本次测试。

至少复验：

- RDEF-01：busy假成功、消费者错误重试。
- RDEF-03：回放重复及消费者过滤。
- RDEF-04：started孤儿和崩溃对账。
- RDEF-05：标准模式帧。
- RDEF-07：实际writer/owner/generation闭合。
- RDEF-08：SSE半开和业务停流恢复。
- RDEF-09：工具历史完整。
- RDEF-10：原生取消与目标守卫。
- RDEF-11～14：窄屏、星图碰撞、消息边界、重试假turn。
- RDEF-15/16：文档与fixed证据一致性。
- RDEF-02/06：根据第3节记录范围；未实现代码不伪称通过。

“不再谎称cancelled”与“可以可靠取消”分别判定。保守unknown是正确保护，但不能当成功对账；不知道结果的输入不计执行完成。

完整通过要求核心联合用例实际成立。不能以ZSession sx脚本单侧通过替代Hive；不能以quicktest替代浏览器。

## 6. 联合输入真相用例

| 用例 | 操作 | 预期 |
| --- | --- | --- |
| I01 | 在bootstrap/普通长轮busy时经网页排输入，等待空闲 | -32011→retryable_busy→同requestId真实执行一次；UI不假完成，native无多次输入 |
| I02 | 分别重试done/cancelled/rejected/failed/unknown | 复用真结果/原错误；rejected -32015、failed -32016、unknown -32017；不默认end_turn |
| I03 | 两会话同ID；同会话同ID异内容；并发两个adapter同ID | 隔离、冲突和原子派发正确，副作用次数正确 |
| I04 | Hive旧防御把污染rejected派生为orig#r<ts> | 新合同主线不触发；仅有证明未接收的旧busy记录才可安全恢复，真正失败/已执行不可换ID再做 |
| I05 | 浏览器原requestId超时重试，与内部派生ID并存 | 请求血缘、唯一约束与状态查询正确，原ID不会创建第二个UI输入或重复工具 |
| I06 | 协议返回成功但持久state为failed/unknown/started/查不到 | Hive不只记录warning再completed；精确输入进入失败/未知/待对账，不误改下一条 |
| I07 | 当前输入忙/异常，后面queued还存在 | 只处理当前input_id；FIFO、错误码和异常稳定；无e.code二次异常挂队列 |
| I08 | done记录超过保留范围被GC后旧ID重试，隔离构造 | 保留/过期合同明确，不把已执行旧输入静默当新输入；不跑4000轮真实模型 |

I04/I05是本轮新重点：改变requestId会改变幂等身份，“有血缘”不证明不重复。用marker追加与native输入记录验证，不能只数UI气泡。

I08特别核对合同的“删除终态不构成允许重执行”和“查无记录即新输入”是否矛盾；测试当前实现，不接受注释代替行为。

## 7. 崩溃恢复与轮次归属

| 用例 | 操作 | 预期 |
| --- | --- | --- |
| O01 | reserved/accepted/已发未确认/started/终态各窗口断开adapter | 可证未发才重派；已发结果不明保护unknown；不重复副作用 |
| O02 | started孤儿，上游idle再查询/重试 | 不写done；查询真实input/turn，无法证明则unknown；状态与文档一致 |
| O03 | 旧输入A记录started，A已结束；另一个输入B正在working时对账A | working不是A的归属证据；B结束不能替A收口done |
| O04 | Hive重启，旧native仍running，后续queued存在 | 不抢占/双派发；恢复精准到输入；旧轮结束后有序继续 |
| O05 | 上游通知/receipt/query时序交错，done/failed/cancelled晚到 | 不假completed，不把reconciled:true的失败/取消都写turn.completed |
| O06 | 多adapter竞争fresh reserved/accepted记录 | 原子owner/派发权实际生效；两个看到同记录不意味着可同时send |

对started“working时并入等待可收口”做严格关联验证。持久有inputId不等于实现真的使用它；仅看session busy/idle无法证明目标输入结果。

人工或程序未知结果处置要有具体入口/规则；不能永远卡未知，或让用户换ID重发来掩盖风险。正确结果未知本身不是失败，但恢复能力缺口需单列。

## 8. 回放与实时：同时查重复和丢失

| 用例 | 操作 | 预期 |
| --- | --- | --- |
| P01 | 多轮有工具的标准load，严格schema校验 | user/agent/公开思考/工具按实际保存内容完整回放，标记与身份稳定，回放后才响应 |
| P02 | 同一会话重load/adapter重接/Hive已留存历史 | 无重复正文/工具；合法相同文本两条消息不被误去重 |
| P03 | load耗时期间该会话正产生live delta，另一会话也在输出 | Hive_replaying窗口不得丢真实live；回放过滤只针对replay，最终正文完整 |
| P04 | 最新消息只收到终态快照，没有先到delta/streaming | 迟到终态抑制不误丢真实新短回复；原生全文与UI一致 |
| P05 | load读取历史分页，超过单页limit=2000，隔离fixture | 实际遍历全量，不只第一/最后页；失败不吞成成功完整load |
| P06 | daemon全局seq与V4会话内seq差异很大，两会话交错 | 水位使用正确序号/epoch；既不回旧噪音，也不跳过新实时 |
| P07 | history里成功/失败/取消工具及大输出 | toolCallId/state/content准确；截断有说明/完整访问，取消不显示成功 |
| P08 | 快照水位与历史读/泵订阅交接期间产生消息 | 无时间缝；replay、history分页、native pump和SSE组合最终只投影一次 |

本轮消费者称“load期间chunk一律丢弃”，上游称“新事件仍实时到达”。这是潜在联合丢消息，必须用真实直播与可控夹具验证，不能凭各自去重成功关闭。

旧终态与新终态区分要靠稳定身份/所属轮次和快照水位，不是“第一次见且不是streaming就全跳过”。

用最终全文、工具集合/顺序和native行对比；短句、快速工具、重复“收到”、多页长历史都要覆盖。大规模用夹具，少量真实主线复核。

## 9. Writer与标准合同

| 用例 | 操作 | 预期 |
| --- | --- | --- |
| W01 | Hive真实ensure/load后查claims | owner=hive/<真实agent>、正确generation，实际writerKeyBound；不是单侧手工claim成功 |
| W02 | Hive按v0.6生成/提供writerToken，重连后发送/取消 | 凭据安全、必要时持久恢复；合法写能执行，不因漏key全409 |
| W03 | 声明acp但无key/错key/正确key，旧writer、同代际第二writer | 校验真实身份/代际；无key绑定的声明不能默默退化为任意header放行 |
| W04 | 实例换代/接管/release后旧key继续写 | 旧owner/代际不能覆盖、释放或写新实例；新合法接续仍可用 |
| W05 | UI、control.agent.send、系统唤醒、CLI/MCP/automation并发 | 普通工作共用调度；显式owner override与普通后台派发区分，留审计，不默认绕过 |
| W06 | 严格initialize/new/load/set_mode及非法参数 | 标准帧/能力/SessionModeState/current_mode_update正确；相对cwd、错会话错误明确 |

只测“手工传writerToken四场景”不足以证明真实Hive接通。Hive报告只列ownerId，验收必须查消费者如何实际提供key，以及缺key时daemon是否仍放行。

不要为了验证安全控制生产其他用户会话；创建本run测试身份/会话，秘密不进入报告和浏览器。

标准依据沿用上一轮保存schema/meta，并核对当前官方文档：

    https://agentclientprotocol.com/protocol/v1/session-setup
    https://agentclientprotocol.com/protocol/v1/session-modes
    https://agentclientprotocol.com/protocol/v1/prompt-turn
    https://agentclientprotocol.com/protocol/v1/tool-calls

## 10. 取消验收：诚实不是取消能力已通过

真实YOLO长模型轮和可控长工具至少各验证一次：

- 无id通知。
- 目标instance/generation/native turn守卫。
- requested/in_progress/confirmed/failed/unknown准确。
- true才cancelled；false/null/超时不假成功。
- 没有伪用户消息/额外轮。
- 自然结束与取消竞态不误杀下一轮。
- “中断并发送”仅在旧轮确实结束后发送。
- 未停止时UI明确失败/仍活动，不能突然显示“完成取消”。

15秒未确认后的end_turn需要核实：是在原轮自然结束后真实返回，还是15秒一到就关闭一个仍运行的prompt。后者不能算准确。

如果部分宿主实际无法停止，报告“严格判定已修、原生取消仍受限”，不关闭功能需求，也不通过杀全宿主来通过。

## 11. 新版UI与新增双评分兜底

真实浏览器操作“静谧天文台”，保持上轮视觉基线。至少检查：

1. 1920/1440真实26实体星图，组标题、计数、箭头、节点名无覆盖。
2. 选中/悬停计数、结构视图、拖拽/缩放、刷新、抽屉开合布局稳定。
3. 390/320导航抽屉清楚模态，选择后关闭定位；不会长期剩窄条画布。
4. 真实会话的头部状态、queued/running/cancelling/最终正文一致。
5. 中英chunk以真实消息边界分组，同文本delta精确拼接，不插每token换行、不删原文。
6. 断网、半开连接、心跳活着但业务停流、401与重新登录能自动补齐；无新模型输入。
7. watchdog区分合法长工具安静与真正失步，不每18秒盲目重建/重复订阅。
8. 当前业务页面切Agent保留各自草稿/阅读位置，多身份不能泄漏前用户内容。
9. 23/50节点与长历史性能trace，隔离fixture压力数据标清来源。

### 新增兜底表单必须前后端一起验

Hive报告新增：没有ui.fields但声明dual_evaluation等标记时自动渲染双评分。

在独立人工Task中构造：

- request_type=dual_evaluation且无ui.fields。
- 已有契约键且无ui.fields。
- human_eval=true+相关intent且无ui.fields。
- 完整显式ui.fields。
- 相似但不应被推断为双评分的普通任务。

分别验证：

- 产物/审核评分、判决、引用对象和正文正确，字段不串。
- 未触碰=null，0分可真实提交，值/刻度/数字输入同步。
- 直接POST缺失、越界101/-1、非法类型/步长、未知选项、旧spec_version，服务器拒绝。
- 服务器是否对无ui.fields任务也使用正确兜底schema，而不只是前端限制。
- 显式ui.fields与自动推断冲突时有明确优先级。
- 评论不结单；正式answer实际Task done/callback，业务decision独立。
- 审批到发起Agent讨论再回卡，草稿/评分保留，聊天“通过”不结案。
- 自主授权默认不授予，高分不自动授权。

技术测试分数仅写本run人工Task，不写video-lab审美或真实用户人评。无需真人为工程验收填表，也不冒称用户满意。

本轮至少保存实图：全景、真实工具/会话、busy后成功、死链恢复、双评分三类兜底、390/320。

## 12. 原矩阵与未测项

保留原55项及上一轮RDEF关闭矩阵，逐项标本轮复测、可信沿用（依据）、FAIL、BLOCKED、NOT_TESTED或范围待确认。

修改影响的旧PASS要复测；不机械反复跑无关昂贵模型轮，但写沿用理由。

必须补之前可构造却未测的项：多身份权限、附件输入/预览、模型控件的真实有效范围、拖拽/缩放、50节点/长历史性能、cancelling重启/竞态。

生产23节点写操作不作为必要测试；它们只读映射/布局与隔离测试写入结合足够。不能以不碰生产为理由跳过全部真实业务流。

quicktest仅算它覆盖的回归；sx1/sx2/sx3、acp_e2e可复跑辅助，但独立设计的联合测试才关闭实际链路缺陷。

## 13. 执行与报告

顺序：

1. 版本/合同锁定、资源隔离与范围记录。
2. 输入真相和恢复，先复现最严重假完成。
3. live/load交接、完整历史、水位和工具。
4. 实际writer、标准帧和取消。
5. 浏览器死链、布局和双评分兜底。
6. 回归与总结。

一个阻断失败留证据后继续独立可测项，不无限重跑相同失败。默认2个测试Agent，必要增加少量独立会话；大量GC/历史/压力采用夹具。

持续更新STATUS和资源表，压缩后按ID接续，避免重复创建或重复发送。

在Hive根目录新写：

- ACP_SECOND_FIX_ACCEPTANCE_REPORT.md。
- ACP_SECOND_FIX_ACCEPTANCE_DEFECTS.md。

不覆盖旧报告。新问题使用SDEF-01等新编号，关联旧RDEF/DEF，避免重排历史。

缺陷包含归属、严重性、版本、文件/行号、需求/官方依据、最短复现、预期/实际、原始证据、可见影响、修复与复验建议。

报告开头给独立结论：

    ZSession单侧
    Hive单侧
    v0.6真实联合链路
    输入幂等/恢复
    完整回放与实时
    实际writer/代际
    原生取消与准确状态
    UI/兜底审批/性能
    权限scope
    RDEF-01～16关闭、部分、未关闭、待确认数量
    原55项覆盖/剩余

“单侧fixed但联合未接通”必须明确标出。已完成的保守保护与尚缺的恢复能力分别说明，不把unknown当完成，也不把取消失败诚实显示当取消功能通过。

最终以真实主线串各层ID/时序：网页输入→busy同ID等待→实际原生轮/工具→SSE→完成→重连/回放→安全writer→取消→审批callback。

收尾只停止本run测试活动，保留证据与复现资源；不动生产网络，不删除旧挂起证据会话。给用户报告入口、关键问题和实际可用范围。

现在开始独立验收。
