# ACP 独立验收缺陷清单 — run acc-20261001-100912

> 独立验收 Agent：ZCode 会话（非施工会话）。日期：2026-10-01。
> 被验收版本：Hive git `e884cdd`（main）；ZSession **非 git 仓库**，以文件快照为准
> （`…/acc-20261001-100912/snapshots/zsession/acp.mjs` md5 `07b55973740f6835b6f22950ea3f2f67`，即被审版本）。
> 每条缺陷可在隔离资源复现；原始证据见本 run 目录绝对路径。

严重性定义：**阻断**=直接阻止完整通过（对应验收提示词 §5 阻止清单或原需求核心项失败）；**高**=重要功能/标准合规失败；**中**=明显行为缺陷有用户影响；**低**=工程/体验问题。

---

## 阻断级

### DEF-01 跨会话 requestId 串号：第二会话输入被吞且假报成功（ZSession）
- 归属：ZSession（`plugins/session-manager/mcp/lib/acp.mjs:48,171-178`）
- 现象：adapter 的 `this.inputs` 是**全局 Map、仅按 requestId 键控**，不按 sessionId 隔离。
- 复现：会话 A 以 requestId=req-z13 完成一次输入（副作用计数 1）后，新会话 B 用**相同
  requestId + 相同文本**再发 session/prompt → 返回 `{stopReason:"end_turn"}`，
  但该输入**从未送交模型**（marker 文件计数不变，仍为 1）；
  `_zsession/get_input_state {sessionId:B, requestId:req-z13}` 返回 A 的记录
  （`state:"done", stopReason:"end_turn"`）。
- 证据：`protocol/frames-G.json`；`protocol/z-results-G.json`（Z14-cross-session）
- 预期：requestId 按 session 范围隔离；B 会话输入应真实执行（计数 2）或明确拒绝。
- 实际：B 会话收到成功信封、输入静默丢失、状态查询串到 A 的记录。
- 影响：多会话/Hive 多 Agent 场景下输入幻觉成功；违反"接收成功不等于模型已处理"与
  原需求"输出与输入严格隔离"。
- 修复方向：`inputs` 改 key `${sessionId}::${requestId}`；get_input_state 强制带 sessionId 比对。
- 复验条件：Z14 场景 sideEffectCount=2 且 get_input_state 返回 B 自身状态。

### DEF-02 跨 adapter 重启后重发已执行输入 = 重复执行（ZSession）
- 归属：ZSession（`acp.mjs:48` 内存登记；`extDispatch get_input_state` 返回 unknown）
- 复现：adapter 运行中发送 requestId=req-z15（文件追加副作用发生 2 次后确认执行）→
  关闭 adapter → 新 adapter 启动 → `get_input_state` 返回 `state:"unknown"` →
  用**相同 requestId+相同内容**重发 → 输入被再次完整执行（副作用 2→4）。
- 证据：`protocol/z-results-H.json`（Z15-restart-idem）；`protocol/frames-H2.json`
- 预期（原需求 §8）：断线后已确认执行的输入不可重发；结果未知先查询，不盲目重新执行。
- 实际：幂等登记纯内存，重启即丢失；重试客户端无从区分，触发重复副作用。
- 影响：断线重试语义不可靠；Hive `outcome_unknown` 后用户显式重发会造成重复模型调用/重复工具副作用。
- 修复方向：幂等登记持久化（daemon 侧或 store），或新 adapter 可通过上游轮次/输入 ID 对账确认已执行。
- 复验条件：Z15 场景 `sideEffectCountAfterResend == sideEffectCountBefore`。

### DEF-03 ACP cancel 向真实会话注入伪用户消息并触发额外原生轮次（ZSession）
- 归属：ZSession（`acp.mjs:248-260`：cancel 走 `/sessions/:id/send {text:'cancel requested by client', mode:'interrupt'}` →
  `manager.mjs:890-893` v4SendText delivery=startNow）
- 复现：长轮次中发送**无 id 的 session/cancel 通知** → prompt 返回 cancelled（该点合规），
  但原生会话 turnCount 5→7（+2），且 daemon 事件流出现 userInput 行文本
  **"cancel requested by client"**——模型实际回答了这条伪用户消息并开启新轮。
