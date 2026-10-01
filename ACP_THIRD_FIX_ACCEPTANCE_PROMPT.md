# ACP第三轮修复独立验收：真实派发、评分校验与强writer

你是独立验收Agent。请验收第三轮Hive修复、ZSession小范围合同补齐，以及之前因SDEF-01被阻断的真实联合链路。

请实际检查代码、运行隔离测试、操作浏览器并提交新报告。不要只转述“已修复”“13/13”“17项PASS”；单侧daemon矩阵、弱模式聊天和完整联合能力分别判定。

本任务是验收，不是修复。允许创建独立测试脚本、代理/夹具与资源；不改产品代码、原报告或历史证据让用例通过。发现失败后继续其它独立可测项，保留新缺陷。

## 1. 目标文件与基线

本轮交付：

    D:\agent_workspace\valuable-projects\hive\HIVE_ACP_THIRD_FIX_REPORT.md
    D:\agent_workspace\projects\zcode-session-manager\ACP_THIRD_FIX_REPORT.md
    D:\agent_workspace\projects\zcode-session-manager\ACP_INTEGRATION.md
    D:\agent_workspace\valuable-projects\hive\UI_API.md

修复要求：

- Hive/HIVE_ACP_THIRD_FIX_PROMPT.md。
- Hive/ZSESSION_ACP_THIRD_FIX_PROMPT.md。

旧独立验收：

- Hive/ACP_SECOND_FIX_ACCEPTANCE_REPORT.md。
- Hive/ACP_SECOND_FIX_ACCEPTANCE_DEFECTS.md。
- Hive/ACP_SECOND_FIX_ACCEPTANCE_PROMPT.md。
- Hive/ACP_FIX_REACCEPTANCE_PROMPT.md及原ACP_INDEPENDENT_ACCEPTANCE_PROMPT.md。

旧证据：

    D:\agent_workspace\tmp\hive-acp-second-acceptance\2acc-20261001-200745-v1

先读适用AGENTS.md、Git状态/文件hash、当前服务代码与版本。ZSession可能非Git，以文件快照/hash锁定。

最新上游合同v0.6.1、adapter声明v0.5.3，必须核对实际源码/运行版本；源文件内部版本值/注释与报告也可能不同，不以报告时间直接推断部署。

## 2. 本轮范围与关键缺口

使用已有YOLO业务路径，工具级权限不重新立项。本轮核验人类Task审批、控制权限和自主授权边界；范围记录要与可信用户指示一致，不通过转述额外修改权限政策。

主验旧SDEF：

- 01：dispatcher属性归属及真正派发。
- 02：兜底评分服务端校验及规范化。
- 03：Hive真实writerToken强接入。
- 04：登录竞态。
- 05：回放文本/工具双层元数据。
- 06：验收工具结构化响应，产品与证据问题分开。
- 07：320px与可读性。

报告仍有明确联合待验：

- Hive强模式需HIVE_ACP_WRITER_KEY=1，默认关闭。
- Hive指称adapter DaemonClient.post丢第三参，writerKey头没有真正到daemon。
- 弱模式会自动release本方带key claim以恢复对话。
- 取消能力仍受原生宿主限制。

这些是验收重点，不预先断定当前仍失败。若已修，独立实测并记录新版本；若仍存在，不能用“机制完成/待上游”关闭SDEF-03。

## 3. 隔离与取证

创建新run：

    D:\agent_workspace\tmp\hive-acp-third-acceptance\<run_id>\
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

独立Hive Home、可用端口、测试项目和2个测试Agent。生产23节点只读观测，不抢任务/取消轮次/写人评分/改方法或授权。

不能复制生产Home保留live句柄启动验收服务，避免eager_attach接管生产会话。

强/弱模式测试均只作用本run资源。禁止全局清claims、kill全部ZCode、停止生产Hive或修改生产环境变量。没有必要不启多个宿主。

writerToken/owner token安全提供，不写入公开帧、日志、截图、报告或普通结果。代理记录“头存在/是否匹配”等脱敏事实，不记录秘密值。

每项保存步骤、版本、预期、实际、verdict、fixture/real、关联ID与时序、绝对证据路径。截图必须实际查看；CLI/API通过不代替浏览器。

## 4. 判定规则

