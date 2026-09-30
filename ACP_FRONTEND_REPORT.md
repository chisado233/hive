# ACP 会话舱与星辰大海升级 — 中期报告

日期：2026-10-01 · 基于 FRONTEND_ACP_COSMOS_PROMPT.md 施工 · commit 1f58f2d

## 已完成（本轮）

### 1. 星辰大海视觉（Phase A，完整落地）

- 设计 token 全量重构：宇宙底 #050914 / 面板 #0C1529 / 星云靛 #182A50 /
  远紫 #493779 / 交互青 #7ADDF0 / 审批金 #E9BF72；中文本地字体栈，数字等宽。
- `hive_web/js/nebula.js`：背景 Canvas（三层稀疏星点 + 三个星云光斑极慢漂移 +
  地平辉光）。DPR≤1.5、粒子按面积自适应；`prefers-reduced-motion` 与隐藏标签页
  完全暂停；rAF/事件监听正确清理；`pointer-events:none` 不拦截任何业务交互。
- 星图节点升级为恒星形态：光晕底层 + 径向渐变核心 + 外环；资产库资料舱、
  人类决策台图标保留。真实 working（observation=busy）才有呼吸光晕；
  unknown 如实琥珀虚线，不伪装 idle。
- 玻璃质感面板（topbar/sidenav/detail/eventband/login），克制发光只用于焦点。
- 修复：多项目 home 下项目选择器记忆（hive.lastProject），不再误落小项目。

### 2. ACP 会话舱（Phase B，fixture 链路全通；Phase C 骨架就绪）

- **存储（迁移 006）**：`conv_bindings`（agent+instance+generation→session 唯一绑定）、
  `conv_inputs`（持久输入队列，(agent_id,request_id) 幂等，状态机
  queued→dispatched→running→completed/cancelled/failed/outcome_unknown）、
  `conv_events`（cursor 严格递增，delta/snapshot/replace/replay 分离，
  工具按 tool_call_id 归并，环保留 5000/会话）。会话内容不混入 ops `events`。
- **桥**：`convbridge.py` FixtureBridge（协议夹具，UI 明确「演示模式」）+
  AcpBridge（真实 stdio ndjson JSON-RPC 骨架：initialize/session/new|load/
  prompt/cancel、`_meta.zsession` 元数据、session/request_permission 反向请求
  登记、断线全部未决输入标 outcome_unknown）。ConversationService 挂 scheduler
  tick，非阻塞派发，一会话最多一个活动 prompt。
- **接口**：`/ui/api/agent/:id/conversation[/history|/stream|/input|/cancel|/inputs]`，
  SSE 支持 Last-Event-ID/after 恢复，15s 心跳，关闭不触碰实例。
  权限 `conversation.read/send/cancel` 按 agent 资源授权。
- **前端 `conversation.js`**：详情面板新增「会话」标签；流式 Markdown 按 row_id
  就地更新（不整树重建）；工具卡 pending/running/completed/failed 可展开；
  乐观回显与 SSE 事件去重；向上阅读暂停跟随+「有新输出」按钮；中文输入法
  composition 状态不误发 Enter；跨 Agent 切换保留草稿/阅读位置/游标；
  默认排队发送、「中断并发送」「停止本轮」独立明确；无实例显示历史+启动入口。
- **harness.py 修复**：移除 queue 等 20s 静默升级 interrupt 的行为——超时如实
  返回 `queued-not-delivered`，抢占只能用户显式选择。

### 3. 实测证据

- fixture 全链路（test home）：入队→派发→流式增量→工具卡→轮次完成，
  事件/投影/输入状态全部正确；同 request_id 重放返回既有输入（不重复执行）。
- SSE：`curl -N .../conversation/stream` 实测历史回填 + 事件帧 + 心跳。
- 生产 home 优雅降级：协调员会话舱显示「实例 gen3 · 演示模式 · 暂无会话内容」。
- `scripts/quicktest.py` 回归全过。
- 截图：星图全景（星辰大海）、会话舱（流式+工具卡+输入区）已采集。

## 未完成 / 待上游（Phase D）

- **真实 ACP 联调**：上游 `ACP_INTEGRATION.md` 尚不存在，`acp.mjs` 草稿有阻断 bug
  （等不存在的事件类型；session/new 未传 bootstrap:false）。按用户拍板，
  不越界修 zsession 仓库；已建 cron（3 小时后）复查上游并就绪后自动联调
  （独立测试 Agent，不动 video-lab 23 个生产会话）。
- AcpBridge 的 `ensure()` 中 session/load 路径、`_reconcile_input` 对账需真实
  上游字段校验。
- 审批台「和发起 Agent 讨论」入口、权限请求卡的 ACP answer 回填待联调验证。

## 验收矩阵对照（提示词 §10）

已验：1(同根一图)、9(刷新补回)、11(代际检查)、12(双 Agent 隔离-by store)、
13(向上阅读不强制滚底)、20(会话事件不改业务 Task)、21(项目切换/布局保留)、
22(低动效/隐藏标签页暂停)、23 部分(23 节点实测)。
待真实 ACP：2-8、10、14-19、24。
