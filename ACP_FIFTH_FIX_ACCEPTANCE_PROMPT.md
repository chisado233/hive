# ACP第五轮独立验收：安全收口与原位恢复

你是独立验收Agent。请实测ZSession/Hive第五轮修复，验证FDEF-01恢复对账、FDEF-02面板原位恢复和FDEF-04体验。保留已通过的强writer、busy、FIFO、评分能力，不重新做无关的大范围施工。

直接检查代码、运行隔离测试、完成真实联合故障重放与浏览器操作，提交新报告。不要仅数“查询后done”“暗号出现一次”就宣称安全恢复。

本任务是验收，不是修复；可建立测试脚本、代理/夹具与资源，但不修改产品、原报告或旧证据让失败通过。

## 1. 目标与基线

本轮产物：

    D:\agent_workspace\projects\zcode-session-manager\ACP_FIFTH_FIX_REPORT.md
    D:\agent_workspace\projects\zcode-session-manager\ACP_INTEGRATION.md
    D:\agent_workspace\valuable-projects\hive\HIVE_ACP_FIFTH_FIX_REPORT.md
    D:\agent_workspace\valuable-projects\hive\UI_API.md

修复要求：

- Hive/ZSESSION_ACP_FIFTH_FIX_PROMPT.md。
- Hive/HIVE_ACP_FIFTH_FIX_PROMPT.md。

原验证：

- Hive/ACP_FOURTH_FIX_ACCEPTANCE_REPORT.md。
- Hive/ACP_FOURTH_FIX_ACCEPTANCE_DEFECTS.md。
- Hive/ACP_FOURTH_FIX_ACCEPTANCE_PROMPT.md。

原J05证据：

    D:\agent_workspace\tmp\hive-acp-fourth-acceptance\4acc-20261002-004613-v1

读适用AGENTS.md、当前源码/改动和真实运行版本。重点文件：ZSession manager.mjs/http.mjs/acp.mjs；Hive convbridge.py/webui.py/conversation.py/main.js/detail.js/conversation.js。

## 2. 先核对双方实际联合版本

ZSession报告声明v0.6.3恢复引擎，Hive报告却说上游五修未落地、用v0.6.1原料自行对账。两份fixed不能直接合成联合PASS。

锁定：

    文件hash/Git
    adapter initialize版本
    daemon实际加载的manager/http版本
    Hive实际代码与强模式配置
    新reconciliation合同是否实际返回/被消费者使用

本轮在同一最新版强writer链路验证。daemon对账引擎改变不是只换adapter进程就生效；核实实际进程加载代码。

生产只读观察，不为验收重启或切生产。未加载新代码时用独立本run服务，或标明确部署阻塞，继续其它独立项。

## 3. 必须独立验证的关联问题

Hive报告把“全文1:1 user回显”称为身份链接。全文完全相同也不唯一：

- 同会话可能反复收到“请查看inbox”“继续”“收到”等相同消息。
- 旧历史已存在相同指令，新输入可能根本没发出去。
- 不同轮次可包含同样正文。

因此自动收口必须有可验证的原生身份/轮次关联，不能只凭文本相等、时间接近、session空闲或后面有assistant文字。

重点检查两端：

- ZSession send后回查“最新user消息”形成nativeMessageId，是否确实属于本请求而非旧消息/并发另一输入。
- inputId→promoted_message_id、nativeMessageId→message/轮次关系，是否验证会话/范围。
- 一条assistant消息completed是否是整轮终态，而非工具调用之间的中间模型回复。
- 查历史只取第一条assistant、最后一个同文user或200条尾部时，是否误配/提前done。
- 上游matched/verified=true是待核验证据，不是免检凭据。
- Hive是否接入新reconciliation合同，还是继续用文本兜底把上游unknown改成completed。

不提前判定所有路径失败，以当前代码和独立反例实测为准。只测唯一暗号不足以证明身份正确。

## 4. 隔离、权限与证据