用例PASS/FAIL/BLOCKED/NOT_TESTED/N/A清楚区分。

旧SDEF逐项CLOSED/PARTIAL/OPEN；旧RDEF和原55项做影响回归/可信沿用/剩余矩阵，不必重复所有无关昂贵调用。

分别出结论：

1. Hive真实派发恢复。
2. 服务端有效schema与规范结果。
3. 弱模式真实对话。
4. 强writer真实联合写入。
5. 历史/实时/恢复。
6. 原生取消能力与诚实状态。
7. 浏览器登录、审批、响应式与性能。

核心完整通过必须强模式有真实证据；默认关强校验、自动release、无头owner直通使聊天成功，只能支持弱模式受限结论。

上游daemon直接HTTP带key的200，不证明stdio adapter带key派发成功。三层分别测：daemon、adapter、Hive。

## 5. 先做低成本真实冒烟

顺序：

1. 锁定实际版本和测试配置。
2. 新鲜测试Agent通过真实ACP发送短中文输入。
3. 执行一次写本run marker的真实工具任务。
4. 核对queued/dispatched/running/终态、started/completed事件、原生输入和工具结果。
5. 再开启本run强writer模式，重复真实派发并检查claims。

若弱模式主链仍崩溃，保留证据，不重测所有派发依赖项；继续评分/登录/上游独立检查。若强模式失败，也保留弱模式与其它功能独立结果。

不得手工改conv_inputs、补事件、释放key或退fixture来让“强模式冒烟”成功。

## 6. SDEF-01与旧BLOCKED联合用例

| 用例 | 操作 | 预期 |
| --- | --- | --- |
| M01 | 第一条网页输入走真正dispatcher | 无AttributeError/NameError，原生真实响应，不是fixture或绕过dispatcher |
| M02 | bridge尚未就绪/重连窗口排输入 | 有限回队退避，恢复后自动执行，不假failed、不无限热循环 |
| M03 | bootstrap/长轮busy后重试 | 同requestId最终执行一次，busy不写假turn.started、不派生ID重执行 |
| M04 | input_state=started、receipt先后/缺失/重复 | DB幂等正确；started对应真实输入，收口补事件不制造一次从未开始的轮 |
| M05 | 两Agent复用同requestId | 通知查询按session/agent范围定位，不能仅request_id全局查第一行，状态和started不串 |
| M06 | Generic Exception/拒绝/失败/unknown | 当前input_id正确落错，后续队列有序继续，不e.code二次异常或假completed |
| M07 | Hive/adapter重启queued/dispatched/running/cancelling | 精准对账，不卡死、不双派发；跨重启started事件仍一条，unknown不猜成功 |
| M08 | 原生活动A仍running，后续B queued | 恢复或异常不能抢占；A结果不污染B，B结束不替旧孤儿收口 |
| M09 | UI/control.agent.send/系统inbox唤醒合流 | 单活动轮，业务任务正常领取/回调，key启用不把旧入口全409或偷偷owner绕过 |
| M10 | 协议返回成功，query state为failed/unknown/started/查不到 | Hive不只warning后completed；精确拒绝/未知/待对账，native副作用与台账一致 |

M04不要只数两条事件，记录出现时点与原生接收/活动事实。上游started表示“原生已接收”，未必已执行；running/生成中不能早于真实开始。

旧双修的派生orig#r<ts>兼容路径仍在时，要验证原ID超时重试不会创建第二条UI输入或重复工具；修新主线后不应靠它恢复busy。

## 7. SDEF-03：writer专项强模式矩阵

在独立测试服务设置HIVE_ACP_WRITER_KEY=1，确认配置实际被加载，不能只给shell设置却测试旧进程。

| 用例 | 操作 | 预期 |
| --- | --- | --- |
| W01 | Hive首次ensure/load带key后查claims | owner/instance/generation正确，writerKeyBound=true，凭据不泄露 |
| W02 | 同一个绑定key会话经stdio session/prompt实际执行 | adapter→DaemonClient.post/call→daemon真正收到正确写凭据头，marker落位 |
| W03 | adapter发cancel/其它受管写操作 | 头同样被转发，key不会只支持send；实际结果与原生对应 |
| W04 | 无key/错key/正确key，声明acp但只有owner token | 前两者409、后者成功；不能退无头owner直通绕过 |
| W05 | Hive与adapter分别重启恢复 | 同代际凭据稳定或按受控轮换，合法接续且旧凭据状态清楚 |
| W06 | 实例换代/不同writer/旧key/旧generation/release | 旧writer拒绝，新writer可用；无无条件清claim、旧主体释放新claim |
| W07 | 强模式写失败，错误处理与启动降级 | 明确失败/不可用，不自动解绑key/切弱模式后仍显示强模式通过 |
| W08 | 默认弱模式遇到残留带key claim | 记录release行为、owner/代际校验与审计；不能不知情削弱其它实例的有效锁 |

