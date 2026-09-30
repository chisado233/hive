# Hive V1 独立验收报告（INDEPENDENT_ACCEPTANCE_REPORT）

- 验收Agent：独立验收（与施工方无关的复核会话）
- 日期：2026-09-30
- 代码基准：git `b2288d3`（HEAD，工作区干净）＝ `cc47a98`（交付代码）＋ REPORT.md
- 验收基准：DESIGN.md / IMPLEMENTATION_PLAN.md / ACCEPTANCE.md（2026-09-30 版，git核对未在施工后改动）
- 环境：Windows 10 19045 · Python 3.12.4 · 本机 zsession（127.0.0.1:8765，ZCode会话编排）· trigger-platform（19961）全程只读未触碰
- 独立证据根（不覆盖施工方证据）：
  - `work/independent/20260930-003405/` —— 探针A–E（隔离home/端口29980）
  - `work/acceptance/20260930-003644/` —— 88确定性案例独立复跑（端口29971）
  - `work/acceptance/20260930-003816/` —— 真实双案例独立复跑（端口29973，zcode-zsession/GLM-5.3±Flash）
  - `work/independent/20260930-004638F/` —— 探针F（真实会话中途换实例续作，会话根=D:\agent_workspace）
- 缺陷清单：[INDEPENDENT_DEFECTS.md](INDEPENDENT_DEFECTS.md)（DEF-01..11）

---

## 1. 结论摘要

**不建议按"90/90 已达签收条件"签收。** 核心账本语义与并发不变量质量较高且可独立复现；真实 zcode-zsession 通道独立复验通过；但存在两处 High 级契约缺陷（真实harness工位缺系统协议/个体说明；STARTUP.md 反馈循环携带已吊销凭据路径）、一处已声明的退避未实现（且为死配置键）、一处幂等占位不可恢复，且"90/90"本身不可稳定复现（独立复跑 87/1/2）、参数级签收条件未满足、验收清理声明与事实不符。

- 施工方"90/90"**能证明**：90个案例在其单次运行中确有真实执行与断言存证；88个确定性案例的核心不变量（并发领取/启动唯一、幂等、终态/回调/等待/恢复/权限围栏）在本次独立复跑中 87/88 复现成立；真实双案例的证据真实（账本含真实 zsession 会话句柄、任务由真实 Agent 自行 claim+emit done 结算）。
- 施工方"90/90"**不能证明**：验收可稳定复现（AC-TASK-08 为哈希运气断言，本复跑即失败）；97命令×384参数的 §5.2 参数级覆盖（实际为抽样，16/97命令无业务执行证据）；真实Agent收到了系统协议与个体说明（实际没有，见 DEF-01）；启动简报在多次重启下的稳定性（见 DEF-02）。

## 2. 验收版本、Git 与基线完整性

- 提交链：`cdd8882`（初版交付，含三份基准文档+全部代码+验收脚本）→ `cc47a98`（幂等占位、cmd_start竞态修复，仅改 PROGRESS/local.py/service.py）→ `b2288d3`（仅加 REPORT.md）。
- **DESIGN/ACCEPTANCE/IMPLEMENTATION_PLAN 在交付后未被改动**；不存在"施工中放宽验收标准"的痕迹。注意：基准文档与实现同仓首次提交，git 无法证明"设计先于代码"，但文档内部一致、未见为迁就实现而回改的迹象。
- 时间核对（run-id 为 UTC，提交为 +0800）：最终run `20260929-191540`（UTC 19:15:40 = +0800 03:15:40）早于 `cc47a98`（03:24:09）——报告所称"修复后复跑全绿"的代码对应关系成立；工作区=该提交代码，可复现性检验有效。

## 3. 对 REPORT 关键声明的独立结论

