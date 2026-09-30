# Hive V1 修复轮全量技术说明（ALL_FIXES）

> **【更正说明 · 2026-09-30 第二轮返修】** 本文写作时对真实三案例 Blocked 的归因
> ——"ZCode 回合执行层损坏"——已被第二轮独立复验收 RDEF-02 **证伪**：执行层实测正常
> （直连会话可完成回合并产出文件），Blocked 的真实原因是预检自身的 spawn 请求把
> 未创建的工位目录发给了 zsession（HTTP 400 bad_workspace），属预检缺陷而非环境故障。
> 原文与原失败证据按"保留历史"原则未作删改；更正详情见文末
> 「附录：第二轮更正」与 SECOND_FIX_REPORT.md §RDEF-02。


- 基准：`b2288d3`（独立验收"不满足签收条件"）→ 修复轮终态：**`4ed1f05`**
- 变更规模：24 个文件，+3416 / −242 行（含 4 个新驱动脚本共 1762 行）
- 独立验收缺陷：[INDEPENDENT_DEFECTS.md](INDEPENDENT_DEFECTS.md)（DEF-01..11）
- 逐项状态报告：[FIX_REPORT.md](FIX_REPORT.md)；回归明细：[REGRESSION_REPORT.md](REGRESSION_REPORT.md)
- 本文给出每个修复的完整技术细节：根因代码位置 → 修复方案 → 为什么这样修 → 回归验证方法与证据路径 → 修复过程中连带发现并修掉的附带缺陷。

最终结果一览：

| 套件 | 结果 | 证据 |
|---|---|---|
| 88 确定性案例 | **88/88 Pass** | work/acceptance/20260930-041804-0f0b6f |
| DEF-01/02 回归 | 25/25 | work/defects/def0102-*/checks.json |
| DEF-04 回归（真实进程击杀） | 15/15 | work/defects/def04-*/checks.json |
| DEF-03/05 回归（时钟驱动） | 12/12 | work/defects/def0305-*/checks.json |
| 参数覆盖矩阵 | 205/205 行，97/97 命令 | work/coverage/20260930-121222-8ce5 |
| 真实 ZCode 三案例 | Blocked（外部环境，预检证据） | 各 case assertions.jsonl |

---

# 第一组 · DEF-01 / DEF-02 · 工位与上下文

## DEF-01：真实 harness 工位缺失系统协议与个体说明

**根因（代码级）**：`hive_core/agents.py` 的 `_rebuild_workspace(svc, agent_id, cfg, revision)` 第一行是 `ws = _workspace_root(svc, agent_id)`，而 `_workspace_root` 恒返回 `<home>/workspaces/<agent_id>`——完全无视 `cfg["workspace"]["path"]`。于是：

- `control agent create --file`（AC-E2E-02/E2E-03 的标准用法）指定显式工位时，agent.md/config.json/prompt.md 永远生成到**默认**目录；
- `_build_startup_context`（runtime_ops.py）从**配置**工位读 agent.md/prompt.md → 落空 → 启动简报里协议与个体说明出现 0 次；
- 独立验收探针B测得 `protocol_header_count=0`，施工方 REPORT §2.3 的声明与实际证据不符。

**修复**：

1. `agents.py` 新增两个函数：
   - `resolve_workspace(svc, agent_id, cfg)` —— 工位解析的单一事实源：配置有 `workspace.path` 就用它，否则用默认根；
   - `_workspace_in_use(svc, agent_id, path)` —— 扫描 agents 表，检测同一绝对路径是否已被**其他** agent 绑定。
2. `_rebuild_workspace` 改用 `resolve_workspace`——派生文件永远落在有效工位。
3. 三处防护接入：
   - `create_agent`：显式工位已被他人绑定 → `INVALID_ARGUMENT`（含 bound_to 详情）；
   - `update_agent`：workspace.path 变更且与他人冲突 → 拒绝；
   - `clone_agent`：克隆携带源工位路径冲突时**回退默认工位**（克隆本就不复制工位内容，只复制配置）。
4. `runtime_ops._build_startup_context`：agent.md/prompt.md 不存在时从"静默跳过"改为**抛 `WORKSPACE_UNAVAILABLE`**——协议缺失是错误，永远不允许再无声发生（这正是 DEF-01 逃过 90/90 的原因：fixture 案例只走默认工位，无断言）。

