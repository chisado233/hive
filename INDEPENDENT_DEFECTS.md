# Hive V1 独立验收缺陷清单（INDEPENDENT_DEFECTS）

- 验收Agent：独立验收（非施工方）
- 日期：2026-09-30
- 代码基准：`b2288d3`（= `cc47a98` 代码 + REPORT.md），工作区干净
- 严重度：Blocker（阻止签收）/ High（签收前必须修）/ Medium（应修，可带条件签收）/ Low（记录在案）
- 证据根：`work/independent/20260930-003405/`（探针A–E）、`work/acceptance/20260930-003644/`（88案例独立复跑）、`work/acceptance/20260930-003816/`（真实双案例独立复跑）、`work/independent/<ts>F/`（探针F）

---

## DEF-01 · High · 真实harness工位缺失系统协议与个体说明（工位派生文件split-brain）

- **规范条款**：DESIGN §3.3（工位固定文件 agent.md/config.json/prompt.md）、§3.2（派生视图随有效版本发布）；REPORT §2.3/§5.5（"spawn prompt = 系统协议 agent.md + 个体 prompt + …"）。
- **复现步骤**：
  1. `control agent create --file` 指定 `workspace.path`（真实harness的标准用法，AC-E2E-02 即如此）。
  2. `control agent start`。
  3. 检查该工位目录与 `home/workspaces/<agent_id>/` 两处文件。
- **预期结果**：配置的工位目录内生成 agent.md/config.json/prompt.md；启动简报含系统协议与个体说明各一次。
- **实际结果**：`_rebuild_workspace`（hive_core/agents.py）无视 `cfg.workspace.path`，恒写入 `home/workspaces/<agent_id>`。显式工位内三文件永远缺失；`_build_startup_context`（runtime_ops.py:83-88）从配置工位读 agent.md/prompt.md 落空 → **启动简报（STARTUP.md/spawn prompt）不含任何系统协议与个体说明**。
- **证据**：
  - 探针B（work/independent/20260930-003405/probes/B_startup_md_duplication.json）：`protocol_header_count=0`，工位仅 CONTEXT.md/STARTUP.md；
  - 施工方真实E2E工位 `%TEMP%\hive-e2e-ws-20260929-191540\STARTUP.md`（1935字节）：`# Hive Agent 系统工作协议` 出现0次、`个体工作说明` 出现0次 —— REPORT §2.3 的声明与其实际证据不符。
- **影响**：真实Agent从未收到系统工作协议（三入口规则、终态纪律、凭证纪律），全靠wake消息逐条指令补救；DESIGN 工位契约对真实通道未兑现。fixture案例（AC-ID/AC-CTX）只用默认工位agent，未覆盖此路径，故90/90未发现。
- **回归建议**：`_rebuild_workspace`/`ensure_workspace` 以配置工位为准；AC-E2E-02 增加断言：spawn prompt 含协议头与个体说明各恰一次。

## DEF-02 · High · STARTUP.md 反馈循环：重复加载逐次增长、携带已吊销旧实例凭据路径与过时摘要

- **规范条款**：DESIGN §3.1（exclude 默认仅 agent.md/prompt.md；系统派生文件不应混入业务md）、§3.3（加载顺序，不重复注入）；§2.2（凭据不进 prompt——文件路径虽非正文，但已吊销路径属过时运行信息）。
- **复现步骤**：真实harness（process/zsession）agent：start → stop → start → start；每次检查工位 `STARTUP.md`。
- **预期结果**：每次启动输入=协议+说明+业务md各一次+当前摘要；不包含上一轮简报、不包含已失效实例凭据路径。
- **实际结果**：STARTUP.md 写在工位根且匹配 `*.md` glob、不在 exclude 内 → 下次 gather_context 把旧 STARTUP.md 当业务md整体吞入，再覆写为新 STARTUP.md：
  - 探针B：965 → 1965 → 2965 字节线性增长；`运行接入信息` 1→2→3 次；第2轮起包含**已吊销旧实例凭据文件路径**；
  - 施工方真实E2E工位：`--- 工位文件 STARTUP.md ---` 嵌套1层、`责任摘要`×2（新旧并存）、同时含 ins_906b…（已吊销）与 ins_a210… 两个凭据路径、且把上一轮产物 `output.md` 当上下文再次注入。
