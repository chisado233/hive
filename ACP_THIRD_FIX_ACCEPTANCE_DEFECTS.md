# ACP 第三轮修复独立验收缺陷清单 — run 3acc-20261001-225426-v1

> 验收 Agent：ZCode 会话（独立验收）。日期：2026-10-01。
> 版本：Hive git `a5da2b9`（生产 daemon PID 70160 @21:24:21 加载）；ZSession adapter 实际源码
> `acp.mjs` md5 `b15e359686175d98a91b775a3fb780aa` @21:03——**源码内部版本值仍为 '0.5.0'**，
> 报告却称 v0.5.3（文档-源码不一致，见 TDEF-05）。
> 证据：`D:\agent_workspace\tmp\hive-acp-third-acceptance\3acc-20261001-225426-v1\`
> （protocol/t-results-T1/T2、api/e-series*.json、browser/ 4 截图、tmp_crops/、defects/TDEF-02-evidence.md、
> hive-home/logs/daemon.err.log 完整 traceback）。

---

## 阻断

### TDEF-01（阻断·强模式）adapter `post()` 丢弃第三参：writerKey 头永远到不了 daemon
- 归属：ZSession `mcp/lib/acp.mjs:50`——`async post(path, body) { return this.call('POST', path, body); }`
  丢弃 `extraHeaders`；而 `call(method, path, body, extraHeaders)` 支持且 374/464 行的调用方
  都正确传了 `this.writeHeaders(...)`。
- 三层实测（run t-results-T1.json）：
  1. daemon 层 ✓：直连 HTTP 带 `X-ZS-Writer: acp + X-ZS-Writer-Key` → 200 accepted；
  2. adapter 层 ✗：new 带 writerToken → claim writerKeyHash 绑定成功 → 同一 adapter 的
     session/prompt → **409 managed_session（"会话已由 hive/t4 受管…写入被拒绝"）**——
     adapter 被自己登记的 key 锁在门外；
  3. Hive 层 BLOCKED（见 TDEF-02）；且 Hive 三修报告自述已发现此缺陷、以 `HIVE_ACP_WRITER_KEY=1`
     默认关闭规避——**SDEF-03 未闭合**，强模式在 adapter 层即断。
- 影响：v0.6.1 合同 §4 强 writer 模式无法经 stdio adapter 生效；"受管写入凭据"只有 daemon
  直连可用，对 Hive 生产链路无意义。
- 修复：`async post(path, body, extraHeaders) { return this.call('POST', path, body, extraHeaders); }`（一行）。
- 复验：T1 场景 adapter prompt 真实执行（marker 落位）而非 409。

### TDEF-02（阻断·弱模式）Hive a5da2b9 首次 busy 冲突即队列死锁
- 归属：Hive `hive_core/convbridge.py:821`——`_fail_input` 内 `inp.get("session_id")`，
  `inp` 是 `sqlite3.Row`（无 `.get`）→ AttributeError 在 except 处理器内抛出 → 派发线程静默死亡
  → 输入永卡 `dispatched` → `active_input` 门永挡 tick → 死锁。
- 实测：M01 弱模式首条输入撞 bootstrap busy（-32011）后 180s+ 无任何 conv.* 日志、无事件、
  无 marker；完整 traceback 在 `hive-home/logs/daemon.err.log`（"During handling of the above
  exception, another exception occurred"）。
- 影响：**弱模式主链在任意 busy 窗口即死锁**——比二修的"全崩"窄（不 busy 时能跑通：
  施工报告的 req-sdef1-real-6 即此类），但首次 busy 必触发。M02-M10 派发类联合用例、
  强模式 Hive 层、SSE watchdog 活体全部 BLOCKED-by-TDEF-02。
- 修复：`inp["session_id"]`；并给 `_fail_input` 全函数兜底 try。
- 复验：busy 窗口输入 → 回队 → 空闲后真实执行（marker=1）。

## 中

### TDEF-03（中）评分答案顶层表示仅对兜底 schema 生效，显式 ui.fields 任务不归一
- 归属：Hive `evalschema.canonicalize_answers`。
- 实测：兜底任务顶层 `{"artifact_score": 999}` → 正确触发范围校验（顶层被读取）；
  显式 ui.fields 任务顶层 `{"artifact_score": 77, "decision": "pass"}` → 报
  "必填项未填写"（顶层被忽略）；同值放 `answers` 包装 → 步长 77 拒/75 过（校验正常）。
- 影响：合同声称"顶层与 answers 两处表示归一"，实际仅兜底路径实现——API 消费者用顶层表示
  会得到误导性的"未填写"错误。
- 修复：显式 fields 路径同样走 canonicalize。

### TDEF-04（低-中）兜底评分对未知顶层键不剥离：恶意 grant/authorize 原样入账 result
- 实测：合法双评 + 顶层 `"grant": "production", "authorize": true` → 200，
  result.data **原样保留两个恶意键**（`explicit_autonomy_grant` 正确为 null，未产生真实授权——
  无权限提升，但脏数据且为下游误读留面）。
- 修复：normalize_canonical 白名单化（schema 外键丢弃或拒绝）。

## 低

### TDEF-05（低·文档）版本与文档不一致
- adapter 源码 `this.version = '0.5.0'`，报告/合同称 v0.5.3；initialize agentInfo 对外也报 0.5.0。
- T3 取消用例脚本超时未收（进程级挂起）——沿用第二轮 P5 活体证据（同代码路径：严格判定
  confirmed===true、零伪消息、未停如实 end_turn），本轮标 NOT_RETESTED-live。
- 视觉三条低风险项：320 退出图标触达区偏小、窄视口星区节点滚动可达待确认、兜底表单授权框未入帧。

---

## 记录：本轮证实关闭的旧 SDEF

- **SDEF-01 CLOSED**：`_started_inputs` 错位已除（DB 级 dedupe）；不 busy 路径真实派发可用
  （施工报告 req-sdef1-real-6 + 本轮 M01 在 busy 前正常进入 dispatched/adapter 交互）。
  但其修复报告未覆盖 busy 路径（TDEF-02 即在该路径）。
- **SDEF-02 CLOSED（主体）**：fresh 任务矩阵——999/101/-1/bool/字符串/数组/空 → 400+field_errors；
  顶层与 answers 冲突 → 400"两处取值冲突"；未知选项 → 400；显式 step5 77 拒/75 过；
  合法 0 分入账保留（0≠null）；评论不结单；普通任务自由答复正常；被拒任务未终态可重试。
  残留 TDEF-03/TDEF-04。
- **SDEF-04 CLOSED**：冷启动 +300ms 立即登录 → app 进入、登录卡隐藏、token 清空，无 reload。
- **SDEF-05 CLOSED**：回放 5/5 帧（user2/agent2/tool1）params+update 双标、重复 load 全文一致、
  load 后新回复实时到达（水位不吞）。
- **SDEF-06 CLOSED**：本轮证据全结构化 JSON；旧证据未改动。
- **SDEF-07 CLOSED**：320px 退出 right=314 完整可点（图标化）；390 底部导航完整；视觉 4/4 PASS。
- **SDEF-03 OPEN**（=TDEF-01 adapter 层 + Hive 未启用强模式）：机制三层中 daemon 层 ✓，
  adapter 层 ✗，Hive 层默认关闭未接入。