**为什么这样修**：不引入"白名单只读几个文件"的捷径（那会破坏既定的"默认加载根目录业务 Markdown"规则），而是把工位解析收敛到一个函数，让所有消费方（生成、启动上下文、运行适配器）强制一致。

**回归**（`scripts/defect_def0102.py`，25 项断言）：
- 注册 `dummy-proc` **真实 process 通道**（本地哑进程），确保走的就是 opencode/zsession 同一条简报代码路径，而不是拿 fixture 通道敷衍；
- 显式工位三件套齐全 + 默认根未被误用；
- 简报含协议头恰 1 次、个体说明 1 次、业务 md（CONTEXT.md 标记）已加载、实时责任摘要存在；
- 简报不落业务工位；
- 共享工位第二个 agent 被拒绝（错误信息含占用者）；
- 配置更新后新指令进入下一轮简报。

## DEF-02：STARTUP.md 反馈循环（逐轮增长 + 已吊销凭据路径）

**根因**：真实通道启动简报写在**工位根目录**的 `STARTUP.md`，它匹配 `*.md` glob 且不在 exclude 里 → 下一轮 `gather_context` 把上一轮简报**整体当业务 md 吞入**，然后再覆写新的 → 965→1965→2965 字节线性增长；第 2 轮起含**已吊销**旧实例凭据路径；上一轮产物 output.md 也被回流。

**修复**（三道防线）：

1. **位置迁移**（runtime_ops.py）：简报改写到 `<home>/runtime/startups/<agent_id>/<instance_id>.md`——每实例一份、在 runtime 区、**物理上不在业务扫描范围**；路径经 `options["startup_file"]` 传给适配器；
2. **模板支持**（harness.py）：ProcessAdapter 新增 `{startup_file}` 占位符展开；旧 spec 里写死的裸 `STARTUP.md` 字样自动替换为 runtime 绝对路径（向后兼容）；
3. **防御性排除**（contextgen.py）：即使某工位残留旧版 STARTUP.md，`gather_context` 也按系统派生文件跳过（不改变其它业务 md 的加载规则；output.md 等产物是否加载完全服从 glob 配置——独立验收要求保留的"既定规则"原样保留）。

**回归**（同一脚本）：连续 3 次 restart——简报大小恒 3880B（零增长）、每轮协议头恰 1 次、`--- 工位文件 STARTUP.md ---` 嵌套出现 0 次、所有引用的凭据路径都属于**当前**实例（断言 `set(refs) == {当前实例}`，允许多次引用同一路径属正常）。

---

# 第二组 · DEF-04 · 幂等占位崩溃恢复

**根因**：上一轮引入的占位式幂等（`_idem_reserve_or_replay`）把「INSERT 占位」和「业务执行+回填」放在**不同事务**里。进程在占位提交后、回填前死亡（或等价的账本状态），留下 `response IS NULL` 的孤儿行；同 key 重试每次空等 10.2s 后 `STORAGE_UNAVAILABLE`，**永久**不可用——与 DESIGN §16 "用相同 request-id 核查"的指引直接矛盾。

**修复方案（按命令分治）**——`hive_core/service.py` 重写 dispatch 的幂等路径：

1. **快命令**（`emit.*` / `inbox.*` / `producer.emit` / `control.task.*`，即纯账本操作）：
   占位 INSERT + handler 执行 + response 回填在**同一个 `store.write()` 事务**内完成。因为 `Store.write()` 可重入（嵌套自动加入外层事务），handler 内部的领域写全部并入外层。崩溃时只有两种结局：
   - 提交前死 → **整体回滚**（业务、占位、响应都不存在，重试全新执行）；
   - 提交后死 → 业务+响应原子可见，重试直接重放。
   **不存在"占位已提交而响应永空"的中间态。** 并发同 key 请求在写锁上串行，后来者自然重放赢家。
