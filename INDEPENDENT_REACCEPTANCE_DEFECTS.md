# Hive V1 修复轮独立复验缺陷清单（INDEPENDENT_REACCEPTANCE_DEFECTS）

- 验收Agent：修复轮独立复验（非施工方）
- 日期：2026-09-30
- 代码基准：`78380ad`（＝修复终态 `4ed1f05` ＋ ALL_FIXES.md），工作区干净
- 上轮缺陷：[INDEPENDENT_DEFECTS.md](INDEPENDENT_DEFECTS.md)（DEF-01..11）
- 本轮新缺陷编号：RDEF-01..（R=Re-acceptance）；分类：产品缺陷 / 测试缺陷 / 归因未明
- 独立证据根：`work/independent/20260930-135645R`（P1/P3/P6）、`20260930-140433R2`（P2/P3b/P6b）、`20260930-140651R4`（P4）、`20260930-140842R3`+probe_fix3b/3c（真实三案例等效）、`env_probe*.json`（环境归因）、复跑套件 `work/acceptance/20260930-054913-49b7d8`、`work/defects/def*（本轮重跑）`、`work/coverage/20260930-135440-b262`

---

## RDEF-01 · High(产品) · 慢命令租约接管对"无业务级唯一键"的命令产生重复副作用（DEF-04 未完全关闭）

- **规范依据**：DESIGN §16（同request-id重试不得造成重复业务效果）；上轮DEF-04验收要求（"不会创建两个Agent、重复授权"）；FIX_REPORT 声称"效果安全性由命令本身的合并语义保证"。
- **复现步骤**（窗口2：handler已持久化、幂等回填前进程死亡——无现成故障点，用账本等价状态构造：对已成功行置 `response=NULL, lease_until=过去`，该状态任何真实崩溃均可产生，SQL仅构造状态）：
  1. `control agent create --file <同名配置> --request-id W2A`（成功）；
  2. 将 W2A 行置为孤儿租约；
  3. 同key同参数重试。
- **预期**：重试要么重放原结果，要么拒绝并指引；不得创建第二个Agent。
- **实际**（work/independent/20260930-140433R2/p2.json）：**重试接管后重跑handler，创建了第二个同名Agent**（agents_with_name=2，两个不同agent_id）；`control permission grant` 同样**重复授权**（grant_rows=2）。对照：`control trigger fire`（事件ID去重）与 `control agent start`（槽位合并）安全——租约机制只对"业务可合并"命令安全。
- **影响**：对 create/clone/grant 类命令，崩溃+同key重试=双实体/双授权；DEF-04的"接管重跑"策略对这类命令不成立。窗口4（原请求活着但超租约被接管）同根因，代码路径相同（未单独注入验证）。
- **证据位置**：`work/independent/20260930-140433R2/p2.json`（w2a/w2b/w2c/w3各节）。
- **回归建议**：慢命令按类分流——create/grant类改为"占位事务内查业务键"或接管时先探测已产生效果（如按request_id记录业务对象ID于占位行payload）；或在文档明确此类命令不支持崩溃接管并给出恢复指引。

## RDEF-02 · Medium-High(测试) · 真实三案例Blocked归因错误：预检events调用自身400，执行层实际可用

- **规范依据**：本轮指令重点五（不得仅凭无message_done判宿主不可用）；FIX_REPORT/REGRESSION_REPORT"ZCode回合执行层损坏/秒级dead/自seq≈719无message_done"。
- **独立对照**（work/independent/env_probe.json、env_probe2.json）：
  1. **直连宿主**（不经Hive适配链）：spawn最小会话"请只回复：收到"→ 状态 working→idle（~9s，两模型均然）；
  2. **决定性验证**：指示会话创建标记文件 → `%TMP/../tmp/env-probe-marker.txt` 实际产生，内容 `EXECUTOR_OK` —— **回合执行层正常**；
  3. 施工方预检失败原因：其 `zsession_env_preflight` 的 events 请求被 zsession 拒以 **HTTP 400 Bad Request**（acceptance assertions: "env preflight failed: preflight exception: <HTTPError 400>"）——是**预检自身请求构造问题**，不是执行层故障；
  4. 我的等效真实链路验证（见报告§6）：E2E-02等效**真实通过**（真实Agent自行领取、写产物、emit done），进一步反证环境可用。
