# HANDOVER_修复轮.md · Hive V1 上下文压缩交接文档

- 写于：2026-09-30（修复轮全部完成后）
- 给：接续会话（压缩/新开会话的施工 Agent）
- 仓库：`D:\agent_workspace\valuable-projects\hive`（独立 git 仓库）
- **当前 HEAD：`78380ad`，工作区干净**（`git status` 除 work/ 证据与 INDEPENDENT_*.md 外无未提交改动；两者本就不入库/已入库）

---

## 0. 项目一句话

Hive V1 = 稳定Agent + 统一Task + 三入口CLI（inbox/emit/control）的通用自动化脚手架。
Python 3.12 纯标准库，SQLite(WAL/每线程连接)，本机 HTTP daemon。
已完成：初版交付（P00–P10，90案例）→ 独立验收（判"不满足签收"，DEF-01..11）→ **修复轮（本轮，全部缺陷修复并验证）**。

## 1. 必读文件（按序，均为仓库根）

| 文件 | 内容 |
|---|---|
| DESIGN.md | 施工契约（97命令/字段/错误码；**不得改语义**） |
| ACCEPTANCE.md | 验收基准（90案例、§5.2 参数矩阵、§7.2 签收条件） |
| INDEPENDENT_DEFECTS.md | 独立验收缺陷清单 DEF-01..11（原始输入） |
| FIX_REPORT.md | 修复轮逐项状态（每项：根因/文件/回归/证据） |
| ALL_FIXES.md | **最详细**的全量技术说明（十组，含连带缺陷A–H、环境诊断、E2E-03设计） |
| REGRESSION_REPORT.md | 本轮回归范围与结果总表 |
| PROGRESS.md | 全程施工流水（含两轮摘要） |
| README.md | 启动/复现命令（含真实harness重跑命令） |

核心结论（一句话版）：**DEF-01..11 全部"已修复并验证"；最终回归 88 Pass + 3 Blocked；覆盖矩阵 205/205；真实三案例因 ZCode 执行层环境损坏如实 Blocked（非产品缺陷，预检证据齐全）。**

## 2. 当前系统状态

- 代码：`cli/hive.py`（入口）+ `hive_core/`（store/auth/agents/ports/tasks/waits/delivery/harness/runtime_ops/scheduler/triggers/retire/backup/webhuman/contextgen + handlers_{core,task,ext,test}.py + service/server/local/home/manifest/errors/ids/timeutil/clock）。
- 数据根：`--home` 指定（验收用 `work/acceptance/<run>/runtime`，test profile）。
- 测试脚本（都在 scripts/）：
  - `quicktest.py` 快速链路（通过）
  - `acceptance.py` 91案例驱动器（88确定性+3真实；端口/run-id 自动唯一；run-manifest.json；受管teardown）
  - `defect_def0102.py`(25项) / `defect_def04.py`(15项) / `defect_def0305.py`(12项) 缺陷回归
  - `coverage_matrix.py` 97命令×384参数覆盖（205/205）
- 环境：
  - trigger-platform(19961) **全程只读未触碰**（仍3个进程监听，不要动）
  - zsession(127.0.0.1:8765) daemon 健康；token 读 `~/.zcode/zsession/config.json` 的 owner-local（**不外泄不入档**）
  - 测试遗留已清零：验收端口无 LISTEN 残留、hive-*测试会话 kill+archive（活跃0）

## 3. 修复轮技术要点速查（细节看 ALL_FIXES.md）