2. **慢命令**（agent./harness./test./system./port./subscription./permission./producer./trigger./channel./human./event./audit./delivery./operation.，含外部调用不能长期持锁）：
   保留"先占位后执行"，但占位带 **lease**（迁移002：`ALTER TABLE idempotency ADD COLUMN lease_until`；策略键 `idem_lease_seconds` 默认 60s）。同 key 遇到 NULL 响应：租约有效→短等赢家；租约过期→**CAS 接管**（`UPDATE ... WHERE response IS NULL AND lease_until<=now`，rowcount=1 者获得执行权）→重跑→回填。效果安全性由命令本身的合并语义保证（start 有唯一槽位、stop 幂等）。handler 异常仍即时释放占位。
3. **启动清扫**：daemon 启动时 `idem_startup_sweep()` 删除租约早已过期的 NULL 存根（防御从旧版本升级带来的存量）。
4. **故障注入（真实路径，非 SQL 探针）**：
   - `idem_kill_in_tx`：快路径 handler 完成、UPDATE 回填**前** `os._exit(70)`——验证整体回滚；
   - `idem_kill_after_reserve`：慢路径占位事务提交后、handler 前 `os._exit(70)`——验证孤儿接管。
   两点均加入 manifest 故障词表，通过 `control test fault set` 正常设置。

**修复过程中的连带缺陷（也修了）**：once 模式故障在事务内消费时，"解除武装"本身随事务回滚——击杀后故障仍然武装，重试再次被杀。修复：把击杀故障的解除移到**进入业务事务之前**用独立事务提交。

**回归**（`scripts/defect_def04.py`，15 项）：
- 真实击杀 → 用 ctypes OpenProgress 核实 daemon 进程确实死亡 → 重启（健康探针就绪）→ 同 key 重试恰一次执行（DB 里恰 1 个 kill-in-tx 任务）→ 第三次重放同 task_id；
- 慢路径：击杀后无实例（handler 未跑）→ 短租约（3s）到期 → 接管启动 1 个实例 → 再重放返回**逐字相同**的原始响应（同 instance_id + 同 operation_id——比"already_running"更严格的幂等证据）→ 全程恰 1 个活槽位；
- 同 key 异内容 `IDEMPOTENCY_CONFLICT`；跨主体同 key 隔离；20 并发同 key 恰 1 Task；结束时孤儿占位 0 行。

---

# 第三组 · DEF-03 / DEF-05 · 调度策略真实生效

## DEF-03：restart_backoff 死键 → 持久逐级退避

**根因**：`confirm_crash` 在确认崩溃的同一函数里**同步** `op_start_agent`——探针A测得 3 次崩溃均 ~0.13s 完成重启；把 backoff 配成 [300,600,900] 仍 0.129s。键全库无消费方（"参数有名无实"，违反 IMPLEMENTATION_PLAN §8.9）。

**修复**：
1. `confirm_crash` 只做三件事：置实例 failed、吊销凭据、**登记退避计划**——`kv restart:<agent_id> = {attempts, next_allowed_at}`，`next_allowed_at = now + restart_backoff[min(attempts-1, len-1)]`；同时发 `agent.restart_scheduled` 事件（带 attempts/backoff_s/next_allowed_at，可作证据）。限次检查（restart_max_per_hour，按最近 1h 的 `agent.started` 事件数）先行，超限发 `agent.restart_limit`。
2. `scheduler.py` 新增 `run_due_restarts()`（并入 tick）：到期计划才拉起；逐一校验 lifecycle=enabled、runtime.restart_on_exit、无活槽位；**先消费计划再启动**（防重入风暴）；agent 稳定存活 1h 后计数重置；计划对应 agent 已停/退役时清空。启动失败有 warning 日志，不无限循环。
3. 与其余机制衔接：paused/retiring 不拉起（lifecycle 检查）；重复崩溃事件（无活实例的 crash）不产生新计划；daemon 重启后计划在 kv 里仍在。

**回归**（`scripts/defect_def0305.py` 时钟驱动）：崩溃→`restart_scheduled=true, restarted=false, backoff_s=5`（不同步重启）；推进时钟 30s 后恰一次拉起（事件数 delta=1）；第二次崩溃 attempts=2 且 next_allowed_at 后移；连续崩溃+推进直到 `RESTART_LIMIT_EXCEEDED`；paused agent 崩溃后推进 10 分钟仍无实例；全程活槽位 ≤1。AC-RUN-07 驱动器也按新语义重写（不再假设同步重启）。

## DEF-05：唤醒丢弃后无对账 + 三个死键