- **影响**：多次重启后启动输入无界增长（最终触发 CONTEXT_TOO_LARGE）、新旧指令/摘要混淆、Agent可能拿已吊销凭据路径调用失败；`output.md` 等产物被无条件回流为上下文。
- **回归建议**：STARTUP.md 移出业务md区（存 `runtime/` 或加入默认 exclude）；启动简报不落工位根；补"连续3次重启，协议头恰3×1次、无旧实例路径"断言。

## DEF-03 · Medium-High · restart_backoff 未实现且为死配置键（即时重启）

- **规范条款**：DESIGN §10.1（restart_backoff 默认 5s,15s,60s，"已确认退出的实例重启退避"）；AC-RUN-07 步骤1（"按退避策略恢复"）。施工方已在 REPORT §6 / PROGRESS 如实声明"仅限次未做逐级间隔"。
- **复现步骤**：fixture agent `control test harness <A> --action crash`；或将 `restart_backoff` 配为 [300,600,900] 后再 crash。
- **预期结果**：确认退出后按 5s/15s/60s 逐级退避再重启；调大配置后退避变长。
- **实际结果**：`confirm_crash`（runtime_ops.py:514-554）同步立即 `op_start_agent`。探针A：3次crash均 ~0.13s 完成重启，账本 started_at 同秒；设 backoff=300s 后仍 0.129s 即重启 —— `restart_backoff` 键（service.py:28）全库无消费方，属可配置但不生效的死键。`restart_max_per_hour=3` 限次生效正常。
- **影响**：崩溃风暴时无退避，放大宿主/zsession负载；配置面存在"写了不生效"的键，违背"参数不得有名无实"（IMPLEMENTATION_PLAN §8.9）。
- **为何90/90未发现**：AC-RUN-07 驱动断言只查"有重启"与"超限停止"，未断言间隔。
- **回归建议**：实现逐级退避（含配置消费），AC-RUN-07 增加"重启间隔≥backoff[0]"计时断言。

## DEF-04 · Medium · 幂等占位孤儿永久阻塞（daemon在占位后、回填前崩溃）

- **规范条款**：ACCEPTANCE 二、C项要求（占位后崩溃可恢复，不留永久"处理中"）；DESIGN §16（STORAGE_UNAVAILABLE 引导同request-id核查——占位残留使该路径失效）。
- **复现步骤**：以 request-id=X 成功执行一次 `emit new`；直接将该 idempotency 行 `response` 置 NULL（等价于"占位已提交、回填前进程死亡"的账本状态）；再用同 key 同参数重试两次。
- **预期结果**：重试可恢复执行或明确告知占位可清理；不应永久阻塞。
- **实际结果**：探针C-c4：每次重试等待 40×0.25s=10.2s 后返回 `STORAGE_UNAVAILABLE(retryable)`，第二次依旧；无 daemon 启动清理、无 TTL —— 该 request-id 永久不可用（service.py `_idem_reserve_or_replay` 仅在 handler 异常时释放占位，进程死亡路径无恢复）。
- **影响**：客户端按设计指引"用相同request-id重试"将永远失败，只能人工改库；高频崩溃场景会积累死键。
- **回归建议**：daemon启动时清理 `response IS NULL` 且超过阈值的占位；或占位带 lease 时间戳，过期可被同key请求接管。加故障注入案例覆盖"占位后进程被杀"。

## DEF-05 · Medium · 唤醒信号丢弃后无对账重入队；notify_retry_after / send_unknown_retry_after / notify_jitter 为死配置键

- **规范条款**：DESIGN §10.1（notify_retry_after=5m "未领再核实"；reconcile_interval 对账）；REPORT §5.3 声明"连败3次丢弃，由后续事件重新入队"。
- **复现步骤**：process-harness agent（send必败）订阅端口；投递任务；等待3次调度失败（~12s）后推进时钟2h观察。
- **预期结果**：唤醒可丢，但定期对账应能发现待办并恢复（重新通知/冷启动轮转）；notify_retry_after 生效。
- **实际结果**：探针E：通知行3败后被 DELETE；时钟推进2h后 notifications 仍为空、任务永久 queued、agent 既有实例也不被再次唤醒；仅产生 `task.unclaimed` 系统事件（可查询）——"恢复待办"链路不存在，只有"后续**新**事件"路径。三个策略键全库无消费方。
- **影响**：单条投递的唤醒若三连败且无后续事件，任务静默滞留（有系统观测事件可查，但不自愈）；REPORT"可丢可重发"的"重发"仅指新事件触发。
- **回归建议**：对账扫描对超时未领任务按 notify_retry_after 重建通知（幂等键去重）；或在文档中明示该行为并移除死键。

