# ACP 第二轮修复独立联合验收缺陷清单 — run 2acc-20261001-200745-v1

> 验收 Agent：ZCode 会话（独立验收）。日期：2026-10-01。
> 版本：Hive git `96b3c1a`；ZSession adapter v0.5.2（acp.mjs md5 `0ef44484c3a3940e5bd73a9ddc645a69`），
> daemon PID 66020 @17:42、生产 Hive PID 56100 @17:35 —— **两侧二修均已实际部署**（Hive 报告
> "上游未落地"的表述已过时）。
> 证据：`D:\agent_workspace\tmp\hive-acp-second-acceptance\2acc-20261001-200745-v1\`
> （protocol/s-results-*.json + frames、api/、browser/ 截图、defects/SDEF-01-evidence.md）。

---

## 阻断

### SDEF-01（阻断）Hive 96b3c1a 会话派发全链路崩溃：`_started_inputs` 挂错类
- 归属：Hive `hive_core/convbridge.py`——属性初始化在 `AcpBridge.__init__`（L132），使用在
  `ConversationService._dispatch_one`（L681-682）。
- 实测：本 run agent `agt_4e5d60d30ddd85b54682` 首条输入 i01-r1 于 2 秒内
  `failed: INTERNAL_ERROR: AttributeError: 'ConversationService' object has no attribute '_started_inputs'`
  （`api/i01.json`、`hive-home/logs/hive.log` 12:10:00Z）。
- 影响：**会话舱输入/队列/恢复/取消在 96b3c1a 下 100% 不可用**。RDEF-14 的幂等修复本身即缺陷源。
  HIVE_ACP_SECOND_FIX_REPORT 的 RDEF-01"三层防御"与 RDEF-14 相关"实测"为旧记录清点
  （报告自述"代码路径+清点"），任何一次真实派发都会立即崩溃——证明未做过活体验证。
- 修复：初始化移入 ConversationService.__init__（一行）。
- 复验：任一输入派发成功且 busy 重试只写一次 turn.started。
- 连带：本报告所有需 Hive 真实派发的联合用例（I01 网页版/I04-I07、恢复联合、取消联合、SSE watchdog
  活体）标 **BLOCKED-by-SDEF-01**。

## 高

### SDEF-02（高）双评分兜底表单无服务端校验：越界分值直接入账
- 归属：Hive `hive_core/webui.py:995-1006`——`_human_answer` 仅当任务带 `ui.fields` 时调用
  `validate_ui_answers`；无 fields 的兜底任务（request_type=dual_evaluation 等）**完全跳过校验**。
- 实测：兜底任务 tsk_3ea9fb58（request_type=dual_evaluation、无 fields）
  `POST /ui/api/human/answer {"artifact_score": 999, ...}` → **200 收账**（`api/sdef02-server-validation.json`）。
- 对照：显式 ui.fields 任务越界 → 400（本轮+上轮 B09 一致）。
- 影响：兜底路径可写入 999/-1/任意值进正式 result；前端 dualEvalFields 校验形同虚设
  （可绕过）；违反"服务器对无 ui.fields 任务也使用正确兜底 schema"的验收要求。
- 修复：服务端对兜底任务按 dualEvalFields 等价 schema 复验。
- 复验：同 POST 返回 400+field_errors。

## 中

### SDEF-03（中）W02 未实现：Hive 不提供 writerToken，受管会话运行于弱声明模式
- 归属：Hive `convbridge.py` ensure/send_prompt meta 仅 `ownerId`，无 `writerToken`。
- 实测：daemon 侧 writer 凭据机制完整且强制（本 run W01/W03/W04 全过：claim 存 sha256、
  声明无 key 409、错 key 409、对 key 200、旧代际 claim_conflict）；Hive claim 未绑 key →
  `if (!claim.writerKeyHash) return true`（manager.mjs:1008）放行声明式写。
- 影响：单写入约束在生产为"声明即通过"弱模式；X-ZS-Writer 头仍可被第三方伪造
  （RDEF-07 残留面，机制已备而消费者未启用）。归 Hive 接入缺口，非上游缺陷。

### SDEF-04（低-中）IAB 登录竞态：页面加载先于登录判定 → 应用停在登录屏（需二次 reload）
- 本 run 两次复现：fetch 登录成功后 app 仍 `app hidden`，reload 后恢复。
  与历史 cookie 失效问题叠加，属环境+前端启动顺序问题；建议登录后主动重拉 `/ui/api/session` 驱动视图。

## 低

### SDEF-05（低）回放帧 update 级 replay 标记仅工具帧携带
- 实测 P01：15 帧全部 params 级标记（Hive 读取位）✓；update 级仅 3 帧（tool_call）。
  合同 v0.6 §3 写明"及 update._meta 同标"——文本帧未按合同双标。消费者兼容（Hive 读 params 级），
  记文档-实现偏差。

### SDEF-06（低）测试工具注记
- 本 run O06 用例 respB 序列化为 "[object Object]"（测试脚本 bug，非产品问题，marker=1 已证单次执行）；
  A 兜底任务在表单检查前已被 SDEF-02 探针答复为终态，A 卡视觉证据改以 B/C 覆盖。

### SDEF-07（低）320px 极窄下顶栏右侧"退出"溢出裁切
- 视觉评审实锤（`browser/s3-narrow-320.png` + `tmp_crops/n320-logout.png`）：顶栏单行保持（上轮
  修复未回归），但 320px 下右端"退出"被视口裁切仅剩"退"字左半——入口不可读不可点。
- 修复：320 断点下退出图标化或收进溢出菜单。兜底表单另录两条体验项：首行卡片标题被吸顶栏截断、
  滑块刻度数字对比度偏低（`tmp_crops/f1440-*.png`）。

---

## 记录：本轮证实的修复（供关闭矩阵引用）

- **RDEF-01（ZSession 侧）CLOSED**：busy → 当场 -32011 + 落账 `retryable_busy`（非终态）；
  空闲后同 ID 同内容重试 **真实执行恰一次**（13.6s 真实轮，marker=1，终态 done）；
  rejected→**-32015 带原错误** / failed→-32016 / unknown→-32017 / 异内容→-32010；失败永不返回 end_turn。
  （`protocol/s-results-A/B.json` + retry 实测）
- **RDEF-03 CLOSED（adapter 侧）**：15 回放帧全部 params 级 `_meta.zsession.replay`；同会话两次 load
  全文一致（8741=8741）；load 后新消息 growth 仅"交接正常"零旧文重复；水位 watermarkSeq 生效。
- **RDEF-04 CLOSED（adapter 侧）**：started 孤儿（会话 idle）重试 → -32017 拒绝盲重派；
  输入 B working 期间孤儿 A 保持 started 不被 B 收口；B 恰执行一次。
- **RDEF-05 CLOSED**：new/load/set_mode 均返回标准 `SessionModeState{currentModeId,availableModes[5]}`
  （set_mode 经 `_meta.zsession.modeState`）+ `current_mode_update` 通知实测收到。
- **RDEF-07 CLOSED（daemon/adapter 侧）**：writerToken 四场景矩阵全过（见 SDEF-03 联合缺口）。
- **RDEF-09 CLOSED**：load 回放含工具帧（本会话 3 个 tool_call，状态映射符合合同）。
- **RDEF-15 CLOSED**：ACP_INTEGRATION.md v0.6 单点真源，与实测一致（除 SDEF-05 小偏差）。
- RDEF-10：严格判定 `interruptConfirmed === true` 代码确认（上轮活体已证诚实语义；
  "原生停止能力"仍受宿主限制，按合同文档化——不判取消功能通过）。
- RDEF-02/06：scope 记录——**本会话已有用户直接指示**（2026-10-01："Hive 就是恒 yolo、
  工具级权限不做了，没必要做不是 yolo 的"），来源可信度高于报告转述；维持 N/A·留档。