**根因**：通知 3 连败即 DELETE，且唯一的恢复路径是"后续**新**事件"；`notify_retry_after`/`send_unknown_retry_after`/`notify_jitter` 全库无消费方。探针E：投递后推进 2h，任务永久 queued。

**修复**（scheduler.py）：
1. `run_reconcile_wakes()`（并入 tick）：扫描 queued 超过 `notify_retry_after` 且**无任何在途唤醒**的待办 → 为其候选重入队（个人目标直接入队；端口目标复用 `notify_new_task` 的完整候选逻辑——enabled+容量+decline 全部重新检查）；每任务 dedupe_key 带轮次号 `reconcile:<task>:<N>`（唯一键天然去重），最多 `notify_max_rounds`（默认12）轮——**有界，不会提醒风暴**；只产生 notification 行，**零新任务/回执/@通知**（有断言）。
2. `notify_jitter` 落地：`_queue_notif()` 为每条唤醒计算 `send_after = now + rand(0, jitter)`（迁移003：`ALTER TABLE notifications ADD COLUMN send_after`）；`run_notifications` 只处理 `send_after<=now` 的行——双订阅者错峰由数据结构保证。
3. `send_unknown_retry_after` 落地：发送失败（busy/unknown）不再"3 败即删"，而是 attempts+1 并把 send_after 推后该间隔（合并低频重试），仍 3 次后删除（唤醒可丢，reconcile 会兜底重入队）。

**回归**：用 **dummy-proc 真实发送失败路径**（send=unknown）验证 attempts≥1 且 send_after 推进；推进时钟后 reconcile 键出现、任务仍恰 1 条、无 receipt、无 notice 泛滥。

---

# 第四组 · DEF-06 · 验收脚本正确性与证据

逐项（全部落在 `scripts/acceptance.py`）：

1. **AC-TASK-08 翻页运气**：两处 `control task list` 后按 title 全表扫描改为——失败侧 `--root <parent> --limit 200` 过滤验证；成功侧拿 `refs["child2"]` 精确 ID `peek` 验证 + 重放同 ID 断言。不再依赖随机 ID 恰落第一页（独立复跑正是排在第 102 位而挂）。
2. **列表排序契约**（产品侧）：`tasks.visible_tasks` 从裸 `ORDER BY task_id` 改为 `datetime(created_at) DESC, task_id`——符合 DESIGN §12"排序有 ID 次级键"的稳定翻页要求（这不是为测试定制：created_at DESC 是任务账本的合理默认序）。
3. **空断言清零**：5 处 `assert ... or True` 全部转为硬断言——RUN-05（槽位释放后 start 必须返回**新**实例而非 already_running）；PROD-01（producer 投递前后 agent 总数不变——比"名字里没有-external"严格得多）；PROD-05（接收端同 delivery_id ≥1 次到达 + 账本恰 1 行 webhook 投递）；E2E-01（A 确实收到指向子任务的 @评论 notice，data.task_id 精确比对）。
4. **禁用分支清零**：3 处 `if False else`——SCHED-04 的"新订阅者可领取"补全真实流程（新建 S4-late agent + 订阅 + 授权）；STORE-01 的 wait 创建补真实执行（建任务→领取→wait create→断言 wtt_ 前缀）。
5. **AC-CTX-01 硬断言**：协议恰 1 次、个体说明加载、业务 md 加载、history 不递归、STARTUP.md 排除——全部从 note() 转为 assert。
6. **并发子进程入证**：新增 `run_concurrent(specs)`——每个并发 CLI 的 argv/exit/响应 ok/错误/task_id 逐条写入 commands.jsonl；TASK-02（20路claim）、RUN-01（20路start）、STORE-02（20路幂等）、HUM-03（双人并发抢答）全部改用。
7. **REC-04 真实路径**：删除 SQL INSERT 伪造迟到事件；改为真实 `test harness crash`（gen N 崩溃+退避计划留存）→ start gen N+1 → 断言新实例 live 不被旧计划影响 → 推进 10 分钟 → 活槽位仍恰 1。同时把 `event_duplicate` 故障点**真正实现**（Store.add_event 内重放同 event_id，PK 吸收，违反即抛错）——原来是枚举里有名字但零消费的又一个"有名无实"。
8. **schema 按 profile 精确断言**：test profile 恰 97（含 5 个 test 叶子）、另起 prod profile 辅助 daemon 恰 92/0。**顺带修出产品级真 bug**：`manifest.schema_export` 的 prod 过滤写的是 `c.path[1]`（对 dict 取 .path）→ 非 test profile 请求 /v1/schema 直接 500。改为在生成时过滤。
9. **Pass 判定审计**：`_Blocked` 机制保留并扩展（真实三案例的环境预检也走它），Blocked 绝不计 Pass。

