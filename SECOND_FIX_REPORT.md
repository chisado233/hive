# Hive V1 第二轮返修报告（SECOND_FIX_REPORT）

- 施工方：Hive V1 返修会话（第二轮）
- 日期：2026-09-30
- 输入缺陷：[INDEPENDENT_REACCEPTANCE_DEFECTS.md](INDEPENDENT_REACCEPTANCE_DEFECTS.md)（RDEF-01..10，复验代码基准 `78380ad`）
- 返修基准：`c409490`（= `8f29f0a` 交接态 + 两份第二轮独立复验报告原样入库，他人工作未改动）
- 返修提交：`35448a1`（产品侧 RDEF-01/03/04/05/07/08/09/10 + 预检重写 + 六个专项回归脚本）→ `2540004`（RDEF-06 参数矩阵真实判定 + 连带产品缺陷修复）→ 本报告提交（见文末）
- 规范：DESIGN/ACCEPTANCE/IMPLEMENTATION_PLAN 本轮仅一处**增量**改动（§10.1 策略表新增 `notify_max_rounds` 一行——RDEF-04 明确要求该键"可配置、与文档一致"，属补全而非放宽；详见 §RDEF-04）。未裁剪任何案例、未扩大错误码接受集、未放宽断言。
- 回归总览：quicktest 通过；确定性 88 案例 **88 Pass + 3 Blocked**（与复验基线一致）；旧缺陷套件 def0102=25/25、def04=15/15、def0305=12/12；新专项套件 rdef01=35/35、rdef0405=33/33、rdef03=25/25、rdef0708=18/18、rdef09=20/20、rdef10=10/10；参数矩阵 **97/97 命令业务执行、384/384 参数具名 PASS 行、0 缺口**（完整性校验通过，exit 0）。真实三案例结果见 [SECOND_REGRESSION_REPORT.md](SECOND_REGRESSION_REPORT.md)。

---

## 逐项状态

| 缺陷 | 状态 | 修复方式 | 实际回归 |
|---|---|---|---|
| RDEF-01 慢命令接管重复副作用 | **已修复并验证** | 原子/外置双路径重分类 + executor_gen 执行者围栏 + 孤儿占位审计核实 | defect_rdef01.py 35/35（真实 os._exit(70) 击杀五窗口）+ defect_def04.py 15/15 |
| RDEF-02 预检归因错误 | **已修复并验证** | 预检重写（先建工位；三态返回；产物/状态/事件三重证据；预检自身缺陷记 Fail 不记 Blocked）；三份旧报告追加强更正附录 | 预检实测 ok（15s 内产物证据）；三真实案例真跑（见回归报告） |
| RDEF-03 退避计划丢失 | **已修复并验证** | 计划改为可恢复状态机（scheduled→starting→failed/awaiting_intervention，kv 持久）+ 冷启动退避门控 + 观测面 | defect_rdef03.py 25/25（8 项要求全覆盖）+ defect_def0305.py 12/12 |
| RDEF-04 轮次上限失效 | **已修复并验证** | 持久轮次计数（kv wakeround，任务+规格+候选者作用域；行删除不可归零）+ notify_max_rounds 入 DEFAULT_POLICY/DESIGN + task.notify_exhausted 事件 | defect_rdef0405.py 33/33 |
| RDEF-05 坏订阅者压制 | **已修复并验证** | 在途判定按候选者各自 pending 新鲜度；坏订阅者不阻塞他人；轮次按 notify_retry_after 步进 | defect_rdef0405.py 33/33（含组合场景矩阵） |
| RDEF-06 矩阵可信度 | **已修复并验证** | covered() 规定种类+预期/实际/证据/断言；6 条失败行逐条定性（3 产品缺陷+3 测试缺陷，全部修复）；79 项补齐具名行；声明-证据完整性校验 | coverage_matrix.py：371 行全 PASS、384/384、0 缺口、exit 0 |
| RDEF-07 工位别名绕过 | **已修复并验证** | store.workspace_key 归一（大小写/尾分隔符/../junction/8.3 短名）+ workspace_key 唯一索引（并发绑定数据库级兜底）+ 迁移005回填 | defect_rdef0708.py 18/18 + defect_def0102.py 25/25 |
| RDEF-08 clone 显式冲突静默 | **已修复并验证** | 显式 --workspace 冲突 → INVALID_ARGUMENT；继承冲突 → 保持默认分配（规范行为） | defect_rdef0708.py 18/18 |
| RDEF-09 --grace 有名无实 | **已修复并验证** | 异步受理：置 stopping（停新唤醒、权限/领取不受影响）→ 保存提示送达 → 全宽限真实等待（持久 kv，daemon 重启可恢复）→ 到期终止+确认+撤凭据；force 显式跳过；restart 宽限后自动起替身 | defect_rdef09.py 20/20（不同时长/force/busy/unknown/停止失败/单实例/daemon重启） |
| RDEF-10 凭据错误码不稳 | **已核实并验证（产品本已正确）** | 四态区分实测稳定：未提供→AUTH_REQUIRED；文件缺失→AUTH_REQUIRED(客户端)；损坏文件/未知token→TOKEN_INVALID；已撤销完整token→TOKEN_REVOKED（该码只能由服务端对已识别 token 发出，即证明旧凭据确实送达）；未撤销但实例已结束→INSTANCE_FENCED；token 正文零入日志/审计 | defect_rdef10.py 10/10 |

