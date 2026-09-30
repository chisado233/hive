# Hive V1 第二轮返修 · 独立复验（第三轮验收）任务书

你是 Hive V1 的**独立复验 Agent**，与第二轮返修的施工方无任何关联，也不得把施工方的报告结论当作事实采信。你的任务：独立核实"第二轮返修全部收口"这一声明，逐项判定 RDEF-01..10 是否真正关闭，复验施工方全部回归声明，主动寻找施工方脚本盲区之外的新缺陷，最终给出签收结论。

---

## 0. 背景与基准固定

- 仓库：`D:\agent_workspace\valuable-projects\hive`（独立 git 仓库）。
- Hive V1 = 稳定 Agent + 统一 Task + 三入口 CLI（inbox/emit/ask 语义由 inbox/emit/control 承载）的通用自动化脚手架；Python 3.12 纯标准库，SQLite（WAL），本机 HTTP daemon。
- 历史沿革：初版交付（90 案例）→ 第一轮独立验收（DEF-01..11，判不满足签收）→ 修复轮 → 第二轮独立复验（RDEF-01..10，判仍不建议签收）→ **第二轮返修（本次被验对象，自称全部收口）**。
- **验收代码基准**：git HEAD 应为 `1906cce`。开工前先 `git log --oneline` 核对提交链（c409490 复验基准 → 35448a1 产品侧修复 → 2540004 参数矩阵 → 1906cce 真实通道收口），并 `git status` 确认工作区干净。全部验收在此固定版本上进行，发现任何未提交改动都要记录。
- **验收规范基准**：DESIGN.md / ACCEPTANCE.md / IMPLEMENTATION_PLAN.md。
  - 施工方声明：本轮规范**仅一处增量**——DESIGN §10.1 策略表新增 `notify_max_rounds` 一行，属补全而非放宽。
  - 你必须执行 `git diff c409490..1906cce -- DESIGN.md ACCEPTANCE.md IMPLEMENTATION_PLAN.md` **独立核实**此声明。任何超出声明的规范改动都要单独定性（是否借规范修订放宽验收口径）。

## 1. 必读文件（按序）

| 文件 | 用途 |
|---|---|
| DESIGN.md | 施工契约（97 命令/字段/错误码，语义基准） |
| ACCEPTANCE.md | 验收基准（案例集、参数矩阵、签收条件） |
| INDEPENDENT_REACCEPTANCE_DEFECTS.md | RDEF-01..10 原始缺陷清单（你逐项判定的对象） |
| INDEPENDENT_REACCEPTANCE_REPORT.md | 上轮复验报告（含环境归因教训与方法论） |
| SECOND_FIX_REPORT.md | 施工方逐项修复声明（你的核实对象） |
| SECOND_REGRESSION_REPORT.md | 施工方回归声明与真实通道修复过程（你的核实对象） |
| README.md | 启动/复现命令（含真实 harness 重跑方式） |

## 2. 环境约束

- Windows 10 19045，Python 3.12.4。
- zsession daemon：`127.0.0.1:8765`（用前先健康检查）。token 读 `~/.zcode/zsession/config.json` 的 owner-local 配置——**token 正文零入日志/证据/报告/git 提交**。
- trigger-platform（端口 19961）**全程只读，不得触碰**。
- 你自己的测试 home 一律使用 `work/` 下按 run-id 隔离的目录；端口与 run-id 保持唯一。
- 注意机器负载：历史轮次曾因遗留测试 daemon 拖垮机器。开工前先看一下 python 进程数与端口监听；**你只能按 run-manifest 登记的 PID/句柄清理自己产生的资源，绝不按进程名/前缀批量杀**。

## 3. 诚实记录与红线（不可逾越）

1. **Fail ≠ Blocked ≠ Pass**。预检/探针自身缺陷导致案例无法运行 → 记 Fail（响亮失败），不得记环境 Blocked；环境不可达须有直接证据才可记 Blocked。
2. SQL/直接改库仅允许用于构造"真实崩溃可产生的等价状态"，且必须在证据中如实标注；不得用它绕过产品路径制造"通过"。
3. 不修改任何既有文件（含施工方与历轮验收的报告、证据、脚本）；你的报告与证据一律新增。
4. 独立证据根：`work/independent2/<timestamp>-*/`，按探针分目录，不覆盖任何既有证据。
5. 施工方的专项脚本可以复跑，但**复跑通过不构成关闭的充分条件**——上轮教训正是"脚本全过、盲区藏缺陷"（RDEF-01 就是在 defect_def04 未覆盖的窗口 2 用等价状态构造发现的）。每项缺陷除复跑外，至少做一次施工方脚本未覆盖的独立探针。
6. 结束收尾：你自己启动的全部 daemon/会话按 run-manifest 受管清零（先停真实 Agent 再停 daemon），netstat 复核无你使用的端口段残留。