创建run：

    D:\agent_workspace\tmp\hive-acp-fifth-acceptance\<run_id>\
        STATUS.md
        resources.json
        versions.json
        case-matrix.json
        closure-matrix.json
        protocol/
        native-evidence/
        api/
        browser/
        timings/
        defects/

独立Hive Home/端口、2个测试Agent、强writer配置与少量真实会话。生产23节点只读，不抢任务、改模型/方法/授权或写真实人评。

只停止本run的adapter/测试daemon，禁止杀ZCode宿主、清生产claims、删旧证据。不要复制生产Home保留live句柄启动测试服务。

凭据安全提供，原生DB查询限定测试会话，只输出必要脱敏记录，不导出整库/认证文件。

本轮使用YOLO主线，工具级权限不新立项；人工对账API权限、业务人类审批和control仍需验证。

每项保存：版本、步骤、预期、实际、PASS/FAIL/BLOCKED/NOT_TESTED/N/A、real/fixture、request/input/native message/turn/session/instance/generation关联、原始证据和单调时序。

## 5. 最小真实J05重放

同一run完成：

1. 通过网页发有限长轮，带一次可核实工具marker。
2. 确认原生已接收/开始，记下原生身份；另准备草稿、上滚位置、展开工具卡/思考区。
3. 仅终止本run adapter，Hive/SSE/原生会话继续。
4. 观察unknown保护、桥重连、原生继续产出。
5. 原生真实结束且证据可取得后，≤60秒自动收口；无证据则有可实际使用的人工处置，不猜。
6. 同一Pane原位更新，草稿/阅读位置/折叠态保留。
7. 核对native输入/轮次/marker次数，恢复期间零新send/prompt、零额外工具执行。
8. 重复查询、重连、刷新仍不双写终态/正文。

60秒从“原生已终止且可查到对应证据”起算，记录落库延迟，不要求强停合法长轮。

对账可以修正输入登记，但对原生工作执行只读；不要把“登记更新”误算重执行，也不要因GET名字就假定没有账本变化。

## 6. 原生关联与错误收口用例

| 用例 | 操作 | 预期 |
| --- | --- | --- |
| R01 | 真实J05长轮adapter死亡、完成后新连接查询 | 按本输入原生证据收口，副作用不增加，证据字段可查 |
| R02 | 原生仍运行时重复查询，包括已有assistant文字 | 保持running/observe或未知，不因中间文字先done |
| R03 | 同一会话两次完全相同指令，分别不同requestId | 各自正确锚定/终态，不能用最后同文回显替两个输入收口 |
| R04 | 历史已有同文成功轮，新同文输入未接收/拒绝/未知 | 不借旧轮答案把新输入completed |
| R05 | A未知/失败/取消，后续B有assistant最终答案 | B不能替A证明成功；状态/stopReason分开 |
| R06 | 同一用户轮包含多个assistant消息/多步工具 | 中间assistant completed不是整轮完成，直到对应原生终态才收口 |
| R07 | send后DB落盘延迟，另一输入/显式owner操作并发 | 最新user回查不能误取别人的message；唯一关联或安全unknown |
| R08 | inputId未promoted但仍在原生队列等待 | 不能误判“没promoted所以可重发”，旧队列不能随后再执行同输入 |
| R09 | 锚点缺失/属于另一session、记录超过历史页尾 | 不误配、不泄露；明确manual_required/证据缺口或正确分页 |
| R10 | 失败/取消/自然结束与迟到事件 | 精确结果，终态不回started，不把所有有assistant记录都end_turn |
| R11 | adapter/daemon重启、多连接/并发查询 | 关联持久，幂等收口；无重复输入/终态/工具 |
| R12 | 旧无锚点记录、重复正文、不唯一匹配 | 保守unknown+可执行处置，禁止正文/时间窗猜完成 |

重复文本和中间工具轮是本轮关键反例。夹具可稳定构造旧记录/并发/迟到，至少补真实同文两轮与多步工具。