- 证据：`protocol/z-results-E.json`（Z09-cancel turnCountBefore5/After7、Z09-native-user-inputs）；
  Hive 会话舱历史中可见该轮的英文思考残留（`browser/b03-session-cabin.png` 及 DOM）
- 预期：取消只中断当前轮，不产生新轮、不污染会话记录（原需求：取消被原生确认；不声称物理撤销的
  外部副作用）。
- 实际：每次"停止本轮"都在 Agent 真实 transcript 注入一条英文用户消息并多烧一轮模型。
- 影响：成本 + 对话记录污染 + 误导（模型随后会回复"收到，已取消写作"之类）。
- 修复方向：使用原生 interrupt API（不带文本），或经 V4 协议的真实取消命令；禁止 startNow+文本。
- 复验条件：Z09 场景 turnCountBefore+1（仅原轮结束）且无新 userInput 行。

### DEF-04 非 yolo 权限请求未桥接且 prompt 无限挂起（ZSession，原需求 §5.8）
- 归属：ZSession（`acp.mjs dispatch` 无 `session/request_permission`；`awaitTurnEnd` 轮询
  working/waiting_input 期间无权限出口）
- 复现：`session/new` mode=plan（非 yolo）+ 要求使用 Bash 工具 → 240s 内**未**收到
  `session/request_permission` 反向请求；prompt 请求直到 240s 超时仍未返回
  （原生停在 pendingApproval，adapter 轮询永不停摆）。
- 证据：`protocol/z-results-I.json`（Z16-permission：reverseRequests=[]，promptResult=timeout）
- 预期：原需求明确要求非 yolo 权限桥接（session/request_permission），且待决/取消/断线/超时都要
  有明确处置，不能无限挂起。
- 实际：桥接不存在；客户端被挂死；UI 也无从出现权限卡。
- 影响：原需求核心项缺失；不可因默认 yolo 记为 N/A（验收提示词 §6 明确）。
- 修复方向：订阅原生 pendingInteraction 事件→转发 session/request_permission；prompt 等待期间
  处理取消；超时如实收口。
- 复验条件：Z16 场景收到合法 request_permission 帧，答复后原生执行/拒绝与选择一致，
  无答复时 cancel 能解除挂起。

---

## 高

### DEF-05 session/load 声明 loadSession=true 但不回放历史（ZSession，官方 ACP v1 §Session Setup）
- 归属：ZSession（`acp.mjs:99-104,140-154`；`ACP_INTEGRATION.md` §2 自述 live-forward）
- 复现：严格 client（自研 driver）对有多轮真实历史的会话 `session/load` → 139ms 即返回
  `{loaded:true}`，期间 **0 条 session/update 回放**（replayedUserChunks=0，
  replayedAgentChunks=0）；`_meta.zsession.replayed:true` 属误导字段。
- 官方依据：schema-v1.24.1 `SessionLoadResponse`={modes,configOptions,_meta}；
  v1 文档：loadSession=true 时 "The Agent MUST replay the entire conversation to the Client
  in the form of session/update notifications"，回放完成后才响应。
- 证据：`protocol/z-results-F.json`；`protocol/frames-F.json`；schema 基线
  `protocol/acp-schema-v1.24.1.json`
- 另：响应含非标准 `loaded`/`sessionId` 字段；`loaded:false` 对不存在会话返回**成功信封**
  （标准 client 会当作加载成功）。
- 影响：严格标准 client 无法把本 adapter 的 load 当标准 load 使用；`loadSession=true` 声明不实。
- 修复方向：实现全量 replay（用户消息+agent 文本+工具卡）后再响应；不存在会话返回错误而非 loaded:false。
- 复验条件：Z11 场景 load 后先收到完整历史 update，再收到 `{}` 响应。

### DEF-06 initialize 响应缺 agentInfo；promptCapabilities 形状非法（ZSession，官方 §Initialization）
- 归属：ZSession（`acp.mjs:97-106`）
- 复现：所有捕获的 initialize 响应均无 `agentInfo`（schema 要求 name/title/version）；
  `promptCapabilities` 为 `{loadSession:true}`（schema 只允许 image/audio/embeddedContext）。
- 证据：`protocol/schema-validation.json`（每组 `init: agentInfo? False`）；`protocol/frames-A.json`
- 另：未初始化即发送 session/new 被**接受并创建真实会话**（`protocol/z-results-A.json` Z03-pre-init
  创建 sess_6f57260e-bcb0-44ef-af4b-173945e46291）——协议顺序违规。
