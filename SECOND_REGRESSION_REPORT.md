# Hive V1 第二轮返修回归报告（SECOND_REGRESSION_REPORT）

- 日期：2026-09-30
- 返修基准：`c409490`（含第二轮独立复验报告原样入库）
- 最终构建：`2540004` + 真实通道修复（见 §3；提交号见文末 git log）
- 上轮回归：[REGRESSION_REPORT.md](REGRESSION_REPORT.md)（含已更正的环境归因附录）
- 逐项修复：[SECOND_FIX_REPORT.md](SECOND_FIX_REPORT.md)

---

## 1. 结论总表

| 验证项 | 命令 | 结果 |
|---|---|---|
| quicktest | `python scripts/quicktest.py` | **通过**（最终构建复跑） |
| 确定性 88 案例（返修中段） | `python scripts/acceptance.py` | **88 Pass + 3 Blocked**（20260930-102209） |
| 确定性 88 案例（最终构建） | `python scripts/acceptance.py` | **88 Pass + 3 Blocked**（20260930-115138；Blocked=三真实案例未选入确定性口径，符合设计） |
| 真实 AC-HAR-02（五能力 smoke） | `HIVE_REAL_HARNESS=zcode-zsession … acceptance.py AC-HAR-02` | **Pass**（最终构建 20260930-114726，与 E2E-02 同 run） |
| 真实 AC-E2E-02（端到端+换实例） | 同上 | **Pass**（最终构建 20260930-114726；真实 Agent 自行 claim+emit done） |
| 真实 AC-E2E-03（七步单链换实例续作） | 同上 | **Pass**（最终构建 20260930-114854；同一 run 内七步全链贯通，详见 §4） |
| DEF-01/02 套件（回归） | `python scripts/defect_def0102.py` | **25/25** |
| DEF-04 套件（回归，send 路径修改后复跑） | `python scripts/defect_def04.py` | **15/15** |
| DEF-03/05 套件（回归，调度器重写后复跑） | `python scripts/defect_def0305.py` | **12/12** |
| RDEF-01 专项（幂等五窗口真实击杀） | `python scripts/defect_rdef01.py` | **35/35** |
| RDEF-03 专项（退避状态机 8 项要求） | `python scripts/defect_rdef03.py` | **25/25** |
| RDEF-04/05 专项（轮次+压制组合矩阵） | `python scripts/defect_rdef0405.py` | **33/33** |
| RDEF-07/08 专项（工位别名+clone） | `python scripts/defect_rdef0708.py` | **18/18** |
| RDEF-09 专项（grace 全矩阵，含时钟级证明；send 路径修改后复跑） | `python scripts/defect_rdef09.py` | **20/20** |
| RDEF-10 专项（凭据四态） | `python scripts/defect_rdef10.py` | **10/10** |
| 参数矩阵（RDEF-06） | `python scripts/coverage_matrix.py` | **97/97 命令业务执行、384/384 参数具名 PASS 行、371 行全 PASS、0 缺口、exit 0**（完整性校验通过） |

核心不变量（并发领取/启动唯一、终态/回调/等待/恢复/权限围栏、幂等、工位归属）在上述全部套件中保持成立。

## 2. 真实环境预检更正（RDEF-02 落地证据）

- 预检实测：`state=ok`，约 8–15 秒内探针会话**实际产出** `PREFLIGHT_OK.md`（内容 `EXECUTOR_OK`）——ZCode 执行层正常的直接证据，与独立复验 RDEF-02 的结论一致。
- 三份旧报告（FIX_REPORT/REGRESSION_REPORT/ALL_FIXES）已各追加「更正说明」横幅与「附录：第二轮更正」，原文与原失败证据未删改。
- 预检三态语义在本轮全部真实案例中生效：预检通过→真跑；预检自身缺陷→案例 Fail（响亮失败，绝不记环境 Blocked）。

## 3. 真实通道修复过程（全部留痕，含失败尝试）

E2E-03 首次真跑（20260930-103315）与措辞改进后的重试（20260930-104658）均**如实记 Fail**：两次都是实例1 真实 claim+写 CHECKPOINT、stop 换实例成功、实例2 读取 checkpoint 并追加接管记录，但**收不到第二阶段指令**（CHECKPOINT 内实例2 自述"已轮询约5分钟，指令未到达"）。根因排查：