| REPORT 声明 | 独立结论 |
|---|---|
| §3 "90/90 全过（单次完整运行）" | **属实但不可稳定复现**：证据真实（90唯一案例/1289条命令/断言存证），但独立复跑=87 Pass/1 Fail(AC-TASK-08)/2 Blocked；失败为断言的分页随机性，非产品重复造单（DB核对两run均恰1条）——见 DEF-06 |
| §2.3/§5.5 "真实harness启动简报=系统协议+个体prompt+工位md+实时摘要+CLI接入信息" | **与其实际证据不符**：真实E2E工位的 STARTUP.md 中协议与个体说明出现0次（DEF-01）；且第二次启动的简报嵌套上一次简报、含已吊销旧实例凭据路径（DEF-02） |
| §3.2 关键阻断项全部通过（并发/围栏/回调/等待/恢复/泄露） | **独立复现成立**：本验收复跑与探针C/D均复核通过（详见 §6、DEF清单"未发现问题事项"） |
| §4 修复项（每线程连接/幂等占位/start竞态/终态顺序/batch原子/退役匹配/zsession 404重试） | **代码与运行均核实**；但幂等占位的进程崩溃路径不可恢复（DEF-04） |
| §6 已知限制"restart_backoff 仅限次未做逐级间隔" | **属实且更严重**：`restart_backoff` 为无消费方死配置键，配置任意值均即时重启（DEF-03） |
| §6 已知限制"producer/trigger/channel 名片对已认证主体可见" | **复核属实**（探针E），仅名片级、未见私有信息泄露 |
| §7 "验收 daemon 已全部停止" | **不实**：29963 遗留验收 daemon（home=work/acceptance/20260929-180325）验收后仍存活（DEF-07） |
| §7 "本轮创建的21个 hive-* 会话已 kill+archive" | 无法从本会话独立核实（zsession查询角色无权限）；从账本看终验run实例2遗留 `phase='live'`，与"外部kill收尾"方式一致，未见反证 |

## 4. 90案例 / 275步骤 / 97命令 / 384参数 覆盖核算

- **90案例**：最终run确有90个唯一案例、每例 commands/results/assertions 三件套齐全、状态全部Pass；分组统计与REPORT一致。
- **275步骤**：ACCEPTANCE 共275个"步骤"标记；驱动脚本对绝大多数步骤有对应执行与断言。已核实的重要例外：AC-E2E-02 步骤3的"任务中途保存checkpoint→停→新实例继续完成"**未被驱动**（脚本在任务完成后才stop/start，仅验证换实例与旧凭据围栏）——本验收以探针F独立补测（见 §7）；另有弱化断言/禁用分支清单见 DEF-06。
- **97命令**：manifest 97项与 DESIGN §14 一致（schema/help 级全覆盖）；最终run commands.jsonl 有业务执行记录者 **81/97**；其余16项（含 `control task trash --cascade`、port.update、producer.show/update/revoke、trigger.update、channel.show/update/list、human.list、harness.list/show、system.serve 等）仅有 help 级检查且未逐条入证。
- **384参数**：AC-CLI-02 为抽样（17条命令/8条断言），远不满足 ACCEPTANCE §5.2 的逐参数记录要求；§5.3 特别项中 `--cascade`、`--grace/--force`、`--missed/--timezone`、`--apply` 等无专项证据。**签收条件2未满足**（DEF-08）。

## 5. 独立复跑结果

| 项 | 命令 | 结果 |
|---|---|---|
| quicktest | `python scripts/quicktest.py` | **通过**（全部链路正常） |
| 88确定性案例 | `HIVE_ACC_PORT=29971 python scripts/acceptance.py` | **87 Pass / 1 Fail / 2 Blocked**；Fail=AC-TASK-08（断言随机性，产品无缺陷）；Blocked=AC-HAR-02/AC-E2E-02（未设真实环境变量，符合脚本设计） |
| 真实双案例 | `HIVE_REAL_HARNESS=zcode-zsession … acceptance.py AC-HAR-02 AC-E2E-02` | **2/2 Pass**。AC-E2E-02：真实ZCode会话（GLM）冷启动→读 input.txt→原样写 output.md→自行 `inbox claim`+`emit done`（ended_by=agent:agt_5e31…），输出与输入逐字一致；stop→start 换实例（同agent_id、新instance_id、旧凭据拒用） |

## 6. 针对性独立验证（新增测试与证据）

### A · 重启退避（DEF-03）
探针A（探针目录 `A_restart_backoff.json`）：3次确认崩溃均在 ~0.13s 内完成自动重启（账本 started_at 同秒）；将 `restart_backoff` 配为 [300,600,900] 后仍 0.129s 即重启——键无消费方。`restart_max_per_hour=3` 限次正常（第3次即 RESTART_LIMIT_EXCEEDED）。enabled/paused 行为：paused 崩溃不拉起（复跑 AC-RUN-07 通过）。**原90/90未发现原因：AC-RUN-07 未断言间隔。**

