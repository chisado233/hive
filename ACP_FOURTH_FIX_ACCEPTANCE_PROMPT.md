# ACP第四轮修复独立验证提示词

你是独立验收Agent。请读取第四轮产物，实测验证ZSession与Hive的修复，重点关闭TDEF-01～05，并完成此前被busy死锁阻断的联合用例。

直接执行代码检查、隔离测试、真实ACP/ZCode联调和浏览器操作，交付新验证报告与缺陷清单。不要只读报告、复跑施工脚本或把“ALL PASSED”当整个系统通过。

本任务是验证，不是修复。允许创建独立测试脚本、代理/夹具和资源；不修改产品实现、原报告或历史证据来消除失败。

## 1. 输入与版本

本轮产物：

    D:\agent_workspace\projects\zcode-session-manager\ACP_FOURTH_FIX_REPORT.md
    D:\agent_workspace\projects\zcode-session-manager\ACP_INTEGRATION.md
    D:\agent_workspace\valuable-projects\hive\HIVE_ACP_FOURTH_FIX_REPORT.md
    D:\agent_workspace\valuable-projects\hive\UI_API.md

修复要求：

- Hive/ZSESSION_ACP_FOURTH_FIX_PROMPT.md。
- Hive/HIVE_ACP_FOURTH_FIX_PROMPT.md。

原验收基线：

- Hive/ACP_THIRD_FIX_ACCEPTANCE_REPORT.md。
- Hive/ACP_THIRD_FIX_ACCEPTANCE_DEFECTS.md。
- Hive/ACP_THIRD_FIX_ACCEPTANCE_PROMPT.md。
- 按变更影响选读更早的RDEF/SDEF矩阵与原55项。

旧证据：

    D:\agent_workspace\tmp\hive-acp-third-acceptance\3acc-20261001-225426-v1

读适用AGENTS.md、当前代码与改动状态。记录Hive Git/hash、ZSession文件hash、initialize实际版本、daemon与adapter实际加载版本，不只依据文件时间或报告自称。

本轮合同v0.6.2与adapter v0.6.1是不同版本概念，可以并存；同一个adapter对外版本、代码值、日志和报告必须一致。

## 2. 范围和独立结论

使用现有YOLO业务路径，不重新开启工具级权限实验；人类Task审批、control权限和自主授权边界照常验证。

本轮报告明确：

- busy修复声称已在strong-home真实验证。
- post/get已透传extraHeaders，send/interrupt/interaction链补齐。
- 强writer仅在隔离Home启用，生产daemon尚未切换。
- 取消活体和SSE watchdog停流注入没有本轮重测。
- 视觉小项部分沿用旧证据。

分别判：

1. 代码修复及新TDEF关闭。
2. 强模式独立联合链路。
3. 恢复、取消、SSE与浏览器能力。
4. 当前生产部署状态。

隔离强模式全部通过、生产仍弱时，可以写“强模式验证通过；生产切换未执行”。不能写“生产已经强模式”，也不把未部署直接混成代码缺陷。

验收不帮用户切生产配置，不停生产Hive或重启ZCode。生产切换是后续部署任务，本轮只读记录现状。

## 3. 隔离与证据

新建run：

    D:\agent_workspace\tmp\hive-acp-fourth-acceptance\<run_id>\
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

独立Hive Home、可用端口、项目工位、2个测试Agent与少量真实会话。测试强模式启用：

    HIVE_ACP_WRITER_KEY=1
    HIVE_ACP_ADAPTER=<真实adapter命令>

核实测试服务确实加载环境，不测试旧进程。

生产video-lab只读核对身份/布局/当前模式；不抢任务、取消轮次、改模型/审美/方法/人评/授权。

不得复制生产Home保留live句柄启动测试服务，避免eager_attach接管真实会话。存储故障、迁移、GC和大量压力在隔离资源做。

不全局kill、清claims、删旧证据或弹新终端窗口。后台helper静默运行，只停止本run进程。

secret安全提供；writerToken、owner token、凭据正文不进帧文件/日志/截图。代理记录头是否存在/匹配的脱敏事实。

用例保存：操作、预期、实际、版本、PASS/FAIL/BLOCKED/NOT_TESTED/N/A、fixture或real、关联ID、单调时序、原始证据路径。

## 4. 最先完成两条真实冒烟

先强模式，不依赖清claim或owner旁路：

1. 真实Hive Agent绑定key，查正确owner/generation与writerKeyBound。
2. 从网页发送有限工具任务，在本run写marker。
3. 核对stdio→daemon有效key、native输入/工具、事件→SSE→completed，marker一次。
4. 制造一次真实上游busy，让新输入实际收到-32011，之后同ID执行一次。

第二条必须真的经过busy错误分支。仅在长轮期间“先排队再执行”不证明发生过-32011；保存错误帧/日志、retryable_busy和回队时序。

可在本run受控原生会话先启动一项有限任务制造busy，不用生产节点。与正常Hive串行排队分开说明。

任一失败先留最短复现，继续独立可测评分/协议/UI，不通过弱模式凑强模式PASS。

