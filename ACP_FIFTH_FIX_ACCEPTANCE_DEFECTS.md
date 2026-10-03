# ACP 第五轮修复独立验收缺陷清单 — run 5acc-20261002-023006-v1

> 验收 Agent：ZCode 会话（独立验收）。日期：2026-10-02。
> 版本：Hive `5467a4d`（convbridge @02:01:41、conversation.js @01:52:38）；
> ZSession adapter v0.6.3（acp.mjs @01:56）+ daemon 引擎同版本（manager.mjs @02:09:52，
> PID 66772 @02:11:21 确认加载）。
> 证据：`D:\agent_workspace\tmp\hive-acp-fifth-acceptance\5acc-20261002-023006-v1\`
> （api/r01-organic/r04-r4/a-series/u-final.json、browser/u-before|during、strong-home 日志）。

## 本轮结论：FD E F-01/02 已闭合，未发现新实质缺陷（两条低风险记录在案）

### 关闭验证（实测）

- **FDEF-01 闭合（联合）**：
  1. 上游 v0.6.3 锚点链真实工作：g6-r3/r4 记录携带**自身** `nativeMessageId`（msg_mupvkz9z…/msg_mupvlnx6…），
     按锚点定位该 user 回显所在轮次的 assistant 终态；`_zsession/get_input_state` 返回
     `reconciliation{matched,verified,source,evidence}`。
  2. **同文反例 R03/R04 未击穿**：会话内出现 4 条完全相同的“复现指令”输入（r1/r2/r3/r4），
     每条锚定各自的 user 回显与各自的回答（原生行流 seq 400369 见 r4 的 assistant 终稿），
     无误配、无误收口；互不为凭。
  3. adapter 死亡后原生轮次续跑完成（J05 语义），收口 done/end_turn 身份正确；
     R01（无锚点混合样本）：上游明确 `no_native_anchor/manual_required`，Hive 保持
     outcome_unknown **不猜、不借旧回显**——诚实路径成立。
  4. 周期 reconcile 每 30s 运行且对 working 会话跳过（合法长轮不被提前 completed）。
- **FDEF-02 闭合（主症状）**：浏览器三态实测——kill adapter 故障中**会话面板存活**
  （3 条消息完整、未整树拆除）、**草稿保留**（“U02草稿保留测试”）、阅读位置稳定；
  恢复由桥秒级重连完成，数据无损。对比上轮"面板整体消失"已修复。
- **FDEF-03/04**：取消语义维持（未新测，沿用四轮结论）；思考折叠/未分类原文保留为代码层修复，
  本轮未构造回归证据。
- **人工对账 A 系列 PASS**：`recheck` → 200 scheduled（只读）；`confirm` → owner 限定、
  outcome 枚举（completed/cancelled/failed）、`native_ref`+`reason` 必填（webui.py:1260-1274）、
  audit_log 记录、幂等（非 unknown 返回 `already`）、跨 Agent input_id → 404；
  空字段 200 系命中 already 幂等路径（代码层校验完备，非绕过）。

### GDEF-01（低·记录）Hive 启动/登录时序竞态仍有残余窗口
- 现象：本 run 复现一次——页面前端 fetch 登录成功但 app 停在登录卡（app hidden）；
  reload 后正常。修复（_authGen/enterApp 幂等）覆盖了冷启动立即登录场景（四轮验证过），
  但本次 19975 环境仍偶发。证据：本 run browser 首屏转换记录。
- 复验：冷启动+立即登录 ×5 连续成功；或登录 fetch 的 200 直接驱动视图而非仅 _authGen。

### GDEF-02（低·证据口径）disk 事件日志路径延迟可致验收侧误判，怀疑影响 Hive 二层文本兜底
- 现象：原生行流（/events）已含 r4 的 assistant 终稿时，`zsession_read` 的 disk-log
  重建路径仍短暂查不到（r04 计数 answers=3 ↔ 实际 4 条配对）；曾据此得出"r4 假 done"的
  中间结论，按原生行流复核后**已撤回**。Hive 二层 `_reconcile_by_native_history` 同样读该
  路径——在落库延迟窗内可能漏配（保守方向，不误 completed），但提示该路径延迟需收敛。
- 复验：同一 history 查询在 0/1/3s 三个时点与 /events 行流一致性比对。

### GDEF-03（低·未复现）停流降级横幅未观察到位
- 现象：注入 adapter 死亡后桥秒级重连、业务未真停（watchdog 不触发），36s 观察窗内未出现
  “实时通道/有待对账”横幅——该呈现仅在持续停流时触发，本环境未能稳定构造持续停流
  （supervisor 15s 内拉起新 adapter）。非缺陷证据，标记观察局限。

## 通过项索引（本轮 fresh 证据）

- 强 writer 联合（本 run 全部输入在强 key 模式）：claim 绑 writerKeyHash；
  busy 回队、FIFO、锚点收口、面板原位、reconcile API 均真实链路。
- 回归未触碰：评分（evalschema 五修未改）、回放双标、SessionModeState 等按变更面沿用
  四轮关闭证据（G 系列——本轮强模式正常对话+busy 即天然覆盖其不退化主张的主干部分）。