## DEF-06 · Medium · "90/90"不可稳定复现：AC-TASK-08 断言依赖task_id字典序运气；驱动脚本存在弱化断言与证据缺口

- **规范条款**：ACCEPTANCE §7.2 签收条件1（90例实际状态）；§2.4（证据保存）；§7.1（状态不虚报）。
- **复现步骤**：在不设 `HIVE_REAL_HARNESS` 的干净环境全量复跑 `python scripts/acceptance.py`。
- **预期结果**：确定性88例全部复现通过。
- **实际结果**：本验收复跑（run 20260930-003644）= **87 Pass / 1 Fail（AC-TASK-08）/ 2 Blocked（真实双案例，未设环境属预期）**。失败点：`control task list`（按 task_id 字典序、默认limit=50，handlers_core.py `visible_tasks`）首页是否包含 `batch-child2` 取决于随机ID字典序——施工方run中排第28位（过），本复跑排第102位（挂）。产品本身无重复造单（两run DB均恰1条 batch-child2）。
- **伴随证据质量问题**（不改变产品判定，但削弱"90/90"证明力）：
  1. 空断言5处：`assert … or True`（acceptance.py 行179/2048/2418/2658/3402）；
  2. 禁用分支3处：`if False else`（行2776/2777/3232），AC-STORE-01 的wait创建、AC-SCHED-04 的授权分支被跳过；
  3. AC-CTX-01 步骤2的关键预期（协议一次/history不递归）仅 note() 记录未断言；
  4. 并发调用（AC-TASK-02/STORE-02/RUN-01/HUM-03 的20路Popen）不落 commands.jsonl，仅存汇总note；
  5. AC-REC-04 直接 SQL INSERT events 模拟迟到事件（测试造数，非伪造通过，但违反"不能以直接SQL改库"的字面约定）；
  6. AC-CLI-01 的 schema 数量断言放宽为 97 或 92 均可。
- **回归建议**：分页断言改用 `--root`/过滤或翻页取全量；删除空断言与禁用分支；并发调用入证；AC-CTX-01 预期转硬断言。

## DEF-07 · Medium · 验收清理声明不实：29963 遗留验收daemon仍在运行

- **规范条款**：ACCEPTANCE §8（收尾须停止当前run创建的测试daemon）；REPORT §7 / PROGRESS（"验收 daemon 已全部停止（残留仅 TIME_WAIT）"）。
- **复现步骤**：`netstat -ano | findstr 29963`；查进程命令行。
- **实际结果**：PID 34464 = `python cli/hive.py --home …work/acceptance/20260929-180325/runtime control system serve --listen 127.0.0.1:29963 --test-mode`，验收后长期存活（本验收已按"保留失败现场"原则未动）。另：施工方终验run的E2E实例2在账本中遗留 `phase='live'`（daemon停止未终结实例状态，靠外部kill会话收尾）。
- **影响**：环境清理承诺未兑现；占端口、留活进程。
- **回归建议**：交付前逐run核对进程清单；`system stop` 时对活跃真实实例执行受管stop或至少标记账本状态。

## DEF-08 · Medium · 签收条件2未满足：参数级覆盖为抽样，16/97命令无业务执行证据

- **规范条款**：ACCEPTANCE §4（每命令须有真实执行记录）、§5.2（384个本地参数逐一检查记录）、§7.2 条件2；§5.3 清单。
- **实际结果**：
  - 最终run（20260929-191540）commands.jsonl 中仅 **81/97** 命令有业务执行记录；`control.task.trash（--cascade 从未执行）`、`control.port.list/update`、`control.producer.show/update/revoke/list`、`control.trigger.list/update`、`control.channel.list/show/update`、`control.human.list`、`control.harness.list/show`、`control.system.serve` 共16项仅有 AC-CLI-01 的 --help 级检查（且help执行未逐条入证）。
  - AC-CLI-02 实测为抽样：**17条命令/8条断言**（脚本自述"抽样覆盖每类"），对照 §5.2 的 384参数×测试种类矩阵缺口巨大；§5.3 多数项（--apply/--dry-run部分/--force/--grace/--missed/--timezone/--cascade等）无专项证据。
