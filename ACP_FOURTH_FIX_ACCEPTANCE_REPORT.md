# ACP 第四轮修复独立验证报告 — run 4acc-20261002-004613-v1

> 独立验证 Agent：ZCode 会话。日期：2026-10-02。
> 输入：ZSession `ACP_FOURTH_FIX_REPORT.md`（adapter 0.6.1）+ Hive `HIVE_ACP_FOURTH_FIX_REPORT.md`
> （git `9aeefe5`）+ 合同 v0.6.1。
> 证据根：`D:\agent_workspace\tmp\hive-acp-fourth-acceptance\4acc-20261002-004613-v1\`

---

## 0. 结论（先行）

| 维度 | 结论 |
|---|---|
| **TDEF-01～05** | **全部 CLOSED**（代码修复 + 本轮活体验证，见 §2） |
| **实际 busy 异常路径 / FIFO** | **通过**：bootstrap busy 与受控长轮 busy 均真实经过 -32011 分支（帧+日志+回队时序存证），同 ID 重试恰执行一次，同秒多输入 FIFO 正确，无死锁、无派生 ID、无假 started |
| **强 writer 三层联合** | **隔离验证通过**：claim 绑 key → stdio adapter 真实写（非直连对照）→ 原生轮次/工具/marker 恰一次 → SSE/完成；busy 链在强模式下同样成立 |
| **表单两表示/白名单/授权边界** | **通过**：顶层=answers 归一（含自定义字段）、步长/类型/范围/冲突/选项拒绝、恶意键零残留、0≠null、普通任务自由 data 完整、explicit_autonomy_grant 不隐式授予 |
| **恢复与历史/实时** | **部分**：回放/水位/幂等沿用已关闭证据且零回归；**新增 FDEF-01**——adapter 死亡时 started 孤儿无收口入口（原生已完成仍永卡 outcome_unknown，本轮实测） |
| **原生取消与准确状态** | **准确状态通过，取消能力仍受限**（J03 活体：cancelling 分态诚实、零伪消息；5935 字长文未被停止、自然完成后如实 completed） |
| **SSE watchdog 与浏览器** | **部分**：停流后数据无损、重开补齐完整；**新增 FDEF-02**——自愈呈现为面板整体消失，无降级横幅 |
| **视觉/性能** | 视觉 2 PASS+1 需改进（截图视口问题非缺陷）；320 退出口完整可点维持；性能沿用前注（本轮未重跑 trace） |
| **代码验证结论** | 两侧四修代码与报告一致（post 透传/版本 0.6.1/Row→dict/归一/白名单逐项核实） |
| **生产部署现状** | **pending_deployment**：生产 daemon（PID 70160，10-01 21:24 启动）仍为三修代码 + 弱模式；四修与强模式均未上生产。本轮只在隔离 home 验证，未代为切换 |
| 未测/受限/新阻断 | 无新阻断；FD E F-01/02 为恢复与体验缺口（中/低-中）；取消能力受宿主限制维持；大流量性能未重测 |

## 1. 版本与隔离

- Hive `9aeefe5`；convbridge.py/evalschema.py @00:01（Oct 2）。
- ZSession acp.mjs @00:00（post/get 透传 ✓、version 0.6.1 ✓、interaction 链补传 ✓）；
  daemon 66020 无需重启（本轮改动仅 adapter 侧，按连接加载新码）。
- 本 run 隔离强模式 home：`strong-home`（19985，`HIVE_ACP_WRITER_KEY=1` + 真实 adapter 命令），
  daemon PID 62744——环境变量确认加载（claim 带 keyHash 即证）。
- 生产只读核对：19962 daemon 70160 启动于四修提交之前（三修代码）；未动生产、未切换配置。

## 2. 核心链验证（同一 run 真实证据）

### 2.1 强模式冒烟（§4 两条必测）
1. **真实工具任务**：UI 输入 fs5-r1 → bootstrap busy（-32011 ×4 + busy_requeued ×4，**真实经过
   busy 错误分支**）→ 回队重试 → 强 key adapter → 原生 Bash 工具 → marker 恰 1 → completed；
   claim：`owner=hive/agt_fcb57b…, generation=1, writerKeyHash=sha256`（脱敏）。
2. **受控 busy**：3500 字长轮 running 期间入队 b1/b2（同秒）→ 全程 queued 不抢占 →
   长轮完成后 FIFO 执行 → b1=1、b2=1（各恰一次）。

### 2.2 表单矩阵（F 系列，fresh tasks）
顶层 77 步长拒 / 75 过（TDEF-03）；两表示同值归一、自定义字段保留；兜底 999/bool 拒；
**恶意 grant/authorize 零残留**（TDEF-04）；普通任务自由 data（text+mood）完整入账。

### 2.3 取消（J03，活体）
cancelling 分态诚实 → 原生未停（5935 字完整产出）→ 如实 completed；零伪消息/零新轮；
同会话续用正常。**准确状态 PASS；取消能力受限（FD E F-03 记录，宿主限制）。**

### 2.4 停流注入（J05，活体）
长轮流式中 kill 本 run adapter → 桥 1s 重连 → 数据无损（重开面板 41 条消息含完整长文）；
但面板被整体拆除、无横幅（FD E F-02）；活动输入永卡 outcome_unknown 而原生轮已完整产出
（FD E F-01，恢复缺口）。

## 3. 修复优先级建议

1. FDEF-01：reconcile 按原生轮次证据收口 started/unknown（或人工对账入口）。
2. FDEF-02：停流自愈改为原位提示/恢复，不拆面板。
3. 视觉小项（思考区分隔、320 退出文字）。
4. **生产部署**（用户决策）：四修代码 + `HIVE_ACP_WRITER_KEY=1` 上生产（步骤见 Hive 四修报告）；
   部署后建议按本报告 §2.1 复跑一次强模式冒烟。

## 4. 交付与收尾

- 本报告 + `ACP_FOURTH_FIX_ACCEPTANCE_DEFECTS.md`（FD E F-01~04）。
- 证据：run 目录全量保留（含 strong-home 日志、j03/f-series JSON、5 截图、tmp_crops）。
- 收尾：停止本 run 测试 daemon（19985）；生产 8765/19962 未动。
