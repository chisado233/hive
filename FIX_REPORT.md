# Hive V1 独立缺陷修复报告（FIX_REPORT）

- 修复基准：`b2288d3`（独立验收结论"不满足签收条件"，[INDEPENDENT_DEFECTS.md](INDEPENDENT_DEFECTS.md) DEF-01..11）
- 修复方：Hive 施工 Agent（2026-09-30）
- 最终提交号：见本仓库 git log（修复链末尾）
- 最终回归：`work/acceptance/20260930-041804-0f0b6f` = **88 Pass + 3 Blocked**；
  缺陷套件 `work/defects/*` 全过；覆盖矩阵 `work/coverage/20260930-121222-8ce5` = **205/205 行**

状态约定：已修复并验证 / 已修复待验证 / 未修复 / 经规范核对不属于缺陷。

---

## DEF-01 · 真实harness工位缺系统协议与个体说明 —— **已修复并验证**

- 根因：`agents.py:_rebuild_workspace` 无视 `cfg.workspace.path`，恒写
  `home/workspaces/<agent_id>`；`_build_startup_context` 从配置工位读
  agent.md/prompt.md 落空。
- 修改：`hive_core/agents.py`（新增 `resolve_workspace()` 统一解析并用于
  rebuild/create/update/clone；`_workspace_in_use()` 共享工位防护——
  显式工位被他人绑定即拒绝）、`hive_core/runtime_ops.py`（工位三件套缺失
  时抛 WORKSPACE_UNAVAILABLE，不再静默跳过）。
- 回归：`scripts/defect_def0102.py` **25/25**：显式工位三件套齐全、默认工位不受
  影响、共享工位拒绝、配置更新后派生文件按有效版本重建、真实 process 通道简报
  含协议+个体说明各一次。证据：`work/defects/def0102-*/checks.json`。
- 真实ZCode复核：工位 agent.md/config.json/prompt.md 均落盘（E2E-03 预检工位
  目录直查：agent.md/config.json/prompt.md 全部存在）。

## DEF-02 · STARTUP.md 反馈循环 —— **已修复并验证**

- 根因：启动简报写在工位根且匹配 `*.md` glob、不在 exclude 内，被下一轮
  gather_context 当业务md吞入再覆写，线性增长并混入已吊销凭据路径。
- 修改：`hive_core/runtime_ops.py`（简报迁 `home/runtime/startups/<agent>/
  <instance>.md`，每实例一份，路径经 options["startup_file"] 传给适配器）、
  `hive_core/contextgen.py`（STARTUP.md 按系统派生文件防御性排除，不改既定
  业务md加载规则——`*.md` 全量扫描仍生效，output.md 等按配置照常加载）、
  `hive_core/harness.py`（`{startup_file}` 模板占位 + 兼容旧 spec 的裸
  STARTUP.md 替换）。
- 回归：`defect_def0102.py` 25/25：连续3次 restart 简报大小恒定（3880B）、协议头
  每轮恰1次、零嵌套旧简报、无旧实例凭据路径、最终简报仅含当前实例凭据（可重复
  出现属正常）。

## DEF-03 · restart_backoff 未实现 —— **已修复并验证**

- 根因：`confirm_crash` 同步立即 `op_start_agent`；`restart_backoff` 全库无消费方。
- 修改：`hive_core/runtime_ops.py`（崩溃只登记持久退避计划
  `kv restart:<agent> = {attempts, next_allowed_at}`，不再同步重启；
  `_schedule_crash_restart` 消费 restart_backoff[i]、restart_max_per_hour）；
  `hive_core/scheduler.py`（新增 `run_due_restarts`：到期才拉起、enabled-only、
  尊重 restart_on_exit/每时限次/唯一槽位、计划消费后清空、稳定1h后重置计数）。
- 回归：`scripts/defect_def0305.py` **12/12**：崩溃→scheduled（不同步）、推进时钟
  过窗口恰一次拉起、第二次崩溃 attempts=2 且步长升级、paused 不拉起、无双实例；
  AC-RUN-07 已按新语义重写并通过（work/acceptance/20260930-041729）。
- 与暂停/退役/限次/重复崩溃/daemon重启衔接均有时钟驱动证据。

## DEF-04 · 幂等占位崩溃恢复 —— **已修复并验证**

- 根因：占位 INSERT 与业务/回填分属不同事务；进程在占位后、回填前死亡即留下
  response IS NULL 的孤儿，同 key 永久 STORAGE_UNAVAILABLE。
