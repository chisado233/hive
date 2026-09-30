# Hive 指挥台升级：ACP 会话舱 + 星辰大海视觉

## 背景事实（已侦察确认）
- ZSession 插件侧 ACP adapter 草稿 `mcp/lib/acp.mjs`（460 行）已由并行 Agent 写出并接线 `bin/zsession.mjs acp`，但有阻断 bug（等不存在的事件类型；session/new 未传 bootstrap:false）；`ACP_INTEGRATION.md` 不存在。**用户拍板：不越界修上游，定 cron 三小时后检查。**
- Hive 侧接缝已定位：`scheduler.tick()` 每 2s（加任务=两行）；`store.py` MIGRATIONS 到 005（追加 006/007）；`server.py` ThreadingHTTPServer 支持长连接 SSE（handle_request 立即分线程）；`harness.py:268-286` 存在"queue 等 20s 静默升级 interrupt"需移除。

## 实施分相

### Phase A — 星辰大海视觉（不依赖上游，最先可见）
- styles.css 全量重构为设计 token：宇宙底 #050914 / 面板 #0C1529 / 星云靛 #182A50 / 远紫 #493779 / 交互青 #7ADDF0 / 审批金 #E9BF72；中文本地字体栈，数字等宽辅字体。
- graph.js：恒星节点（核心+柔和光晕）、资产资料舱、人类舰桥图标保留业务语义；星域低透明边界+标题；布局逻辑（按 line 分区、位置持久化、拖动/缩放/聚焦）**全部保留**，只换视觉表现。
- 新增 `js/nebula.js`：背景 Canvas 层（稀疏分层星点 + 极慢星云漂移 30–90s 周期），`prefers-reduced-motion` 与隐藏标签页时暂停，rAF 正确清理；与业务 SVG 分层，不拦截拖拽。
- 动效按清单逐条：呼吸光晕仅真实 working；流星脉冲仅真实事件；hover/选中 120–220ms；聚焦 350–550ms 可打断；审批到达一次金色提示。

### Phase B — 会话舱 + fixture 投影（UI 完整链路先行）
- 后端 `hive_core/conversation.py`（新模块）：
  - store 迁移 006：`conv_events`（event_id/cursor 严格递增/session_id/agent_id/instance_id/generation/type/source/payload/上游 seq/ replay 标记）、`conv_inputs`（input_id/request_id 唯一、状态机 queued→dispatched→running→completed/cancelled/failed/outcome_unknown、payload、轮次关联）、`conv_bindings`（agent_id+instance_id+generation→session_id，约束唯一）。
  - `InputQueue`：持久队列，一会话最多一个活动输入；入队序发送；同类唤醒可合并、用户消息不按 dedupe 丢；浏览器断开不重发；重连先对账。
  - `EventStore`：delta/snapshot/replace/replay 分类处理，工具调用按 toolCallId 更新卡片，去重；分页历史（游标），过旧游标返回明确缺口。
  - `FixtureBridge`：`control.test.conversation.inject`（test profile）注入合成会话事件，UI 全链路零上游依赖开发。
- 接口（webui.py 扩展，cookie 会话+CSRF 不变）：
  - `GET /ui/api/agent/:id/conversation`（绑定/能力/当前轮次/确认模型/历史摘要）
  - `GET .../history?after=&limit=`（分页）
  - `GET .../stream`（SSE，EventSource 同源 cookie 认证，`after=`/Last-Event-ID 恢复，15s 心跳，close_connection 管理；只读无 CSRF 需求）
  - `POST .../input`（request_id+instance_id+generation 代际检查，队列/中断模式）
  - `POST .../cancel`（停止本轮，带预期轮次，取消中/已取消分态）
  - `GET .../inputs`（输入状态与待发送列表）
  - 权限：新增 `conversation.read/send/cancel` action 字符串（grants 匹配，owner 默认全通）；UI 白名单不动既有命令。
