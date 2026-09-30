# Hive V1 施工进度

状态基准：DESIGN.md / IMPLEMENTATION_PLAN.md / ACCEPTANCE.md。
最后更新：2026-09-30（V1 交付完成）。

## 最终状态

| 阶段 | 状态 | 结果 |
|---|---|---|
| P00 隔离骨架 | **已完成** | init/serve/start/stop/status/单写者锁/日志脱敏/Clock |
| P01 数据模型 | **已完成** | 97命令manifest、迁移、幂等（AC-ENV/CLI 通过） |
| P02 身份权限 | **已完成** | AC-ID×5 / AC-AUTH×4 / AC-CTX×3 全过 |
| P03 Task核心 | **已完成** | AC-PORT×4 / AC-TASK×8 全过（20并发claim唯一owner） |
| P04 评论/期限 | **已完成** | AC-MSG×5 / AC-TIME×4 全过 |
| P05 回调/等待 | **已完成** | AC-CB×5 / AC-WAIT×6 全过（batch/dispatch原子） |
| P06 运行控制 | **已完成** | AC-RUN×7 / AC-HAR-01、03 全过 |
| P07 人工/生产者 | **已完成** | AC-HUM×4 / AC-PROD×5 全过 |
| P08 调度/恢复 | **已完成** | AC-SCHED×5 / AC-REC×6 全过 |
| P09 管理控制面 | **已完成** | AC-CTRL×5 / AC-STORE×4 全过；真实harness接入完成 |
| P10 完整验收 | **已完成** | 88确定性案例全过 + 真实harness双案例通过（见最终报告） |

## 真实 harness 接入（按用户指示：zsession / ZCode）

- **zcode-zsession**（主通道，`examples/harness-zcode-zsession.json`）：
  适配本机 zsession 服务（http://127.0.0.1:8765，Bearer owner-local token
  运行时读取 ~/.zcode/zsession/config.json，不进spec/日志）。
  五能力映射：spawn=启动、/send=发消息(queue/interrupt)、mode=yolo、
  spawn选模型+(/model 热切换)、kill=终止。另有 events/inspect 增强。
- **AC-HAR-02 五能力smoke 通过**：start(handle=sess:..)/send(accepted)/
  yolo/model_at_start(换GLM-5.3-Flash重启)/stop(stopped)，overall=passed。
- **AC-E2E-02 真实端到端通过**：真实 ZCode Agent（temp 隔离工位）通过
  STARTUP.md（agent.md协议+prompt+责任摘要+CLI接入信息）与 wake 指令，
  真实执行：读 input.txt → 写 output.md → `hive inbox claim` → `hive emit done`。
  证据：task state=done、ended_by=agent:agt_xxx、output.md 内容与输入一致；
  随后 stop→start 换实例同 agent_id，旧实例凭据 TOKEN_REVOKED。
- **opencode-run**（备选通道，`examples/harness-opencode-run.json`）：
  本机 opencode CLI 无头模式；send 为全新会话，连续性由工位文件承载；
  五能力smoke 同样通过（模型 mimo-v2.6-flash-free / space-bunny-free）。

## 验收证据位置

- 每例：`work/acceptance/<run-id>/<CASE>/commands.jsonl + results.jsonl +
  assertions.jsonl`；汇总 `work/acceptance/<run-id>/report.json`。
- 最终全量回归 run-id 见 `work/acceptance/` 最新目录（90案例）。
- 快速链路：`python scripts/quicktest.py`（通过）。

## 关键实现决定（累计）

1. Agent/producer 主体ID镜像对象ID；凭据文件 {subject,token}，库内只存 sha256。
2. SQLite 列名 `refs`（references为保留字）；对外仍输出 references 语义。
3. `Store.write()` 可重入（嵌套加入外层事务）→ emit batch / done+dispatch 原子。
4. 手写 INSERT 全部收敛为 `insert_task()` 助手。
5. claim 部分成功：ok+partial=true，CLI exit 6。
6. spec-file 互斥在 CLI 预检与 manifest 校验双层实现（spec组成员彼此不互斥）。
7. CLI 子解析器全局参数 SUPPRESS；命令定位用最长连续路径匹配。
8. 每线程独立 SQLite 连接（WAL）：消除跨线程读写串扰（曾致 AUTH-01 偶发）。
9. 通知（唤醒信号）可丢可重发：notifications 按 (agent_id,dedupe_key) 合并，失败3次丢弃。
10. 时钟偏移持久化 kv.clock_offset；test clock advance 触发即时扫描。
11. settle 顺序：终态检查先于旧claim围栏（已终态任务改写→TASK_TERMINAL）。
12. 真实 harness 的启动简报=协议+prompt+工位md+实时责任摘要+CLI接入信息，
    写入工位 STARTUP.md；zsession 以其为 spawn prompt，process 以其为首条消息。
