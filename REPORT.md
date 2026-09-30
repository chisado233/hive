# Hive V1 完整交付报告

- 日期：2026-09-30
- 施工契约：DESIGN.md / IMPLEMENTATION_PLAN.md / ACCEPTANCE.md（2026-09-30 版，优先于旧 HANDOVER.md）
- 仓库：本目录（独立 git 仓库，main 分支，提交 `cdd8882` → `cc47a98`）
- 最终验收：**90/90 案例通过**（单次完整运行，含真实 ZCode harness 双案例）
  - 证据根：`work/acceptance/20260929-191540/`（report.json + 每案例 commands/results/assertions.jsonl）

---

## 1. 交付概览

Hive V1 按 DESIGN.md 完成：**稳定 Agent + 统一 Task + 三入口 CLI** 的通用自动化脚手架，
Python 3.11+ 标准库实现（零第三方运行时依赖），SQLite 持久账本，本机 HTTP daemon。

| 交付物 | 位置 | 说明 |
|---|---|---|
| CLI 单入口 | `cli/hive.py` | 顶层只有 inbox/emit/control；97 命令叶子全参数 |
| 命令契约源 | `hive_core/manifest.py` | CLI 解析/help、服务端校验、`GET /v1/schema` 同源，不允许两套默认值 |
| HTTP daemon | `hive_core/server.py` | `/v1/health|commands|schema|tasks|human/*` + `/human/` 人工页 |
| 存储 | `hive_core/store.py` | WAL、每线程独立连接、迁移、可重入写事务（状态+事件+outbox+幂等同事务提交） |
| 领域层 | `hive_core/{tasks,waits,agents,ports,retire,delivery}.py` | Task 终态/转交/回调/等待环检测/评论@/配置版本/退役计划 |
| 运行时 | `hive_core/{harness,runtime_ops}.py` | 实例 0/1 槽位、claim_epoch 围栏、fixture 与真实适配器 |
| 调度 | `hive_core/scheduler.py` + `triggers.py` | 期限结算、通知合并、冷启动轮转、outbox 重试、cron/interval(固定时区) |
| 验收驱动 | `scripts/acceptance.py` | 90 案例逐例执行并存证；`scripts/quicktest.py` 快速链路 |
| 无秘密示例 | `examples/*.json` | AgentConfig/TaskSpec/EmitBatch/GrantSpec/RetirePlan/HarnessSpec |
| 文档 | README.md / PROGRESS.md | 启动教程、最短路径、复现命令、施工记录 |

核心语义全部落实：done/fail/trash 终态且不可改写；forward 保留 Task ID 转移责任；
回调独立于来源、无 callback 不向发送者推终态通知；等待登记立即返回、结果先到直接
ready、父终态不级联子任务；work 需 CAS 原子领取（20 并发只产生一个 owner）；Agent
身份与实例解耦（0/1 唯一槽位，旧实例/旧 claim 被围栏）；凭据只存 sha256，不进
prompt/日志/报告。

---

## 2. 真实 Harness 接入：决定换用 zcode（zsession）

### 2.1 背景与过程

ACCEPTANCE 要求至少一个**真实 harness** 通过五能力（启动/发消息/YOLO/选模型/终止）
smoke 与真实端到端（AC-HAR-02 / AC-E2E-02），并明确"不能用 fixture 冒充"。

施工分两步走：

**第一阶段：opencode CLI（探路）**
本机探测到 `opencode` CLI（无头 `run` 模式）。实现了 `process` 适配器
（argv 模板、`{model}`/`{workspace}` 占位、send_argv、.cmd 垫片解析），注册为
`opencode-run`。五能力 smoke 通过（模型 mimo-v2.6-flash-free / space-bunny-free
真实推理）。但端到端暴露了它的结构性短板：

1. `opencode run -c` 的"最近会话"是**跨目录**的——续接会漂到别的项目，Agent 看不到
   自己工位里的文件；
2. 每条消息是短命进程，**会话连续性只能靠工位文件承载**，Agent 对"上一轮发生了什么"
   没有记忆；
3. 项目根解析受全局配置影响，同样的指令在不同目录行为不一致；
4. 没有 observe/热切模型，五能力里两项只能靠"重启大法"凑。

**第二阶段：按 owner 指示切换到 zcode（zsession）**

Owner 提供了本机 **zsession（ZCode 会话编排服务，http://127.0.0.1:8765）** 并明确
指示："真实 agent harness 接入使用这个 zcode 插件……之后都换用这个来测试"。
据此实现 `zcode-zsession` 适配器并**将其定为唯一正式的真实 harness 通道**，
后续所有真实测试全部换用它。

### 2.2 为什么 zsession 是更优解

