# ACP 第四轮修复独立验证缺陷清单 — run 4acc-20261002-004613-v1

> 验证 Agent：ZCode 会话（独立验收）。日期：2026-10-02。
> 版本：Hive git `9aeefe5`（convbridge/evalschema @00:01 Oct 2）；ZSession adapter acp.mjs @00:00
> （`post/get` 透传 extraHeaders ✓、`this.version='0.6.1'` ✓）。**生产 daemon PID 70160 仍为三修代码
> （10-01 21:24 启动）且未开强模式——四修未部署生产，如实记 pending_deployment。**
> 证据：`D:\agent_workspace\tmp\hive-acp-fourth-acceptance\4acc-20261002-004613-v1\`
> （api/smoke1/smoke2/j03/f-series.json、browser/ 5 截图、strong-home 日志与 daemon.err.log）。

---

## 本轮验证结论：旧 TDEF-01～05 全部 CLOSED

- **TDEF-01 CLOSED（代码+活体）**：`post(path, body, extraHeaders)` 透传（acp.mjs:49-50）；
  本 run 强模式真实链验证——claim 绑 writerKeyHash（sha256）后，经 stdio adapter 的 UI 输入
  **真实执行**（marker=1、completed），无 409（上轮被自己 claim 拒之门外的场景消除）。
- **TDEF-02 CLOSED（活体）**：强模式冒烟1 撞 bootstrap busy——adapter -32011 帧 ×4 +
  hive `busy_requeued` ×4，输入正确回队、无 Row.get 崩溃（daemon.err.log 零 AttributeError）、
  最终 completed、marker 恰 1。冒烟2：长轮 busy + 同秒两输入 → FIFO 各恰执行一次（b1=1、b2=1）。
- **TDEF-03 CLOSED**：显式 fields 顶层 77 → 400"评分不符合步长 5"（正确步长错误，不再误报未填）；
  75 → 200；自定义字段（name/rating）顶层入账保留。
- **TDEF-04 CLOSED**：合法双评+恶意 `grant/authorize` → 200，result 仅含合法五键
  （explicit_autonomy_grant=null）——**恶意键零残留**（上轮原样入账已除）。
- **TDEF-05 CLOSED**：源码/agentInfo/日志版本同源 0.6.1。

## 新发现缺陷

### FDEF-01（中）started 孤儿跨 adapter 死亡无收口入口：原生已完成的轮次台账永卡 outcome_unknown
- 场景（J05 实测）：长轮进行中 kill adapter（本 run 进程）→ Hive 桥断线将活动输入置
  outcome_unknown（诚实）→ 桥 1s 重连 → **原生轮次继续并完整产出**（5935 字长文存在于会话历史），
  但注册表记录停在 `started`（收口者已死，新 adapter 不认识该 requestId）→
  `reconcile_once` 对 started/unknown 只保留 outcome_unknown（按设计不猜）——
  **无任何入口能用原生轮次证据把已完成的输入收口**。fj05-long 永卡 unknown。
- 依据：验收提示词 §5"人工或程序未知结果处置要有具体入口/规则；不能永远卡未知"。
- 影响：用户看到"结果未知"但答案明明在会话里；换 ID 重发会重复烧一轮（掩盖风险）。
- 归属：联合（ZSession started 语义 + Hive reconcile 无原生对账路径）。
- 修复方向：reconcile 时按 inputId/时间窗比对原生轮次历史（/sessions/:id/history 有据可查），
  匹配到已完成轮次则按事实收口；或提供显式人工对账入口。
- 复验：重放 J05，重连后 ≤60s 内 fj05-long 按 native 事实转 completed（或明确待人工处置入口）。

### FDEF-02（低-中）停流自愈呈现为"会话面板整体消失"，无降级横幅
- 场景（J05 实测）：adapter 死后（Hive daemon 与 SSE 心跳存活——"心跳在业务停"场景），
  前端在 ~3s 内把会话舱整体拆除（conv-flow 消失、面板关闭），72s 观察窗内无任何
  "实时通道校对/重连中"横幅或 toast；重开面板后内容完整补齐（41 条消息，无丢失）。
- 影响：用户视角=面板闪没需手动重开；报告声称的"18s watchdog 校对+重建+提示"中，
  重建（拆面板）可见、提示未见。数据无损，体验缺陷。
- 复验：注入同场景，面板不消失或出现明确状态提示，恢复后原位续流。

### FDEF-03（低·观察）取消能力仍受宿主限制（维持既有结论，非回归）
- J03 联合活体：UI cancel → 200 `cancelling:true`（分态诚实）→ 原生轮次**未被停止**
  （5935 字长文完整自然产出）→ 如实落 completed（无假 cancelled）、零伪消息/零新轮、
  同会话续用正常。与上游 fx4 报告"取消成功一次"并存——宿主停止行为不稳定，
  结论维持"严格判定已修、原生取消能力受限"。

### FDEF-04（低·视觉）
- 会话舱思考性文字与正文无视觉分隔、无独立折叠控件（f5-strong-session 截图）；
  320 退出仅图标无文字（可发现性）。证据：`tmp_crops/s1-*.png`、`s3-top3x.png`。

## 记录

- 强模式三层联合（本轮核心链）**PASS**：UI 输入 → dispatcher → 带 key stdio adapter →
  daemon（claim writerKeyHash）→ 原生 GLM-5.3 轮次+工具 → ACP/SSE → completed → marker 恰一次；
  busy 分支真实经过（-32011 帧+回队时序存证）。
- 表单矩阵 F 系列 PASS（见上 TDEF-03/04）；兜底/显式/普通任务边界维持。
- 320px 退出 right=314 完整可点（复测维持）；视觉 2 PASS + 1 需改进（截图视口问题，非缺陷）。
- **生产部署现状**：daemon 70160 = 三修代码 + 弱模式（无 HIVE_ACP_WRITER_KEY）→
  四修与强模式在生产**均未部署**（pending_deployment）。本验证全部在隔离 home（19985）完成；
  生产 8765/19962 只读未动，未代为切换。