13. zsession 适配器对 spawn→use 竞态做就绪轮询与 404 重试。
14. 退役回调交接用 json_extract 精确匹配 target（避免 handover 元数据误匹配）。

## 遗留与限制（如实）

- AC-HAR-02/AC-E2E-02 的真实证据绑定本机 zsession/opencode 环境；
  换环境需重跑（命令见 README「验收复现」）。
- scheduler 的 restart_backoff 序列仅用于限次（restart_max_per_hour），未做逐级间隔。
- `h_producer_list`/`h_trigger_list`/`h_channel list` 对已认证主体可见
  （仅名片级信息）；如需收紧可加 system.observe。
- trigger-platform 线上环境全程只读参考，未做任何修改（netstat/目录核对留证于
  AC-ENV-01/02 命令记录）。

## 最终回归结果（2026-09-30）

- run-id `work/acceptance/20260929-191540`：**90/90 Pass**（单次完整运行，
  含真实 zsession/ZCode 的 AC-HAR-02 五能力smoke 与 AC-E2E-02 真实端到端）。
- 期间发现并修复两处真实缺陷：
  1) 幂等键竞态：并发同键请求各自执行 → 改为 INSERT 预占位 + 执行后回填
     + 失败释放（AC-STORE-02 20并发只产生一个Task）。
  2) 停服/重启竞态：cmd_start 在旧 daemon 退出途中失败后不再落到拉新
     → 持锁者死亡时重试整轮（AC-REC-02/03 稳定）。
- 测试环境清理：验收 daemon 已停止（29962 仅 TIME_WAIT 残留）；本轮创建的
  21 个 hive-* zsession 会话已 kill+archive（活跃列表 0）；trigger-platform
  19961 全程未触碰。


## 修复轮（独立验收 DEF-01..11，基准 b2288d3）

- 环境隔离：唯一run-id/端口/manifest；quicktest唯一home/端口；29963遗留daemon
  已受管停止（work/cleanup/DEF-07-cleanup-20260930.json）。
- DEF-01/02：resolve_workspace 统一工位；STARTUP.md 迁 runtime/startups；25/25。
- DEF-03：崩溃登记持久退避计划 + run_due_restarts 消费 backoff/限次；12/12。
- DEF-04：快命令「占位+执行+回填同事务」；慢命令 lease 接管；真实击杀 15/15。
- DEF-05：run_reconcile_wakes 对账重入队；jitter/send_after/retry_after 真实生效；12/12。
- DEF-06：TASK-08 翻页稳健、弱断言/禁用分支清零、并发逐子进程入证、REC-04 真实
  路径、schema 按 profile 精确；顺带修 schema_export 过滤与陈旧启动op两处产品级bug。
- DEF-08：coverage_matrix.py —— 97/97命令、205/205行、384参数声明全部业务执行。
- DEF-09/10/11：控制面清单加 system.observe；steer|interrupt 键统一+zsession原生
  interrupt；smoke 会话回读 + zsession_env_preflight（turn 完成才算真实可用）；
  restore local-only 语义；占位/排序/双层路径清理。
- AC-E2E-03：新增真实“任务中途换实例续作”案例（实例1领取+checkpoint→停→实例2
  续作→done），含 env 预检。
- 最终回归：88 Pass + 3 Blocked（ZCode 执行层损坏，预检证据齐全，如实声明）。
- 详细报告：FIX_REPORT.md / REGRESSION_REPORT.md。

## 2026-09-30 第二轮返修（RDEF-01..10，基准 c409490）