---

## RDEF-01 · 慢命令接管重复副作用（High）

**根因**：上一轮把全部 `control.*` 归入"慢路径"（租约接管重跑 handler）。create/grant/producer-create 等无业务级唯一键的命令，在"handler 已持久化效果、响应回填前进程死亡"窗口后，同 key 重试会**重跑 handler 产生第二个 Agent / 重复授权**。start/stop/fire 安全只因其本身可合并（槽位/事件ID去重），不能推广。

**修复方式**（service.py / store.py）：
1. **命令分类反转**：显式 `IDEM_EXTERNAL_COMMANDS`（仅 7 个真正含外部效果的命令：agent.start/wake/send/stop/restart/model、harness.validate）；**其余全部命令（含 create/clone/grant/producer/channel/port/trigger 等）走原子路径**——占位+handler+回填同一事务，崩溃要么全回滚（含占位）要么效果与响应同在，同 key 重试**不可能**产生第二个对象。
2. **执行者围栏**（迁移004 `idempotency.executor_gen`）：外置路径接管时 `executor_gen+1`；回填按 gen CAS。旧执行者超租约被接管后恢复，其回填 CAS 失败→改为返回接管者的响应，**永不覆盖新执行者结果**；异常清理占位同样带 gen 条件，不误删接管者的占位。
3. **孤儿占位不再盲目重做**：原子路径遇 NULL 响应占位（真实崩溃不可达——占位在业务事务内；仅旧版本残留或外部改库可造）先查 `audit_log.request_id` 效果记录：有记录→拒绝并给出审计指引（不产生第二个对象）；无任何效果记录→干净接管执行。无永久死锁。

**五窗口真实注入验证**（defect_rdef01.py，全部 os._exit(70) 真杀 daemon）：
- W1 占位后 handler 前（idem_kill_after_reserve，外置命令）；
- W2 效果已持久化回填前：create/grant/producer 走原子路径击杀→重启→**0 个对象**（回滚证明）→重试恰 1 个；start 走 idem_kill_after_effect→实例已 live→重试**合并回原实例**（同 instance、无第二个 start op）；
- W3 外部动作已发生回填前：stop 击杀→实例已停→重试幂等收敛；
- W4/W5 原请求活着但超租约+旧执行者恢复：idem_delay_backfill{6s}+租约2s+双线程同 key start——B 接管（gen=1）后 A 恢复：**两者返回同一实例、恰 1 个活槽、gen 恰推进一次**。
- 附：独立复验的 SQL 等价探针状态（成功行 response 置 NULL+过期租约）在本轮语义下=「有审计效果的孤儿」→拒绝并指引，Agent 数仍为 1；无效果孤儿→接管执行成功。
- 不变量保持：同 key 异参 IDEMPOTENCY_CONFLICT；20 并发同 key create→1 个 Agent；跨主体隔离（defect_def04 复跑）。

## RDEF-02 · 预检归因错误（测试缺陷）

**根因**：预检 spawn 携带从未创建的工位 `%TEMP%\zs-preflight-ws` → zsession 依契约返回 400 bad_workspace → 预检把自身请求构造错误当成"执行层损坏"。上一轮据此外加"全局事件流缺 message_done"判据，把三个真实案例 Blocked 并写入三份报告。

**修复方式**（acceptance.py）：
- 探针工位先创建（每 run 唯一目录，结束清理）；
- 返回三态 `ok/blocked/defect`：**预检自身请求/解析缺陷→案例记 Fail**（响亮失败，不得记环境 Blocked）；环境不可达/认证配置/回合未完成→Blocked（带证据）；执行证明=**本会话产物文件 / 会话状态推进 / 本会话 message_done** 三重证据任一成立（不再以全局事件流缺失为唯一判据）；
- 案例站点改为消费三态。
- 实测：预检 ok（15s 内探针会话产出 `PREFLIGHT_OK.md`/`EXECUTOR_OK`）——执行层正常的直接证据。