R08核对原生session_input真实状态，不把“尚未执行”和“从未接收”混为一谈。若允许重试，必须证明原队列不会再投递。

## 7. Hive消费者与双真源一致性

| 用例 | 操作 | 预期 |
| --- | --- | --- |
| H01 | 新v0.6.3上游给matched/verified/evidence，Hive自动恢复 | 使用同一恢复合同，输入与事件证据一致 |
| H02 | 上游明确unknown/manual_required，旧历史有相同文本 | Hive不绕过上游用user-echo猜completed |
| H03 | Hive有完整答案但原生终态/归属不充分 | 展示答案与输入未知可并存，不能强行让状态看起来成功 |
| H04 | 多次周期对账、用户recheck、重连同时发生 | 状态条件更新与事件幂等，只有实际应用的结果产生终态事件 |
| H05 | native failed/cancelled/rejected映射 | 对应正确input与turn事件，不都写turn.completed |
| H06 | 旧实例/新实例、两Agent同requestId | 正确session/代际隔离，旧结果不污染当前Pane |
| H07 | unknown后队列有下一条，原生仍活动/已结束 | 不抢占、不无限卡死；恢复后FIFO继续，业务Task状态不自动变化 |
| H08 | 登记终态与Hive完成不一致，重复原requestId | 查可追溯差异，不通过新requestId重烧模型掩盖未收口 |

分别记录ZSession持久登记与Hiveconv_inputs。Hivecompleted但上游仍unknown仅因文本匹配，不能算“联合关闭”。

## 8. 人工对账API与实际可用性

目标接口：

    POST /ui/api/agent/:id/conversation/reconcile
    action=recheck或confirm

| 用例 | 操作 | 预期 |
| --- | --- | --- |
| A01 | unknown chip点对账/recheck | 实际可操作、反馈scheduled/结果，查询不发送新模型输入 |
| A02 | owner/system确认：结果+原生引用+原因 | 有明确人工性质和审计，不能伪装自动verified；旧状态留依据 |
| A03 | 观察者/无权限主体confirm、跨Agent/input | 服务端拒绝，不只隐藏按钮 |
| A04 | 空/不存在/不属于该会话的native_ref、非法outcome/原因 | 校验/拒绝；不能任意字符串造“已核验”证据 |
| A05 | 旧generation、原生仍运行、自动收口与人工并发 | 不误改新实例；条件更新和审计/事件一致，不双终态 |
| A06 | 同request_id重复、异内容重试、终态重复确认 | 幂等/冲突正确，无第二次改账；正文/聊天/评论不触发此入口 |

若产品允许管理员凭主观核对手动处理旧无链记录，明确manual/unverified属性和责任，不能标为强原生证明。报告区分自动核验能力与人工兜底。

人工入口存在但实际上只有recheck按钮、没有可用的confirm流程时，不能宣称完整处置能力。

技术对账不结算作品审核Task，不写TASTE/方法/业务自主授权；测试只用本run输入和身份。

## 9. 面板原位恢复与状态呈现

真实浏览器操作，不能只以DOM函数名_mergeTail/状态条代码存在判通过。

| 用例 | 操作 | 预期 |
| --- | --- | --- |
| U01 | J05故障前/中/后三态 | Pane始终存在，显示上游断连/对账/恢复，非手动重开才补齐 |
| U02 | 故障前输入草稿、附件、上滚、展开工具/思考 | 恢复后都保留，不整树清空/强制滚底 |
| U03 | SSE心跳在线、ACP离线；网络断与401分开 | 传输状态不盖上游状态，认证失败安全处理，不误踢登录 |
| U04 | 同Pane同步旧/新tail与live交错、已有较长历史 | 原位归并无重文/漏文；tail暂缺的历史不被误删除，阅读位置稳定 |
| U05 | 快速切Agent/实际会话，迟到异步回复 | 代次/绑定守卫生效，内容/草稿不串 |
| U06 | watchdog反复重连，手动同步、关闭/重开 | 无多SSE/timer/监听泄漏，无重复query热循环或新模型发送 |
| U07 | 明确reasoning/未分类assistantText，终态更新 | 折叠态保留、终态不遗留思考中，未分类原文不被删/伪清洗 |
| U08 | 320/390/1440/1920、退出图标、抽屉/授权区域 | 清楚标签和触达、布局无裁切，原位状态条可读，节点可达 |

