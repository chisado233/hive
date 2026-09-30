# Hive V1 修复轮回归报告（REGRESSION_REPORT）

> **【更正说明 · 2026-09-30 第二轮返修】** 本文写作时对真实三案例 Blocked 的归因
> ——"ZCode 回合执行层损坏"——已被第二轮独立复验收 RDEF-02 **证伪**：执行层实测正常
> （直连会话可完成回合并产出文件），Blocked 的真实原因是预检自身的 spawn 请求把
> 未创建的工位目录发给了 zsession（HTTP 400 bad_workspace），属预检缺陷而非环境故障。
> 原文与原失败证据按"保留历史"原则未作删改；更正详情见文末
> 「附录：第二轮更正」与 SECOND_FIX_REPORT.md §RDEF-02。


- 轮次：独立验收缺陷修复轮（基准 `b2288d3` + DEF-01..11）
- 日期：2026-09-30
- 环境：Windows 10 19045 · Python 3.12.4 · 本机 zsession 8765 · trigger-platform 19961 全程只读未触碰
- 证据根：`work/acceptance/20260930-041804-0f0b6f`（最终全量）、`work/defects/def0102|def04|def0305-*`、`work/coverage/20260930-121222-8ce5`、`work/independent/20260930-041050-fe2488`

## 1. 执行范围与结果总表

| 范围 | 结果 | 证据 |
|---|---|---|
| quicktest | **通过**（全链路） | 本 run 输出 |
| 88 确定性案例 | **88/88 Pass** | work/acceptance/20260930-041804-0f0b6f/report.json |
| 真实 harness 三案例（HAR-02/E2E-02/E2E-03） | **Blocked（外部条件）** | 同 run assertions + 预检 |
| DEF-01/02 工位/上下文回归 | 25/25 | work/defects/def0102-*/checks.json |
| DEF-04 幂等占位回归（真实击杀） | 15/15 | work/defects/def04-*/checks.json |
| DEF-03/05 退避/对账回归（时钟驱动） | 12/12 | work/defects/def0305-*/checks.json |
| 参数覆盖矩阵（97命令×384参数声明） | **205/205 行，97/97 命令** | work/coverage/20260930-121222-8ce5/ |
| 资源收尾 | 无残留监听/会话 | 下方 §5 |

未裁剪：本轮覆盖与修复均按独立验收既有范围执行；没有任何案例被删除、断言被放宽或语义被修改以制造全绿。

## 2. 案例执行明细（要点）

- 88/88 确定性案例：ENV/CLI/ID/AUTH/CTX/PORT/TASK/MSG/TIME/CB/WAIT/RUN/HAR
  (01,03)/HUM/PROD/SCHED/REC/CTRL/STORE/E2E-01 全部 Pass。
- AC-RUN-07 已按 DEF-03 新语义重写（崩溃→退避计划→时钟推进→限次停计划）。
- AC-REC-04 改用真实 crash 路径替代 SQL 注入；并发场景逐子进程入证。
- 真实三案例：预检 spawn→等 message_done；当前 ZCode 执行层损坏（详见 §4），
  如实 Blocked 并留预检证据。

## 3. 参数覆盖（DEF-08）

- `scripts/coverage_matrix.py`：97/97 命令业务执行；384 参数声明全部有执行行；
  205/205 行 Pass（zero failed rows, zero failed scenarios）。
- 特别项证据齐全：`--cascade/--apply/--dry-run/--force/--grace/--missed/
  --timezone/--if-version/--replace/--fallback/--include-declined/--mark-seen/
  --include-workspaces/--include-credentials/--with-credentials`。
- 服务端参数校验严格性保留：未知参数拒绝、互斥双端拒绝、file_* 不得直达服务端。

## 4. 真实 ZCode 环境（如实）

- zsession daemon：health OK、spawn/send/kill/model 回读正常（HAR-02 管路证据）。
- ZCode 回合执行：自事件流 seq≈719 起零 message_done；10+ 探针（3模型/4工位/
  有无prompt/90s-5min窗口）全部秒死→dead。预检 helper
  `zsession_env_preflight()` 使三个真实案例在环境恢复前如实 Blocked。
- 历史上（独立验收时）AC-HAR-02/AC-E2E-02 曾真实通过；环境恢复后按 README
  「验收复现」重跑即可复核 AC-E2E-03。

## 5. 资源收尾（DEF-07）

- 受管停止的 daemon：29963（b2288d3 遗留，PID 34464）、29962 与 54501（本修复轮
  隔离改造前的同轮遗留）；证据 work/cleanup/DEF-07-cleanup-20260930.json +
  本报告 netstat 复核（无 LISTEN 残留，仅 TIME_WAIT）。
- zsession：24 个 hive-*/probe/watch 测试会话已 kill+archive（活跃列表 0）。
- 每轮 run-manifest.json 记录 daemon/pid/home/端点/真实 agent；teardown 先受管
  stop 真实 harness Agent 实例再 stop daemon，cleanup_log 含真实结果。
- trigger-platform（19961）验收前后均在监听，进程未动。

## 6. 未完成项 / 待验证项

| 项 | 状态 | 说明 |
|---|---|---|
| AC-HAR-02/AC-E2E-02/AC-E2E-03 真实执行 | **Blocked（外部条件）** | ZCode 回合执行层损坏；案例与预检已就绪，环境恢复后可复核 |
| restart_backoff 逐级间隔 | 已实现并验证 | 时钟驱动证据（12/12） |
| §5.2 参数级检查记录 | 已实现并验证 | 205/205 行 |
| 幂等占位崩溃恢复 | 已实现并验证 | 真实击杀证据（15/15） |
| 唤醒对账重入队 | 已实现并验证 | 时钟驱动证据（12/12） |
| 跨环境可移植性 | 绑定本机 zsession/opencode（既有声明） | 不属本轮修复范围 |
| 生产迁移/多机/OS沙箱 | 明确不在范围 | DESIGN §18 |

## 7. 提交与工作区

- 修复链提交见 git log（以 b2288d3 为基准的后续提交）；报告时工作区干净
  （除独立验收产物 INDEPENDENT_*.md 与 work/** 证据目录，均保留不删）。
- 原签收阻碍解除情况：
  - §7.2 条件2（参数级覆盖）—— **已满足**（205/205 行、97/97 命令）。
  - DEF-01/02 两个 High 契约缺陷 —— **已修复并验证**。
  - DEF-03/04/05 机制缺陷 —— **已修复并验证**。
  - DEF-06 可复现性 —— **已修复并验证**（翻页稳健 + 断言加固 + 并发入证）。
  - DEF-07 清理声明 —— **已修复并验证**（受管停止+manifest+复核）。
  - 真实接入 —— 历史上曾通过；本轮因 ZCode 执行层损坏如实 **Blocked**，不作
    “全绿”结论。


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