- manifest 新命令（控制面）：`control conversation show/inputs/cancel`（CMD-098+），外挂效果命令入 IDEM_EXTERNAL_COMMANDS。
- 前端 `js/conversation.js`：
  - 详情面板加"会话"标签（与概览/任务/操作/事件并列）；可拖宽面板、专注模式。
  - 消息流：用户/Agent/系统来源可辨；流式 Markdown 增量渲染（按 rowId 就地更新，不整树重建）；工具卡片 pending/running/completed/failed/cancelled 可展开；长内容折叠+代码复制；向上阅读暂停跟随+"有新输出"按钮；跨 Agent 保留草稿/阅读位置/游标。
  - 输入区：中文输入法安全（compositionstart/end 不把 Enter 误判为发送）；Enter/Ctrl+Enter 偏好；默认排队、中断并发送独立明确；附件复用现有上传；发送后立即显示受理状态，上游事实到达后更新；"发正式任务"打开既有 emit 表单。
  - 无实例时显示历史+"启动 Agent"入口（仅浏览不启动）；历史实例标不可发送。

### Phase C — 真实 ACP 桥（协议骨架 + 联调钩子）
- `hive_core/acp_bridge.py`：
  - `AcpProcess`：`subprocess.Popen(["node", ".../bin/zsession.mjs", "acp", "--stdio"], stdin/stdout=PIPE, stderr→日志文件, text/utf-8, CREATE_NEW_PROCESS_GROUP)`；专用 stdout 读取线程（ndjson 行帧，UTF-8，多帧/分片处理）；`queue.Queue` 解耦，scheduler 内零阻塞。
  - JSON-RPC client：请求/响应/通知分发；Agent 反向请求（session/request_permission）路由到待决权限卡。
  - 生命周期：initialize 协商 → 按绑定 session/new（增强模式新实例）或显式拒绝不可安全接入的旧会话（显示限制，不复制/重启）；实例更换后旧绑定 stale-instance 拒绝并刷新。
  - `_meta.zsession` 携带 requestId/inputId/agentId/instanceId/generation；重连先 `_zsession/get_input_state` 对账，未知=显示未知不盲目重放。
  - supervisor：scheduler.tick 加 `run_conversation_dispatch()` + `run_acp_supervisor()`（非阻塞轮询队列状态）；server.py finally 显式 stop+reap 子进程。
  - **移除** harness.py queue→interrupt 静默升级（268-286 段改为：超时即返回 "queued-not-delivered" 明确状态，不抢占）。
- 上游未就绪时：`conversation` 面板顶部固定"上游 ACP 未就绪 — fixture 演示模式"标识，fixture 数据明确标注；不宣称 ACP 完成。

### Phase D — Cron 与联调
- 创建一次性 cron（3 小时后）：检查 `ACP_INTEGRATION.md` 与 acp.mjs 是否更新；若上游就绪→发起真实联调（独立测试 Agent，一轮中文对话+一次工具调用+取消+刷新恢复）；若未就绪→再延后并汇报。
- 联调用独立测试 Agent（不动 video-lab 23 个生产会话）。

### Phase E — 审批联动 + 回归 + 文档
- 审批卡"和发起 Agent 讨论"入口：跳转会话舱并引用任务/产物，返回审批时草稿评分保留；权限请求卡与业务审批卡类型区分。
- 回归：scripts/quicktest.py 全过；23/50 节点性能记录；窄屏/专注模式浏览器检查；截图（星图全景/实时会话工具卡/审批台/专注模式）。
- 文档：UI_API.md（接口+幂等+SSE 恢复+错误+上游契约）、ACP_FRONTEND_REPORT.md、README/DEPLOYMENT 更新。

## 验收对照
按提示词 §10 表格逐项；协议 fixture 测试与真实联调分开写。至少交付 4 张浏览器截图。

## 边界与保护
- 不改 zsession 仓库一行代码（cron 复查后若上游仍缺，缺口写入报告）。
- 不重启/kill 用户其它 ZCode 会话；video-lab 生产 Agent 零消息（本轮此前的回环验证已完成，不再打扰）。
- Hive 语义十条（提示词 §3）全部遵守：终态不被 ACP 事件改写、聊天不自动生成 work、审批走既有 answer。