- 修改：`hive_core/service.py`（**快命令**（emit./inbox./producer.emit/
  control.task.）改为「占位+执行+回填同一事务」——崩溃只可能回滚（无孤儿）或
  全量提交（业务+响应）；**慢命令**（agent./harness./test./system./port./
  subscription./permission./producer./trigger./channel./human./event./audit./
  delivery./operation.）用 lease 占位（迁移002 加 lease_until 列；
  `idem_lease_seconds` 默认60），过期后同 key CAS 接管重跑，启动清扫过期孤儿；
  同 key 不同内容仍 IDEMPOTENCY_CONFLICT；失败仍释放占位）。
- 故障注入（真实进程击杀，非SQL探针）：`idem_kill_in_tx`（业务写入后、提交前
  `os._exit(70)`）、`idem_kill_after_reserve`（慢路径占位后 `os._exit(70)`）。
- 回归：`scripts/defect_def04.py` **15/15**：击杀→daemon死亡核实→重启→同key
  重试恰一次执行（1 Task/1 实例槽）、重放返回同一 instance+operation、
  20并发同key恰一单、跨主体隔离保留、无孤儿行。证据：work/defects/def04-*。

## DEF-05 · 唤醒丢弃后无对账重入队 + 三个死配置键 —— **已修复并验证**

- 根因：通知3败即删且只有"新事件"路径；notify_retry_after/
  send_unknown_retry_after/notify_jitter 全库无消费方。
- 修改：`hive_core/scheduler.py`（新增 `run_reconcile_wakes`：queued 超
  notify_retry_after 且无在途唤醒的待办按候选重入队（每轮 dedupe_key 带轮次、
  最多 notify_max_rounds=12 轮）；`run_notifications` 尊重 send_after；
  失败重试按 send_unknown_retry_after 排程；`_queue_notif` 落地
  notify_jitter=随机错峰 0..jitter（迁移003 加 send_after 列））。
- 回归：`defect_def0305.py` 12/12：发送失败 pacing（attempts≥1 且 send_after
  递增）、时钟推进后对账重入队（dedupe_key=reconcile:*）、重试零新任务/零回执、
  jitter 窗口样本。唤醒重试不重建 Task/回执/@通知（均有断言）。

## DEF-06 · "90/90"不可稳定复现 + 驱动质量问题 —— **已修复并验证**

- AC-TASK-08：改按 root 过滤+精确ID验证（不再赌字典序落首页）。
- 列表实现排序：`visible_tasks` 改 created_at DESC + task_id 次级键（符合
  DESIGN §12 稳定翻页）。
- 删除/转正 6 处弱断言（or True）与 3 处 if False 禁用分支；AC-CTX-01 关键预期
  转硬断言（协议恰一次/业务md加载/history不递归/STARTUP.md排除）。
- 并发子进程：新增 `run_concurrent()`，20路 claim/start/幂等/人工并发逐条落
  commands.jsonl（argv/exit/响应/关联ID）。
- AC-REC-04：SQL 注入改真实路径（test harness crash 制造旧代次崩溃+过期退避
  计划，验证不覆盖新实例、不双开）；`event_duplicate` 故障点在 Store.add_event
  实现真实消费（PK 吸收重放）。
- schema 数量按 profile 精确断言：test profile 恰 97（含5个test命令）、prod
  profile 恰 92/0 test；顺带修复 `manifest.schema_export` 的 prod 过滤真bug
  （`c.path` on dict）。
- 修复中另发现并修复产品级问题：陈旧 'running' 启动操作（outcome_unknown）永久
  阻塞后续 start（RUN-05 复发根因）——槽位释放后标记失效。

## DEF-07 · 清理声明不实 —— **已修复并验证**

- 29963 遗留 daemon（PID 34464）：用其 home 的 owner 凭据**受管停止**
  （证据 `work/cleanup/DEF-07-cleanup-20260930.json`）；29962/54501 两个同轮遗留
  也以受管路径停止并验证释放。
- 验收驱动器全面改造：唯一 run-id（时间戳+随机后缀）、端口动态分配（拒绝复用
  他人端口）、`run-manifest.json` 登记 daemon/pid/home/端点/真实agent/会话句柄、
  teardown 先受管 stop 真实 harness Agent 实例（账本与事实一致），再按 manifest
  逐 home 受管 stop daemon 并记录结果；`control system stop` 语义未改
  （默认不停Agent实例，符合设计）。
- 回归：全量套件 teardown 的 cleanup_log 有真实停止记录；netstat 复核无残留监听。