**报告更正**：FIX_REPORT/REGRESSION_REPORT/ALL_FIXES 三份旧报告**保留原文与原失败证据**，各在文首加更正横幅、文末加「附录：第二轮更正」，明确归因证伪与真实原因；不抹除任何历史证据。

## RDEF-03 · 退避计划先消费后丢失（产品缺陷）

**根因**：run_due_restarts 到期时先把 kv 计划清成 `{attempts}` 再调 op_start_agent；启动失败仅 warning，计划（next_allowed_at）已不可恢复。

**修复方式**（runtime_ops.py `_schedule_crash_restart` / scheduler.py `run_due_restarts`）：计划升级为持久状态机 `restart:<agent> = {attempts, next_allowed_at, state: scheduled|starting|failed|awaiting_intervention, fail_count, last_error, started_at, op_id}`：
- 消费=状态迁移（scheduled→starting，原子 kv 写，不删 next_allowed_at）——**迁移与启动调用之间崩溃可恢复**：下轮 tick 见 state=starting 且 started_at 超过15s 无活实例→按失败处理；实例其实已起→计划完成。
- 启动失败：`_restart_attempt_failed`——计划保留为 failed，**同档位顺延**（不升级不丢失），事件 agent.restart_failed；连续 3 次失败→`awaiting_intervention`（无 next_allowed_at，自动路径全停，事件 agent.restart_stalled）。
- 达到 crash 限次（restart_max_per_hour）同样进入 awaiting_intervention。
- 观测面：`control agent show --runtime` 返回 `restart_plan` 全字段；恢复路径=手动 start 成功清计划（op_start_agent 成功即清）、spec/候选变化、限次窗口滚动。
- **冷启动门控**（`_restart_blocked`）：带未到期计划或待干预的 Agent 不被自动通知/冷启动路径拉起（上轮可绕过退避）。
- paused/retired 不拉起、外部 start 占槽后旧计划作废（不覆盖新实例）——原有行为保持并纳入验证。

## RDEF-04 / RDEF-05 · 对账轮次与候选者压制（产品缺陷）

**RDEF-04 根因**：轮次=当前存在的通知行数计数——行被 3 连败删除后计数归零，dedupe 永远 `reconcile:<task>:1`，12 轮上限不可达；`notify_max_rounds` 不在 DEFAULT_POLICY（不可配置不可见）。

**修复方式**（scheduler.py）：
- 轮次改 **kv 持久计数** `wakeround:<task> = {total（终身单调，作 dedupe 后缀）, offset（条件重置点）, spec, candidates, exhausted, last_at}`——**通知行删除不可能归零计数**；dedupe `reconcile:<task>:<total>` 终身唯一，重置后也不撞键。
- `notify_max_rounds` 入 DEFAULT_POLICY（默认 12，int，1..1000，`control system config` 可读可改且范围校验）与 DESIGN §10.1（增量一行）。
- 达上限：**恰一次** `task.notify_exhausted` 事件（含轮次/上限/候选者/恢复指引）+ kv `exhausted` 可查询；任务仍在队列（不静默遗忘）。
- 恢复路径（全部实测）：任务规格变更（amend→spec_version 变化）/ 候选集变化（新订阅、paused 退出）/ 调大上限 → 计数重置或上限重评，自动续轮。
- 轮次步进按 notify_retry_after 自上轮起算（墙钟+偏移钟），不按任务年龄每 tick 重发（轮次间实际间隔≥notify_retry_after，从有效调度记录核对）。

**RDEF-05 根因**：对账的"无在途唤醒"判定以**任一**订阅者的 pending 为准——某坏订阅者（harness 损坏、冷启动反复失败）的 pending 永不消解→整端口候选者全部被压制。

**修复方式**：候选者逐个判定在途（其**自身** pending 且在 notify_retry_after 窗口内→跳过该候选者本轮；陈旧 pending 视为死重量，不阻塞他人）。候选者门（enabled/容量/decline/spec）与首轮派发共用 `_task_candidates`。

