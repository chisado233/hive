# Hive V1 修复轮独立复验报告（INDEPENDENT_REACCEPTANCE_REPORT）

- 验收Agent：修复轮独立复验（与施工方无关）
- 日期：2026-09-30
- 代码基准：git `78380ad`（=修复终态 `4ed1f05` + ALL_FIXES.md），工作区干净
- 验收基准：DESIGN.md / IMPLEMENTATION_PLAN.md / ACCEPTANCE.md（git核对：**修复轮未改动任何规范文档**——全部差异为实现修复+测试补充+报告，无需区分规范修订）
- 上轮缺陷与结论：[INDEPENDENT_DEFECTS.md](INDEPENDENT_DEFECTS.md) / [INDEPENDENT_ACCEPTANCE_REPORT.md](INDEPENDENT_ACCEPTANCE_REPORT.md)
- 本轮新缺陷：[INDEPENDENT_REACCEPTANCE_DEFECTS.md](INDEPENDENT_REACCEPTANCE_DEFECTS.md)（RDEF-01..10）
- 环境：Windows 10 19045 · Python 3.12.4 · zsession 8765（健康）· trigger-platform 19961 全程只读未触碰
- 独立证据根（不覆盖施工方与上轮证据）：`work/independent/20260930-135645R|R2|R4|R3|R3C|env_probe*`、复跑 `work/acceptance/20260930-054913-49b7d8`、`work/defects/`（本轮重跑）、`work/coverage/20260930-135440-b262`

---

## 1. 结论摘要

**仍不建议签收，但较上轮显著接近。** 施工方六项声明中：88/88、三个缺陷套件、矩阵205行均**复现通过**；DEF-01/02/06/07/09/11 确认关闭；DEF-03/04/05/08/10 主体成立但被本轮新缺陷击穿关键边界。三个真实案例的"环境Blocked"**归因错误**（执行层实测正常，是预检自身的events请求400），且我用等效真实链路独立复跑：HAR smoke **真实通过（含模型回读）**、E2E-02 **真实通过**、E2E-03 **部分验证**（七步各有真实证据、未单链贯通；期间证实DEF-01修复的深层效果——真实Agent按注入协议自发写接续文档/留含claim_id的评论）。

- 施工方声明能证明的：确定性回归全绿可复现；工位/简报缺陷真修好（真实通道证实）；崩溃退避真实现（阶梯精确）；幂等快路径原子性成立。
- 不能证明/被证伪的：**"不会创建两个Agent、重复授权"（RDEF-01实测反例）**；**"ZCode执行层损坏"（实测正常，预检自身400）**；**"384参数逐项覆盖"（矩阵有6条失败记PASS+79项无行级记录，无完整性校验）**；对账轮次上限与压制语义（RDEF-04/05）。

## 2. 版本与差异固定

- `b2288d3` → `4ed1f05`：24文件 +3416/−242；分类：产品修复（hive_core 12个文件）、测试补充（4个新脚本+acceptance/quicktest改造）、报告文档。**DESIGN/ACCEPTANCE/IMPLEMENTATION_PLAN 零改动**——不存在借规范修订放宽验收。
- HEAD=`78380ad` 仅追加 ALL_FIXES.md。上轮两份独立报告被施工方一并提交入库，内容与我所写一致（未被篡改）。
- 施工方修复过程连带修掉的8个产品bug（ALL_FIXES 第八组）中，我复核了两个关键项：stale running启动操作解除（实测：新实例创建、旧操作转failed、单活槽✓）、schema prod过滤（复跑AC-CLI-01通过✓）。

## 3. DEF-01..11 逐项关闭判定

