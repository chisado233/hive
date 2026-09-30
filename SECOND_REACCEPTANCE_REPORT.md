# Hive V1 第三轮独立复验报告（SECOND_REACCEPTANCE_REPORT）

- 验收Agent：第三轮独立复验（与第二轮返修施工方无关联）
- 日期：2026-09-30
- 代码基准：git **`1906cce`**（链 c409490 复验基准 → 35448a1 产品修复 → 2540004 参数矩阵 → 1906cce 真实通道收口；工作区干净，仅未跟踪 THIRD_ACCEPTANCE_PROMPT.md）
- 缺陷清单：[SECOND_REACCEPTANCE_DEFECTS.md](SECOND_REACCEPTANCE_DEFECTS.md)（RDEF-01..10 判定 + 新缺陷 R2DEF-01/02）
- 独立证据根：`work/independent2/`（各子目录含 run-manifest 与逐项 JSON）

---

## 1. 结论摘要

**建议签收（有条件记录项）。** 第二轮返修的"全部收口"声明经独立核实成立：RDEF-01..10 **全部关闭**（每项均有施工方脚本之外的独立探针证据）；12 项套件数字逐项复现；**真实三案例 3/3 Pass**（独立复跑，AC-E2E-03 七步单链贯通）；红线核验全过。本轮新发现 2 项 Low 级缺陷（收尾manifest漏登记E2E-02替换实例、信封retryable覆盖details），均不构成签收阻碍，建议随后续小修收口。ACCEPTANCE §7.2 五项签收条件逐项满足（见§6）。

与前两轮"不建议签收"结论的差异来源：不是标准放宽——规范本轮仅增量一行（见§2），而是上轮列明的阻碍（幂等接管双执行、对账上限失效、真实案例归因、参数矩阵可信度、--grace、工位别名）逐项被真实修复且被我独立复验。

## 2. 版本与规范差异固定

- HEAD=`1906cce`，四提交链与任务书一致；工作区除任务书外干净。
- **规范差异独立核实**：`git diff c409490..1906cce -- DESIGN/ACCEPTANCE/IMPLEMENTATION_PLAN` = **仅 DESIGN §10.1 新增 `notify_max_rounds` 一行**（默认12、1..1000、达上限记事件+三路重置）——与声明一致，属补全收紧（RDEF-04 要求可配置可文档化），无任何放宽；ACCEPTANCE 零改动。
- 三份旧报告（FIX/REGRESSION/ALL_FIXES）更正附录 **0 行删除、纯增量**，原文与失败证据保留（RDEF-02 核实项）。

## 3. RDEF-01..10 逐项关闭判定

**全部关闭**。逐项独立验证证据见缺陷清单判定表，要点：

- **RDEF-01**：四类命令孤儿占位（效果已提交）同key重试全部拒绝+指引+0第二对象；无效果孤儿干净接管；双执行者竞争恰1活槽/gen恰推进一次/响应一致；命令分类（7外置+其余原子）核实。
- **RDEF-02**：预检三态落地；三旧报告纯增量更正；预检实测ok并驱动三案例真跑。
- **RDEF-03**：退避状态机（scheduled/starting/failed/awaiting_intervention）各转移独立驱动：starting中断恢复保留同档位计划并按期重启；待干预态冷启动全停、手动start清计划；计划daemon重启持久。
- **RDEF-04/05**：轮次kv终身单调（:1→:2→:3不归零）；notify_max_rounds可读可改+越界拒绝；上限事件按episode恰一次（受控实验）；amend/调大上限恢复续轮；坏订阅者与健康者共存互不压制；零新增Task/receipt。
- **RDEF-06**：独立重算 97/97、384/384、371行全PASS、0缺口、exit 0；**负向双注入**（FAIL行→exit1；参数缺口→param_gaps+exit1）；12行证据抽检可追溯（10直查idempotency+2按设计无痕）；6定性行中3条产品侧修复独立实证。
- **RDEF-07/08**：别名矩阵（大小写/尾缀/../junction/8.3短名）全拦截；全新目录8并发恰1胜；clone显式拒绝、继承新工位。
- **RDEF-09**：grace全矩阵独立时钟级验证（异步受理/stopping/期内领取与结算可用且凭据有效/op时长≥宽限/到期TOKEN_REVOKED/daemon重启续期/restart替身/force跳过）。
- **RDEF-10**：四态错误码真实客户端路径稳定复现；token零泄漏。

## 4. 施工方声明核实（复跑复现 vs 独立探针分开计）