---

# 第五组 · DEF-08 · 参数覆盖矩阵（签收条件2）

新增 `scripts/coverage_matrix.py`（1084 行）：24 个业务场景 × 全参数遍历，输出 `matrix.jsonl`（每行 = cmd/param/kind/status/detail）+ `summary.json`。

- **97/97 命令全部有业务执行记录**（含独立验收点名的 16 个：task trash --cascade、port.list/update、producer.show/update/revoke/list、trigger.list/update、channel.list/show/update、human.list、harness.list/show、system.serve/start/stop…）；
- **384 个参数声明项逐项覆盖**：合法值+实际效果、默认值、必填缺失、互斥、非法枚举、int 边界、文件输入（body-file/data-file/text-file/spec-file/dispatch-file/plan-file/task-file）、权限拒绝、旧版本（if-version/spec-version/claim/instance）、幂等重放与失败无副作用；
- **特别项专项**：`--cascade`（级联处置 affected≥2）、`--apply` 四种模式、`--dry-run` 无写入、`--force/--grace` 真实停止过程、`--missed` skip/once、`--timezone` UTC 与 +08:00 各建一个 cron、`--replace`（完整配置替换，用专用目标 agent——避免把 fixture A 的授权洗掉）、`--fallback`、`--include-declined`、`--mark-seen`、备份三开关；
- 最终一版 **205/205 行 Pass、0 失败场景**。过程中的真实产品级发现（已修）：`--replace` 部分文件会整体替换配置——这是**设计语义**（DESIGN CMD-030"完整配置替换"），矩阵改为用全量配置+专用 agent 验证，并保留"部分文件 replace 是破坏性操作"的事实记录。

---

# 第六组 · DEF-07 · 受管测试资源收尾

1. **29963 遗留 daemon**（PID 34464，b2288d3 验收残留）：用**它自己 home 的 owner 凭据**执行 `control system stop`——受管路径，不按 PID/进程名裸杀；证据 `work/cleanup/DEF-07-cleanup-20260930.json`。同法清理了本修复轮早期的 29962/54501 两个同轮遗留。
2. **驱动器隔离改造**（治本）：
   - run-id = 时间戳 + 随机后缀（两轮同秒不再冲突）；
   - 端口 `_pick_free_port()` 动态探测（主端口 + PORT_B/PORT_C 辅助端口都 run 唯一；辅助 daemon 启动器带换端口重试）；
   - `run-manifest.json` 登记 daemon pid/home/endpoint/真实 harness agent/webhook 端口；
   - teardown 顺序：先对 manifest 中每个**真实 harness agent** 执行受管 `control agent stop`（账本与事实一致），再逐 home 受管 stop daemon，结果全部写 cleanup_log；
   - `control system stop` 语义**未改**（默认不停 Agent 实例——设计允许 Hive 重启接回活实例）；
   - quicktest：唯一 home（`tmp/quicktest-<port>`，占用即再随机）+ 动态端口。
3. 收尾复核：netstat 无验收端口 LISTEN 残留；24 个测试 zsession 会话（hive-*/probe/watch 系列）经 API kill+archive，活跃列表 0。

---

# 第七组 · DEF-09 / DEF-10 / DEF-11

## DEF-09 名片可见性（分类处理，不一刀切）

- **保留开放**：`control port list` / `control agent list`——DESIGN §5.2 明确 `visibility=authenticated`、§9.1 有 `port.discover`/`agent.discover` 动作，公开名片属既定发现能力；
- **收紧**：`producer.list` / `trigger.list` / `channel.list` / `harness.list` 是**控制面清单**（暴露集成面与运行配置），分别加 `producer.manage` / `trigger.manage` / `channel.manage` / `system.observe` 门槛——最低权 Agent C 调用被拒、owner/observer 不受影响（CTRL-05 复验通过）；
- show 类不放凭证正文、私有 payload 不变的规则未动。