- 修复方向：补齐 agentInfo；promptCapabilities 用标准三布尔；initialize 前拒绝对话方法。

### DEF-07 标准 session/set_mode 未实现，仅有非标准 set_mode（ZSession，官方 meta.json）
- 归属：ZSession（`acp.mjs:86` 注册 `set_mode`；官方 v1.24.1 `meta.json` 方法名
  `session/set_mode`）
- 复现：严格 client 调 `session/set_mode` → `-32601 method not found: session/set_mode`。
- 证据：`protocol/z-results-A.json`（Z17-set-mode-standard）；`protocol/acp-meta-v1.24.1.json`
- 影响：标准客户端无法切换模式；ACP_INTEGRATION.md 将其列为"✅"未标注非标准名。
- 修复方向：实现 `session/set_mode`（params: sessionId, modeId），`set_mode` 仅作兼容别名并文档标注。

### DEF-08 Hive ACP cancel 依赖非标准 cancel 响应（Hive，官方 §Cancellation：session/cancel 是通知）
- 归属：Hive（`hive_core/convbridge.py:443-448` `cancel_turn` 用 `self.call("session/cancel", …)`
  等待响应）
- 复现：静态确认 + 运行时同屋证据：重启后 adapter 未 load 会话时 cancel 返回
  `ACP session/cancel: unknown session…`（调用按 request 处理，要求响应）。
- 官方依据：v1 文档/schema：session/cancel 为 notification（无 id、无响应）。
- 影响：换严格标准 agent 后端时，Hive cancel 将挂起至 60s 超时（→ outcome_unknown 误标）。
- 修复方向：cancel 改为无 id 通知；"取消完成"以 prompt 返回 cancelled / input_state 为准。
- 复验条件：mock 不回响应的标准 agent 上，cancel_turn 立即返回且不误标状态。

### DEF-09 会话舱排队状态 chip 完成后不刷新；ACP 链路 SSE 无 turn 事件（Hive）
- 归属：Hive（`hive_web/js/conversation.js:277-292,350`：chip 仅在 send/cancel/turn.* 刷新；
  `convbridge.py _project_update` 只投影 user/agent/tool 更新，`finalize_turn` 只写 final 行，
  **不写 turn.started/turn.completed 事件**）
- 复现：后端 conv_inputs=completed（API 实证），浏览器 chip 持续显示
  "队列: queued inp_6faee286…"；重载后才正确。fixture 链路有 turn 事件故正常——ACP 链路缺失。
- 证据：`api/h02-input-trace.json` + 会话 DOM 抓取；`protocol/frames-E.json` 等
- 影响：用户看到"排队中"假状态；与"受理-事实分态"设计目标相悖。
- 修复方向：AcpBridge finalize_turn/启动时补写 turn.started/turn.completed conv_events（带 turnId）。

### DEF-10 Hive daemon 重启后 dispatched 输入永久卡死并阻塞队列（Hive）
- 归属：Hive（`hive_core/conversation.py:262-272`；`server.py idem_startup_sweep` 不覆盖 conv_inputs）
- 复现：输入 dispatched 时重启 daemon → 重启后该输入 141s 仍为 dispatched，
  后续 queued 输入全被 `active_input` 挡住（H12 实测；r2 stuck 后 r1 无法执行）；
  必须人工 cancel 才解锁。绑定/实例恢复正常（sess_f58c4a05…、ins_7a89519d…不变）。
- 证据：`api/h12-restart-recovery.json`、`api/h12-unblock-cancel.json`、`api/h12-post-restart-conv.json`
- 预期（原需求 H12）：重启后持久队列恢复，无丢失或双派发。
- 实际：dispatched/running 输入无启动对账清扫，头阻塞。
- 修复方向：daemon 启动时把 dispatched/running 输入按上游 get_input_state 对账或置
  outcome_unknown（并记录），释放队头。
- 复验条件：H12 场景重启后 ≤30s 内该输入进入终态或重新派发，后续输入按序执行。

### DEF-11 权限答复端点对不存在的答复实现返回成功（Hive）
- 归属：Hive（`hive_core/webui.py:1145-1154`：`hasattr(bridge.bridge,"answer_permission")`
  为假时仍回 `answered:True`）