| # | 判定 | 独立证据要点 |
|---|---|---|
| DEF-01 工位split-brain | **关闭** | 真实通道：简报含协议头恰1次+个体说明1次（R3/B_startup_check）；显式工位三件套落盘；协议缺失改抛WORKSPACE_UNAVAILABLE；共享防护实际拦截了本验收自己的第二个同径agent。边界缺陷RDEF-07（大小写别名）/RDEF-08（clone静默） |
| DEF-02 STARTUP反馈循环 | **关闭** | 简报迁runtime/startups每实例一份；残留旧STARTUP防御排除（brief不含旧简报、原文件不动）；4轮重启3889B恒定；仅当前实例凭据路径；glob未收紧（中文名/空格名加载、子目录不加载） |
| DEF-03 退避死键 | **主体关闭** | 配置[2,4,8]精确消费（backoff_s=2→4→attempts=3档8s）；不同步重启；限次生效；paused不拉起；daemon重启后计划持久并按期触发。**边界：RDEF-03 启动失败即丢计划** |
| DEF-04 幂等占位 | **部分关闭** | 快路径占位+执行+回填单事务（击杀验证：回滚后同key重试恰1 Task）；慢路径租约接管对 start/stop/fire 安全（fire靠事件去重、start靠槽位合并，均实测）。**RDEF-01：create/grant 接管重跑=双Agent/双授权**；孤儿清扫存在但"核实外部效果"语义未实现 |
| DEF-05 唤醒对账 | **部分关闭** | pacing实测（attempts+1且send_after=+5s）；干净场景对账重入队出现 `reconcile:<task>:1`；jitter落地（send_after=now+U(0,jitter)）。**RDEF-04：轮次上限失效（:1无限循环）+notify_max_rounds不可配置不可见；RDEF-05：坏订阅者滞留pending压制整端口对账** |
| DEF-06 可复现性 | **关闭** | 88/88两次独立复现（施工方041804+本验收054913）；TASK-08改root过滤+精确ID；CTX-01硬断言；AC-TASK-02并发20条逐条入commands.jsonl；产品代码`if False`=0 |
| DEF-07 清理声明 | **关闭** | 29963遗留已受管停止（验证无监听）；run-manifest登记pid/home/endpoint；cleanup_log如实记录（含一条ok:false未被吞掉）；当前无任何验收端口残留；control system stop语义未变 |
| DEF-08 参数覆盖 | **部分关闭** | 97/97命令业务执行✓（复算commands_executed）；--timezone实证（两cron计划时点差8h，事件ID含计划时刻）；分页（created DESC+ID次键/无重叠/游标）✓；--cascade✓；--dry-run零写入✓。**RDEF-06：矩阵6条失败记PASS+105条kind=bool无条件PASS+79/384无行级记录+无完整性校验；RDEF-09：--grace有名无实** |
| DEF-09 名片可见性 | **关闭** | C调producer/trigger/channel/harness list均FORBIDDEN；owner正常；port/agent list的discover门槛为既有行为（C无discover授权，非本轮收紧） |
| DEF-10 interrupt/证据强度 | **部分关闭** | steer|interrupt统一+mode透传（代码核实）；smoke新增yolo/model**会话回读**——model回读实证匹配（R3C/A_smoke：readback=GLM-5.3-Flash✓）；yolo回读字段缺失时如实标注（zsession不返回mode）。**但预检zsession_env_preflight自身HTTP 400（RDEF-02），三案例被错误Blocked** |
| DEF-11 杂项 | **关闭** | restore服务端INVALID_ARGUMENT+local-only提示（HTTP直测）；notify_jitter实现；visible_tasks排序实证；死代码/占位行清零 |

## 4. 施工方六项声明的核实结果

| 声明 | 结果 |
|---|---|
| 确定性88/88 | **复现**（本验收独立复跑 88 Pass + 3 Blocked；quicktest过） |
| DEF-01/02回归25/25 | **复现**（且真实通道独立证实修复） |
| DEF-04回归15/15 | 脚本通过**但覆盖不足**：仅窗口1（占位后handler前）与快路径；窗口2/3/4/5未测——窗口2实测暴露RDEF-01 |
| DEF-03/05回归12/12 | 脚本通过**但边界未测**：RDEF-03/04/05均在脚本盲区 |
| 参数矩阵205/205、97/97 | 行通过**但可信度缺陷**（RDEF-06/09）；"384逐项"不成立 |
| 真实三案例Blocked（外部） | **归因证伪**（RDEF-02）：执行层正常、预检自身400；等效真实链路复跑HAR/E2E-02通过、E2E-03见§6 |