## DEF-10 interrupt/steer 统一 + smoke 证据强度

- `op_send_agent`：`native_interrupt = caps.get("steer") or caps.get("interrupt")`——zsession spec 声明的 `interrupt:true` 从此**真正被使用**：命中时对 zsession 调 `/send mode=interrupt`（Registry.send 增加 mode 透传），不再恒走 reject/restart 降级；无该能力的 harness 仍按最小契约接入（AC-HAR-01 复验）。
- smoke 回读：yolo 证据改为**会话 mode 回读**（zsession GET /sessions），model_at_start 改为**会话 model 回读比对**（modelId 与请求一致才算过）；适配器无回读能力时如实标注。
- **新增 `zsession_env_preflight()`**：真实 spawn → 等待该会话的 `message_done` 事件（可配超时）。这是本修复轮最重要的诚实性改进：它把"管路通"（spawn/send/kill 都 200）与"agent 真的能执行回合"区分开。三个真实案例（HAR-02/E2E-02/E2E-03）预检失败时如实 **Blocked**——本轮环境诊断正是靠它坐实的（见下"环境诊断"）。

## DEF-11 杂项（全部处理）

1. `control.system.restore`：服务端占位改为**明确的 local-only 拒绝**（INVALID_ARGUMENT + local_only 提示与本地命令写法），不新增远程恢复能力；本地离线恢复经 AC-STORE-03 + 覆盖矩阵双验证；
2. `notify_jitter`：已实现（见 DEF-05）；
3. `visible_tasks` 排序：已修（见 DEF-06）；
4. `confirm_crash` 残留 `if False else` 占位行：已清除；
5. 双层 `runtime\runtime\credentials` 路径观感：简报迁 runtime/startups 后该路径不再产生；源码审计确认无双层拼接；
6. （扩展）`handlers_task.py` 等**全部产品代码中的 `if False` 残留清零**（grep 0 处）。

---

# 第八组 · 修复过程中连带发现并修复的产品级缺陷（独立清单之外的）

这些是修 DEF 时被回归套件逼出来的真 bug，均修复并有证据：

| # | 缺陷 | 修复 | 暴露来源 |
|---|---|---|---|
| A | `schema_export` prod 过滤对 dict 取 `.path` → 非 test profile 的 /v1/schema 500 | 生成时过滤 | DEF-06 schema 精确断言 |
| B | 陈旧 `running` 启动操作（outcome_unknown）**永久阻塞**后续 start（accepted_only、instance_id=None） | 槽位已空时把陈旧 running 启动操作标记 failed（RUNTIME_OUTCOME_UNKNOWN），start 只合并真正 accepted 的 | RUN-05 复发 |
| C | once 故障在注定回滚的事务内"解除武装"→ 击杀后故障仍武装，重试再被杀 | 击杀故障解除移到业务事务前独立提交 | DEF-04 击杀测试 |
| D | `_idem_reserve_leased` 用 `now_utc().timestamp()+lease` 喂给期望 datetime 的 format_rfc3339 → AttributeError | timedelta 计算 | DEF-04 首跑 |
| E | lease_until 无列 / send_after 无列（两张迁移缺失） | 迁移002/003（幂等可用 ALTER 原位升级，旧库无损） | DEF-04/05 |
| F | `_queue_notif` 定义在使用之后、`dedupe_prefix` 参数未入签名 | 重排 + 签名补全 | DEF-05 首跑 |
| G | `handlers_task` 两处死代码（`if False` 三元、永假 spec 展开） | 清除 | DEF-11 清理 |
| H | teardown `_stop_home` 传不存在的 `expect_ok` 形参 / 返回 tuple 未解包 | 修正 | manifest teardown 首跑 |

---

# 第九组 · 真实 ZCode 环境诊断（如实声明）

修复后期真实三案例反复失败，逐层排查：