### B · STARTUP.md 与上下文（DEF-01/02）
- 探针B（process harness，走与 zsession 相同的真实简报路径）：3次启动 STARTUP.md 965→1965→2965 字节线性增长，接入信息段 1→2→3 次，第2轮起含已吊销旧实例凭据路径；配置工位内 agent.md/config.json/prompt.md **始终缺失**（写入默认根 `home/workspaces/<id>`），启动简报协议头计数=0。
- 施工方真实E2E工位复核（`%TEMP%\hive-e2e-ws-20260929-191540`）：协议0次/个体说明0次；第二次简报嵌套第一次（`工位文件 STARTUP.md` 段）、新旧两份责任摘要并存、同时含已吊销（ins_906b…）与现行（ins_a210…）凭据路径、上轮产物 output.md 被当上下文回流。

### C · 幂等占位与事务（DEF-04）
探针C：20并发同key→1个Task（20/20 ok）；同key异内容→IDEMPOTENCY_CONFLICT；跨主体同key隔离正常；**孤儿占位**（占位已提交、回填前进程死亡的等价账本状态）→ 重试每次等待10.2s后 STORAGE_UNAVAILABLE 且永久阻塞（两次复测一致），无启动清理。嵌套事务：`Store.write()` 可重入无SAVEPOINT，但 emit batch/done+dispatch/comment+@/create+wait 均为"静态预校验+单外层事务+内层异常穿透"，未发现可提交半批的捕获路径（复跑案例亦验证）。

### D · 实例唯一性与ZCode竞态（通过）
探针D：20并发 start → 1新建+19 already_running（0错误，DB唯一活槽）；20并发 claim → 1胜+9 already_owned+10 TASK_ALREADY_CLAIMED，有效claim/事件均恰1。代码复核：spawn 后就绪轮询（10×0.6s）与 send/kill 404重试（6×1s）不会产生第二个会话；stop unknown 保留槽位且不启替身；daemon 重启按账本核对旧实例；旧凭据/旧claim 围栏在复跑与真实E2E中均验证。

### E · 通知、回调与可见性（DEF-05/09）
探针E：send 必败的真实路径下通知3连败即删；时钟推进2h后无任何对账重入队/再唤醒（notifications 恒空、任务仍 queued），仅产生可查询的 `task.unclaimed` 系统事件——"无新事件则不自愈"；`notify_retry_after`/`send_unknown_retry_after`/`notify_jitter` 均死键。无 callback 不通知发送者、forward 保留 Task ID 与回调目标（复跑通过）。名片可见性：agent C 可见 producer/channel 名片（已声明限制，未见私有泄露）。

### F · 真实端到端与可替换实例
- 独立复跑 AC-HAR-02/AC-E2E-02：**通过**（见 §5）。真实性判定依据：账本 instance 记录 harness=zcode-zsession、handle=sess:*；任务 ended_by=agent:agt_…（由真实会话用其实例凭据执行 claim/done，验收脚本未代写产物或代为结单）；output.md 逐字一致。
- **探针F（本验收新增，按 owner 指示以 D:\agent_workspace 为会话根；证据 `work/independent/20260930-004638F/probeF/`）**：两阶段任务——要求实例1"读 input2.txt → 写 PROGRESS.md 后等待（不领任务）"，随后任务未完成时 stop→start 换实例，要求实例2"写 result.md → claim → emit done"。**实测（如实记录）**：
  - 成立的部分：实例1期间任务保持 queued（中途未完）；stop→start 后同 agent_id、新 instance_id（ins_2dc0…→ins_ddb1…）、旧实例凭据拒绝（TOKEN_REVOKED）；**实例2真实自行 claim+emit done 完成任务**（state=done，ended_by=agent:agt_0d38…）——"更换实例续接未完成责任"的核心不变量在真实通道成立。
  - 未成立的部分：两个实例都**没有执行文件步骤**（PROGRESS.md、result.md 均未产生）——真实Agent按自己的理解直接走了结单路径；单次尝试未重试（控制真实会话成本）。因此"保存checkpoint→文件续作"未获完整演示，平台侧消息送达与工位保留均正常，归因偏向Agent指令遵循而非平台机制；留待修复 DEF-01（补系统协议）后重测。
  - 同步复现缺陷：第二次启动的 STARTUP.md（根目录副本已存证 `STARTUP_root_copy.md` 后清理）**8747 字节、协议头0次、含已吊销旧实例凭据路径、嵌套上一轮简报1层**——DEF-01/02 在以 D:\agent_workspace 为根的真实环境再次复现。