## 5. 原套件 vs 独立补充验证（分别统计）

**原套件（施工方脚本，本验收复跑）**：quicktest通过；acceptance 88 Pass/3 Blocked/0 Fail（Blocked均为预检400）；defect_def0102 25/25；defect_def04 15/15；defect_def0305 12/12；coverage_matrix 205/205行、97/97命令。

**独立补充（本验收探针）**：
- P1 工位边界：大小写别名绕过（**新缺陷**）、clone静默忽略（**新缺陷**）、旧STARTUP排除✓、4轮恒定✓、glob✓；
- P2 幂等窗口：W2 create双Agent/双grant（**新缺陷**）、fire/start安全✓、W3接管合并✓、W5重放稳定✓、迁移002/003旧库(v1→v3)升级无损且幂等✓；
- P3 退避/通知：阶梯[2,4,8]✓、daemon重启持久✓、**失败丢计划**、干净对账:1轮出现✓、**上限失效循环**、（首轮探针）**坏订阅者压制对账**、decline候选不重唤醒✓、pacing✓、零副作用（无新Task/回执）✓；
- P4 参数实效：timezone计划时点差8h✓、分页✓、cascade✓、dry-run✓、**--grace死参数**；
- P6 杂项：stale-op解除不双开✓、restore边界✓、schema test=97(含5 test)/prod=92✓（复跑AC-CLI-01）、名片门槛✓、run-manifest+cleanup按具体home✓、随机run-id/动态端口✓（两轮同秒不冲突）。

## 6. 真实ZCode三案例（重点五）

**归因独立对照**：
1. 直连宿主最小任务：两模型会话 working→idle ~9s；
2. 决定性验证：直连会话被指示创建文件→`tmp/env-probe-marker.txt` 实际产生（内容EXECUTOR_OK）——**执行层正常**；
3. 施工方预检失败=其events请求HTTP 400（请求构造问题），非执行层；
4. 结论：三案例Blocked依据不成立，属测试缺陷+报告归因错误（RDEF-02）。

**等效真实链路复跑（绕过坏预检，经Hive适配链，会话根=D:\agent_workspace）**：
- **AC-HAR-02等效：通过**。`control harness validate --smoke`：start=真实会话句柄、send=accepted、**model_at_start=会话回读GLM-5.3-Flash与请求一致**、stop=stopped、yolo=启动侧声明（zsession不回读mode，如实标注）→ overall=passed（20260930-142923R3C/A_smoke.json）。
- **AC-E2E-02等效：通过**。真实Agent（GLM-5.3-Flash）冷启动→读input.txt→原样写result_e2e02.md→自行`inbox claim`+`emit done`（ended_by=agent:agt_f986…）；启动简报协议+个体说明各1次（DEF-01真通道修复兑现）；工位无STARTUP污染（20260930-140842R3/B_*）。
- **AC-E2E-03等效（任务中途换实例续作，七步逐步证明）：见下**。

### AC-E2E-03 等效实测（5次尝试；判定：**部分验证，未获七步贯通的完整通过**）

| 步骤 | 结果 | 证据 |
|---|---|---|
| 1 实例1 claim | **5/5次真实达成**（owner=agent_id，claim_id入评论） | R3C-143254/D_phase1 + 任务评论 cmt_e65d… |
| 2 真实Agent写出checkpoint并独立读取验证 | **达成**（Agent按系统协议把接续点写入工位CONTEXT.md + checkpoints/r3c-e2e03-handoff-*.md，并在任务上留正式评论记录claim_id——此即DEF-01修复的深层证据：Agent真在遵循注入协议） | D:\agent_workspace\CONTEXT.md、checkpoints\、评论 |
| 3 任务非终态才stop | 达成 | D_phase1 non_terminal |
| 4 换实例+旧凭据拒绝 | **另一次运行中达成**（3b：同agent/新iid/旧凭据被拒AUTH_REQUIRED） | R3/C_phase2 |
| 5-7 续作完成 | **由原实例达成**（第5次运行：依接续文档写RESULT.md含双实例标记、emit done、ended_by=agent、handoff文件幸存）——但该次运行中"中途stop"静默未生效（实例未换），完成者非新实例 | R3C/E_final.json + instances表（仅1实例） |

