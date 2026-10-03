# ACP 第五轮修复独立验收报告 — run 5acc-20261002-023006-v1

> 独立验收 Agent：ZCode 会话。日期：2026-10-02。
> 输入：ZSession `ACP_FIFTH_FIX_REPORT.md`（v0.6.3 对账引擎）+ Hive
> `HIVE_ACP_FIFTH_FIX_REPORT.md`（git `5467a4d`）。
> 证据根：`D:\agent_workspace\tmp\hive-acp-fifth-acceptance\5acc-20261002-023006-v1\`

---

## 0. 结论（先行）

| 维度 | 结论 |
|---|---|
| **FDEF-01 自动强关联恢复** | **闭合**。上游 v0.6.3 锚点链真实生效（nativeMessageId→user 回显→该轮 assistant 终态），同文多输入（4 条相同文本）各自正确锚定收口，互不顶替；adapter 死亡后原生轮次续跑完成并按锚点收口；无锚点记录保持 unknown+manual_required（不猜）。 |
| **FDEF-01 旧记录人工兜底** | **闭合**。`reconcile` API：recheck 只读、confirm 限 owner/system、outcome 枚举+native_ref+reason 必填、审计留痕、幂等、跨 Agent 404——全部实测/代码核实。 |
| **FDEF-01 两端一致性** | **闭合**。Hive 第一层消费上游结算状态（registry 层），上游 matched/verified 为真实锚点证据；Hive 二层文本回显兜底在本轮所有场景中**未被触发误收口**（每次都由锚点链先行正确收口）。 |
| **FDEF-02 面板原位恢复** | **闭合**。kill adapter 故障中：面板存活（消息完整、非整树重建）、草稿“U02草稿保留测试”保留、阅读位置稳定、恢复后数据无损。对照上轮"面板整体消失"已修复。 |
| **FDEF-03 原生取消限制** | 维持（本轮未触碰，沿用四轮结论：严格判定已修、宿主停止能力仍受限）。 |
| **FDEF-04 体验** | 代码层修复确认（折叠保留/未分类原文不删）；本轮未构造回归反例。 |
| **不误收口/不重执行证据** | **通过**。4 条同文输入各有 anchor 与各自回答（原生行流 seq 400369 等）；零新 send/零额外工具；一次中间误判（disk-log 延迟口径）经原生行流复核后**主动撤回**（GDEF-02 记录口径问题）。 |
| **新缺陷** | 无实质缺陷；3 条低风险记录（GDEF-01 登录竞态残余窗口/GDEF-02 disk-log 延迟口径/GDEF-03 横幅观察局限）。 |
| **生产部署现状** | 生产 19962 daemon（PID 70160）**仍未部署四/五修、仍弱模式**（pending_deployment，本轮只读记录，未代为切换）。 |

## 1. 版本与关键事实核对（§2）

- ZSession：adapter v0.6.3；**daemon PID 66772 启动于 02:11:21，晚于 manager.mjs 02:09:52** →
  daemon 实际加载 v0.6.3 对账引擎（非仅换 adapter 进程）。
- Hive `5467a4d`：`reconcile_once` 三层——① 上游登记终态；② started/unknown+会话空闲 →
  `/sessions/:id/history` 中**本输入全文 user 回显精确匹配**+其后 assistant 终稿 → 补 completed
  （attribution=user-echo-exact-match）；③ 无证据保持 unknown+处置入口。**确认为文本兜底层**，
  但本轮所有收口实际由上游锚点链完成——该层未成为误收口通道。
- 新 `reconciliation` 合同：`get_input_state` 实测返回 `{matched, source, recoverability,
  evidence{anchorMessageId,assistantSeq,completedAt}, checkedAt}`（Hive 现消费 state 结果；
  evidence 字段尚未被 Hive 显式落账——改进建议见 §3）。

## 2. 核心实测（§5§6§7§8 关联）

1. **J05 语义重放（adapters kill 三次）**：r3/r4 均在 dispatched 窗口被杀 → Hive 诚实
   outcome_unknown → 原生轮次续跑（r3 的 "灯塔之光" 回答在 kill 后产出）→ 新连接查询 →
   上游按 anchor 收口 done/end_turn → Hive ≤15s completed；marker/echo 无重复执行。
2. **同文隔离（R03/R04）**：同一会话 4 条完全相同输入，注册表各自 anchor（4 个不同
   nativeMessageId），原生行流各自配对回答；无误配。
3. **无锚点（R01）**：g6-warm（bridge 竞态未送达）→ 上游 `no_native_anchor/manual_required`
   → Hive 周期对账**不借旧回显**、保持 unknown 117s+，直到人工 confirm 收口（演示了人工兜底
   端到端：recheck scheduled → owner confirm（outcome+native_ref+reason）→ completed+audit）。
4. **面板（U01/U02）**：故障前三态（草稿/滚动/消息）在故障中与恢复后保持；无整树重建。

## 3. 改进建议（不阻塞）

1. Hive 收口时落账上游 `evidence`（anchorMessageId/assistantSeq）到 turn.completed payload，
   使人工复核与审计可回溯到原生行号（现仅 layer2 的文本匹配有 attribution）。
2. 收敛 disk-log 重建路径与 /events 行流的时差（GDEF-02，影响对账及时性与证据口径）。
3. 持续停流（桥反复起不来）场景再验一次降级横幅呈现（GDEF-03）。

## 4. 交付与收尾

- 本报告 + `ACP_FIFTH_FIX_ACCEPTANCE_DEFECTS.md`（GDEF-01~03 均低风险）。
- 证据：run 目录全量保留（含 strong-home 日志、截图、api JSON）。
- 收尾：停止本 run 测试 daemon（19975）；生产未动；本 run 产生的验收 node 进程已清理。