- **影响**：按 ACCEPTANCE §7.2，签收条件2不成立；REPORT"90/90"不能证明参数级合同完成。
- **回归建议**：生成参数矩阵驱动器逐参数留证，或与owner正式协商裁剪 §5.2 范围并记录变更。

## DEF-09 · Low · producer/trigger/channel 名片对任意已认证主体可见

- **规范条款**：DESIGN §9.1（跨节点 control 需显式 allow）；施工方已在 REPORT §6/PROGRESS 声明为已知限制。
- **实际结果**：探针E以最低权 agent C 调用三个 list 全部成功（producer 1条、channel 2条名片）。名片不含任务正文/凭据；未见越权私有信息。
- **回归建议**：如设计本意收紧，增加 system.observe 门槛；维持现状则同步 DESIGN 用词。

## DEF-10 · Low · zsession 声明的 interrupt 能力未被使用；smoke 的 yolo/model 证据偏弱

- **规范条款**：DESIGN §4.3（可缺增强不判失败——故不构成失败）；examples/harness-zcode-zsession.json 声明 `interrupt:true`。
- **实际结果**：`op_send_agent`（runtime_ops.py:297-332）以 `caps.get("steer")` 判定原生打断，zsession spec 键名为 `interrupt` → 真实通道 `--mode interrupt` 恒走 reject/restart 降级，声明的原生能力未接。HAR-02 smoke 的 yolo 证据为"started with yolo=True"（适配器硬编码 mode:yolo，未回读会话核实）；model_at_start 证据为"以该模型重启成功"，未核实会话实际模型。
- **回归建议**：caps 键名对齐或在 spec 中同时声明 steer；smoke 增加会话回读（GET /sessions/{sid} 的 mode/model）作为证据。

## DEF-11 · Info · 杂项记录

1. `control.system.restore` 服务端注册为 NOT_IMPLEMENTED 占位（真实实现走 CLI 本地路径，功能可用；IMPLEMENTATION_PLAN P09"无NOT_IMPLEMENTED占位"字面未满足）。
2. `notify_jitter` 策略键无消费方（通知未实现抖动错峰）。
3. `visible_tasks` 仅按 task_id 排序（DESIGN §12 "排序有 ID 次级键"，主键缺失使 control task list 默认序对人类不友好，亦诱发 DEF-06）。
4. `confirm_crash` 内残留无意义占位行（runtime_ops.py:533-534 `if False else` placeholder）。
5. 真实E2E工位的接入信息中凭据路径出现 `runtime\runtime\credentials` 双层拼接（功能可达，观感瑕疵）。

---

## 未发现问题的事项（独立复核通过）

- 20并发同key幂等唯一Task；同key异内容 IDEMPOTENCY_CONFLICT；跨主体隔离（探针C c1–c3）。
- 20并发 start 合并（1新+19 already_running，0错误，DB唯一活槽）；20并发 claim 唯一跃迁/唯一claim/唯一事件（探针D）。
- spawn→use 404 竞态有就绪轮询与重试，不会双开会话（harness.py ZSessionAdapter）。
- stop 结果未知保留槽位、不启替身；restart 遇 unknown 不启动；daemon 重启核对旧实例不双开（AC-RUN-05/REC-03 复跑通过）。
- 旧实例凭据/旧claim 围栏（TOKEN_REVOKED/CLAIM_FENCED/TASK_TERMINAL，独立复跑与真实E2E复验均过）。
- emit batch / done+dispatch / comment+@ / create+wait 单事务原子，内层异常穿透回滚（代码复核+AC-TASK-08/WAIT-06/REC-01 复跑）。
- 无 callback 不向发送者推终态通知；forward 保留 Task ID 与回调目标；等待环拒绝、结果先到即 ready、父终态取消自身等待（对应案例复跑通过）。
- 真实 zcode-zsession 双案例（AC-HAR-02 五能力smoke、AC-E2E-02 端到端）在本验收独立复跑通过：真实Agent自行 claim+emit done（ended_by=agent:agt_5e31…），output.md 与输入逐字一致。
- 凭据正文零泄露复核（stdout/日志/工位/prompt 扫描；token 仅存于受保护文件与sha256）。
- trigger-platform（19961）验收前后均在运行，未被触碰。