| 声明 | 复跑复现（fresh run） | 独立探针（其脚本之外） |
|---|---|---|
| quicktest 通过 | ✓ | — |
| 确定性88案例 88P+3B | ✓（88 Pass+3 Blocked；Blocked=三真实案例未选入，设计内） | — |
| def0102=25/25, def04=15/15, def0305=12/12 | ✓✓✓ | 上轮等效状态探针已由本轮S1/F5重证 |
| rdef01=35/35, rdef03=25/25, rdef0405=33/33, rdef0708=18/18, rdef09=20/20, rdef10=10/10 | ✓×6 | S1/S2+F3redo/S3+F1/DB直查/S4/S5/F2/S6 全部独立复证 |
| coverage 97/97、384/384、371行、0缺口、exit0 | ✓（重算不抄数） | 负向双注入+12行抽检+3条产品侧定性实证 |
| 真实三案例 Pass | ✓（独立复跑3/3） | E2E-03七步逐条核验（两代实例/checkpoint/ended_by） |
| 连带6缺陷修复 | — | S7实证4条（port replace/create--start/wait--claim/init默认harness）；stop异常无accepted残留✓；zsession 404幂等（代码+真链） |

## 5. 真实三案例与本轮新适配器行为

- **AC-HAR-02 / AC-E2E-02 / AC-E2E-03：3/3 Pass**（run `20260930-141738-56ab1c`，zcode-zsession/GLM-5.3±Flash，会话根独立工位）。E2E-03 七步单链：实例1真实claim→CHECKPOINT含实例ID（独立读取）→非终态stop→换实例（不同instance_id同agent_id）→旧凭据TOKEN_REVOKED→实例2依checkpoint续作产出RESULT→emit done，ended_by=agent主体。
- 新适配器行为：`ZSessionAdapter.send` 有界idle等待（20s/1.5s探测）+仍忙降级interrupt；唤醒类 wait_idle=False（scheduler:478）指令类 True（runtime_ops:345/374）——代码核实+真实链路送达证据（E2E-03二阶段指令实际到达并执行）。
- 不回退抽检：DEF-01/02（真通道简报协议恰1次/4108B稳定、简报在runtime区）；DEF-06（88/88两轮复现）；DEF-07（本轮收尾netstat无残留，见R2DEF-01的量级注记）；DEF-09（rdef套件+上轮语义保持）；DEF-11（restore local-only/排序/死代码保持）。

## 6. 签收条件核对（ACCEPTANCE §7.2）

1. 90案例实际状态：确定性88 Pass（两轮复现）+真实3 Pass（独立复跑），无NotRun；§1.1阻断项全过 ✔
2. 每命令及参数实际检查记录：97/97命令业务执行、384/384参数具名PASS行、0缺口（独立重算+负向注入验证判定器有效性）✔
3. AC-HAR-02/AC-E2E-02真实证据 + E2E-03：3/3 Pass 且七步单链 ✔
4. 运行状态/结果/回执/文件互证：E2E-03 文件+账本+实例三代互证 ✔
5. 可复现README：本轮全部按 README/任务书命令复现成功 ✔

**判定：满足签收条件。** 附带两条Low级记录项（R2DEF-01收尾manifest漏E2E-02替换实例、R2DEF-02信封retryable覆盖details），建议在签收后随小版本收口，不构成本轮阻碍。

## 7. 红线核验与资源收尾

- **token零泄漏**：本轮10个实例凭据token在daemon日志、全部jsonl证据、两份施工方报告、git全部跟踪文件中0命中；token仅存在于受保护凭据文件。
- **端口**：验收全程动态端口，收尾netstat无29xxx段监听；无遗留测试daemon。
- **会话**：本轮消耗zsession会话4个（真实三案例），其中1个因R2DEF-01被收尾漏杀，已由我按记录kill（cleanup_leftover_e2e02.json）；其余3个随案例受管停止。
- **trigger-platform（19961）**：验收前后均在监听、零写入、进程未动。
- **既有文件**：未修改任何施工方/历轮文件；负向注入使用coverage_matrix副本（工作于独立run目录；一次误写入work/coverage的负向run已迁移至我的证据目录并留痕）。
- 工作区新增文件仅：本报告、SECOND_REACCEPTANCE_DEFECTS.md、work/independent2/**（未commit，入库由业主决定）。

## 8. 残余风险与后续建议

1. R2DEF-01/02 随小修收口（改动面小：manifest登记+to_dict取值优先级）。
2. 观察项：coverage summary的rows/passed恒差1（teardown后追加行），建议把CMD-020收尾行移入summary前。
3. 长期：notify_max_rounds事件粒度=每episode一次，运维侧消费该事件时应按（task,episode）去重；已在DESIGN增量行语义内。