重点静态检查与实测DaemonClient.post第三参：当前报告显示call支持extraHeaders，但post(path,body)未透传。用隔离代理或严格fake daemon验证所有相关写请求，再以真实绑定key的原生轮次证明。

若缺陷仍在，给新TDEF编号并关联SDEF-03；不要借用旧SDEF-05编号，因为它指回放标记。

Windows凭据文件的安全性按实际ACL/读取暴露路径检查；0600只是代码意图，不宣称已形成OS级Agent沙箱。秘密不得被UI文件预览、普通日志或报告带出。

## 8. SDEF-02：有效schema与规范输出矩阵

重新建立fresh测试Task；非法请求后同Task必须仍未结算，不能把已终态任务复用导致只测到TASK_TERMINAL。

覆盖5类：

- request_type=dual_evaluation无ui.fields。
- 契约键无ui.fields。
- human_eval+相关intent无ui.fields。
- 显式ui.fields。
- 普通任务及相似但不匹配标记。

至少执行以下检查：

| 用例 | 输入/操作 | 预期 |
| --- | --- | --- |
| E01 | artifact_score=999/101/-1，顶层和answers分别提交 | 400+field_errors，Task未终态、无成功callback |
| E02 | bool、字符串/非法类型、空对象/数组、不能解析值 | 按有效schema拒绝；bool不能当0/1数字 |
| E03 | answers=80且顶层=999；相反与判决冲突 | 冲突拒绝，无兼容字段夹带非法值 |
| E04 | 两处相同合法值、分别只有一种表示 | 按同一规范值入账，不重复/丢字段 |
| E05 | 缺必填判决/评分、未知选项、显式step5的77与75 | 有效schema决定必填；错误拒绝，合法步长通过 |
| E06 | 合法0/100，明确可选字段null/缺失 | 0不变null，null不自动补0；必填与可选按任务声明 |
| E07 | GET task eval_fields/版本与浏览器字段/POST校验 | 同源schema一致，显式优先，不误推断普通任务 |
| E08 | 普通显式表单含非双评字段name/rating/date等 | 合法值仍保存，不被normalize_canonical只留双评字段而丢失 |
| E09 | schema版本/Task spec变化后提交旧草稿 | 版本行为明确，旧spec拒绝且草稿保留；不能仅返回eval_schema_version而从不使用 |
| E10 | 自主授权未选/恶意顶层grant/冲突/未知范围 | 不产生非显式授权，不因高分授权，保持既有权限 |
| E11 | 合法浏览器双评分+判决正式提交 | Task done、两个分数/判决/原文正确，实际callback恰一次 |
| E12 | comment、fail及所有同语义公开answer入口 | comment不结单，入口校验一致或明确不同合同，无API绕过 |

对“未评分=null”与兜底schema required=true的关系，按项目/任务已确认约定验证：不能用一份不同于前端的默认required临时改变原语义。若原始任务无明确必填依据且修复新增限制，记录范围变化，不自称已保持。

授权对象、列表字段与兼容key规范化都检查最终result，不只HTTP 200。不要把测试评分/授权写入video-lab实际审美、方法或authorizations。

## 9. 上游小修与完整历史/实时回归

SDEF-05：

- user/agent/thought/tool回放帧的params级和update级replay/replaySeq/kind均按合同存在。
- 标记一致，文本/工具顺序与身份不变，重复load不重复追加。
- SDEF-06结构化响应新证据可读，旧验收证据不被修改。

上游已有独立核心PASS，可有依据地沿用不受小修改影响的部分；但这些联合场景不能沿用单側结果：

