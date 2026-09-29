# Hive V1 施工进度

状态基准：以 DESIGN.md / IMPLEMENTATION_PLAN.md / ACCEPTANCE.md 为施工契约。
本文件由施工 Agent 维护，供接续施工使用。最后更新：2026-09-30。

## 当前状态总览

| 阶段 | 状态 | 说明 |
|---|---|---|
| P00 隔离骨架 | **已完成** | init/serve/start/stop/status/单写者锁/日志脱敏/Clock 已冒烟验证 |
| P01 数据模型 | 代码完成，验收中 | 97命令manifest、迁移、幂等已实现；schema导出与幂等冲突待AC验收 |
| P02 身份权限 | 代码完成，待验收 | subjects/credentials/grants、Agent配置版本发布、工位三件套 |
| P03 Task核心 | 代码完成，核心已通 | emit/claim/终态/forward/tree/done+dispatch 快测通过 |
| P04 评论@/decline/期限 | 代码完成，待验收 | 评论@通知已通；decline与时钟驱动期限待AC |
| P05 回调/outbox/wait/batch | 代码完成，部分已通 | 回执+wait ready已通；batch原子性/outbox重试待AC |
| P06 运行控制 | 代码完成，待验收 | 实例槽位/fixture harness/start-stop-restart-model |
| P07 人工端口/producer/trigger | 代码完成，待验收 | 人工页面/producer/interval+cron/webhook |
| P08 调度器/恢复 | 代码完成，待验收 | 期限扫描/通知合并/冷启动轮转/重启策略 |
| P09 管理控制面 | 代码完成，待验收 | backup/restore/config/log/audit/event/delivery/retire |
| P10 完整验收 | 进行中 | 验收驱动器开发中 |

## 已验证（真实 CLI 执行）

- `control system init/start/stop/status`：全新目录初始化、后台启动、健康检查、重复 start 幂等、停止。
- `control test seed`：创建 A/B/C Agent、main/private/human/external 端口、human/producer/observer 主体、fixture 活实例、凭据文件。
- `emit new`（root/parent、port/agent 目标）→ `inbox claim`（并发拒绝 TASK_ALREADY_CLAIMED）→ `emit done --dispatch-file`（原子子任务）→ `inbox tree`（open_work 计数）。
- `emit fail` 带 callback → 回执 Task 落到 A 个人队列（receipt 不回执回执）。
- `emit comment --mention` @B → 单条 notice。
- `inbox wait create` → 依赖完成后 wait ready。
- CLI 三层 help（hive/inbox wait create/control system init）、--version、重复参数检测、JSON envelope、partial exit 6。

## 代码结构（当前）

```
cli/hive.py            CLI 入口（manifest 驱动，最长路径匹配定位命令）
hive_core/
  manifest.py          97命令+全参数单一契约源（CLI/服务端校验/schema导出）
  store.py             SQLite 迁移+可重入写事务+事件/outbox/幂等表
  auth.py              主体/凭据(sha256落库)/授权引擎/可转授范围
  service.py           dispatch（幂等、故障注入、test gate）
  server.py            HTTP daemon（/v1/* + /human/）
  local.py             init/serve/start/restore 本地命令
  tasks.py             Task领域（insert_task助手/claim CAS/终态/forward/评论@/tree）
  waits.py             wait登记/环检测/求值/恢复通知
  delivery.py          回执生成（语义唯一键）+ webhook投递/重试
  agents.py            配置版本发布/订阅投影/权限投影/工位重建/责任摘要
  ports.py             端口CRUD/schema校验/退役计划
  harness.py           Registry + FixtureAdapter + ProcessAdapter + smoke
  runtime_ops.py       start/wake/send/stop/restart/model/槽位/fence/崩溃恢复
  scheduler.py         期限结算/通知合并/冷启动轮转/outbox/trigger
  triggers.py          manual/interval/cron（五字段子集，固定时区）
  retire.py            Agent退役计划/blockers/回调交接
  backup.py            SQLite backup API一致性备份/恢复(recovery_hold)
  webhuman.py          最小人工页面
  handlers_*.py        命令处理器（core/task/ext/test）
scripts/quicktest.py   快速功能测试（已通过）
```

## 关键实现决定（记录）

1. Agent/producer 的 subject_id 直接镜像对象 ID（agt_xxx 即主体ID），简化 grants 与凭据绑定；凭据文件格式 `{"subject","token"}`。
2. `references` 是 SQLite 保留字，列名用 `refs`（对外 schema 无影响：公开字段名为 references，卡片输出由 task_card 提供）。
3. 事务可重入：`Store.write()` 嵌套时加入外层事务，emit batch / done+dispatch 借此获得真正原子性。
4. 手写 INSERT 全部替换为 `insert_task()` 助手，杜绝列数/占位符错配。
5. claim 部分成功：envelope ok=true + partial=true，CLI exit 6（DESIGN §16）。
6. spec-file 与内容参数互斥在 manifest 层实现（spec 组成员彼此不互斥，仅与 spec 互斥）。
7. CLI 子解析器全局参数用 argparse.SUPPRESS，避免覆盖根解析值。
8. 通知（唤醒信号）可丢可重发：notifications 表按 (agent_id, dedupe_key) 合并，失败3次后丢弃，由后续事件重新入队。
9. 时钟：Service.clock 从 kv.clock_offset 恢复；test clock advance 持久化偏移并立即触发扫描。

## 下一步（按序执行）

1. 写 `scripts/acceptance.py` 验收驱动器（对应 ACCEPTANCE.md 90案例，存证到 work/acceptance/<run-id>/）。
2. 逐案例执行并修复：AC-ENV/AC-CLI → AC-ID/AUTH/CTX → AC-PORT/TASK/MSG/TIME → AC-CB/WAIT → AC-RUN/HAR → AC-HUM/PROD → AC-SCHED/REC → AC-CTRL/STORE → AC-E2E。
3. 真实 harness 探测（本机可用的 agent CLI），AC-HAR-02 五能力 smoke；没有则标"待验收"。
4. README、无秘密示例、git init、最终报告。

## 未完成/已知问题

- control.system.restore 处理器还是 NOT_IMPLEMENTED 占位（本地 restore 逻辑已在 backup.py，待接 CLI 分支）。
- `h_producer_list`/`h_trigger_list`/`h_harness_list` 权限暂未收紧（当前任何已认证主体可见），P09 收尾时统一为 system.observe。
- 真实 harness（AC-HAR-02/AC-E2E-02）未执行——需要探测本机可用 CLI。
- scheduler 的 restart_backoff 序列尚未用于自动重启间隔（当前只限次数）。
