# Hive V1 第三轮验收 · 缺陷清单（SECOND_REACCEPTANCE_DEFECTS）

- 验收Agent：第三轮独立复验（与第二轮返修施工方无关联）
- 日期：2026-09-30 · 代码基准：git `1906cce`（工作区干净，仅未跟踪任务书 THIRD_ACCEPTANCE_PROMPT.md）
- 被验对象：RDEF-01..10 的"全部收口"声明（[INDEPENDENT_REACCEPTANCE_DEFECTS.md](INDEPENDENT_REACCEPTANCE_DEFECTS.md)）
- 独立证据根：`work/independent2/20260930-220645-main/`（S1-S7）、`work/independent2/20260930-221452-fup/`（F1-F5）、`work/independent2/f3_redo.json`、`work/independent2/covneg/`（负向注入）、`work/independent2/rerun-all.log`（12套件复跑）、`work/coverage/20260930-215625-d4b1`（我的coverage重算）、`work/acceptance/20260930-141738-56ab1c`（真实三案例复跑）、`work/independent2/cleanup_leftover_e2e02.json`
- SQL使用声明：仅用于构造"真实崩溃可产生的等价账本状态"（孤儿幂等占位、starting态kv、未撤销已结束实例），均在证据中标注。

---

## RDEF-01..10 关闭判定（独立证据，非采信施工方结论）

| # | 判定 | 独立验证要点（我的探针，非其脚本） |
|---|---|---|
| RDEF-01 | **关闭** | ①四类"效果已提交回填前"等价孤儿（create/grant/clone/producer，SQL构造崩溃可达态）：同key重试**全部拒绝并附审计指引、0个第二对象**（s1.json）；②无效果孤儿→干净接管恰1对象；③双执行者竞争（idem_delay_backfill 6s+租约2s+双线程同key start）：同实例、恰1活槽、executor_gen恰推进1次、响应逐字一致；④分类核实：仅7个外置命令，create等走原子路径（代码+行为） |
| RDEF-02 | **关闭** | 三份旧报告 git diff **0行删除**（更正横幅纯增量、原文未动）；预检三态代码核实（defect→assert Fail，blocked→Blocked）；真实三案例预检实测 ok→真跑全Pass |
| RDEF-03 | **关闭** | crash→scheduled（backoff_s按配置[3,6,12]）不同步重启；**starting态中断恢复**（字节同构kv+正确RFC3339）：→failed+fail_count=1+**同档位next_allowed_at保留**→按期重启（f3_redo.json）；awaiting_intervention：投递不冷启、runtime可见restart_plan、手动start成功且清计划 |
| RDEF-04 | **关闭** | 默认12可读、越界1500拒绝；轮次:1→:2→:3单调不归零（行删除后继续）；**达上限事件按episode恰一次**（episode1=1条；封顶空转+3轮时钟推进新增0条；amend恢复后episode2恰1条——F1受控实验，此前S3观测的"同任务3条"系多episode合法各发一次）；三路恢复中amend与调大上限实证续轮（total 16/offset 11/capped两次）；任务不重复、无新receipt |
| RDEF-05 | **关闭** | 坏订阅者（send必败进程）与健康订阅者（fixture B）共存：健康者持续收到:1..:5轮（sent），坏者pending:9-:11不阻塞他人（run DB直查；主探针时序读空系探针采样过早） |
| RDEF-06 | **关闭** | 独立重算：**97/97命令、384/384具名PASS、371行全PASS、0缺口、exit 0**（我的fresh run）；**负向双注入**：注入1条FAIL→failed=1+exit 1；剥离CMD-002/--comments行→param_gaps=["CMD-002/comments"]+exit 1（副本运行，已隔离）；12行证据抽检：10条request_id直查idempotency命中，2条按设计无账本痕（查询类、回滚的reject-error）；6定性行中3条产品侧修复经我实证（wait --claim→CLAIM_FENCED、create --start失败保留身份、port replace成功） |
| RDEF-07 | **关闭** | 别名矩阵**全部拦截**：大小写/单双尾分隔符/相对..路径/junction/8.3短名（SHORTPATH实测）→INVALID_ARGUMENT；**全新目录8并发（含4种别名混用）恰1胜者**、败者INVALID_ARGUMENT、胜者三件套完好；唯一索引迁移005生效 |
| RDEF-08 | **关闭** | clone显式冲突--workspace→INVALID_ARGUMENT；继承→新默认工位（与源不同） |
| RDEF-09 | **关闭** | stop --grace 30s：异步受理（返回grace_deadline）、实例stopping、**宽限期内emit done成功+实例凭据有效**（inbox ok；此前疑点系我用system status测——它需system.observe，与凭据有效性无关）、op时长≥宽限（31s+墙钟32s）、到期stopped+TOKEN_REVOKED；daemon重启续期恢复；restart --grace真实拉起替身（新instance_id）；--force立即停 |
| RDEF-10 | **关闭** | 真实客户端路径四态：无凭据/文件缺失→AUTH_REQUIRED；未知token/损坏文件→TOKEN_INVALID；已撤销完整token→TOKEN_REVOKED×2稳定；未撤销已结束实例（等价账本态）→INSTANCE_FENCED；token零泄漏（本轮10个实例token在日志/jsonl/报告/git跟踪文件全文扫描0命中） |

