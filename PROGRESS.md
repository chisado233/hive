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
