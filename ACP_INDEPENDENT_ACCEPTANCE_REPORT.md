# ACP 独立验收报告 — ZSession ACP 接口 & Hive 会话舱/星辰大海

> 独立验收 Agent：ZCode 会话（非施工会话）。日期：2026-10-01。
> 验收依据：`ACP_INDEPENDENT_ACCEPTANCE_PROMPT.md`；原需求
> `ZSESSION_ACP_UPGRADE_PROMPT.md`、`FRONTEND_ACP_COSMOS_PROMPT.md`、`DESIGN.md`（抽查）。
> 本 run 全部原始证据：`D:\agent_workspace\tmp\hive-acp-acceptance\acc-20261001-100912\`
> （协议帧、API 取证、浏览器截图、schema 校验、逐用例结果 JSON）。

---

## 0. 结论（先行）

| 维度 | 结论 |
|---|---|
| **ZSession 原需求完成度** | **不通过**。中文对话/真实流式/工具生命周期/取消返回/stdin 断开隔离等核心链路真实可用；但跨会话输入隔离（DEF-01）、跨连接幂等（DEF-02）、失败工具语义（DEF-03/DEF-17 相关）、非 yolo 权限桥接（DEF-04）、load 历史回放（DEF-05）、标准 set_mode（DEF-07）等原需求项失败。 |
| **ACP 标准兼容性** | **不通过**。initialize 缺 agentInfo 且 promptCapabilities 形状非法（DEF-06）；loadSession=true 但不做协议规定的历史回放、load 响应用非标准 `loaded` 字段（DEF-05）；失败工具映射 completed 而非 failed、tool_call.content 非 ToolCallContent 包装结构（DEF-17）；标准 `session/set_mode` 未实现（DEF-07）；未初始化即可建会话（DEF-06）。严格标准 client 不能把当前链路当标准 ACP 互操作。 |
| **Hive 后端与前端完成度** | **部分实现**。输入队列/状态机/SSE 恢复/审批台/服务端鉴权与 CSRF/断线对账等多项实测通过；但同秒 FIFO 乱序（DEF-12）、重启后 dispatched 卡死阻塞队列（DEF-10）、STALE_INSTANCE 错误码丢失（DEF-13）、权限答复假成功（DEF-11）、ACP 链路无 turn 事件导致排队状态假显示（DEF-09）、fixture 冒充风险（H17 观察）。 |
| **真实有限对话链** | **受限可用**。"YOLO 中文实时对话 + 真实工具 + 取消返回 + 刷新补历史"端到端真实跑通（有真实 sessionId/instance/轮次/截图），可作为有限可用结论；不能据此豁免历史/权限/幂等/隔离项。 |
| **视觉与性能** | **需改进**。星辰大海方向成立（星图、会话舱有截图与视觉评审证据）；审批台要素齐备；但窄屏顶栏字符级换行破损（390px 实测）、idle/思考中矛盾、英文推理前导混入正文；50 节点/长历史性能未测（测试数据不具备，如实标未测）。 |
| 用例计数 | Z 组 20 项：PASS 7 / FAIL 10 / 部分 3；H 组 20 项：PASS 8 / FAIL 7 / 部分 5；B 组 15 项：PASS 6 / FAIL 3 / 部分 5 / NOT_TESTED 1；E2E 主线 1 项：**完成（受限）**。明细见 §3。 |
| **最重要阻断项** | DEF-01（跨会话 requestId 串号，输入被吞且假报成功）、DEF-02（重启后重发=重复执行）、DEF-03（cancel 注入伪用户消息+额外原生轮次）、DEF-04（非 yolo 权限不桥接且无限挂起）、DEF-05（loadSession=true 但不回放历史）。 |

---

## 1. 测试环境与版本

| 项 | 值 |
|---|---|
| Hive 被验收版本 | git `e884cdd`（main；工作区仅未跟踪 `ACP_INDEPENDENT_ACCEPTANCE_PROMPT.md`） |
| ZSession 被验收版本 | **非 git 仓库**（无版本控制）；快照 `snapshots/zsession/acp.mjs` md5 `07b55973740f6835b6f22950ea3f2f67`，mtime 2026-10-01 02:04 |
| 官方基线 | ACP **schema-v1.24.1**（GitHub release `schema-v1.24.1`，含 `meta.json`）+ agentclientprotocol.com/protocol/v1 七页文档，均已落盘 `protocol/` |
| ZSession daemon | 127.0.0.1:8765（生产，PID 21144，未动） |
| 生产 Hive daemon | 127.0.0.1:19962（PID 36264，未动；只读核对） |
| 验收 Hive 测试 daemon #1（ACP 桥） | 127.0.0.1:**19970**，home `<run>/hive-home`，`HIVE_ACP_ADAPTER="node …/bin/zsession.mjs acp --stdio"`；测试 Agent A `agt_99537db86fdaa7e070b4`（实例 `ins_7a89519df897ebf4fa49` gen1 → 真实会话 `sess_f58c4a05-4f41-4efe-b905-af7a49a05169`）、B `agt_c41ac3cd5eb3f3c9bd1c` |
| 验收 Hive 测试 daemon #2（fixture 回退） | 127.0.0.1:**19971**，home `<run>/hive-home-h17`，**无** HIVE_ACP_ADAPTER；Agent C `agt_68841f848898de558d11`（实例 `ins_9e324425effd855b4d80` → `sess:sess_94ddf242-846e-43e3-84b4-a4e905674bd4`） |
| Z 会话（adapter 直连 daemon） | `sess_899b051d-dce1-4410-a56f-81aada734005`（Z04/Z05-Z20 主线）、`sess_b457f5a3-…`（Z14 隔离）、`sess_62d918a5-…`（Z16 plan 模式）、`sess_6f57260e…`（Z03 未初始化违规创建的会话，已记录） |
| 浏览器 | ZCode IAB（iab 后端），1280×720/1440×900/390×844 三档 viewport |
| 模型 | bigmodel-api / GLM-5.3（真实计费调用，全部来自隔离测试资源） |

## 2. 与施工报告的版本差异说明

- ZSession 报告 §4 自述的限制（load live-forward、request_permission 未实现、内存幂等、
  error→completed 映射、turn 收口轮询）与当前代码一致——本次逐条实测确认，非旧闻。
- Hive 报告 §6"全项已验：1-24"与自身 §4"已知限制"矛盾：其自述未实现的权限桥接（项16）、
  live-forward 历史（会话舱历史来源）被计入"全项已验"。本次按实测独立判定（见 §3、§5）。
- `scripts/acp_e2e.py` 本次复审确认为**协议级夹具驱动，不驱动浏览器**；其 8 步证据
  （`work/acp-e2e/e2e-evidence.json`）覆盖 initialize/new/流式/幂等/取消/对账/断开/重接 lane，
  但断言强度不足（见 §5 表）。前端截图在施工报告中只有"见 ZCode 工件目录"表述、
  **无绝对路径与版本**，按证据缺口处理；本次 B 组截图已按绝对路径补齐 4 张。

## 3. 用例矩阵（实测判定）

### 3.1 ZSession 协议（Z01-Z20，自研严格 client，帧存 `protocol/frames-*.json`，结果 `protocol/z-results-*.json`）

| ID | 判定 | 关键证据 |
|---|---|---|
| Z01 | **PASS** | `--help` 无凭据泄漏；stdin 关闭 adapter exit 0 不kill会话；daemon PID 稳定无第二宿主 |
| Z02 | **FAIL** | initialize 无 agentInfo；promptCapabilities={loadSession:true} 非法；协议版本协商本身正确 |
| Z03 | **FAIL** | **未初始化即接受 session/new 并创建真实会话**；非法方法 -32601；碎帧中文不损坏、进程可继续（子项 PASS） |
| Z04 | **PASS** | 绝对 cwd 真实生效（daemon workspace=测试目录）；bootstrap:false turnCount=0；错 cwd -32602 |
| Z05 | **PASS** | 两轮暗号上下文保持（蓝鲸七号）；stopReason=end_turn×2 |
| Z06 | **PASS** | 45 个字符级增量，末帧早于 prompt 响应，全文 762 字 |
| Z07 | **PASS** | 稳定 toolCallId `call_cc56196d…` pending→in_progress→completed；marker 文件落盘一致 |
| Z08 | **FAIL** | 原生失败的 Read 工具 ACP 报 **status=completed**（无 failed、无错误内容） |
| Z09 | **FAIL** | cancel 返回 cancelled 合规；但原生 turnCount 5→7，userInput 注入 "cancel requested by client" 并多跑一轮 |
| Z10 | **部分/FAIL** | 迟到 cancel 仍返回 cancelled（竞态语义），但 transcript 残留注入轮内容；下一轮可跑 |
| Z11 | **FAIL** | session/load 139ms 返回 loaded:true，**0 条历史回放**；`replayed:true` 字段误导 |
| Z12 | **FAIL** | 不存在会话返回成功信封 loaded:false（标准应为错误）；归档探测无该 daemon 路由 |
| Z13 | **PASS** | 同 requestId 同内容重放 0 副作用；异内容 -32010 拒绝 |
| Z14 | **FAIL（阻断）** | 同 requestId 跨会话被吞：返回 end_turn 但模型未执行、计数不变；状态查询串号 |
| Z15 | **FAIL（阻断）** | 重启后 get_input_state=unknown；同 ID 重发**重复执行**（副作用 2→4） |
| Z16 | **FAIL（阻断）** | plan 模式 240s 无 session/request_permission；prompt 无限挂起到超时 |
| Z17 | **FAIL** | 标准 `session/set_mode` → -32601；仅有非标准 `set_mode` |
| Z18 | **FAIL** | daemon HTTP 其他入口对 ACP 活动轮会话仍 accepted:true（无受管写入约束） |
| Z19 | **PASS** | stdin 关闭 adapter exit 0；原生会话存活不归档；未决调用收口 |
| Z20 | **部分** | 重连后 get_session_state 对未 load 会话返回 -32001（契约内）；迟到尾部未实测到丢失 |

### 3.2 Hive 后端（H01-H20，独立 home + 真实 ACP 桥；证据 `api/*.json`）

| ID | 判定 | 关键证据 |
|---|---|---|
| H01 | **PASS** | 3 次读取实例/绑定不变（ins_7a89519d…/sess_f58c4a05…），不新建实例不复制会话 |
| H02 | **PASS** | 受理 3ms；dispatched(1.5s)→running(3.3s)→completed(10.2s)，各态有真实时间戳 |
| H03 | **FAIL** | 长轮期间两条输入保持 queued（不自动 interrupt ✓）；**同秒入队 FIFO 乱序**（r3 先于 r2，created_at 同秒+随机 input_id 决胜） |
| H04 | **部分** | 统一调度入口存在（ConversationService）；inbox 唤醒与 control send 走同一队列；未构造并发竞运 |
| H05 | **PASS** | 同 ID 重放 replayed:true 无二执行；异内容 409 IDEMPOTENCY_CONFLICT；H12 场景见 DEF-10 |
| H06 | **FAIL/部分** | 取消分态存在（cancelled:false+upstream_error 可呈现）；但 DB 先置 cancelled 不回滚（DEF-18），重启后首次 cancel 报 unknown session |
| H07 | **FAIL** | cancel 端点只校 instance/generation，**不校预期 turn**；仅 UI 防重 |
| H08 | **FAIL** | fencing 逻辑正确（拒绝旧实例写入）但错误码变 500 INTERNAL_ERROR（DEF-13） |
| H09 | **PASS** | history 分页 next_after 正确；gap_before 逻辑存在 |
| H10 | **PASS** | SSE after=0 回放 148 帧；after=60 续 88 帧（147-60+hello）；心跳存在 |
| H11 | **PASS** | kill 本 run adapter → 活动输入 outcome_unknown → supervisor 15s 退避重连 → 不盲目重放 |
| H12 | **FAIL** | 重启后 dispatched 输入永久卡死并阻塞全队列（141s 实测；需人工 cancel 解锁）；绑定/实例恢复 ✓ |
| H13 | **PASS** | 网页/上游双通道独立；断线不标 idle/completed（outcome_unknown 实证） |
| H14 | **部分** | 绑定前 bootstrap 轮历史经上游会话可读（daemon 事件环深度受限，见 DEF-14） |
| H15 | **FAIL** | permission 答复端点对未实现通道返回 answered:true 假成功（DEF-11）；上游无权限卡（DEF-04） |
| H16 | **部分** | 文本输入真实送达；附件/图片/音频 ContentBlock 未实测（IAB 上传不可用）；模型/模式 UI 未测 |
| H17 | **部分** | 无 HIVE_ACP_ADAPTER：真实 harness Agent C 的聊天输入得到 fixture 模拟回答；后端接口如实暴露 bridge=fixture、UI 标注"演示模式"——机制明确但同屏有误读风险 |
| H18 | **部分** | 坏 token 401 TOKEN_INVALID；owner 跨 agent 全通（设计）；非 owner 受管资源 belt 未穷尽（缺第二个 human 身份实测） |
| H19 | **PASS** | 缺 X-Requested-With → 403；无 cookie → 401 AUTH_REQUIRED；写路径均拒绝 |
| H20 | **部分** | delta/思考/工具投影正确（按 tool_call_id 归并）；**无 turn.* 事件**导致 UI 状态假显示（DEF-09）；迟到旧实例事件隔离由 session_id 绑定保证 |

### 3.3 浏览器（B01-B15，IAB 真实浏览器；截图 `browser/*.png`）

| ID | 判定 | 关键证据 |
|---|---|---|
| B01 | **PASS** | 登录→星图（A/B 真人节点+人类决策台）→侧栏打开 A：真实 instance/gen/session/历史 |
| B02 | **部分/FAIL** | 中文多行受理即时、区分排队/进行；**完成后状态 chip 不刷新**（后端 completed vs UI queued，DEF-09） |
| B03 | **部分** | 长回答/思考行/代码渲染可见；工具成功/失败卡 UI 级直拍未获得（历史被事件环挤掉，DEF-14）；协议级工具卡证据充分（Z07/Z08 帧） |
| B04 | **部分** | "↓ 有新输出"按钮与 follow 逻辑在 DOM 实证；阅读位置保留（paneCache.scrollTop） |
| B05 | **部分** | A/B 草稿+阅读位置+cursor 按 agent 隔离（paneCache，静态+实测）；附件选择不保留 |
| B06 | **部分** | 按钮组（发送/中断并发送/停止本轮/发任务）与后端 cancel 语义一致；取消中态 UI 文案"已请求取消本轮"诚实；H03/H06 事实见上 |
| B07 | **NOT_TESTED** | 测试任务由 owner 创建、无发起 Agent，"和发起 Agent 讨论"入口不适用；未假装通过 |
| B08 | **PASS** | 真实 Task（tsk_7abe0676…）：双评分滑块(step5/10)+结论单选+意见 → 提交"答复已入账" → task done、answers 85/90/pass 落账（API+CLI 双证）；草稿跨重登/重载保留 |
| B09 | **PASS** | 必填/步长/越界/未知选项/spec_version 五项拒绝（400/409 带 field_errors）；评论不结单（task 仍 queued） |
| B10 | **NOT_TESTED/部分** | 模型/模式切换 UI 未实测（控件入口未触发）；能力提示文案在 UI_API 文档层 |
| B11 | **FAIL/观察** | 会话 cookie 在 webview 周期性失效：SSE 静默停摆、回退登录卡可见红条（DEF-16） |
| B12 | **部分** | 视图稳定/刷新保留（截图前后一致）；拖拽/缩放坐标实测受 SVG rect 异常干扰，未取得干净数据 |
| B13 | **PASS（附改进项）** | 星图全景截图经视觉评审：深空底/玻璃面板/中文清晰/恒星+人形节点可辨/无破损；缺方形资产节点数据；改进项见 DEF-20 |
| B14 | **部分/FAIL** | prefers-reduced-motion CSS+nebula.js 静态帧/隐藏页暂停/DPR≤1.5 实证；键盘导航未系统测；**390px 顶栏字符级换行破损**（视觉评审实证，B15 兼任） |
| B15 | **部分** | 390 窄屏重拍为登录态主台（非登录页）；顶栏破损即缺陷证据；1440×900/1920×1080 与 23/50 节点性能 trace **未测**（隔离测试数据不具备，如实标注） |

### 3.4 E2E 最小真实主线（§10）

**完成（受限）**。逐事件时间线（本地时间）：

```
10:23:35  control agent start A → instance ins_7a89519df897ebf4fa49 gen1
          → zsession spawn sess_f58c4a05-4f41-4efe-b905-af7a49a05169（GLM-5.3, bootstrap 轮1）
10:23:47  acp.connected（initialize 协商，Hive daemon 日志）
10:25:09  UI input req acc-h02-r1 → 3ms accepted（conv_inputs queued）
10:25:10  dispatched → ensure() session/load live-forward（binding 建立）
10:25:12  running（_zsession/input_state started）
10:25:13  agent.delta×N → conv_events → SSE → 浏览器渲染
10:25:19  completed（stopReason=end_turn，原生轮2）
10:30:28  H11: kill adapter → 输入 outcome_unknown → 15s 后 supervisor 重连 → 对账不重放
10:31:17  H12: daemon 重启 → dispatched 卡死（DEF-10）→ cancel 解锁 → r1 completed
10:43:09  UI B02 消息 req_4f7de61d… → 浏览器端到端流式渲染（截图 b03）
10:47+     B01 真实会话面板（sess_f58c4a0…）；B08 审批链（task→双评分→done）
```
串联标识：agent `agt_99537db8…` ↔ instance `ins_7a89519d…`(gen1) ↔ session `sess_f58c4a05…`
↔ requestId `acc-h02-r1`/`req_4f7de61d…` ↔ 原生 turnCount（bootstrap+1）↔ 浏览器截图。
ACP 直连新建（session/new）与 Hive 绑定接入（session/load on handle）两条建会话路径均分别验证
（Z04 vs H02/Hive ensure()），未互相顶替。

## 4. 证据索引（绝对路径）

- 协议帧与结果：`<run>\protocol\frames-*.json`、`z-results-*.json`、`schema-validation.json`、
  `acp-schema-v1.24.1.json`、`official-*.md`
- Hive API 取证：`<run>\api\h01..h20-*.json`、`b08/b09-*.json`
- 浏览器截图：`<run>\browser\b13-starmap.png`、`b03-session-cabin.png`、`b08-approval-desk.png`、
  `b15-narrow-390.png`
- 被验收版本快照：`<run>\snapshots\`（ZSession 非 git，md5 已记录）
- 测试资源：`<run>\hive-home\`（19970）、`hive-home-h17\`（19971）、`workdir\`（marker/计数文件）
- run 根：`D:\agent_workspace\tmp\hive-acp-acceptance\acc-20261001-100912\`

## 5. 施工声称 → 实际覆盖（§9）

| 施工条目 | 引用证据 | 脚本真正断言 | 本次独立验证 | 结论 |
|---|---|---|---|---|
| acp_e2e.py "协议 8/8 PASS" | `work/acp-e2e/e2e-evidence.json` | 协议级 8 步（不驱动浏览器）；"流式"仅断言 chunk≥1+终文非空+stopReason；取消仅断言字符串 cancelled | 自研严格 client 复测全部场景+时间戳+副作用计数 | 部分成立：8 步本身真实，但**断言强度不足**；流式真实（本次 45 chunk 时序证明），取消**不干净**（DEF-03） |
| "幂等重放不重发" | e2e step4 | 仅同连接同 ID 同内容立即返回 | Z13 复测 PASS + 副作用计数；**跨连接** Z15 实测重复执行 | 同连接成立；跨连接**不成立**（DEF-02） |
| "断开不终止会话" | e2e step7 | adapter 退出后 daemon 可查 | Z19 复测 PASS（exit 0、会话不归档） | 成立 |
| "重接 load + 对账" | e2e step8 | load loaded:true；跨重启 state=unknown 如实 | Z11/Z15 复测：load 无回放（DEF-05）、unknown 后重发重复执行 | 部分成立；报告已披露 unknown 但未披露重复执行后果 |
| 前端"全项已验 1-24" | ACP_FRONTEND_REPORT §6 | quicktest.py=CLI 任务流（emit/claim/inbox），**不含**前端输入/评分/媒体/授权/性能 | B01-B15 浏览器实测 | 不成立：项16 权限（上游未实现+端点假成功）、13/15/21-24 无本次可复证证据；B08/B09 本次实测 PASS |
| "截图见 ZCode 工件目录" | ACP_FRONTEND_REPORT §3 | 无绝对路径/版本/内容 | 本报告 4 张绝对路径截图+视觉评审 | 按证据缺口处理，已补齐 |
| "queue 20s 静默升级 interrupt 已移除" | ACP_FRONTEND_REPORT §2 | harness.py 返回 queued-not-delivered（静态确认+运行时符合） | 本次 H03 长轮排队 60s+ 无 interrupt 升级 | 成立（属实） |
| "历史经消息查询/事件流可读" | 报告 §4 | /messages zod 兼容问题披露 | 实测 /messages 500；事件环 ≤500 上限致历史浅 | 部分成立（DEF-14） |

## 6. 视觉评价（glm-vision 独立评审 + 截图证据）

- **星图全景（b13-starmap.png，1280×720）**：深空底+玻璃面板+中文标签层级成立；圆形 Agent 恒星
  （光环实线/虚线表活跃）与人形人类节点可辨；工程面干净。改进：右侧空态提示贴边有截断风险；
  辅助文字对比度偏低；分组名 "other" 英文小写破坏中文语境。
- **会话舱（b03-session-cabin.png）**：用户/助手/思考三类消息与输入区结构完整，快捷键说明清晰；
  "聊天不入任务账"提示明确。问题：头部"会话 idle"与流内"思考中…"矛盾（状态刷新）；
  正文混入英文推理前导段（上游已知问题在 Hive 层无标注）。
- **审批台（b08-approval-desk.png，1440×900，ffprobe+DOM 双验viewport）**：**PASS（附3小项）**。
  三层布局清晰（顶栏/侧栏+星图/右侧审批面板）；双评分滑块组标题+金色手柄+数值框+"未评分"状态、
  结论单选、意见/说明双文本域、上传附件、"提交答复/裁决"金色主按钮+"无法回答"齐全；
  中文与对比度达标。遗留：标题行首图标被面板左缘裁切；两个模式盒缺主标签（仅描述文案、左盒灰字对比度
  偏低）；画布左下角暗文字被面板边缘截断。
- **窄屏（b15-narrow-390.png，390×844，DOM 验证主工作台态）**：星图三节点（圆形 Agent×2、
  人形人类）完整可读、无横向溢出；事件带折叠条可读；**顶栏 CJK 字符级换行破损第三轮原样复现**
  （"蜂群指挥台/已连接·更新于刚刚/收件箱"竖排，HIVE 徽标被挤出），且窄屏下侧栏无可见入口
  （汉堡/抽屉触发器未发现）。verdict：需改进（B14/B15 FAIL 项）。
- **动效**：CSS prefers-reduced-motion + nebula.js 静态帧 + 隐藏页暂停 + DPR≤1.5 + 粒子≤260
  （静态+Browser 实证）；装饰/业务动效分离原则在代码层成立。
- **性能**：23 节点项目实测交互流畅为施工报告声称；本次隔离环境仅 2 Agent 节点，50 节点/长历史
  **未测**（不谎报）。
- **截图环境说明**：IAB webview 会话 cookie 周期性失效（DEF-16）曾导致两次截图拍到登录回退页，
  已通过"登录+操作+截图"原子 cell 修正；该现象本身已作为缺陷记录。

## 7. 范围与未测项（如实披露）

未测/NOT_TESTED：B07（测试任务无发起 Agent）；B10 模型/模式 UI 切换；B12 拖拽/缩放坐标级证据；
B15 性能 trace（1440×900/1920×1080、23/50 节点、长历史分页）；H16 附件 ContentBlock 直传；
H04/H18 的并发与多身份穷尽；E2E 的"真实测试审批与 callback"只走了测试人工通道（owner 身份），
未接入 video-lab 23 节点业务责任（按要求只读）。

环境限制说明：IAB webview 会话 cookie 周期性失效（DEF-16）导致个别截图需登录后同 cell 完成；
该问题本身已作为缺陷记录，不影响 API 层结论。

## 8. 修复优先级建议（给施工方）

1. DEF-01/02/03/04（ZSession 输入与取消/权限语义）——阻断。
2. DEF-05/06/07（ACP 标准合规）——阻断"标准兼容"结论 Flip 的前提。
3. DEF-10/12/13/09（Hive 队列恢复/顺序/错误码/状态投影）——阻断"后端完整通过"。
4. DEF-11/16/18（假成功/静默停摆/状态竞态）。
5. DEF-14/15/17/19/20（工程与体验）。

## 9. 清理与保留

- 保留：本 run 目录全部证据、两个隔离 Hive home（19970/19971）、测试会话与 marker 文件，供复现。
- 已停止：仅本 run 创建的验收活动（测试 daemon 在收尾时停止）；生产 8765/19962 与
  video-lab 节点未触碰。
- ZSession 只读组件结果摘要已按要求不写入其项目目录（避免污染非 git 工作区）；
  施工报告未做任何修改。