**组合场景验证**（defect_rdef0405.py 33 项）：单坏订阅者；坏+健康共存（健康者持续收到轮次唤醒）；pending 长期滞留；通知行删除后计数继续（:2 不回 :1）；上限到达（无 :4）与三路恢复；新候选者加入即被唤醒；paused/decline/容量满排除；无新业务事件（纯时钟推进）恢复；唤醒重试零新增 Task/receipt/@通知；无 callback 零外推；唤醒≠授权（被撤 grant 的 Agent 被唤醒但 claim 被 FORBIDDEN 拒绝）。

## RDEF-06 · 参数矩阵可信度（测试缺陷 + 连带产品缺陷）

**根因**：covered() 把布尔塞进 kind 位且恒写 PASS；无声明-证据完整性校验；79/384 参数无具名行。

**修复方式**（coverage_matrix.py 全面重写记录层 + AST 迁移全部 99 处调用点）：
- **行契约**：每行含 cmd、参数声明项、测试种类（规定字符串 effect/default/reject-error/boundary/type-check——非规定值直接抛错）、预期、实际、**命令/响应证据**（argv+request_id）、断言、状态（PASS/FAIL/Blocked/NotRun）。判定如实：布尔/env 按实际值；失败=FAIL；预期拒绝必须精确错误码+无副作用。
- **6 条失败行定性**：
  - 产品缺陷×3（修复产品）：CMD-006 `--claim` 无效领取凭据被静默接受（waits.py 增加凭据校验，DESIGN"必需有效本次领取凭据"）；CMD-028 `--start` 启动失败回滚已注册身份（违反 DESIGN"失败不回滚"，改为保留身份+错误入 start_operation）；CMD-041 `--replace` 必然 INTERNAL_ERROR（ports.py replace 分支缺键 KeyError；连带修复 replace 基线 visibility 非法值）。
  - 测试缺陷×3（修正测试）：CMD-090 `--text`+`--text-file` 旧断言"file wins"违反 DESIGN 互斥条款→改为预期 MUTUALLY_EXCLUSIVE（精确码+任务不作答）；CMD-010 `--claim-by`/`--expires-at` 场景漏带 `--body`（work 必填 body 或 data，产品正确拒绝）。
- **79 项补齐**：`fill_param_gaps()`——场景真实携带过的参数以**该次成功调用的证据**具名成行；从未携带的参数逐一补充真实执行（专用新鲜状态调用：external producer 的 --credential-out、--if-version 过期值的 VERSION_CONFLICT 拒绝证据、互斥对拆分携带、serve 探针真实启动+健康检查后受管停止等）。
- **完整性校验**：声明参数集（规范化匹配，含位置参数）− PASS 行参数集 = 空集才允许 summary 通过；数量全部由实际证据计算（commands_executed/params_with_pass_rows/param_gaps），不回显 manifest 总数当覆盖；有缺口或失败→exit 1。
- **最终证据**：`work/coverage/<最新run>` = 97/97 命令业务执行、384/384 参数具名 PASS 行、371 行全 PASS、kinds={effect, reject-error}、0 缺口、exit 0。已独立验证的 timezone/分页/cascade/dry-run 行为全部保留。

**连带发现并修复**：`init --default-harness` 死参数（接受但永不落盘；现落盘 system.json 并在 daemon 启动时种入 policy default_harness）。

## RDEF-07 / RDEF-08 · 工位归属与 clone（产品缺陷）

**RDEF-07 根因**：`_workspace_in_use` 仅 `os.path.abspath` 比较——Windows 大小写/尾分隔符/相对路径/`..`/junction/8.3 短名等同一物理目录别名全部绕过。

**修复方式**（store.py + agents.py）：
- `workspace_key(path)`：abspath+normpath（消 `..`/尾分隔符）→存在则 realpath（junction）→GetLongPathNameW（8.3 短名，本卷实际生成短名并实测拦截）→normcase（大小写折叠）。create/update/clone 共用同一归属规则。
- **并发绑定数据库级兜底**（迁移005）：`agents.workspace_key` 唯一部分索引 + 存量回填（重复绑定最旧者保留键，其余 NULL 仍受运行时检查保护）。8 并发同目录 create→**恰 1 胜**、7 败 INVALID_ARGUMENT、胜者派生文件完整；冲突失败不触碰原 Agent 文件（键冲突在写盘前抛出，整事务回滚）。
- 归一化别名全量实测拦截：大小写、单/双尾分隔符、相对路径、`..`、junction、8.3 短名。

**RDEF-08**：clone 显式 `--workspace` 冲突→INVALID_ARGUMENT（与 create/update 一致）；继承/缺省仍分配新默认工位（规范行为保持）；实现中曾误删继承回退，已按复验结论（"源工位继承场景的默认回退是正确行为"）恢复并双路验证。

