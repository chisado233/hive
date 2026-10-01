# 给Hive Agent：恢复对账与原位会话体验提示词

第四轮独立验证确认TDEF-01～05全部关闭，强writer、真实busy回队/FIFO与评分主体通过。本轮不重写已过核心，集中修FDEF-01联合恢复与FDEF-02会话面板消失，补FDEF-04小体验。

请直接实施、真实测试并与ZSession联调。保留“静谧天文台”风格、当前身份/任务语义及已通过能力。

## 1. 范围与依据

只改：

    D:\agent_workspace\valuable-projects\hive

上游只读：

    D:\agent_workspace\projects\zcode-session-manager

读适用AGENTS.md、版本/改动与：

- ACP_FOURTH_FIX_ACCEPTANCE_REPORT.md。
- ACP_FOURTH_FIX_ACCEPTANCE_DEFECTS.md。
- ACP_FOURTH_FIX_ACCEPTANCE_PROMPT.md。
- HIVE_ACP_FOURTH_FIX_REPORT.md、UI_API.md、UI_REDESIGN.md。
- 上游ACP_INTEGRATION.md及新ACP_FIFTH_FIX_REPORT.md。
- ZSESSION_ACP_FIFTH_FIX_PROMPT.md了解职责与交接。

证据：

    D:\agent_workspace\tmp\hive-acp-fourth-acceptance\4acc-20261002-004613-v1

重点重放J05/fj05-long：adapter死后，原生答案完整但input永unknown；面板在约3秒消失，重开才补齐。

FDEF-01双方共同关闭；ZSession提供持久原生关联与可验证终态，你接入状态、操作入口、事件和UI。FDEF-02主归你，先定位真正触发路径，不预设一定是watchdog。

## 2. FDEF-01：unknown有安全的收口路径

接入上游修复后的输入对账合同，不仅对done/cancelled/rejected/failed作静态映射：

- adapter失联立即unknown保护是正确行为，保留。
- 桥重连后与适当低频恢复周期，按原session/requestId/native input/turn对账。
- 可强关联的真实终态补到对应输入、事件、消息最终态和UI。
- 仍运行保持可理解恢复中，不提前completed。
- 旧轮结果不改新轮，旧实例不会污染当前绑定。
- 重复查询/重复终态幂等，不重复started/completed或回调。
- 对账没有session/prompt/send，不调用第二轮模型，不创建替身实例。

不能只因session idle、答案在历史里、正文相似或时间接近就自动done。时间窗可帮助人定位，不能替代唯一原生关联。

原生已结束且证据充分后，目标≤60秒内补回真实状态；合法长轮不受此时限强停。缺证据显示具体原因与可操作入口，不能永unknown且没有处置。

会话输入completed仅表示该输入轮次完成，不自动改Hive业务Task done/fail/trash、审核判决或自主授权。

## 3. 人工对账入口：仅作为无强证据的安全兜底

如存在无法自动匹配的旧记录，提供实际可用的管理员/control入口或UI：

- 展示原input/request/session/instance、未知原因、实际历史/工具和证据。
- 可触发只读重新对账。
- 必须人工确认时要求具体结果/原生引用/原因，服务器核对权限、资源与代际。
- 对无法验证的结果，不允许“看到文字就完成”按钮直接绕过真源；明确保留人工处置性质和不可自动证明的部分。
- 操作保留旧状态与来源，写审计；终态竞态/重复提交拒绝或幂等。
- 观察者可看不等于可改账，agent身份不因YOLO绕过API权限。

这属于运行control/会话管理，不新增顶层工具，不把技术对账伪装成作品人评Task，也不通过普通聊天/评论“已经好了”结单。

若新增CLI/API，补详细参数、错误、权限、幂等、示例与UI_API/DESIGN/Hive skill相关说明。两个入口使用同一服务规则。

没有必要不新加实体。优先使用既有输入、operation、事件和控制面。

## 4. FDEF-02：断线恢复不拆会话面板

追踪并留证据：

- main.js的onStoreChange、selection和closeDetail。
- detail.agent重建与_conv.destroy。
- auth-lost判定。
- ConversationPane的watchdog、_refreshTail、SSE重建与destroy。

修正导致adapter断线后会话舱整体拆掉的实际路径。不能仅加toast后仍消失。

会话Pane生命周期按稳定Agent/实例/会话管理；同一绑定的网络/桥状态更新不销毁整块阅读区。

故障期间保留：

- Agent选择与当前标签。
- 已显示正文和工具记录。
- 草稿、附件状态。
- 阅读位置，用户向上读时不强制滚底。
- 可见输入状态与错误/恢复提示。

仅显式关闭、切换实际会话或退出/身份变化需要销毁。身份失效时安全处理私密内容，不能为保留面板向另一个登录者泄露。

## 5. 连接与运行状态分开