## 4. 任务一：RDEF-01..10 逐项关闭判定

逐项给出 **关闭 / 部分关闭 / 未关闭**，附独立证据位置。各项最低独立探针要求（不限于此）：

| 缺陷 | 施工方修复主张 | 你至少要做的独立验证 |
|---|---|---|
| RDEF-01 慢命令接管重复副作用 | 原子/外置双路径重分类（仅 7 个外置命令）+ executor_gen 围栏 + 孤儿占位审计 | 复跑 defect_rdef01（35/35）；独立构造：对 create/grant/clone/producer 各造"效果已持久化回填前"等价状态后同 key 重试，核实 0 个第二对象；构造"有审计效果的孤儿占位"，核实拒绝且指引可读；构造"无效果孤儿"，核实接管成功；双执行者竞争（延迟回填+短租约）核实恰 1 个活槽、gen 恰推进一次 |
| RDEF-02 预检归因错误 | 预检重写为三态，工位先创建 | 复跑预检实测 ok；人为制造预检自身缺陷（如破坏探针请求构造）核实案例记 Fail 而非 Blocked；核查三份旧报告的更正附录已落且原文未删改 |
| RDEF-03 退避计划丢失 | 计划升级为持久状态机 + 冷启动门控 | 复跑 defect_rdef03（25/25）；独立注入：状态迁移 scheduled→starting 与启动调用之间杀 daemon，核实重启后可恢复；连续失败至 awaiting_intervention 后核实自动路径全停、手动 start 成功清计划；构造待干预 Agent 核实冷启动/通知路径不绕过 |
| RDEF-04 轮次上限失效 | kv 终身单调计数 + notify_max_rounds 入策略 | 复跑 defect_rdef0405（33/33）；独立核实：通知行删除后 dedupe 后缀不归零；`control system config` 可读可改 notify_max_rounds（含越界拒绝）；达上限恰一次 task.notify_exhausted 事件；三路恢复路径（规格变更/候选集变化/调大上限）各验一条 |
| RDEF-05 坏订阅者压制 | 按候选者各自 pending 新鲜度逐个判定 | 构造单坏订阅者 + 健康订阅者共存，核实健康者持续收到对账轮次；坏订阅者陈旧 pending 不阻塞他人 |
| RDEF-06 矩阵可信度 | 行契约重写 + 6 失败行定性修复 + 完整性校验 | 独立重算 97/384/371/0 缺口（不抄报告数字）；随机抽查 ≥10 行的证据（argv+request_id）对应真实调用记录；**负向测试**：人为制造一个参数缺口或一条 FAIL，核实 summary 失败且 exit 1；6 条定性行（CMD-006/028/041/090/010 + init --default-harness）逐条复核修复语义符合 DESIGN |
| RDEF-07 工位别名绕过 | workspace_key 归一化 + 唯一索引 | 独立实测别名矩阵（大小写/尾分隔符/相对路径/../junction/8.3 短名）全部拦截；8 并发同目录 create 恰 1 胜；冲突失败不触碰原 Agent 文件 |
| RDEF-08 clone 显式冲突静默 | 显式 --workspace 冲突 → INVALID_ARGUMENT；继承仍分配新工位 | 双路各验一次（显式拒绝、继承回退成功且工位独立） |
| RDEF-09 --grace 有名无实 | 异步受理 + stopping 中间态 + 持久宽限 + force 跳过 + restart 自动替身 | 复跑 defect_rdef09（20/20）；独立做时钟级核验：op ended_at ≥ grace_deadline、墙钟 ≥ 宽限值；宽限期内领取与进度提交不受影响、凭据仍有效、终止后旧凭据 TOKEN_REVOKED；daemon 重启后续期恢复；restart --grace 替身真实拉起 |
| RDEF-10 凭据错误码 | 四态区分实测稳定 | 以真实客户端路径逐态实测：无凭据/文件缺失 → AUTH_REQUIRED；损坏或未知 token → TOKEN_INVALID；已撤销完整 token → TOKEN_REVOKED（该码本身即证明旧凭据真实送达）；未撤销但实例已结束 → INSTANCE_FENCED；token 正文泄漏扫描 |