---

## 新发现缺陷（本轮独立探针发现）

### R2DEF-01 · Low（测试/清理） · acceptance 收尾 manifest 漏登记 E2E-02 的替换实例

- **规范依据**：任务书§7/上轮DEF-07收尾语义（"先停真实Agent再停daemon，按manifest清零"）；SECOND_REGRESSION_REPORT §5"本轮全部…受管清零"。
- **复现**：`HIVE_REAL_HARNESS=… python scripts/acceptance.py AC-HAR-02 AC-E2E-02 AC-E2E-03`。run `20260930-141738-56ab1c`（我的复跑）：run-manifest.real_harness_agents 仅含 E2E-03 的 agent；**E2E-02 的 agent 第二实例账本 phase=live，且其 zsession 会话在收尾后仍存活（idle）**——teardown 只对其登记过的 agent 做 agent stop（该agent当时已无活实例，返回none）。
- **预期**：E2E-02 换替实例同样登记并受管停止；"受管清零"成立。
- **实际**：漏一个；施工方历史run靠事后人工kill兜底（其报告"27个会话已kill"为人工清点），单run自动收尾不为零。
- **影响**：每次真实三案例跑完遗留1个空闲ZCode会话+账本live行（与上轮DEF-07同型，量级更小）。
- **证据**：`work/independent2/cleanup_leftover_e2e02.json`（kill前账本/会话双活快照+kill记录）；run-manifest.json。
- **回归建议**：E2E-02 案例把（换替后的）agent 登记进 run-manifest 的 real_harness_agents；或 teardown 对账本内全部 zsession 活实例执行受管 stop。

### R2DEF-02 · Low（产品/API语义） · 幂等孤儿拒绝的信封级 retryable=true 覆盖 details.retryable=false

- **规范依据**：DESIGN §16（错误 retryable 语义指导客户端行为）；RDEF-01 修复声明的"拒绝并指引（不可盲目重试）"。
- **复现**（f5_retryable.json）：对"效果已提交"的孤儿占位同key重试→错误信封 `code=STORAGE_UNAVAILABLE, retryable=true`，而 `details.retryable=false` + remediation 指引。两处不一致：errors.py 的 code→retryable 默认映射覆盖了 details 内的显式值。
- **预期**：信封 retryable 与 details 一致（此场景为 false——重试永远徒劳，需人工处理占位）。
- **影响**：按信封字段驱动的自动重试客户端会在永久阻塞键上空转重试。
- **证据**：`work/independent2/20260930-221452-fup/f5.json`（完整错误对象）。
- **回归建议**：HiveError.to_dict 优先采用 details.retryable（显式值优先于code默认映射）。

### 观察项（不计缺陷）

1. coverage summary 的 rows=372 vs passed=371 恒差1：teardown 后追加的 CMD-020/--reason 行在 summary 计算之后写入（打印顺序可见）；不影响缺口/失败判定（该参数在 matrix 内已有具名行）。证据：rerun-all.log 尾部 + matrix.jsonl 371行全PASS。
2. 唤醒轮次事件"恰一次"的粒度=**每episode一次**（封顶→恢复→再封顶会再次发事件，DESIGN新增行语义允可）；受控实验证明单episode不重复、封顶空转不重发（f1.json）。
3. 本轮我方消耗的zsession会话：真实三案例复跑4个（1个收尾漏杀已由我补kill，见R2DEF-01）+无其他；全部处置记录于独立证据。

---

## 与施工方声明的差异汇总

- 全部12项套件声明数字**逐项复现**（fresh run 重算，未抄报告）。
- RDEF-01..10 **全部关闭**（独立探针证据如上），无一采信其脚本结论作为唯一依据。
- 新缺陷2项（R2DEF-01/02），均为Low级，不构成签收阻碍（见主报告判定）。
- 真实三案例 3/3 Pass（独立复跑），E2E-03 七步单链贯通（两代实例/checkpoint含实例ID/ended_by=agent）。