- Hive load窗口同时有真实live输出：不能一律丢chunk造成数据缺口。
- 首次绑定/已有历史/replay/pump/SSE最终只投影一次。
- 短回复只到终态快照不被抑制。
- 两会话同时load/live，global seq和会话seq不混用。
- 成功/失败/取消工具历史正确，取消不显示绿色成功。
- 多页/超过limit历史实际遍历，不吞错误成完整load。
- 相同文本不同消息保留，不按正文hash误去重。

最后用native全文与UI最终正文、工具集合/顺序逐项比对。

## 10. 取消、SSE和浏览器真实联调

本轮只测YOLO主线取消，不新开工具级权限实验。

取消要区分准确状态与真正停止：

- instance/generation/expected_turn守卫，旧请求不杀下一轮。
- requested/cancelling/confirmed/failed/unknown真实。
- confirmed=true才cancelled，false/null/超时不假完成。
- 原生停止marker和轮次核实，无伪输入/额外模型轮。
- 中断后新消息在旧轮已结束后再发。
- 15秒窗口未停，确认是在自然结束后end_turn，不是还运行就收口。

如果原生受限，独立报告受限能力与UI诚实处理，不能算取消功能完整PASS。

浏览器还要真实测：

1. 队列chip、头部、思考/工具、最终事件无需刷新一致。
2. SSE网络断、半开、heartbeat活但业务停流、401/重登录。
3. watchdog自动校对/重建能补齐，不重复订阅、不重发输入；合法长工具安静不误报。
4. 登录SDEF-04冷启动立即登录、迟到旧session判断、退出后迟到成功、重复enterApp。
5. 320px退出完整可点、390底导航/抽屉、审批标题不被吸顶遮、滑块刻度可读。
6. 1440/1920真实星图组标题/边计数/节点名无叠字，拖拽/缩放/面板开合正常。
7. 审批跳发起Agent讨论再回卡保留草稿/评分，聊天“通过”不结案。
8. 图片/视频/附件实际预览，AI输入能力如实；多身份/跨项目授权不回退。
9. 隔离23/50节点和长历史性能trace，明确fixture压力与真实业务证据。

采用可用真实浏览器工具，截图保存绝对路径并实际查看。没有浏览器能力记BLOCKED，不以API或quicktest替代。

## 11. 执行顺序与阻断处置

1. 版本、资源、配置锁定。
2. 弱模式真实dispatch冒烟。
3. 强writer三层冒烟，优先发现头转发/自动release。
4. 评分服务端矩阵可独立继续。
5. 修复未决的联合回归与真实浏览器。
6. 小修协议回归和报告。

强模式失败不要靠放宽校验继续并宣布通过；可以另跑明确弱模式以验证派发/评分/浏览器，结论分开。

默认2个测试Agent、少量真实轮次；批量边界、长历史、崩溃时序用隔离fixture并补真实主线，不重跑所有无关昂贵案例。

每阶段更新STATUS，压缩后按已有ID接续，不重复创建/发单。

## 12. 最终产物

Hive根目录新写：

- ACP_THIRD_FIX_ACCEPTANCE_REPORT.md。
- ACP_THIRD_FIX_ACCEPTANCE_DEFECTS.md。

旧报告不覆盖。新问题TDEF-01等，关联SDEF/RDEF/DEF，不混淆编号。产品缺陷、文档偏差、测试工具问题分开。

报告开头给：

    Hive派发恢复
    兜底评分服务器校验
    弱模式真实会话
    强writer联合写入
    历史/实时/恢复
    原生取消与准确状态
    登录/响应式/审批/性能
    SDEF-01～07关闭状态
    旧BLOCKED用例本轮结果
    新缺陷与实际可用范围

每条缺陷包含版本、归属、文件/行号、最短复现、合法请求、预期/实际、影响、证据、修复/复验方向。

真实完整主线以ID与时间串起：

    网页 → dispatcher → 带有效writer key的stdio adapter
      → daemon原生输入/工具 → ACP增量 → Hive事件 → SSE
      → 正确完成 → busy/重启/回放 → 取消分态 → 审批callback

这条强模式主线没有跑通，不能完整通过。文件有token、hash已绑定、daemon直接HTTP矩阵或弱模式completed都不能替代。

收尾只停止本run测试活动，保留证据/可复现资源，不删除旧证据会话，不动生产网络。给用户报告入口与清楚结论。

现在开始独立验收。