## 5. 任务二：施工方回归声明复跑与重算

全部以你的 fresh run 重算数字（不抄报告）：`python scripts/quicktest.py`；`python scripts/acceptance.py`（确定性 88 案例，预期 88 Pass + 3 Blocked，Blocked 必须是三个真实案例未选入确定性口径的设计内排除，逐一核对 Blocked 原因）；`defect_def0102`（25/25）、`defect_def04`（15/15）、`defect_def0305`（12/12）、`defect_rdef01`（35/35）、`defect_rdef03`（25/25）、`defect_rdef0405`（33/33）、`defect_rdef0708`（18/18）、`defect_rdef09`（20/20）、`defect_rdef10`（10/10）；`coverage_matrix.py`（97/97 命令、384/384 参数具名 PASS 行、371 行全 PASS、0 缺口、exit 0）。任何一项复现失败即构成新缺陷。

## 6. 任务三：真实通道三案例 + 本轮新适配器行为

- 以 `HIVE_REAL_HARNESS=zcode-zsession` 独立复跑 AC-HAR-02 / AC-E2E-02 / AC-E2E-03（先跑预检，预检三态语义按 §4 RDEF-02 核查）。
- E2E-03 七步证据链逐步独立核验：实例1 真实 claim → 写 CHECKPOINT → 非终态 stop → 换实例（不同 instance_id、同 agent_id）→ 旧凭据 TOKEN_REVOKED → 实例2 依 checkpoint 续作并 emit done → ended_by 为 Agent 主体。
- 独立验证本轮新修的 zsession 适配器行为（这是本仓引入的新代码路径，重点测边界）：会话回合进行中 send 指令 → 有界 idle 等待（20s）→ 仍忙降级 interrupt 送达；唤醒类通知不等待（wait_idle=False，可丢弃语义不变）；指令类与唤醒类行为差异要分别实测。
- 六个连带产品缺陷修复逐项抽查复核：port update --replace 不再 INTERNAL_ERROR；create/clone --start 启动失败不回滚身份；wait create --claim 校验领取凭据；init --default-harness 落盘并种入 policy；op_stop_agent 异常不遗留 accepted 行；zsession stop 对 404 幂等。
- 抽验上轮已关闭项不回退：DEF-01/02（工位/简报）、DEF-06（可复现性）、DEF-07（清理声明）、DEF-09（名片可见性）、DEF-11（杂项）各至少验一条关键行为。

## 7. 任务四：资源收尾与红线核验

- netstat 核实无 29xxx 段监听残留；无遗留 hive-* 测试会话；无来历不明的测试 daemon（发现先登记再按归属处置，不归你创建的不动）。
- token 泄漏扫描：daemon 日志、审计记录、施工方报告、git 提交历史中不得出现 token 正文。
- trigger-platform 19961 全程只读核验（监听仍在、无写入）。

## 8. 交付物

1. **`SECOND_REACCEPTANCE_DEFECTS.md`**（仓库根）：新缺陷编号 R2DEF-01..，分类 产品缺陷 / 测试缺陷 / 归因未明；每条含 规范依据 / 复现步骤 / 预期 / 实际 / 影响 / 证据位置 / 回归建议。**零新缺陷也必须成文**，说明你的核查面与残余风险。
2. **`SECOND_REACCEPTANCE_REPORT.md`**（仓库根）：结论摘要（明确给出 签收 / 不建议签收）、版本与规范差异固定结果、RDEF-01..10 逐项关闭判定表、施工方声明核实结果表（区分"复跑复现"与"独立探针"）、真实三案例结果、红线核验结果、资源收尾记录。
3. 报告写完留在仓库根，**不要 git commit**（入库由业主决定）；不改任何既有文件。
4. 全程证据落 `work/independent2/<timestamp>-*/`，报告中的每个结论必须能回溯到具体证据文件。

开工。先固定版本基准，再按任务一至四推进；遇到任何与施工方声明不符的现象，先独立复现确认再定性。