## 5. busy与异常可靠性

| 用例 | 步骤 | 预期 |
| --- | --- | --- |
| Q01 | bootstrap忙窗口，真实SQLite输入触发_fail_input | 无Row.get/二次异常；当前输入正确回队，不永卡dispatched |
| Q02 | 普通长轮忙，连续重试后空闲 | 同requestId执行一次；无orig#r派生、假started或自动interrupt |
| Q03 | 同秒及并发排多个输入，当前一条busy/失败 | FIFO正确，当前input_id精确处理，不误改next_queued |
| Q04 | query_input_state失败/超时、Generic Exception | 错误/未知真实，线程可退出，队列可恢复；不假completed |
| Q05 | 落账短暂失败后存储恢复，隔离注入 | 诊断可见且状态能实际接续；不是只释放_working却被active_input永久挡住 |
| Q06 | 真rejected/failed/unknown与伪造不一致receipt | 原错误/待对账；不能换ID重试已执行/真实拒绝输入；warning不足以证明成功 |
| Q07 | busy/活动中重启Hive与adapter | 已发先对账、未发有序继续，无双副作用；旧轮仍活动不抢占 |
| Q08 | 两Agent同requestId，通知与结果交错 | session/Agent/input隔离；started/终态不串到第一条全局同ID记录 |

Q04/Q05可以严格fixture或可控代理稳定注入，再有真实主线证据。不要修改产品源码后验改后的临时逻辑而称原版通过。

检查dict转换失败/None与输入身份：若对象不可解析，也要有具体可恢复处置，不把“安全落unknown”仅写成注释。

原生副作用/输入次数与UI气泡分别计数。一个marker不等于所有状态机正确，最终查询和事件需一致。

## 6. 强writer与旧绕过

| 用例 | 步骤 | 预期 |
| --- | --- | --- |
| K01 | 同一stdio adapter new/load带key再prompt | 绑定key后实际执行，extraHeaders真到daemon，不是直接HTTP对照 |
| K02 | 无key/错key/对key，声明acp仍带owner token | 前两者409、正确key成功；普通派发不能退无头owner |
| K03 | send/interrupt等受管写路径、adapter重启 | 头都转发；同代际凭据恢复；secret不泄露 |
| K04 | 实例换代/key轮换/旧writer发送取消释放 | 旧key/代际拒绝，新合法写可用；不能释放新实例锁 |
| K05 | UI/control.agent.send/系统唤醒/其它入口并发 | 普通工作统一调度，单活动轮；强模式失败不自动release或切弱 |
| K06 | 只读生产配置与默认弱兼容路径 | 生产是否强模式如实；弱路径自动release的作用范围/审计清楚，不冒称保护已启用 |

daemon、adapter、Hive三层证据都需关联同session/owner/generation。检查正确key实际使用，不只看hash非空。

弱模式如果会释放既有带key claim，记录实际行为与风险、owner/代际核对；不得用这条路径来完成强模式测试。不要改生产环境来规避结论。

## 7. 答复表示、白名单与评分

每次非法测试用未终态fresh Task，被拒后确认Task仍可合法回答且无成功callback。

覆盖：

- 显式ui.fields双评。
- 显式非双评自定义字段name/rating/date等。
- 三种已定义双评兜底标记，无ui.fields。
- 普通自由data任务及相似不匹配标记。

| 用例 | 步骤 | 预期 |
| --- | --- | --- |
| F01 | 显式fields顶层77/75，step5 | 77步长拒、75过，与answers包装等价，不误报未填 |
| F02 | 顶层/answers分别提交、自定义字段、两处同值/异值 | 同值归一、异值冲突；name/rating/date不丢，合法result完整 |
| F03 | 999/101/-1、bool/非法类型、缺必填/未知选项 | 服务端拒绝，不能绕前端入账；被拒Task未终态 |
| F04 | 合法0/100、可选null/缺失、有效schema/版本变化 | 0≠null；必填与可选遵循任务合同；草稿/旧spec处理正确 |
| F05 | 合法表单夹grant/authorize/_verified，顶层与answers两处 | 未声明键拒绝或剥离，不进result/callback；不能规范化后又合回raw_data |
| F06 | 合法追踪metadata及普通自由data | 白名单按合同保留；不白名单化所有自由任务而丢其业务数据 |
| F07 | 未选/非法/冲突自主授权对象与高分 | 不隐式授权，不因分数/别名授权；显式流程按已有权限与范围 |
| F08 | 浏览器合法双评分/判决、评论、讨论回卡与正式answer | 两分独立、原话/引用正确，comment不结单；done与decision分开，callback一次 |

检查GET eval_fields/eval_schema_version与实际浏览器/POST所用schema一致。检查最终result内容，不只返回码。

对结构化结果的自定义字段保留、列表规范化和自主授权对象验证都取实际数据。测试值仅存本run，不进入video-lab真实TASTE/方法库/authorizations。

如不同公开answer入口采用不同合同，说明依据；同语义入口不得仍可绕过校验。

## 8. 未重测联合用例必须补