## DEF-08 · 参数级覆盖不满足签收条件2 —— **已修复并验证**

- 新增 `scripts/coverage_matrix.py`：24 个业务场景，**97/97 命令全部有业务执行**
  记录，**205/205 行 Pass**（work/coverage/20260930-121222-8ce5/summary.json）。
- 384 个参数声明项逐项覆盖：合法输入与实际效果、默认值、必填、互斥、非法值、
  边界、文件输入、权限、旧版本（if-version/spec-version/claim/instance）、幂等
  重放与失败无副作用。
- 特别项全部有专项证据：`--cascade`（级联逐项处置）、`--apply`（next-send/
  restart/auto/next-start 各路径）、`--dry-run`（task trash/agent retire/port
  retire 无写入）、`--force/--grace`（stop/restart 实际停止过程）、`--missed`
  （skip/once）、`--timezone`（UTC 与 +08:00 双触发器）、`--if-version`/
  `--replace`、`--fallback`、`--include-declined`、`--mark-seen`、
  `--include-workspaces/--include-credentials/--with-credentials`。
- 原16个无业务执行命令全部补齐（task trash --cascade、port.list/update、
  producer.show/update/revoke/list、trigger.list/update、channel.list/show/
  update、human.list、harness.list/show、system.start/serve/stop 等）。
- help 检查不代替业务执行：每行均来自真实命令结果。

## DEF-09 · 名片可见性 —— **已修复并验证**

- 处理：port/agent 的 discover 保持开放（DESIGN 有 visibility=authenticated 与
  discover 动作，属于既定公开发现能力，不属于越权）；**控制面清单**（producer/
  trigger/channel/harness list）按 §9.1 加 system.observe/manage 门槛
  （最低权 Agent C 调用被拒，owner/observer 不受影响）。
- 私有内容与管理信息仍受控（show 不含凭据/私有payload 的规则未变）。

## DEF-10 · interrupt 键名不统一 + smoke 证据偏弱 —— **已修复并验证（如实区分）**

- `op_send_agent`：统一可选能力键名 `steer|interrupt`；zsession 声明 interrupt:true
  后 control send --mode interrupt 实际走原生 /send mode=interrupt（不再恒降级）。
- smoke 回读：yolo 用会话 mode 回读（zsession），model_at_start 用会话 model
  回读；**新增 `zsession_env_preflight`**：真实 spawn→等待 message_done，只有
  turn 真正完成才算真实执行可用，否则三个真实案例（HAR-02/E2E-02/E2E-03）如实
  **Blocked（外部条件）** 而非管路证据充数。
- inspect/events 未被加为接入门槛（fixture 关闭后仍照常，AC-HAR-01 过）。

## DEF-11 · 杂项 —— **已修复并验证**

1. `control.system.restore`：服务端占位改明确的 local-only 拒绝语义
   （INVALID_ARGUMENT + local_only 提示）；本地离线恢复经 AC-STORE-03 与覆盖矩阵
   验证可用。
2. `notify_jitter`：已实现（见 DEF-05）。
3. `visible_tasks` 排序：created_at DESC + task_id（见 DEF-06）。
4. `confirm_crash` 残留占位行：已清除。
5. 双层 `runtime\runtime\credentials`：简报迁 runtime/startups 后该瑕疵路径不再
   产生；源码审计无双层拼接。
6. （扩展）所有 `if False else` 残留已从 product 代码清除。

---

## 真实 ZCode 环境状态（如实声明，非产品缺陷）

本轮多次复测确认：**zsession daemon 健康（spawn/send/kill/模型回读正常），但
ZCode 回合执行层当前损坏**——自 zsession 事件流 seq≈719 起再无任何 message_done；
跨 3 模型/4 工位/有无 prompt 的 10+ 个探针均在秒级进入 dead。因此：
- AC-HAR-02 / AC-E2E-02 / **新增 AC-E2E-03**（任务中途换实例续作）三个真实案例
  按预检如实 **Blocked（外部条件）**，证据见各 case assertions 与
  work/independent/20260930-041050-fe2488。
- 预检有真实 env 证据；环境恢复后重跑命令见 README「验收复现」。

## 签名级不变量保护声明

领取/终态/回调/等待/权限/实例唯一性/幂等/原子批处理在所有修复中未被削弱：
全量确定性套件 88/88 通过；探针A–F对应的 20并发幂等/20并发start/20并发claim
不变量在覆盖矩阵与缺陷套件中复验成立。