1. **zsession 队列送达丢失（外部行为，实测定位）**：回合进行中接受的 queue 模式 send 被接受（返回 inputId）但永不处理（120s+ 探针无果）；interrupt 模式（delivery=startNow）4 秒内送达。修复：`ZSessionAdapter.send` 对指令类发送先等会话 idle（有界 20s，探测超时 5s），仍忙则降级 interrupt 送达；唤醒类通知不做等待（wait_idle=False，可丢弃语义不变）。
2. **CLI 默认 15s HTTP 超时过短**：真实会话 spawn 瞬时负载下，E2E-02 全部命令超时（而守护进程对其他客户端健康应答——由旁路健康探针证实）。修复：验收驱动器所有调用显式携带 CLI `--timeout`。
3. 期间引入的一次临时请求追踪包装存在 `super().do_POST()` 缺陷，曾使 11:23–11:28 的两次尝试全失败——定位后已完全移除（最终 `server.py` 无该代码），此为排查过程缺陷，如实记录。
4. 修复后：HAR-02 与 E2E-02 同 run 双 Pass（20260930-114726），E2E-03 单链 Pass（20260930-114854）。

机器负载背景：当日累积的遗留测试 daemon（36 个 python 进程，进程创建 29 秒）与本轮及历史 run 遗留的 zsession 会话曾拖垮机器；按 run-manifest 登记的 PID/账本登记的会话句柄受管清理（绝无按进程名/前缀批量杀），清理后进程创建恢复 0 秒。此为测试环境事件，非产品缺陷，一并留痕。

## 4. AC-E2E-03 七步单链证据（20260930-114854，最终构建）

| 步骤 | 证据 |
|---|---|
| 1 实例1 真实 claim | 任务 `tsk_45e9…` 由真实会话（`ins_c418c890…`，harness=zcode-zsession，handle=sess:*）自行 `inbox claim`，claim `clm_4be1…` 入账 |
| 2 实例1 写 checkpoint（独立核验） | `CHECKPOINT.md`：内容含 E2E03 + 实例ID + claim_id，驱动脚本独立读取断言 |
| 3 非终态才 stop | stop 前任务 state=claimed（断言） |
| 4 stop 受检 | `--force` 停止成功（实例1 phase=stopped）；随后 start 产生**不同 instance_id**、agent_id 不变（断言） |
| 5 旧凭据拒用 | 实例1 凭据调用 → TOKEN_REVOKED（RDEF-10 语义） |
| 6 实例2 依 checkpoint 续作 | 实例2（`ins_418021…`）读取 CHECKPOINT.md，写 `RESULT.md`（内容同时含 E2E03 + 两个实例ID），自行 `emit done` |
| 7 完成者=新实例对照 | 任务 done、`ended_by=agent:agt_dfde…`（Agent 主体，非脚本）；实例表两代 zsession 实例（一 stopped 一收尾 stopped）；文件+账本+操作日志一致 |

历史失败尝试的证据目录（20260930-103315 / 104658 / 105955）保留不删。

## 5. 资源收尾

- 本轮全部验收 daemon：run-manifest 受管停止；受管清理脚本按登记 PID 处理（先优雅 stop、失联才按登记 PID 强杀），最终 `netstat` 无 29xxx 段监听残留。
- 本轮账本登记的全部 zsession 会话（27 个活跃）已逐句柄 kill；非本仓库账本的会话未触碰。
- trigger-platform（19961）全程只读未触碰（收尾仍在监听）；zsession daemon（8765）健康；用户 ZCode 桌面宿主未重启；token 未入任何日志/报告/提交。
- 测试遗留临时目录均在各 run 目录内（work/ 下按 run-id 隔离）。

## 6. 与独立复验的对照

- 上轮复验确认关闭的 DEF-01/02/06/07/09/11 行为在本轮全部套件中保持（def0102=25/25、88 案例两遍 88 Pass、工位/简报/收尾语义未回退）。
- 上轮部分关闭的 DEF-03/04/05/08/10 由本轮 RDEF-01/03/04/05/06/09/10 修复关闭（逐项见 SECOND_FIX_REPORT）。
- 本轮新增的产品缺陷（port replace INTERNAL_ERROR、create --start 回滚、wait --claim 校验缺失、init --default-harness 死参数、E2E-03 队列送达）全部修复并在专项/真实案例中验证。