保存连续DOM或短截图序列与故障前/中/后三张绝对路径截图。恢复期间删掉内容后立刻重建仍算闪烁/非原位问题，不仅测最后一张完整截图。

身份切换/退出属于可销毁边界：不为“保留Pane”向下一登录者泄露前用户私密内容。

## 10. 已通过能力与边界回归

| 用例 | 操作 | 预期 |
| --- | --- | --- |
| G01 | 强key与无key/错key，busy同ID执行一次 | 恢复新代码不退弱、不自动release，不重复副作用 |
| G02 | 显式/兜底评分与未知grant键 | 已关闭归一/校验/白名单不回归，评论与Task done语义不变 |
| G03 | 回放双标、水位/工具历史、相同正文两消息 | 不重不丢，正文与原生一致；恢复查询不触发回放业务动作 |
| G04 | 原生取消准确状态与只读生产现状 | 宿主限制如实，未确认不假cancelled；生产切换仍单列，不由验收代部署 |

无需再重复全部原55项；有依据沿用未受影响的旧PASS。FDEF-01/02的新路径和上述反例必须本轮fresh证据。

生产当前版本/强弱只读记录。若隔离新版本通过但生产尚旧，写验证通过与pending_deployment，不把它们混为一个结论。

## 11. 执行、预算与故障收尾

顺序：

1. 双方实际版本/合同和独立强模式资源锁定。
2. 真实J05正常恢复与副作用次数。
3. 同文重复、旧未发、B替A、多步工具等错误收口反例。
4. Hive消费者与人工入口。
5. 浏览器原位状态/草稿/折叠/订阅与小回归。
6. 结论与新缺陷。

建议2个测试Agent、少量真实轮次；重复/错误历史大样本用隔离fixture，真实场景补核实。一个失败先留最短证据，再继续独立可测项，不无限重跑。

不要让验收脚本永久挂起，设置可解释进程/调用观察窗；超时记结果，且仅停止本run脚本，不kill上游宿主。实时更新STATUS，压缩后按已有ID接续。

## 12. 最终报告

Hive根目录写：

- ACP_FIFTH_FIX_ACCEPTANCE_REPORT.md。
- ACP_FIFTH_FIX_ACCEPTANCE_DEFECTS.md。

新问题用GDEF-01等编号，关联FDEF/TDEF，不重排历史；旧报告/证据不改。

报告开头独立给：

    FDEF-01：自动强关联恢复 / 旧记录人工兜底 / 两端一致性
    FDEF-02：面板原位恢复与实际提示
    FDEF-03：原生取消既有限制
    FDEF-04：类别/折叠/触达体验
    不误收口、不重执行证据
    核心回归
    代码/隔离验证结论
    生产部署现状
    未测/受限/新缺陷

每缺陷有归属、当前版本/文件行号、原需求、最短复现、预期/实际、证据、可见影响、修复与复验方向。

没有关联的新输入被历史同文“完成”、中间工具轮被提前done、任意引用变verified，都属于实质状态错误，不能因J05单一暗号成功而完整通过。

真正通过需在同run证明：

    原生输入开始 → adapter失联 → 原生继续 → 新连接取正确原生证据
      → Hive同输入安全收口 → UI原位更新
      → 无新send/prompt/副作用 → 重复查询/后续轮次不误配

无强证据的旧输入可保留unknown与真实可用人工处置；报告明确其范围，不伪造自动能力。

收尾只停止本run测试活动，保留可复现证据/资源，不动生产或旧挂起证据。给用户报告路径、主要结论与实际可用范围。

现在开始独立验收。