- **DEF-01/02 工位**：`agents.resolve_workspace()` 单一事实源；`_rebuild_workspace` 用它；`_workspace_in_use` 共享工位拒绝；agent.md/prompt.md 缺失→WORKSPACE_UNAVAILABLE 硬错误；启动简报迁 `home/runtime/startups/<agent>/<iid>.md`（不再写工位根）；`contextgen.gather_context` 防御性排除 STARTUP.md；harness 模板支持 `{startup_file}`（旧spec裸名自动替换）。
- **DEF-03 退避**：`confirm_crash` 只登记 `kv restart:<agent>={attempts,next_allowed_at}`（消费 restart_backoff[i]，限次 restart_max_per_hour，事件 agent.restart_scheduled）；`scheduler.run_due_restarts()` 到期拉起（enabled/restart_on_exit/唯一槽位校验；稳定1h重置）。
- **DEF-04 幂等**：`service.py` dispatch 分治——快命令(emit./inbox./producer.emit/control.task.)「占位+执行+回填同一事务」；慢命令 lease 接管（迁移002 lease_until；`idem_lease_seconds` 默认60；CAS UPDATE 接管；启动 `idem_startup_sweep()` 清孤儿）；故障注入 `idem_kill_in_tx`/`idem_kill_after_reserve`（os._exit(70)，once 解除在进事务前独立提交）。
- **DEF-05 对账**：`scheduler.run_reconcile_wakes()`（queued 超 notify_retry_after 且无在途唤醒→按候选重入队，dedupe=reconcile:<task>:<N>，≤notify_max_rounds=12）；`_queue_notif` 落 notify_jitter→send_after（迁移003 列）；失败重试按 send_unknown_retry_after。
- **DEF-06 驱动器**：TASK-08 root过滤+精确ID；visible_tasks 改 created_at DESC+task_id；5空断言/3禁用分支清零；CTX-01硬断言；`run_concurrent()` 并发逐子进程入证；REC-04 真实crash路径；schema 按 profile 精确（test=97/prod=92）；**连带修**：schema_export prod过滤500、陈旧running启动op永久阻塞。
- **DEF-08 覆盖**：coverage_matrix.py，24场景，97/97命令业务执行，205/205行。
- **DEF-07 收尾**：29963遗留受管停止（work/cleanup/DEF-07-cleanup-20260930.json）；acceptance.py 有 MANIFEST/run-manifest.json/受管teardown（先stop真实agent再stop daemon）；quicktest 唯一home/端口。
- **DEF-09/10/11**：producer/trigger/channel/harness.list 加 manage/observe 门槛（port/agent discover 保持开放——既定能力）；`steer|interrupt` 键统一，zsession 原生 interrupt 实接（Registry.send mode 透传）；smoke 会话 mode/model 回读；`zsession_env_preflight()`（spawn→等message_done，turn完成才算真实可用）；restore 服务端=明确 local-only 拒绝；notify_jitter/visible_tasks排序/if False残留 全部处理。

## 4. 真实 ZCode 环境诊断（重要，勿重复排查）

- 现象：zsession daemon 全部 API 正常（spawn/send/kill/model回读），但**回合执行零完成**——事件流 seq≈719 起无任何 message_done；10+探针（GLM-5.3/Flash、多工位、最小指令"只回复:收到"）秒级 dead。
- 结论：ZCode 执行层（桌面宿主）损坏，**外部环境问题，非 Hive 产品缺陷**。
- 处置：三个真实案例（AC-HAR-02/AC-E2E-02/AC-E2E-03）前置 `zsession_env_preflight()`，预检失败→如实 Blocked（证据在各自 assertions.jsonl + work/acceptance/20260930-041050-fe2488）。
- **不要**重启 ZCode 桌面宿主/zsession daemon（用户环境；桌面宿主可能承载运行中的会话）。
- 环境恢复判定：`python scripts/acceptance.py AC-HAR-02`（带 HIVE_REAL_HARNESS=zcode-zsession …）若预检过即环境恢复，可跑三真实案例。

## 5. 复现命令（当前全绿的）

```bash
cd D:\agent_workspace\valuable-projects\hive
python scripts/quicktest.py                          # 通过
python scripts/acceptance.py                         # 88 Pass + 3 Blocked
python scripts/defect_def0102.py                     # 25/25
python scripts/defect_def04.py                       # 15/15
python scripts/defect_def0305.py                     # 12/12
python scripts/coverage_matrix.py                    # 205/205
# 真实三案例（环境恢复后）：
HIVE_REAL_HARNESS=zcode-zsession \
HIVE_REAL_HARNESS_SPEC=examples/harness-zcode-zsession.json \
HIVE_SMOKE_MODEL1=bigmodel-api/GLM-5.3 \
HIVE_SMOKE_MODEL2=bigmodel-api/GLM-5.3-Flash \
python scripts/acceptance.py AC-HAR-02 AC-E2E-02 AC-E2E-03
```

## 6. 唯一剩余待办（下个会话接手即做）

1. **等 ZCode 执行层恢复**（用户侧重启桌面宿主后），重跑上节最后一条命令，预期 3 案例转 Pass（历史上 HAR-02/E2E-02 均真实通过过；E2E-03 首跑会在环境好后完成首验）。
2. 若 E2E-03 实跑中真实 Agent 未按指令领取/写文件：**如实记失败**（案例断言已写死，不许脚本代写、不许以"指令遵循"替代证据）；可调 wake 措辞重试一次并留痕。
3. 三个案例转 Pass 后：更新 REGRESSION_REPORT §6 表格 + PROGRESS.md 追记一行 + git commit。
4. （可选）owner 若要复核，可用 `git diff b2288d3..78380ad` 审阅全部修复。

## 7. 红线提醒（不变）

- 不改 DESIGN/ACCEPTANCE 语义；不裁剪案例/放宽断言制造全绿。
- 不动 trigger-platform(19961)；不动用户 zsession 全局配置/桌面宿主。
- 凭证不入 prompt/日志/报告；测试资源按 manifest 受管收尾，不按进程名批量杀。
- Blocked ≠ Pass；产品缺陷与环境阻塞分开记录。