- 净结论：七步各自都有真实证据，但**没有任何一次运行同时满足"真实换实例"+"新实例续作完成"**。第5次运行暴露一个待解释现象：abort路径的`control agent stop`未生效（实例保持live）——该次调用结果被我的探针忽略，不能定性为产品缺陷，记录为观察项。
- 施工方案例本身仍Blocked于预检缺陷（RDEF-02）。本验收为此消耗7个真实会话后停止重试。

## 7. 97命令与384参数的真实覆盖映射与缺口

- 97/97命令有业务执行行（两轮matrix一致）。
- 行级参数记录：205行；按规范化名称匹配，**79/384项参数声明无行级具名记录**（位置参数TASK/AGENT/PORT等与--reason/--to类全程携带项为主——命令确有使用，但无逐项效果断言）。缺口清单见RDEF-06分析（可复算：manifest declared vs matrix行名归一差集）。
- 6行"失败记PASS"：CMD-006/CMD-010×2/CMD-028/CMD-041/CMD-090。
- 实证有效重点项：--timezone/--cascade/--dry-run/--if-version(复跑)/分页/--include-declined(SCHED-04)/--mark-seen/备份三开关(矩阵行+AC-STORE-03)。**--grace无行为效果**。
- schema组成：test=97（含5个control.test叶子）、prod=92/0（复跑AC-CLI-01的prod辅助daemon断言）。

## 8. 88+3 案例口径

- 确定性88：**88 Pass / 0 Fail / 0 NotRun**（本验收复跑）。
- 真实3：AC-HAR-02（施工方脚本Blocked—预检缺陷；本验收等效**通过**，含模型回读）、AC-E2E-02（同上，等效**通过**）、AC-E2E-03（施工方脚本Blocked；本验收5次等效尝试**部分验证**——七步各有真实证据但未在单次运行贯通：claim/checkpoint/续作完成✓（真实Agent按协议写接续文档并done），真实换实例+新实例完成未同链达成，见§6）。
- 注册表核对：90确定性+真实口径未变，新增AC-E2E-03在册，无案例删除/改名。

## 9. 资源收尾（本验收）

- 本轮探针/复跑daemon全部受管stop（各探针脚本内置；netstat核对29962-29964/31924/探针端口无监听残留）。
- 真实zsession会话：env探针2个+R3两个agent的会话已kill（R3/R3C脚本teardown）；R3C-C段会话随脚本结束kill。
- trigger-platform（19961）与zsession（8765）为用户服务，全程未触碰。
- 探针在共享根D:\agent_workspace仅产生agent.md/config.json/prompt.md（r3/r3c两个agent的派生文件，位于仓库外根目录——由共享防护要求每agent独占，本轮r3c探针因r3已绑定同根而改用同agent续作方案）；产物文件在tmp/hive-ind-r3*。

## 10. 签收判定

- ACCEPTANCE §7.2 条件2（参数级记录）：**仍不满足**（RDEF-06：完整性校验缺失+失败记PASS+79项无行级证据；RDEF-09 --grace）。
- 真实接入三案例：**环境可用**（归因已证伪）；等效验证 HAR-02✓、E2E-02✓、**E2E-03未完成**（§6）；施工方脚本内三案例仍Blocked于预检缺陷——修复预检后应可直接转绿（E2E-03能否通过未知）。
- 新增阻碍：RDEF-01（租约接管双执行，High）必须修复；RDEF-03/04/05（恢复与对账语义）应修。
- **结论：不满足签收。** 建议修复顺序：RDEF-02（预检，小改）→ RDEF-01（幂等接管分流）→ RDEF-04/05（对账轮次与压制）→ RDEF-03 → RDEF-06矩阵加固（含失败行定性）→ RDEF-07/08/09。修复后重跑：88套件+三真实案例（预检修后应全绿）+本验收探针脚本（可直接复用）。