- 复现：`POST /ui/api/agent/:id/conversation/permission` 任意参数 → `{"answered": true}`，
  实际没有任何动作（AcpBridge 无 answer_permission）。
- 证据：`api/h15-permission-answer.json`
- 影响：前端可显示"已答复"假象（当前因上游无权限卡而未暴露，一旦上游补齐即成真欺诈路径）。
- 修复方向：未实现答复通道时返回明确错误（NOT_IMPLEMENTED），不返回成功。

---

## 中

### DEF-12 同秒入队 FIFO 乱序（Hive）
- 归属：Hive（`hive_core/conversation.py:262-265` next_queued `ORDER BY created_at, input_id`；
  created_at 为秒级 RFC3339，input_id 为随机串）
- 复现：两条输入同一秒入队（02:25:46Z）→ 后输入的 input_id 字典序更小 → **先派发**
  （实测两次：H03 场景 r3 先于 r2；B02 前 H12 场景第二次复现）。
- 证据：`api/h06-h03-after-cancel.json`、`api/h12-restart-recovery.json`、inputs API created_at 同秒
- 预期：有限 FIFO，按入队顺序。
- 修复方向：created_at 提高到毫秒/单调序列，或 ORDER BY 增加自增列。

### DEF-13 stale-instance 错误码被吞为 500 INTERNAL_ERROR（Hive）
- 归属：Hive（`webui.py` HiveError→HTTP 映射缺 STALE_INSTANCE）
- 复现：携带旧 instance_id 发送 → HTTP 500 + `code:"INTERNAL_ERROR"`，
  但 message/details 正确（"实例已更换…"+current_instance）。代际不符同样 500。
- 证据：`api/h08-stale-instance.json`
- 影响：客户端无法按 STALE_INSTANCE 编程处理；fencing 逻辑本身正确（拒绝写入）。
- 修复方向：STALE_INSTANCE → 409；错误码直通。

### DEF-14 Hive 的 zsession 历史重建深度受 daemon /events 500 上限削弱（Hive）
- 归属：Hive（`hive_core/zsession_read.py:60-62` 请求 limit=3000；
  ZSession `mcp/lib/http.mjs:114-121` `/events` 将 limit 硬截 `Math.min(500,…)`）
- 复现：daemon 事件环超过 500 条后，fetch_session_history 只能看到最后 500 条全 daemon 事件；
  实测 155+ 会话环境下读不到指定会话的早期历史（tool 行不可见）。
  （叠加已知上游缺陷：`/sessions/:id/messages` ws 模式 500——实测复现。）
- 证据：`workdir`/`api/h01..h12` 中 upstream 历史缺早期行；daemon /events 直取实测；
  `z-results-F.json` Z12 证据（messages endpoint 500 由施工报告披露，本次实测确认）
- 影响：会话舱"绑定前历史"浅；UI_API.md 未披露该深度限制。
- 修复方向：daemon /events 支持按 sessionId 过滤与更高 limit；或 Hive 直读 ZCode 会话存储。

### DEF-15 上游其他写入口对 Hive 受管会话无约束执行（ZSession 侧对原需求 §8）
- 归属：ZSession（daemon send 接受 queue 模式）
- 复现：ACP 活动轮期间经 daemon HTTP `/sessions/:id/send {mode:'queue'}` 仍被
  `accepted:true`，未按"受管写入约束/明确冲突"处理（get_session_state 却声明
  `managedWrite:"single-active-turn"`）。
- 证据：`protocol/z-results-J.json`（Z18-managed-write daemonOtherEntrance.accepted=true）
- 影响：Hive 之外的入口可向同会话追加原生队列输入，绕过 Hive 统一调度与顺序。
- 修复方向：受管登记落 daemon（store.getManaged 已有雏形），非受管入口对受管会话返回冲突。

### DEF-16 浏览器会话 cookie 周期性失效导致 SSE 静默停摆（Hive 前端/IAB 环境观察）
- 归属：Hive 前端（`conversation.js:295-312` EventSource onerror 仅注释"自动重连"）
- 现象：IAB webview 中 HttpOnly 会话 cookie 约 20-30 分钟（有时 reload 后）即不再发送，
  后台轮询 401 后应用回退登录卡（红条可见，这点正确），但已打开的会话舱
  **EventSource Stream 停止推送且无提示**（B03 消息、状态 chip 全部冻结），
  需整页重载+重登录才恢复。同 cookie 经 Python 客户端 2 小时正常（TTL 7 天）——
  服务端会话机制本身无误，失效发生在浏览器侧。
