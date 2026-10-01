# Hive ACP 第四轮修复报告

日期：2026-10-02 · 对 ACP_THIRD_FIX_ACCEPTANCE_DEFECTS 主修 TDEF-02/03/04 + 强 writer 闭合
上游：ZSession 四修已落地（`DaemonClient.post` 第三参透传，acp.mjs @23:59，
TDEF-01 修复注释在源码中可查）——本轮接入并全链验证

## TDEF-02（阻断）：busy 异常路径死锁 —— 已修复 + 真实 busy 活体闭环

**根因**：`_fail_input` 内 `inp.get("session_id")`，`inp` 是 sqlite3.Row
（无 `.get`）→ 异常处理器内二次 AttributeError → 派发线程静默死亡 →
输入永卡 dispatched → `active_input` 门永挡 tick。

**修复**：
1. **类型契约边界**：`_fail_input_inner` 入口统一 `sqlite3.Row → dict`
   （转换失败置 None，安全落 outcome_unknown）——全路径唯一 `.get` 点收口。
2. **异常器自身可靠**：`_fail_input` 全函数兜底——落账失败时至少保证
   `_working` 释放、`conv.fail_input_broken` 诊断日志、输入置
   outcome_unknown（不盲重发、不永卡 dispatched、不静默 pass）。
3. input_id 由调用方贯穿（不重猜 next_queued）；新合同 busy 落
   `retryable_busy`（非终态）——同 requestId 回队有限退避（10s），
   不触发旧 `orig#r<ts>` 派生路径（仅查实 rejected 才派生）；
   不假 turn.started、不自动 interrupt。

**真实活体证据**（strong-home，强模式，本 run fresh）：
- **长轮 busy 窗口**：req-strong-busy-3（3000 字长轮 running）期间入队
  第 4 条 → **-32011 → 正确回队 `queued`（无死锁、无派生 ID、无假
  started）** → 长轮完成后 **FIFO 执行各恰一次（3、4 均 completed）**。
- **重启窗口**：dispatched 中重启 daemon → 启动清扫 outcome_unknown
  （"daemon 重启，轮次结果未知（未重发）"——不盲重发）；重启后新输入
  正常派发 completed（队列未被卡死输入阻塞）。
- 生产 home 既有滞留记录只读清点（未批量改写）。

## TDEF-03（中）：显式 schema 顶层表示归一 —— 已修复 + 矩阵

**根因**：`canonicalize_answers` 顶层归一只用 DUAL_EVAL_IDS 白名单，
显式 ui.fields 的顶层答复被忽略（误报"必填项未填写"）。

**修复**：归一字段 ID 集 = **`effective_fields(task)` 的全部声明字段**
（显式 name/rating/date 等一律参与）+ 双评契约键；两种表示同规则
（同值合并、异值 400 冲突）。

**隔离矩阵**（fresh tasks）：

| 用例 | 结果 |
|---|---|
| 显式 step5 顶层 77 | 400（步长拒）✓ |
| 显式顶层 75 + 判决 | 200 ✓ |
| 显式非双评（name/rating）顶层 | 200，result `{"answers":{"name":"测试","rating":4}}` ✓ |
| 双评三种兜底标记 + 显式 | 前轮矩阵维持 ✓ |
| 0/100、null、缺必填、bool、冲突 | 前轮矩阵维持 ✓ |

## TDEF-04（低-中）：结果白名单 —— 已修复 + result 实查

**修复**：`canonical_result` 白名单化——schema 声明字段 + 契约键
（explicit_autonomy_grant）+ 显式跟踪 metadata 白名单
（video_notes/image_notes/ui_attachments）；未声明顶层键（grant/
authorize/_verified 等）**不回填**。普通自由 data 任务不经此路径，
合法业务 metadata 不受影响。显式非双评表单保持 answers 包装形态。

**实测**：合法双评 + 恶意 `grant:"production"/authorize:true` → 200，
result keys = `['artifact_decision','artifact_score',
'explicit_autonomy_grant','review_decision','review_score']`——
**恶意键零残留**，explicit_autonomy_grant=null（未选不授权）。

## 强 writer 三层闭合（SDEF-03/TDEF-01 消费侧）

独立 home（`work/strong-home`，`HIVE_ACP_WRITER_KEY=1`，隔离目录
`work/strong-e2e`，adapter 上游四修版）：

| 层 | 证据 |
|---|---|
| daemon | 无 key（仅 X-ZS-Writer）→ **409**；错 key → **409**；owner 直通 → 200（设计保留）✓ |
| adapter | Hive→stdio adapter→daemon 写请求带有效 key（busy-1..6 全部真实执行，非 409）✓ |
| Hive | claim `owner=hive/agt_751b…`、generation=3、`writerKeyHash`（真实 sha256）✓；重启后同代际 key 文件接续，新输入 completed ✓ |

- **网页真实输入全链（强模式）**：UI 入队 → dispatcher → 强 key adapter →
  原生 GLM-5.3 轮次 → 流式 → completed（6 条输入，含 busy 重试、
  长轮、重启恢复全部真实执行）。
- **临时绕过移除**：弱模式自动 release 仅在 `HIVE_ACP_WRITER_KEY≠1` 时
  执行（强模式不自动解绑/不退弱）；失败如实暴露（不静默换路径）。
- writerToken 安全：`<home>/runtime/conv-writer-keys/*.key` 服务端文件，
  不下发浏览器/不进日志/prompt/result。**Windows 说明**：脚本尽力
  chmod 0600，但 NTFS 无 POSIX 0600 语义——实际保护 = 文件系统 ACL +
  本机单用户管理员边界，不宣称 OS 沙箱。

## 旧 BLOCKED 联合用例补测（本 run fresh）

| 用例 | 结果 |
|---|---|
| M02 网页→强key→原生轮→completed | ✓ busy-1/2 |
| M04 busy 同 ID 回队真实执行一次 | ✓ busy-3/4（FIFO 各一次） |
| M07 重启窗口精准对账 | ✓ busy-5（unknown 不盲重发）+ busy-6（接续派发） |
| 评分两表示 + 白名单 | ✓ T1/T2/T3 矩阵 |
| 409 矩阵（无 key/错 key/owner 直通） | ✓ 上表 |
| 取消/中断 | 未在本 run 活体重测（沿用二轮诚实分态实现；上游 interrupt 宿主限制仍如实标注） |
| SSE watchdog 活体 | 机制在（18s 校对+重建）；本轮未注入人为停流——标记 NOT-RETESTED-live |

## 视觉小项

320 退出图标触达区/授权框入帧等：未重新设计（静谧天文台维持）；320px
退出完整可点已在前轮实测，本轮无回归。

## 复跑

```bash
# TDEF-03/04 矩阵（隔离 home + fresh tasks）
# 强模式全链：work/strong-home（HIVE_ACP_WRITER_KEY=1）+ work/strong-e2e Agent
python scripts/quicktest.py   # ALL PASSED
```

## 生产部署接续说明

生产 daemon 当前仍弱模式（无 HIVE_ACP_WRITER_KEY）。开启步骤：
停止 → `HIVE_ACP_WRITER_KEY=1 HIVE_ACP_ADAPTER=… start` → 首次
eager_attach 自动 claim 绑 key（现有会话/责任不受影响，claims 不清）。
未执行生产切换（本轮验证在隔离 home 完成），如实标注。