- 入库第二轮独立复验报告（INDEPENDENT_REACCEPTANCE_*.md，他人工作原样）。
- RDEF-01：幂等分类反转（仅7个外置命令，create/grant等全走原子单事务）+
  executor_gen 执行者围栏（迁移004）+ 孤儿占位审计核实拒绝/干净接管；真实五窗口
  击杀验证 35/35。
- RDEF-02：预检根因=spawn 携带未创建工位（400 bad_workspace），执行层实测正常；
  预检重写（三态 ok/blocked/defect、产物/状态/事件三重证据、预检缺陷记 Fail）；
  三份旧报告追加强更正附录（保留历史证据）。
- RDEF-03：退避计划状态机（scheduled/starting/failed/awaiting_intervention，kv持久，
  消费=状态迁移不删计划）+ 冷启动退避门控 + restart_plan 观测面；25/25。
- RDEF-04/05：对账轮次持久计数（kv wakeround，total/offset 终身单调 dedupe）+
  notify_max_rounds 入策略（DESIGN 增量一行）+ task.notify_exhausted 一次性事件 +
  三路恢复；按候选者判定 pending 新鲜度，坏订阅者不再压制整端口；组合矩阵 33/33。
- RDEF-06：矩阵记录层全面重写（规定种类/预期/实际/证据/断言/状态；非规定种类抛错）；
  6失败行定性（CMD-006 凭据校验、CMD-028 启动失败不回滚、CMD-041 replace缺键=产品
  缺陷；CMD-090 互斥、CMD-010 缺body=测试缺陷）；fill_param_gaps 补齐全部具名行 +
  声明-证据完整性校验；97/97命令、384/384参数、371行全Pass、0缺口、exit 0。
- RDEF-07/08：workspace_key 归一（大小写/尾分隔符/../junction/8.3）+ 唯一索引并发
  兜底（迁移005）；clone 显式冲突拒绝、继承回退保留；18/18。
- RDEF-09：grace 真实生效（异步受理、stopping 停新唤醒、保存提示送达、全宽限持久
  等待、到期终止撤凭据；force 跳过；restart 到期自动起替身）；时钟级证明 20/20；
  测试脚本 44 处 stop/restart 显式 --force 适配。
- RDEF-10：四态错误码实测稳定（TOKEN_REVOKED=完整token真实送达的证明）；token
  零泄漏；10/10。
- 连带修复：port replace INTERNAL_ERROR、create --start 回滚身份、wait --claim 校验、
  init --default-harness 死参数、stop 异常遗留op、zsession kill 404 幂等。
- 回归：quicktest 过；88 Pass+3 Blocked（与复验基线一致）；旧套件 25/15/12；
  新套件 35/33/25/18/20/10；矩阵 0 缺口。
- 报告：SECOND_FIX_REPORT.md / SECOND_REGRESSION_REPORT.md。

## 2026-09-30 第二轮返修复核（真实通道收口）

- 真实三案例（最终构建）：AC-HAR-02 Pass、AC-E2E-02 Pass（同 run 20260930-114726）、
  AC-E2E-03 Pass（20260930-114854，七步单链贯通：实例1 claim+CHECKPOINT→force stop→
  实例2 续作 RESULT.md+自行 emit done，ended_by=agent）。
- E2E-03 排障链（全部留痕）：两次如实 Fail → 根因1 zsession 队列模式回合中送达丢失
  （实测 inputId 接受但永不处理；interrupt=startNow 4s 送达）→ 指令类发送 idle-wait+interrupt
  兜底；根因2 CLI 默认 15s HTTP 超时在真实 spawn 负载下不足 → 驱动器显式携带 --timeout；
  期间临时请求追踪包装缺陷曾致两试全败，已彻底移除并记录。
- 机器负载事件：36 个遗留测试 python 进程（manifest 登记 PID 受管清理）+ 遗留会话
  （账本登记句柄受管 kill 27 个）；清理后恢复。非产品缺陷。
- 最终回归：88 Pass + 3 Blocked（20260930-115138）；旧套件 25/15/12；新套件
  35/33/25/18/20/10；矩阵 97/97 命令、384/384 参数、0 缺口。
- 报告：SECOND_REGRESSION_REPORT.md（含真实案例证据链与资源收尾）。