- smoke 证据强度备注：yolo 证据为"以 yolo=True 启动"（适配器硬编码，未回读会话）；model_at_start 证据为"以该模型重启成功"（未核实会话实际模型）——不构成失败，但证明力弱于 REPORT 措辞（DEF-10）。

## 7. 已知未实现项 / 未验收项汇总

| 项 | 状态 | 来源 |
|---|---|---|
| restart_backoff 逐级间隔 | 未实现（死配置键），限次已实现 | 施工方已声明 + 本验收证实 |
| §5.2 参数级检查记录 | 未完成（抽样替代） | 本验收核算 |
| 16/97 命令业务执行证据 | 缺失 | 本验收核算 |
| AC-E2E-02 步骤3"任务中途换实例续作" | 原脚本未驱动；本验收探针F补测：**责任续接不变量通过**（同agent_id/新实例/旧凭据围栏/新实例自行claim+done），文件checkpoint续作未获完整演示（真实Agent跳过文件步骤，单次未重试） | 本验收 |
| 幂等占位崩溃恢复 | 未实现 | 本验收发现 |
| 唤醒丢弃后的对账重入队 | 未实现（仅新事件路径） | 本验收发现 |
| zsession events/inspect 增强接入 | 未接（声明为增强，符合设计不判失败） | REPORT |
| 真实证据的跨环境可移植性 | 绑定本机 zsession/opencode（已声明） | REPORT §6 |
| "21个会话已清理" | 无法独立核实（查询角色无权限） | REPORT §7 |
| 生产迁移、多机、OS沙箱 | 明确不在范围 | DESIGN §18 |

## 8. 签收判定

- ACCEPTANCE §7.2 五项签收最低条件核对：
  1. 90案例实际状态 —— 施工方证据成立，但可复现性受损（DEF-06）；阻断项（§1.1）全部在独立复跑中通过 ✔（带 DEF-03/04/05 例外项均不在阻断清单内）。
  2. 每命令及参数实际检查记录 —— **不满足**（DEF-08）。
  3. AC-HAR-02/AC-E2E-02 真实证据 —— **满足且独立复验通过** ✔。
  4. 运行状态/结果/回执/文件互相对照 —— 满足 ✔。
  5. 可复现 README —— 满足（本验收按 README 命令完成三类复跑）✔，但收尾清理声明不实（DEF-07）。
- **结论：条件2不满足 + 两个 High 缺陷（DEF-01/02）+ 清理声明不实（DEF-07）→ 不满足签收条件。** 建议修复优先级：DEF-01 ≈ DEF-02 > DEF-04 > DEF-03 > DEF-06（断言加固后重跑全量）> DEF-05 > DEF-08（补参数矩阵或与owner正式裁剪范围）> 其余。
- 修复 DEF-01/02/04 后，预计只需针对性回归（CTX/RUN/E2E/STORE族 + 本验收探针A–F），无需等待全量重跑即可重评。

## 9. 工作边界声明

- 未修改任何产品代码、设计规范、施工方验收脚本与施工方证据目录；本验收产物仅新增（本报告、INDEPENDENT_DEFECTS.md、work/independent/**、work/acceptance/20260930-*）。
- 未触碰 trigger-platform（19961 验收前后均在监听，进程未动）；未使用生产数据库；未改用户 zsession 全局配置；zsession 调用仅使用受保护配置中的 owner-local token，未打印、未入档。
- 本轮创建的对象：探针/复跑 home 均已 `system stop`；真实 zsession 会话仅探针F与双案例复跑所建，探针F脚本内已逐句柄 kill；探针F写入共享根的 STARTUP.md 已存证副本后删除；遗留发现（29963 施工方 daemon，PID 34464）按"保留失败现场"原则未动，交 owner 处置。