| 能力 | opencode-run（process 通道） | zcode-zsession（正式通道） |
|---|---|---|
| 启动会话 | 每条消息一个短命进程 | `POST /sessions/spawn`，**持久 ZCode 会话**，上下文跨轮保留 |
| 发消息 | 再起一个进程，靠工位文件续接 | `POST /sessions/{sid}/send`，原生 queue/**interrupt** 双模式 |
| YOLO | 无头模式默认放行 | spawn 时 `mode:"yolo"` 显式声明，语义清晰 |
| 选/切模型 | 仅启动时（换模型=杀进程重启） | 启动时选模型 + `POST /sessions/{sid}/model` **原地热切换** |
| 终止 | taskkill 进程树 | `POST /sessions/{sid}/kill {force:true}` 干净收口 |
| 状态观测 | 无（unknown） | `GET /sessions/{sid}`：idle/working → **真实 observation** |
| 事件流 | 无 | `/events` 游标增量（本版未接，属增强） |

即：zsession 在 DESIGN §4.3 的五项最小能力上**全部原生**，且额外命中多项"增强能力"
（热切模型、interrupt、inspect），Agent 会话还与 Hive 的"稳定身份 + 工位上下文"模型
天然对齐——实例可以换，会话记录与工位文件都在。

**安全边界同样合规**：Bearer token 运行时读取受保护的
`~/.zcode/zsession/config.json`（owner-local），或 `HIVE_ZSESSION_TOKEN` 环境变量；
token 不落入 HarnessSpec、prompt、日志、验收报告（spec 里只有文件/环境变量引用，
见 `examples/harness-zcode-zsession.json`）。

### 2.3 真实接入证据

**AC-HAR-02 五能力 smoke（Pass，run 20260929-185454）**
```
start           passed | handle=sess:...（真实ZCode会话）
send            passed | send=accepted（真实消息入会话）
yolo            passed | mode=yolo 启动
model_at_start  passed | 换 bigmodel-api/GLM-5.3-Flash 重启核实
stop            passed | kill=stopped
overall: passed   harness.validation_level = smoke:passed
```

**AC-E2E-02 真实端到端（Pass，run 20260929-185845）**
1. 创建真实 Agent（zcode-zsession，temp 隔离工位），投递 work 任务；
2. 投递即冷启动真实 ZCode 实例：spawn prompt = 系统协议 agent.md + 个体 prompt +
   工位业务 md + 实时责任摘要 + CLI 接入信息（落盘为工位 `STARTUP.md`）；
3. wake 指令直达真实会话，ZCode **真实执行**：读 `input.txt` → 原样写 `output.md` →
   `hive inbox claim <TSK>` → `hive emit done <TSK> --claim <CLM>`；
4. 账本核实：任务 `state=done`、`ended_by=agent:agt_53ff...`、`output.md` 内容与输入
   逐字一致；
5. 随后 stop→start 换实例：同 agent_id、唯一新 instance_id、旧实例凭据
   `TOKEN_REVOKED` 被围栏。

opencode-run 作为**备选通道**保留在 examples 中（其五能力 smoke 亦通过），文档已注明
其"每次 send 新会话、连续性靠工位文件"的限制。

---

## 3. 验收结果

### 3.1 总表

- **确定性案例 88/88 Pass**（隔离 SQLite + 真实 CLI + fixture harness + 测试时钟 +
  本地 webhook 接收器 + 故障注入）
- **真实接入案例 2/2 Pass**（AC-HAR-02、AC-E2E-02，zsession/ZCode）
- **合计 90/90**，最终单次完整运行 `work/acceptance/20260929-190754` 之后修复两处
  竞态，复跑 `work/acceptance/20260929-191540` 达成全绿

分组：ENV×2、CLI×3、ID×5、AUTH×4、CTX×3、PORT×4、TASK×8、MSG×5、TIME×4、CB×5、
WAIT×6、RUN×7、HAR×3、HUM×4、PROD×5、SCHED×5、REC×6、CTRL×5、STORE×4、E2E×2。

### 3.2 关键阻断项核验（全部通过）

- 20 并发 claim 只有一次跃迁、一个有效 claim/owner/事件（AC-TASK-02，多进程实测）
- 20 并发 start 只产生一个实例（AC-RUN-01，唯一槽位索引兜底）
- 20 并发同 request-id 只造一个 Task（AC-STORE-02，见 §4 修复）
- 旧实例/旧 claim 提交 → CLAIM_FENCED/TASK_TERMINAL，不产生回执（AC-TASK-06/07）
- 无 callback：终态不给来源任何通知（AC-TIME-02/MSG-01）
- 父 done 不级联子任务；tree 显示 open_work；等待环 WAIT_CYCLE（AC-TASK-01/WAIT-06）
- 结果先到 wait 直接 ready；daemon 重启后从账本补算不丢唤醒（AC-WAIT-03/REC-02）
- 提交前故障不留半笔；提交后 outbox 故障可恢复且回执唯一（AC-REC-01/02）
- 最小 harness 关闭 inspect/events/model-hot 后仍可用五能力组合（AC-HAR-01）
- 凭据正文零泄露（stdout/prompt/日志/审计全扫描，AC-AUTH-04）
- 生产 profile 拒绝 test 命令；单写者锁拒绝第二 daemon（AC-ENV-02）

---

## 4. 验收中发现并修复的真实缺陷（节选）

1. **跨线程共享 SQLite 连接**（隐蔽竞态）：多 HTTP 线程共享单连接时，读者可能看到
   其他线程事务的中间状态（AUTH-01 偶发 NOT_VISIBLE）。→ 改为**每线程独立连接**
   （WAL），写仍经 RLock + BEGIN IMMEDIATE 串行。
2. **幂等键竞态**：并发同键请求都未命中缓存而各自执行（AC-STORE-02 出现 3 个
   Task）。→ 改为 **INSERT 预占位**（键竞争天然互斥）→ 执行 → 回填响应；失败释放
   占位；并发落败方短暂等待赢家结果。
3. **停服/重启竞态**：旧 daemon 尚在退出时 start 失败且不落到"拉新"（全量回归一度
   连锁 39 败）。→ cmd_start 检测持锁者死亡即重试整轮。
4. **终态判定顺序**：已终态任务携带旧 claim 提交时误报 CLAIM_FENCED。→ 终态检查
   前置，恒返回 TASK_TERMINAL。
5. **批量原子性**：emit batch 中途失败会残留半批。→ Store.write() 可重入（嵌套加入
   外层事务）+ 静态预校验，真·全有或全无。
6. **退役回调误匹配**：handover 元数据里的旧 ID 触发 LIKE 误报 blocker。→ 改
   json_extract 精确匹配 target。
7. **zsession spawn→use 竞态**：spawn 返回后立即 send/kill 会 404。→ 适配器加会话
   就绪轮询与 404 重试。

全部修复均已回归验证。

---

## 5. 关键实现决定（对后续施工有用）

1. Agent/Producer 的 subject_id 镜像对象 ID，简化授权绑定；凭据文件 `{subject,token}`。
2. `references` 为 SQLite 保留字，库内列名 `refs`，对外语义不变。
3. 通知（唤醒信号）可丢可重发：按 `(agent_id, dedupe_key)` 合并，连败 3 次丢弃，
   由后续事件重新入队——与 DESIGN"唤醒可丢可重发"一致。
4. claim 部分成功：envelope `ok+partial`，CLI exit 6。
5. 真实 harness 启动简报 = 协议 + 个体 prompt + 工位 md + 实时责任摘要 + CLI 接入
   信息（绝对路径），落盘工位 `STARTUP.md`；zsession 以其为 spawn prompt，process
   通道以其为首条消息。
6. 测试时钟偏移持久化（kv.clock_offset），`test clock advance` 触发即时扫描。
7. 真实 Agent 工位放在仓库树外的隔离目录（避免宿主 CLI 项目根解析串扰）。

---

## 6. 已知限制与如实声明

- 真实接入证据绑定**本机** zsession/opencode 环境；换环境重跑命令见 README
  「验收复现」（`HIVE_REAL_HARNESS=zcode-zsession ...`）。
- scheduler 的 restart_backoff 序列当前仅实现限次（restart_max_per_hour），未做逐级
  间隔退避。
- `producer/trigger/channel list` 对已认证主体可见名片级信息，如需收紧可加
  system.observe（已记录于 PROGRESS）。
- external 端口 V1 只承担结果/通知投递；YOLO 档不承诺操作系统级沙箱（DESIGN §18）。
- Windows 单机部署；不做多主/跨机同步；生产迁移不在本次范围。

## 7. 环境保护声明

- trigger-platform（19961）全程**只读参考**：未修改其数据库/配置/worker，未停止任何
  进程；验收前后 netstat 核对见 AC-ENV-01/02 证据。
- Hive 使用独立数据根/端口（19962；验收 29962），验收 daemon 已全部停止
  （残留仅 TIME_WAIT）；本轮创建的 21 个 `hive-*` zsession 测试会话已 kill+archive，
  活跃列表为 0。
- 凭据零外泄：验收报告/日志/审计只含文件路径与主体 ID，token 正文未出现。

## 8. 最短上手（复制即用）

```bash
cd D:\agent_workspace\valuable-projects\hive
python cli/hive.py --home .hive control system init
python cli/hive.py --home .hive control system start
set HIVE=--home .hive --credential-file .hive/auth/owner.json
python cli/hive.py %HIVE% control agent create --name worker1
python cli/hive.py %HIVE% control port create --route work/general
python cli/hive.py %HIVE% emit new --root --to port:work/general --title 任务 --body 内容
python cli/hive.py %HIVE% control task list
```

真实 harness 注册与五能力 smoke：

```bash
python cli/hive.py %HIVE% control harness register --file examples/harness-zcode-zsession.json
python cli/hive.py %HIVE% control harness validate zcode-zsession --smoke ^
    --workspace <隔离空目录> --model bigmodel-api/GLM-5.3 ^
    --alternate-model bigmodel-api/GLM-5.3-Flash
```
