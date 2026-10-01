# ACP 修复轮复验缺陷清单 — run reacp-20261001-132450-v1

> 复验 Agent：ZCode 会话（独立复验）。日期：2026-10-01。
> **设计裁定（2026-10-01，Owner）**：Hive 会话恒 yolo，工具级权限桥接不做（人类控制在任务层）。
> 据此 DEF-04 = N/A（按设计），RDEF-02/RDEF-06 降级为"低·留档"（不启用路径的死代码缺陷）。
> 版本：Hive git `6aa4089`；ZSession 非 git（快照 `snapshots/zsession/acp.mjs` md5 `e58a74f562f14436979df411cb28e0bd`；daemon PID 59140 @13:17:20 确认运行新代码）。
> 旧验收证据基线：`D:\agent_workspace\tmp\hive-acp-acceptance\acc-20261001-100912\`。
> 本 run 证据：`D:\agent_workspace\tmp\hive-acp-reacceptance\reacp-20261001-132450-v1\`（protocol/ 帧+结果、api/、browser/ 截图、tmp_crops/ 视觉裁剪）。
> 旧 DEF 状态与新增 RDEF 编号不混淆；每条含复现与复验条件。

---

## 阻断级（新发现/修复未完成）

### RDEF-01（阻断·回归）busy 冲突被写入终态 rejected → Hive 重试触发假成功，输入静默丢失
- 归属：ZSession `acp.mjs:299-301` + `acp.mjs:273-274` ＋ Hive `convbridge.py:_fail_input`
- 链：
  1. 会话忙时 adapter `dispatchTurn` 先把 requestId 持久写为**终态 rejected**（忙是瞬态，不是终态语义）。
  2. Hive 收到 -32011 → 按契约"回队重试（上游尚未登记该 requestId，重发安全）"——该契约假设已被修复破坏（预约先于 busy 检查）。
  3. 重试时 adapter 命中终态短路：`rec.state==='rejected'` → 返回 `stopReason:'end_turn'`——**从未执行**。
  4. Hive 把"完成"写进队列，UI 显示已完成。
- 实测：rd09-r1（bootstrap 忙）与 rr03-next（重启窗口忙）两条输入注册表均 rejected、
  对话无对应回答、目标文件无 marker；Hive conv_inputs 显示 completed。对照组 rd12-r0
  （无忙冲突）真实 done。
- 证据：`protocol/d-results-P3/P4.json`、`api/r03-*.json`、`<run>/hive-home/logs/hive.log`（busy_requeued）+
  daemon `/acp/inputs` 注册表取证。
- 影响：原需求"相同业务输入安全重试""非accepted即running"与"真实依据"同时失守；
  比旧版 DEF-01 更隐蔽——旧版至少不假装成功，本轮直接产成功假账。
- 修复方向：busy 不写终态 rejected（应为 retryable 状态或直接 -32011 不落库）；
  `rejected/failed` 终态重试不得返回成功 stopReason；Hive -32011 分支需能识别"已登记 rejected"并重置预约。
- 复验条件：rd09/rr03 场景重跑，重试后真实执行且副作用恰一次。

### RDEF-02（低·留档｜原判阻断，按 §0.1 设计裁定降级）session/request_permission 答复无法送达原生
- **设计裁定影响**：Hive 恒 yolo、工具级权限不做——本链路不在产品路径上。缺陷记录保留，
  仅当未来启用非 yolo（守卫型 Agent）时需按下述复验条件修复后再启用。
- 归属：ZSession adapter→daemon resolveInteraction 链（`acp.mjs:496-506`/`manager.mjs:1056-1070`）
- 实测：plan 模式会话发出合法 request_permission 反向请求（真实 interactionId）；严格 client 以标准
  `{"outcome":{"outcome":"selected","optionId":"allow"}}` 答复后 **30+ 分钟原生仍 pendingPermissions=1、
  工具停 pendingApproval、轮次挂起**；adapter 侧 120s 超时自动 cancelled 同样未送达原生（待决仍在）。
- 证据：`protocol/frames-P6.json`（15.1s 反向请求 → 16.0s 标准答复后零后续帧）、
  daemon 原生状态取证（`working, pendingPermissions=1`）。
- 附证：同会话 daemon `/interrupt` 对 pendingApproval 轮次返回 `confirmed:false, sessionStatus:running`
  ——权限待决轮连原生 stop 都拦不住。
- 影响：原需求非 yolo 权限桥接**未完成**；Hive 待决权限卡即便出现也无路径生效。
- 修复方向：定位 v4 resolveInteraction 为何不生效（ack 被 dlog 吞掉，需上抛）；
  答复/超时/cancel 三类通路各跑一次端到端再标 fixed。
- 复验条件：allow→工具真实执行+marker 落盘；reject→工具不执行且轮次如实终止；
  cancel/超时→待决清除、轮次不误判完成。

### RDEF-03（高）load 回放与事件泵双重投递，且回放帧无 replay 标记
- 归属：ZSession `acp.mjs:193-229,509-527`；Hive `convbridge.py:_project_update`
- 实测：多轮历史会话经严格 client `session/load`：回放 2 user + 3 agent chunk（但**无 _meta.zsession.replay**
  标记——契约 v0.5 声明有）；随后 0.6s 内事件泵从 cursor=0 又把同一会话旧行再发一次
  （`R11-post-load-duplication`：新增流里出现旧暗号原文）。
- 证据：`protocol/frames-P2b/P2c.json`（同文多帧且 replayMeta={}）。
- 影响：消费端收到重复历史；Hive 因无法识别 replay 会双写历史（replay+history 页）。
- 修复方向：所有回放帧统一带 `_meta.zsession.replay`（注意 Hive 当前读的是 params 级 _meta，
  adapter 把标记放 update 级——两边对不上）；事件泵须在 load 后对齐水位（或跟踪行水位）。

### RDEF-04（高）Hive 重启后注册表孤儿 started + 外部等待路径可再写假终态
- 归属：ZSession `acp.mjs:276-279`（started → awaitTurnEnd external）
- 实测：rr03-long 在 Hive daemon 重启时处于 started；新 adapter 查询仍 started 永不落账；
  若此时重试，将进入"并入等待"→ 轮询到 idle → settle 写 done——**把未知结果写成完成**。
- 证据：daemon `/acp/inputs` 注册表取证（rr03-long state=started 滞留）。
- 修复方向：started 且记录原生 inputId/turnId 时按上游 turn 对账；无凭据则标 unknown，禁止转 done。

### RDEF-05（高）load 响应 `modes:["yolo"]` 违反官方 schema
- 归属：ZSession `acp.mjs:226`；set_mode `acp.mjs:408` 同形
- 官方依据：`LoadSessionResponse.modes` 须为 `SessionModeState {currentModeId, availableModes[]}` 或 null
  （schema-v1.24.1 `$defs`）。
- 实测：`{"modes":["yolo"]}` —— 字符串数组，严格 client 无法解析为模式状态（R07 FAIL）。
- 另注：`SetSessionModeResponse` 仅定义 `_meta`（additionalProperties），set_mode 返回 modes 数组不违规但无规范语义；
  建议 set_mode 响应至少返回标准 `{_meta}` + 用 `current_mode_update` 通知或文档明确。

### RDEF-06（低·留档｜随 RDEF-02 降级，同属不启用的权限路径）request_permission 帧不符合官方结构
- 归属：ZSession `acp.mjs:454-486`
- 实测帧：params 只有 `{sessionId, options, _meta}`——**缺必填 `toolCall`**（标准 RequestPermissionRequest）；
  options 元素为 `{type:'allow_once', optionId, _meta.zsession.label}`——标准字段应为
  `{optionId, name, kind}`；原生选项被默认 allow/reject 替代（ExitPlanMode 原生选项未透传）。
- 证据：`protocol/frames-P6.json` 15.1s 帧。
- 修复方向：补 toolCall（ToolCallUpdate：toolCallId/title/kind）；options 用 optionId+name+kind。

---

## 中

### RDEF-07（中）受管写入约束在真实链路完全不生效；writer 仅凭请求头自称
- 归属：ZSession `acp.mjs:153-158`（claim 仅 ownerId 显式时）＋ Hive `convbridge.py:449-482`
  （ensure/load 不传 ownerId/generation，且仍读旧字段 `loaded`）＋ daemon `manager.mjs:865-868`
- 实测：Hive 绑定并跑 8 轮真实对话后 `/acp/claims` 为空（`api/d15-r09.json`）；
  owner-token HTTP 直写被受管会话照常受理（v0.5.1 设计如此）；writer 对非 owner 角色
  仅按 `X-ZS-Writer` 头放行（无密码学凭据，头可伪造；本机无第三方 token，活体攻击测试 BLOCKED）。
- 影响：DEF-15 未在真实链路闭合；Hive 会话不受单写入约束。
- 修复方向：Hive ensure() 传 `_meta.zsession.ownerId/generation`；daemon 至少将
  `X-ZS-Writer` 与受控 token 角色绑定校验。

### RDEF-08（中）D16 静默死链未根除：无降级条、无自动恢复
- 归属：Hive `hive_web/js/conversation.js`（EventSource onerror 处理 + 订阅生命周期）
- 实测：V03 真实流式中途浏览器端再未收到事件（服务端 turn.completed 等已全部落库），
  chip 停留 dispatched、思考行冻结，无降级条；切节点后一切补齐。
- 证据：`browser/v03-session-stream-1440.png` + 服务端历史取证（事件链完整）。
- 注：修复报告声称的"断网降级条"场景有效，但该失效模式（连接未断、数据停流）未被捕获。
- 修复方向：以"最后事件时间戳+心跳超时"为降级依据（而非仅 ES onerror），并主动重建订阅。

### RDEF-09（中）load 回放不含工具行
- 归属：ZSession `acp.mjs:193-219`（回放源 `/sessions/:id/history` 仅含 text 行）
- 实测：有工具执行的会话 load 后 toolReplay=0。
- 影响：回放历史与原生不一致（工具活动缺失）；D05 完整性项未达。

### RDEF-10（中）取消在本环境不可靠：cancelled 只出现在确认成立时
- 归属：ZSession `acp.mjs:385-398` + daemon `/interrupt`（15s 等待上限）
- 实测：长轮 cancel（无 id 通知）→ 原生未在 15s 内离开 running → adapter 如实返回
  `end_turn`（不谎称 cancelled）；零伪消息/零额外轮（该两点已修复）。
- 评价：假成功已消除（诚实），但"真正取消"未达成——原生 stop 在部分宿主/权限待决场景无效。
  联合需求："取消被原生确认"尚未稳定。
- 另：turn 级取消无 turn token 守卫（H07 遗留：竞态下取消可能命中下一轮）。

### RDEF-11（低-中）窄屏残留问题（DEF-20 部分）
- 顶栏逐字竖排**已修复**（390/320 单行横排，导航轨底部横排）✓；
- 新遗留：390px 下 240px 详情抽屉盖住星图节点左缘；窄屏节点全名标签不显示（仅剩盘内短名）；
  审批判决为竖排 radio 而非分段控件且未选态为实心圆点；评分滑块无刻度/范围提示。
  证据：`browser/v06-narrow-390.png`、`tmp_crops/c06-*.png`、`tmp_crops/c04-*.png`。

### RDEF-12（低）生产星图连线/标签叠字
- `browser/v07-prod-starmap-1920.png`（26 节点真实数据）：进化线组标题被"N 件任务"连线标签覆盖
  （"反1 件任务 件任务"叠字不可读）；根节点路径文字与箭头重叠；连线穿过节点标题。
  证据：`tmp_crops/c07-evo-title.png`、`c07-root-node.png`。

### RDEF-13（低）会话流中英拼接无分隔 + meta 行孤词
- `browser/v02-node-detail-1440.png`：相邻流式 chunk 跨语种直接拼接（"…No tool收到 D15 直写探针。"）；
  会话 meta 行换行后剩孤词 "state."。证据：`tmp_crops/c02-mixedline.png`。

### RDEF-14（低）busy 回队会重复写 turn.started
- `api/d12.json`：rd09-r1 两次派发产生两条 turn.started；终态只一条。建议 begin_turn 幂等。

---

## 文档/证据缺陷（不属于功能代码）

### RDEF-15 ACP_INTEGRATION.md v0.5 自冲突
- 开头"⚡ v0.5 关键变更"与 §2 能力表 / §4 工具状态映射矛盾：
  load 一边说全量回放一边说 live-forward 不回放；session/set_mode 与 `set_mode` 标准性描述不一致；
  request_permission 一边声明已桥接一边标"❌ 未实现"；工具 status 映射 §4 仍写 error→completed。
- 接口文档应单点真源。证据：`snapshots/zsession/ACP_INTEGRATION.md` 全文。

### RDEF-16 修复报告的 fixed 与实测不一致项
- `ACP_FIX_REPORT.md` "DEF-03 fixed/turnCount Δ=1" —— 本环境实测 cancel 未停止原生轮（confirmed 不稳定已
  被 Hive 报告承认，但 ZSession 报告仍标 fixed）。
- "DEF-04 core-verified/pending_integration" —— 实测连 core 也不通（答复无法送达原生，超时同样无效）。
  注：该项已按 §0.1 设计裁定改判 N/A（不启用路径），但"报告声称与实测不符"的事实记录保留。
- "DEF-05 fixed" —— 回放发生但 tools 缺失且与事件泵重复投递。
- Hive 报告"DEF-14 已关闭" —— 上游历史在联合链路可用（首绑历史成立），但下游经 load 回放/泵的双写未除。

---

## 记录：已关闭项（本证实测）

- DEF-04 **N/A（按设计裁定，2026-10-01）**：Hive 恒 yolo、工具级权限不做；原"待联调"不适用。
  适配器侧已实现的反向请求桥接存在 RDEF-02 缺陷，留档（修复条件见该条）。
- DEF-01 CLOSED：会话级隔离、同 id 异内容 -32010、get_input_state 严格本 session（P3）。
- DEF-02 CLOSED（含残留 R02 启发式）：adapter 重启后 done 可查、同 ID 同内容零重执行；
  reserved>120s+idle 的"猜测未执行重派"为文档化的已知窗口（未实测触发，代码层为预留风险）。
- DEF-06 CLOSED：initialize 有 agentInfo{name,version}（schema AgentInfo 未在基线命中；
  文档要求 title 字段未提供——作标题级"部分"注记）、promptCapabilities 三标准布尔、未初始化 -32002。
- DEF-07 CLOSED：session/set_mode 标准方法名存在、未知会话/非法 modeId 正确报错；
  真实模式变更本轮未深测（R07 modes 形状问题另列）。
- DEF-12 CLOSED：seq 单调，同秒 6 条按序派发。
- DEF-13 CLOSED：409 STALE_INSTANCE（输入与取消均）。
- DEF-19 CLOSED：登录 password 遮蔽+autocomplete=off+显示切换。
- DEF-08 CLOSED（代码+行为层）：cancel_turn 为无 id 通知，不等 60s；取消终态由 prompt 收口。
- DEF-10 PARTIALLY_FIXED→OPEN-子链：startup_sweep 落账正确（outcome_unknown、queued 续派）；
  但恢复路径撞到 RDEF-01 假完成。卡在取消中（cancelling）窗口未专项复现。
- DEF-09 PARTIALLY_FIXED：turn.* 事件已写、chip 有事件驱动刷新；但 SSE 死链期间界面仍冻结（RDEF-08）。
- DEF-11 CLOSED（接口层）+ N/A（联合层，按 §0.1 设计裁定）：endpoint 验证真实（NOT_FOUND/NOT_IMPLEMENTED），
  假成功已除；联合权限链路因"恒 yolo"裁定不适用。
- DEF-14 PARTIALLY_FIXED：分页/过滤/首绑历史成立；工具行回放缺失、双写风险未除。
- DEF-15 OPEN（联合不生效）。
- DEF-16 PARTIALLY_FIXED（断网场景，见 RDEF-08 未覆盖模式）。
- DEF-17 PARTIALLY_FIXED（代码与部分帧证据：error→failed、content 包装；未采集到本轮活体失败帧）。
- DEF-18 PARTIALLY_FIXED（cancelling+回滚代码成立；cancelling 窗口的重启/竞态未专项复现）。
- DEF-03 PARTIALLY_FIXED：伪用户消息/额外轮次已消除（实测零注入）；但取消未可靠停止原生轮
  （confirmed=false 时如实返回 end_turn——诚实但"没取消成"，见 RDEF-10）。
- DEF-05 PARTIALLY_FIXED：文本回放已实现、不存在会话返回标准错误；但回放缺工具行（RDEF-09）、
  modes 形状非法（RDEF-05）、与事件泵重复投递（RDEF-03）。
- DEF-20 PARTIALLY_FIXED（顶栏修复；残留见 RDEF-11）。

> 20 项合计：CLOSED 9（01/02/06/07/08/11接口层/12/13/19）· PARTIALLY 9（03/05/09/10/14/16/17/18/20）
> · OPEN 1（15）· N/A按设计 1（04）。

## 特别记录：环境
- 本轮生产 Hive 19962 于 12:41 起运行修复后代码；ZSession daemon 59140 于 13:17 起运行 0.5.0 adapter。
- 复验 home `…/reacp-20261001-132450-v1/hive-home`（19980，已停止）+ `hive-home-h17`（未建，本轮未用 fixture fallback 复测）。
- 生产 UI 登录态已退出；P6 挂起会话（sess_68119062）保留作为证据（interrupt confirmed:false 亦实证"取消不可靠"）。