1. zsession daemon 健康（/health OK、spawn/send/kill/模型回读全部正常）；
2. 会话注册成功、消息投递 accepted、队列会被消费；
3. 但**回合执行层损坏**：事件流自 seq≈719 起再无任何 `message_done`；10+ 个对照探针（GLM-5.3 / GLM-5.3-Flash / 3 类工位 / 有无 prompt / 90s–5min 观察窗）全部秒级进入 dead，包括"只回复：收到"这种最小指令；
4. 因此 HAR-02 此前的 Pass 只是**管路证据**（spawn/send/model 回读/stop）——这恰好印证独立验收 DEF-10 对 smoke 证据强度的批评。

处理：`zsession_env_preflight()` 上线后，三个真实案例按预检结果**如实 Blocked（外部条件）**，预检证据（探针会话 ID + 失败原因）写入各自 assertions.jsonl。未重启/未修改用户 ZCode 桌面宿主与 zsession 全局配置（那会影响用户环境，且桌面宿主可能承载当前会话）。环境恢复后重跑命令见 README「验收复现」。

---

# 第十组 · 新增案例：AC-E2E-03 任务中途换实例续作

按独立验收"补做真正的任务中途换实例续作"要求新建（`scripts/acceptance.py`），七步全硬断言：

1. 实例 1（真实 ZCode）领取任务——轮询账本直到 `state=claimed`，断言责任建立；
2. 实例 1 生成 checkpoint——**独立**读取 CHECKPOINT.md，断言内容含 E2E03 标记 + 实例 1 ID（不由脚本代写）；
3. 任务必须仍非终态才允许 stop；
4. 同 agent_id 启动实例 2——断言新 instance_id ≠ 旧；旧实例凭据调用被 TOKEN_REVOKED/INSTANCE_FENCED 拒绝；
5. 实例 2 依据 checkpoint 续作——断言 RESULT.md 真实存在且**同时引用两个实例 ID**；
6. 实例 2 通过 Hive 提交 done——断言 `ended_by=agent:<id>`；
7. 连续性对照——checkpoint 在换实例后仍在盘、任务/文件/实例三者互相印证。

案例内置环境预检；当前因 ZCode 执行层损坏如实 Blocked，**没有任何一步由脚本代写或以"Agent 指令遵循问题"替代证据**。

---

# 验证方法与不变量保护声明

- 所有缺陷回归脚本独立于验收驱动器（`scripts/defect_*.py`），各自隔离 home/端口，检查结果落 `work/defects/<def>-<ts>/checks.json`；
- 可控时钟驱动所有间隔断言（退避步长、对账重入队、jitter 窗口），不靠真实睡眠；
- 真实进程击杀（`os._exit(70)` 注入）验证崩溃路径，SQL 只作只读白盒探针（驱动器内 15 处 sqlite 使用逐一审计为 read-only）；
- 核心不变量未被削弱：20 并发 claim/start/幂等在覆盖矩阵与缺陷套件中复验；终态/回调/等待/权限围栏 88/88 复验；trigger-platform 19961 全程在监听未触碰。


---

## 附录：第二轮更正（2026-09-30）

1. **原归因"ZCode 回合执行层损坏"错误。** 第二轮独立复验（RDEF-02）与第二轮返修
   共同证实：zsession 管路与回合执行层均正常——直连探针会话完成回合并实际产出标记
   文件；经 Hive 适配链的真实 E2E-02 亦真实通过。本文所述"事件流自 seq≈719 起无
   message_done"是**错误判据**：当时探针会话从未真正开始执行（spawn 即被
   bad_workspace 拒绝），自然不会有该会话的 message_done；以全局事件流缺乏某类事件
   作为唯一判据本身就是预检方法缺陷。
2. **Blocked 的真实原因：** 预检函数 zsession_env_preflight 的 spawn 请求携带了
   从未创建的工位目录（%TEMP%\zs-preflight-ws），zsession 依契约返回
   HTTP 400 bad_workspace，预检把自身的请求构造错误当成了环境不可用。
3. **处置：** 预检已重写（先创建探针工位、三态返回 ok/blocked/defect、以本会话
   产物/状态推进/message_done 三重证据判定、预检自身缺陷一律记案例 Fail 而非环境
   Blocked）。三个真实案例在本轮返修后按新预检真实执行，结果见
   SECOND_REGRESSION_REPORT.md（原 Blocked 证据文件保留不动）。
4. 本文其余内容（缺陷修复、确定性回归、幂等/调度/工位等）不受该归因影响。