| 用例 | 步骤 | 预期 |
| --- | --- | --- |
| J01 | load期间真实live输出，已有历史/多会话并发 | _replaying窗口不丢live；正文、工具、顺序与native一致，既不重也不漏 |
| J02 | 重复load、短终态回复、多页历史、global/session seq差异 | 双标稳定，水位正确；终态抑制不丢新短回复，不只读第一页 |
| J03 | 真实长轮和长工具cancel，成功与未确认路径 | 无id通知；true才cancelled；无伪输入/额外轮，实际停止证据独立 |
| J04 | 旧turn取消竞态、中断并发送、cancelling重启 | 不取消下一轮；旧轮结束后再发；Task不因会话状态改变 |
| J05 | SSE断网/半开/无heartbeat/heartbeat在但业务停流 | 明确提示，主动校对/重建补齐，不必须切节点；不重发模型输入 |
| J06 | 合法长工具安静、多个节点来回切、认证撤销重登 | watchdog不误杀/热重连；无重复订阅，草稿按身份和Agent隔离 |
| J07 | 重连/重启的未知结果恢复 | 有对应native输入/turn证据才收口；单独idle/别人的结束不算本输入完成 |
| J08 | 模型/附件/多身份/文件访问与审批保护 | 已有能力不回归；预览≠模型理解；未经授权读写拒绝，人类审批保留 |

上游本轮报告取消已成功一次，Hive报告未活体复测，因此J03/J04必须补真实联合证据。

不能只沿用严格confirmed判定代码。当原生未停，记录失败/未确认；若自然结束后end_turn如实返回，可算诚实处理，不等于取消能力通过。

SSE watchdog也是明确未重测项，需稳定注入停流并真实复核，不能写“机制在”即PASS。

## 9. 版本、UI与性能

| 用例 | 步骤 | 预期 |
| --- | --- | --- |
| V01 | initialize agentInfo、源码、日志、报告/合同版本 | adapter同源0.6.1或真实更新版；合同v0.6.2可独立，变更历史准确 |
| V02 | 冷启动立即登录、迟到旧判断、退出/重登 | 已修认证竞态不回归，无二次reload、凭据无残留 |
| V03 | 1920/1440/390/320全景/会话/审批，真实操作 | 退出有效触达、节点可滚动/选中、授权区完整、标题/刻度可读，无叠字裁切 |
| V04 | 隔离23/50节点、长历史/流式时性能trace | 有环境/测量，分页与短批更新有效，无明显卡顿/订阅计时器泄漏 |

静谧天文台不另换风格；以已确认的空间、层级和阅读目标检查，不靠CSS数值判断优雅。

真实浏览器截图必须实际查看并保留绝对路径：强模式真实对话/工具、busy回队与完成、兜底/显式评分、SSE恢复、320/390、星图全景。

大规模/长历史压力可fixture，标明来源；核心对话与工具至少真实。API回归或quicktest不能代替UI操作。

## 10. 验证顺序与预算

1. 版本/隔离配置锁定。
2. 强writer真实冒烟+实际busy异常路径。
3. 表单直接API矩阵（可独立继续）。
4. 恢复/回放、取消、SSE与浏览器。
5. 有影响的旧PASS回归与性能。
6. 分项报告、生产现状只读记录。

先解决是否存在阻断，不一开始重跑所有原55项。已通过且不受变更影响的项可沿用，写明版本/依据；取消/SSE等明确未测项不可凭沿用关闭。

一个失败留证据后继续独立用例，不无限重试。默认2个测试Agent，少量真实轮次；大量故障/历史/压力用夹具，真实主线复核。

持续更新STATUS，压缩后按已有ID接续，防止重复创建/发送。

## 11. 报告与完成条件

Hive根目录新写：

- ACP_FOURTH_FIX_ACCEPTANCE_REPORT.md。
- ACP_FOURTH_FIX_ACCEPTANCE_DEFECTS.md。

旧报告不覆盖。新增问题用FDEF-01等编号，关联TDEF/SDEF/RDEF，不重排历史。

报告开头独立列：

    TDEF-01～05关闭状态
    实际busy异常路径/FIFO
    强writer三层联合
    表单两表示/白名单/授权边界
    恢复与历史/实时
    原生取消与准确状态
    SSE watchdog与浏览器
    视觉/性能
    代码验证结论
    生产部署现状（强/弱/未确认）
    未测/受限/新阻断

每缺陷有版本、归属、当前文件/行号、最短复现、请求/原始结果、预期/实际、影响、证据路径与修复/复验条件。

核心完整通过必须有同一run真实链：

    网页输入→busy -32011→同ID回队→强key adapter
      →daemon/native工具→marker一次→ACP/SSE→正确完成
      →重启/回放无损→取消分态→审批callback

不能拿正常不busy的一次completed、daemon直连矩阵、未知保护、或生产旧记录清点替代。

生产未切强模式时诚实标pending_deployment，不擅自切换；这与代码/隔离验证是否通过分开。

只停止本run测试活动，保留证据和可复现资源，不动生产网络/旧挂起证据。给用户报告入口、关键结论和实际可用范围。

现在开始独立验证。