- **影响**：AC-HAR-02/E2E-02/E2E-03被错误Blocked；"执行层损坏"的不实归因进入了FIX_REPORT/REGRESSION_REPORT/ALL_FIXES三份文档。
- **分类**：测试缺陷（预检）＋归因错误（报告声明）。
- **回归建议**：修预检的events查询（对照zsession API实际参数），或改用"会话状态working→idle+产物核验"作为执行证据；三份报告的归因段落更正。

## RDEF-03 · Medium(产品) · 退避计划"先消费后启动"：启动失败即永久丢失计划，无恢复途径

- **规范依据**：本轮指令重点三（"先消费计划、后启动之间崩溃/启动暂时失败后是否有可解释的恢复途径"）。
- **复现**：fixture agent崩溃产生计划 → 将其harness换成必然失败的process → 时钟推进至到期 → `run_due_restarts` 先 `kv_set`清掉 `next_allowed_at` 再 `op_start_agent`（失败）→ 计划只剩 `{attempts:N}`；再推进20s仍无重试、无新实例。
- **预期**：启动失败应有可解释恢复（保留/顺延计划，或产生异常事件供观测）。
- **实际**（20260930-135645R/p3.json `failed_restart_plan`）：计划丢失，静默不再尝试。
- **证据**：p3.json；代码 scheduler.run_due_restarts（消费在启动前，异常仅warning日志）。
- **回归建议**：失败时回写新 `next_allowed_at`（沿用当前档位或顺延），并/或产生 `agent.restart_failed` 事件。

## RDEF-04 · Medium(产品) · 唤醒对账轮次上限失效：行删除使轮次计数归零，永久循环"第1轮"；notify_max_rounds 不可配置、不可见

- **规范依据**：本轮指令重点三（notify_max_rounds默认12必须单独核查：是否有明确配置/可见状态/上限后如何观察——不能只是把"3次后静默"换成"12轮后静默"）。
- **复现**（20260930-140433R2/p3b.json，干净单订阅者场景）：投递失败型agent；通知3败删除后，对账重入队的dedupe键始终是 `reconcile:<task>:1` —— 因轮次=当前存在行数计数，行被删后计数回0，下一轮再插 `:1`；14轮×25s推进后 `max_rounds_reached=1`，永无 `:2`。
- **预期**：轮次单调递增至12后停止，且停止后有待办可观测/可再激活路径。
- **实际**：上限对"投递失败"这一对账目标场景**永不可达**（无限:1循环，速率有界但无终止、无升级观测）；`notify_max_rounds` 不在 DEFAULT_POLICY（service.py无此键）→ `control system config` 拒绝修改（未知策略键）、回显也不可见，仅代码默认值（scheduler.py:255）。
- **影响**：DEF-05的"有界轮次"承诺在失败场景落空；上限行为、上限后语义均无配置与观测面。
- **回归建议**：轮次计数改为持久计数（如kv或沿用已删行的历史计数）；`notify_max_rounds` 入 DEFAULT_POLICY；达上限产生一条系统事件。

## RDEF-05 · Medium(产品) · 任一订阅者的滞留pending唤醒会压制整个端口任务的对账重入队