同一时刻可能：网页/SSE心跳在线，但ACP bridge离线且原生仍在运行。

分别处理：

- 网页认证。
- HTTP/SSE传输。
- Hive↔ACP bridge连接。
- 对应原生输入仍运行/已终态/未知。

不要心跳一来就盖掉“上游重连中/结果待对账”，也不要数据安静即断线。

原位显示安静但清楚的状态条：

- 上游连接中断，正在恢复。
- 已重连，正在核对该轮结果。
- 已恢复至原结果。
- 证据不足，需要管理员处理。

必要时提供“重新同步”操作；恢复不要求用户关面板再打开。正常合法长工具安静期不误报。

故障期间发送策略遵循持久队列，重复点击/重连不重发旧输入。上游仍活动不能抢占；不知道结果时不能引导用户换ID再烧一轮。

## 6. SSE/watchdog与原位补齐

- 从最后确认cursor补历史/续流，按稳定ID/版本去重。
- _refreshTail更新现有消息/工具节点，不清空整Pane造成闪烁/草稿损失。
- 重建仅必要的EventSource，不重建整个详情页面。
- 新旧异步返回有绑定/请求代次守卫，旧会话返回不覆盖当前。
- 不并发多个watchdog校对，不积累timer/监听/SSE。
- 认证撤销→重新登录，恢复正确身份的可访问内容与草稿。
- 表示重连/同步的状态随真实事实变化，不无限闪“正在恢复”。

API层恢复终态、事件层和页面chip/头部/思考行一致。查询补回失败/取消时不要都写turn.completed。

## 7. FDEF-04视觉精修

保持现有风格，不重新装修：

- 原生明确reasoning/thought独立折叠区域，Agent正文阅读清楚。
- 无可信分类的assistantText保持原文，不正则删英文或伪造答案；记录源限制，不将未分类内容硬称推理。
- 默认可折叠长公开思考，展开状态在恢复中保留，终态不遗留“思考中”。
- 320退出图标增加清楚可访问标签、键盘焦点/悬浮说明及足够触达；图标-only本身不是缺陷，不强加挤不下的常驻文字。
- 窄屏抽屉、节点可达与授权区完整检查。

已有审批双评分、白名单/归一、登录、320布局与星图不回归。

## 8. 本轮真实验证

独立强writer Home、fresh run、测试Agent，不动生产。

| 场景 | 预期 |
| --- | --- |
| 真实长轮中只终止本run adapter | 面板不消失，原文/草稿/阅读位置保留，状态条出现 |
| 原生继续完成，桥重连 | 自动按原生依据收口或给实际人工处置入口，无新模型输入 |
| SSE心跳仍在但上游离线 | 传输与上游状态不互相覆盖，watchdog原位恢复 |
| 心跳/业务事件分别停、网络恢复、401 | 清楚分态，正确补齐与认证处理 |
| 无证据unknown、重复正文、后续轮次 | 不猜完成，不由B轮替A轮收口，无二次副作用 |
| 人工对账/重试/旧实例/观察者 | 权限、代际、幂等和审计真实，普通评论不改账 |
| 快速切节点/关闭/重开、迟到返回 | 不串会话，订阅和timer正确释放 |
| 320/390/1440/1920及上读长文 | 状态条/折叠区可读，无布局跳动或强制滚底 |

用实际浏览器记录故障前、故障中、恢复后三张截图和连续DOM/事件时序。仅静态存在状态条或重开补齐不算FDEF-02关闭。

对账同时核对native输入/turn/marker次数，证明没有重新执行。原J05的完整长文与input状态要在同run统一证据。

## 9. 取消与生产部署边界

FDEF-03是已有宿主取消限制，保持诚实confirmed判定和expected-turn守卫。本轮可小量回归，不为解决它kill全宿主或另开会话；仍受限就准确记录。

生产仍旧代码/弱模式，部署单列pending_deployment。本轮不自动停生产或切强模式；先交付恢复/体验验证与安全接续说明，后续部署任务再执行。

保留TDEF已关闭的强key/FIFO/评分行为，恢复改动不能自动release key或退弱。

## 10. 交付

Hive根目录写：

    HIVE_ACP_FIFTH_FIX_REPORT.md

更新UI_API、部署说明与必要skill/DESIGN，记录恢复合同、权限、人工兜底、状态/错误、版本和可复跑步骤。

报告包含：

- FDEF-01上游证据→Hive补账→UI原位更新的联合结果。
- FDEF-02导致拆面板的实际根因和修复位置。
- 自动与人工恢复适用条件、未知保留理由。
- fresh run的native/input/事件/截图/副作用证据。
- FDEF-03真实取消限制、FDEF-04分类/触达结果。
- 生产未部署与下一步接续。

旧报告和证据不覆盖，不复建视频网络，不写项目审美分。单侧ready或只有人工按钮无法真正处置时，标pending，不宣称联合关闭。