- 证据：浏览器三次实测（10:47 登录 → ~11:0x 失效 → 重登 → ~11:4x 再失效；
  `fetch /ui/api/session` authenticated:false 而 document.cookie 为空）；
  `browser/b08-approval-desk.png`（首轮误拍登录页——已重拍修正）
- 影响：真实用户长时查看会话会遇到"画面冻结"；无法区分"无新事件"与"连接已死"。
- 修复方向：SSE onerror 加显式降级提示（连接已断开，点击重连/重新登录）；
  定期 `/ui/api/session` 心跳；EventSource 401 时停止静默重试。

---

## 低

### DEF-17 tool_call content 结构不符官方 schema（ZSession）
- `acp.mjs:432-433`：终态 content 直接发 `[{type:"text",text}]`。标准 ToolCallContent 为
  包装结构 `{type:"content", content:{type:"text",text}}` | diff | terminal。
  本次运行时样例恰好无 outputText 未捕获到实例帧；静态位置确定。
- 附：`pendingApproval:'in_progress'`（标准语义 awaiting approval 应为 pending）。

### DEF-18 cancel 前先把输入置 cancelled，上游失败不回滚（Hive）
- `webui.py:1131` 先 `set_input_state(active,'cancelled')` 再调桥；cancel_turn 抛错时
  已置 cancelled 不回滚（Hive-unblock 实测：upstream_error 与 DB=cancelled 并存，
  后续 _dispatch_one 完成时又可能覆写为 completed——状态机竞态）。

### DEF-19 IAB webview 登录输入框为明文 textbox，token 暴露在可访问性树（Hive 登录页）
- 快照/读屏可获得完整 token；建议 `type="password"` + autocomplete="off"。
  另：登录卡在登录后仍留在 DOM（hidden），token 值残留。

### DEF-20 视觉细节（Hive 前端，glm-vision 评审）
- 星图右侧空态提示末字贴视口右缘（1280px）有截断风险；辅助文字（事件带/恢复自动布局/空态提示）对比度偏低；
  节点分组名 "other/other (2)" 为英文小写，与全中文界面不一致；会话舱头部徽章"会话 idle"与
  消息流"思考中…"同屏矛盾（状态刷新缺陷）；Agent 正文混入英文推理前导段（"The user asks…Just comply"，
  与 ZSession 已披露的 GLM-5.3 assistantText 行问题同源，但 Hive 渲染层未做任何标注）。
- **窄屏顶栏逐字换行（390px 三轮复现）**："蜂群指挥台"逐字竖排、HIVE 徽标被挤出；"已连接·更新于刚刚"
  竖列；"收件箱"按钮内竖排撑高；仅"全部项目/适配视图"正常。窄屏下侧栏无可见入口。
  证据：`browser/b15-narrow-390.png` + `tmp_crops/b15-final-topbar.png`。
- 审批台桌面版（b08，1440×900）PASS，遗留：标题行首图标被面板左缘裁切；模式盒缺主标签
  （仅描述文案、左盒灰字对比度低）；画布左下角暗文字被截断。

---

## 记录：非缺陷观察
- Z19（stdin 关闭 adapter exit 0、会话不归档、继续运行）：PASS。
- Z07（工具生命周期稳定 toolCallId、marker 文件一致）：PASS。
- Z13（同连接同 requestId 幂等 + 异内容 -32010 拒绝）：PASS。
- Z05/Z06（两轮上下文、45 个真实增量、末帧早于 prompt 响应）：PASS。
- H01/H02/H05/H09/H10/H11/H19：PASS。
- B08/B09（双评分独立入账、五项校验拒绝矩阵、评论不结单）：PASS。
- H17（无 HIVE_ACP_ADAPTER 时真实 harness 实例的聊天输入得到 fixture 模拟回答，
  后端接口如实暴露 `bridge:"fixture"`、UI 显示"演示模式：fixture 模拟回答"标注）：
  机制按明确模式处理且不伪造已连通，但"真实实例+演示回答"同屏存在误读风险——随 DEF-20 一并改进
  （建议对 fixture 桥下的输入给出更强阻断提示）。