## RDEF-09 · --grace 实际生效（产品缺陷）

**根因**：op_stop_agent 接受并记录 grace 但停止路径从不读取。

**修复方式**（runtime_ops.py `_grace_stop` + scheduler.py `run_grace_stops`，按 DESIGN CMD-034/035 契约）：
- 正常 stop（grace>0 且非 force）：**异步受理**（控制请求即时返回，不阻塞业务写入）——实例置 `stopping`（唤醒派发停送=停领新工作；**领取与进度提交不受影响，凭据在终止前始终有效**）→ 向实例送达保存/交接提示（best-effort，结果入 op）→ 全宽限真实等待（持久 kv `gracestop:<op>`，daemon 重启续期）→ 到期 harness 终止+确认（stopped→撤凭据+围栏+paused+事件 agent.stopped(grace)；unknown→槽位保留既有语义；异常→op failed 可观测）。
- `--force`：显式跳过宽限，立即终止（响应含 force 记录）。
- restart --grace：受理后到期终止并**自动启动替身实例**（then_start）。
- 宽限期内 start 合并进 stopping 实例（单实例约束不破）；已有宽限停止重复受理→合并。
- 时钟级证明：op 的 ended_at ≥ grace_deadline；wall 计时 ≥ 宽限值；提示消息实测送达实例。
- 测试矩阵全过：不同 grace 值、force、busy、unknown（confirm_stop 释放）、停止失败（harness_stop_fail 故障点）、单实例、daemon 重启续期、终止后旧凭据 TOKEN_REVOKED。
- 测试脚本适配：全部测试 stop/restart 显式 `--force`（44 处机械替换，测试意图不变——它们验证的是终态/围栏/槽位而非宽限；宽限行为由 rdef09 专项套件验证）。

## RDEF-10 · 旧凭据错误码（核实：产品已正确）

服务端四分支本就区分且稳定；上一轮两处观测差异来自探针环境而非产品。本轮以**真实客户端路径**逐态实测：未提供凭据→AUTH_REQUIRED；凭据文件不存在→AUTH_REQUIRED（客户端、不发请求）；文件损坏/未知 token→TOKEN_INVALID；**已撤销实例凭据（完整 token 真实送达）→TOKEN_REVOKED 稳定复现两次**（该码只可能发给服务端识别出的 token，即旧凭据确实被携带）；未撤销但实例已结束→INSTANCE_FENCED（纵深防御分支，等价账本状态构造验证）。token 正文零泄漏（daemon 日志+审计全文扫描）。E2E-02/03 的旧凭据断言集 {INSTANCE_FENCED, TOKEN_REVOKED} 维持不变（未扩大）。

---

## 连带产品缺陷（本轮发现并修复，均在回归中验证）

1. `control port update --replace` 必然 INTERNAL_ERROR（replace 配置缺键 KeyError；基线 visibility 非法值）——RDEF-06 矩阵发现。
2. `control agent create/clone --start` 启动失败会回滚已注册身份，违反 DESIGN"失败不回滚"——改为保留身份并在 start_operation 返回错误与 warning。
3. `inbox wait create --claim` 不校验领取凭据（DESIGN 要求有效凭据）——提供时必须是本任务上调用者的有效领取。
4. `control system init --default-harness` 死参数——现落盘并种入 policy。
5. op_stop_agent 在 harness stop 抛异常时遗留 'accepted' 操作行——现按 failed 结束。
6. zsession 适配器 stop 对已消失会话 404 重试后抛错——kill 幂等语义改为视为已停止（服务不可达等其他错误仍抛）。
7. 新增测试故障点：idem_kill_after_effect、idem_delay_backfill、harness_stop_fail（test profile 专用，沿用上轮扩展先例）。

## 边界与红线遵守

- 未修改 trigger-platform（19961 全程只读）；未动用户 zsession 全局配置/桌面宿主；token 只留在受保护配置，不入 prompt/日志/报告/提交。
- 测试资源按 run-manifest 受管收尾（先停真实 Agent 再停 daemon，只停本 run 登记）；未按进程名/前缀批量杀。
- DESIGN 仅增量一行策略表（notify_max_rounds），无任何语义放宽；ACCEPTANCE 案例集零删改（仅 44 处 stop/restart 显式 --force 的机械适配+预检三态化，均有专项套件承接原意图）。
- Blocked≠Pass；预检自身缺陷记 Fail 不记 Blocked；SQL 仅用于构造"真实崩溃可产生的等价状态"并如实标注。