- **规范依据**：DEF-05修复目标（无新事件时待办能重新获得有效唤醒）。
- **复现**（20260930-135645R/p3.json wake节）：端口任务待领；某订阅者（harness损坏、冷启动反复失败）留有一条pending唤醒永不消解 → `run_reconcile_wakes` 的 NOT EXISTS 条件以"任一订阅者有pending"判定"已有唤醒在途" → 健康订阅者也永不对账重入队（14轮推进0重入队）。
- **影响**：一个坏订阅者饿死整端口待办的对账恢复（DEF-05在新障碍下复发）。
- **证据**：p3.json `reconcile_rounds` 全空 + daemon日志 `coldstart.failed: harness未注册/失败` 循环。
- **回归建议**：NOT EXISTS 按"每个候选者自身是否有pending"判定，或给pending唤醒加时效（超龄视为无在途）。

## RDEF-06 · Medium(测试) · 参数覆盖矩阵的可信度缺陷：无条件PASS行、失败被记PASS、384项"逐项覆盖"无完整性校验

- **规范依据**：本轮指令重点四（不得从manifest读总数直接宣布覆盖；一行多参数须各有断言）；ACCEPTANCE §5.2/§7.2条件2。
- **事实**（对施工方与我各自运行产物的相同分析，work/coverage/20260930-121222-8ce5 与 20260930-135440-b262）：
  1. `covered(cid, param, env.get("ok"))` 把布尔塞进 `kind` 位，而 `covered()` **恒写 status="PASS"`** —— 105行的kind是布尔；其中 **6行 kind=False（命令实际失败）仍记PASS**：CMD-006 `--claim invalid rejected`、CMD-010 `--claim-by`、CMD-010 `--expires-at`、CMD-028 `--start`、CMD-041 `--replace`、CMD-090 `answer full`。无法区分"预期失败"与"意外失败"。
  2. `params_declared=384` 只是 `sum(len(c.params))` 的manifest回显；脚本从未校验"有行参数集合==声明集合"。按规范化名称匹配，**79/384项参数声明在行级无具名记录**（多为位置参数与`--reason`/`--to`类全程使用项——实际命令确有携带，但§5.2要求的逐项效果断言缺失）；"384项逐项覆盖"为夸大。
  3. 我独立复核确认真实有效的部分：97/97命令确有业务执行行；--timezone（两cron计划时点恰差8h，事件ID含计划时刻）、分页（created DESC+ID次键、无重叠、游标通）、--cascade（级联全trash）、--dry-run（零写入）均实证有效。
- **回归建议**：`covered()` 增加ok参数并如实记录；补per-param完整性校验（声明-覆盖差集为空才可summary通过）；6条失败行逐条定性。

## RDEF-07 · Medium(产品) · 共享工位防护无大小写归一：Windows下别名路径绕过

- **规范依据**：本轮指令重点一（路径规范化、大小写等别名）。
- **复现**（20260930-135645R/p1.json）：agent1 绑 `…\WS_Case`；agent2 以 `…\WS_case` 创建——**成功**（`_workspace_in_use` 仅 `os.path.abspath` 比较，未归一大小写；NTFS不区分大小写→两agent同目录）。派生文件互相覆盖、启动简报交叉。
- **预期**：同物理目录（不区分大小写语义）应被识别为冲突。
- **回归建议**：Windows下用 `os.path.normcase`（小写化）比较；顺带处理尾部斜杠/短路径别名。

## RDEF-08 · Low-Medium(产品) · clone 显式指定的冲突工位被静默忽略

- **规范依据**：本轮指令重点一第10条（"用户显式指定冲突工位时，不应无提示地忽略参数"）。
- **复现**（p1.json `clone_explicit_conflict`）：`control agent clone A --workspace <已被占用路径>` → ok=true、无warning、实际落到默认工位。
- **预期**：显式参数冲突应拒绝或至少显式warning说明回退。
- **说明**：源工位**继承**场景的默认回退是正确行为；缺陷仅在"用户显式指定"分支未区分。
- **回归建议**：区分args显式传入与配置继承；显式冲突返回 INVALID_ARGUMENT（同 create/update 的行为）。

## RDEF-09 · Low-Medium(产品，既有) · `--grace` 参数有名无实

- **规范依据**：DESIGN CMD-034/035（grace=保存提示宽限）；§5.3（--force/--grace 不能都只返回相同假成功）。
- **事实**：`op_stop_agent` 接受并记录 grace，但停止路径从不读取（无"先提示保存、等grace再停"行为；fixture/process均立即停）。覆盖矩阵 CMD-034 `--grace/--reason/--callback kind=True` 记PASS——仅证明参数被接受，未证明效果（§5.3明示不足）。
- **回归建议**：实现grace（向实例发送保存提示，等待grace或force跳过）或在文档声明V1不支持并在矩阵中如实标注。

## RDEF-10 · Info(产品) · 旧实例凭据拒绝的错误码不稳定（AUTH_REQUIRED vs TOKEN_REVOKED）

- 真实通道两次观测：上轮TOKEN_REVOKED；本轮3b观测到AUTH_REQUIRED（拒绝本身达成，但验收断言接受集为 {INSTANCE_FENCED, TOKEN_REVOKED}，若真实案例可运行会因此挂）。需统一停止后旧实例凭据的撤销语义与错误码。证据：20260930-140842R3/C_phase2.json `old_cred_err`。

## 观察项（不计缺陷）

- E2E-03第5次运行中，abort路径的`control agent stop`未生效（实例保持live、无第二实例行）；该调用结果被本验收探针忽略，无法定性（可能是stop调用失败/时序），留待施工方在修复预检后用正式案例复核（见主报告§6）。

## 归因未明 / 环境阻碍记录

- **RDEF-02已升级为定性结论**（预检缺陷+报告归因错误）；无剩余"归因未明"项。
- 本轮真实案例执行消耗的zsession会话（env探针2个+R3/R3b/R3C各2个）均已在探针内或下文收尾kill。

---

## 上轮 DEF-01..11 关闭判定（详见主报告§3）

| 原缺陷 | 判定 | 本轮新缺陷牵连 |
|---|---|---|
| DEF-01 | **关闭**（真实通道证实：简报协议+个体说明各1次；显式工位三件套；WORKSPACE_UNAVAILABLE防护；共享防护生效——本验收探针自身被正确拦截一次） | RDEF-07/08 为修复引入面的边界缺陷 |
| DEF-02 | **关闭**（runtime/startups每实例一份；残留旧STARTUP防御排除；4轮重启3889B恒定；仅当前实例凭据路径；glob规则未收紧——中文名加载/子目录不加载） | — |
| DEF-03 | **主体关闭**（阶梯[2,4,8]精确消费、不同步重启、限次、paused不拉起、daemon重启计划持久并生效） | RDEF-03（失败即丢计划） |
| DEF-04 | **部分关闭**（快路径单事务击杀验证；慢路径租约对start/stop/fire安全；孤儿清扫存在） | RDEF-01（create/grant双执行） |
| DEF-05 | **部分关闭**（pacing生效attempts+send_after；干净场景对账重入队出现:1轮；jitter落地send_after） | RDEF-04/05（上限失效+压制） |
| DEF-06 | **关闭**（88/88双重复现；TASK-08改根过滤；CTX-01硬断言；并发20条入证；产品`if False`=0） | — |
| DEF-07 | **关闭**（29963等遗留已受管停止；run-manifest+cleanup_log如实，含一条ok:false不吞；无监听残留） | — |
| DEF-08 | **部分关闭**（97/97命令业务执行✓；重点参数实证✓） | RDEF-06（矩阵可信度）+RDEF-09（--grace） |
| DEF-09 | **关闭**（producer/trigger/channel/harness list对C拒绝、owner可读；show无凭据正文不变） | — |
| DEF-10 | **部分关闭**（steer|interrupt统一并透传mode=interrupt——代码核实；smoke回读已实现但被RDEF-02预检挡住未在案例中兑现） | RDEF-02 |
| DEF-11 | **关闭**（restore服务端INVALID_ARGUMENT local-only；notify_jitter实现；visible_tasks排序实证；死代码清零） | — |
