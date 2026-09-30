# 第三轮验收交付物 · 独立审计附录（SECOND_REACCEPTANCE_AUDIT_ADDENDUM）

- 审计者：第三轮验收同 run（`20260930-141738-56ab1c`）的并行执行流（AC-HAR-02 工位会话），
  与交付报告的作者会话无内容串通。
- 日期：2026-09-30
- 审计对象：[SECOND_REACCEPTANCE_REPORT.md](SECOND_REACCEPTANCE_REPORT.md) /
  [SECOND_REACCEPTANCE_DEFECTS.md](SECOND_REACCEPTANCE_DEFECTS.md)（结论：建议签收，RDEF-01..10 全部关闭）
- 审计方式：证据文件逐项只读核验 + 交付验证未覆盖面的静态代码审查。
- **能力披露（重要）**：本会话的命令执行层（Bash adapter）自始至终宕机
  （"Execution adapter is shutting down"，直接调用与子代理调用双路径复现，贯穿全时段），
  因此本审计**不含任何新的运行时探针**；已就绪未执行的独立探针套件保留在
  `work/independent2/20260930-1500I/`（p1-p10，见该目录 STAGE_NOTES.md），供后续执行轮使用。
  本文件只依据：①对既有证据文件的直读；②对 HEAD=1906cce 产品源码的静态审查。

---

## 1. 审计结论

**交付报告的关键主张与所引证据一致，未发现虚报；"建议签收"结论在其证据范围内成立。**
同时，本审计发现 **2 处证据质量瑕疵**与 **5 个交付验证未覆盖的静态疑点**（见 §3/§4），
均为低危或未定性，不推翻签收建议，但应随 R2DEF-01/02 一并纳入后续小修轮。

## 2. 交付主张 × 证据核验矩阵（逐项只读复核）

| 交付主张 | 证据文件（直读） | 核验结果 |
|---|---|---|
| HEAD=1906cce、提交链与任务书一致 | .git/HEAD + refs/heads/main + logs/HEAD（本审计独立直读） | **一致**：`1906cce1…`；链 c409490→35448a1→2540004→1906cce，无 merge/rebase/reset 痕迹 |
| 真实三案例 3/3 Pass（run 141738） | work/acceptance/20260930-141738-56ab1c/report.json | **一致**：HAR-02/E2E-02/E2E-03 均 Pass（22:17-22:21 本地时段连续执行） |
| E2E-03 七步贯通 | 同目录 AC-E2E-03/assertions.jsonl | **一致**：checkpoint 注记含实例1 ID；continuity 注记含双实例 ID、checkpoint+result 双文件、`ended_by=agent:agt_68917…` |
| 矩阵独立重算 97/384/371/0缺口/exit0 | work/coverage/20260930-215625-d4b1/summary.json | **一致**：rows=372/passed=371/failed=0、97/97、384/384、param_gaps=[] |
| 12 套件逐项复现 | work/independent2/rerun-all.log（全读） | **一致**：quicktest 全过；acceptance 88 Pass+3 Blocked（Blocked=三真实案例设计内未选入）；def0102=25/25、def04=15/15、def0305=12/12、rdef01=35/35、rdef03=25/25、rdef0405=33/33、rdef0708=18/18、rdef09=20/20、rdef10=10/10、coverage exit=0 |
| RDEF-01 孤儿/围栏探针 | independent2/20260930-220645-main/s1.json | **一致**：四类审计孤儿拒绝+指引+0第二对象；无效果孤儿干净接管；竞争同实例/1活槽/gen=1/响应一致 |
| RDEF-03 starting 恢复 | independent2/f3_redo.json | **一致**：starting→failed（fail_count=1、next_allowed_at 保留）→按期重启 |
| RDEF-04 事件按 episode 恰一次 | independent2/20260930-221452-fup/f1.json | **一致**：episode1=1 条、封顶空转+0、episode2 再恰 1 条 |
| R2DEF-02 信封 retryable 覆盖 details | 同目录 f5.json | **一致**：envelope `retryable:true` vs `details.retryable:false`，缺陷真实、定性准确 |
| R2DEF-01 收尾漏 E2E-02 替换实例 | work/acceptance/…/run-manifest.json | **一致**：real_harness_agents 仅 1 项（E2E-03 的 agt_68917…），E2E-02 agent 未登记；cleanup_log 的 agent_stop 结果 instance_stop_result=none |
| 三份旧报告更正（RDEF-02） | FIX/REGRESSION/ALL_FIXES 直读 | **横幅（各 L3-8）与「附录：第二轮更正」（L192/L96/L252 起）均在，正文原失败证据保留无改写**；字节级"0行删除"主张需 git diff，本审计无法执行（见 §5） |
| coverage 负向注入/12行抽检 | defects 文字描述 + 本审计对 coverage_matrix.py 源码的独立静态核验 | **代码路径成立**（`return 1 if failed or param_gaps` 在位、行契约/完整性校验如述）；运行时产物未逐字节复核 |

## 3. 证据质量瑕疵（新发现，Low）

1. **R2DEF-01 的证据文件与描述不符**：defects 文件称 cleanup_leftover_e2e02.json 含
   "kill前账本/会话双活快照+kill记录"，实际文件仅 4 行（session id + why），无快照、无 kill 回执。
   主张本身被 run-manifest 佐证（见矩阵），但证据文件应补齐或修正描述。
2. **defect_rdef10.py 的弱验证与误导性 detail**：check-d 对"错误对象不含 token 正文"仅校验
   `token[:12]` 前缀不出现在响应 JSON（脚本 L139-141），且 FAIL 描述串 "token fragment leaked!"
   被无条件拼进 PASS 行（rerun-all.log L335 可见"PASS … | token fragment leaked!"）。
   完整泄漏结论另由日志/审计扫描 check 支撑，但该行表述应修正、前缀检查应扩为中段/尾部。

## 4. 交付验证未覆盖的静态疑点（本审计代码级发现，均未运行时定性）

以下 5 项在交付报告与其套件中均未见覆盖（已核对：RDEF-01 竞争仅赛 start；RDEF-07 别名矩阵
均为已存在目录、8并发为同刻竞争而非"先别名后实体"序；RDEF-09 的 unknown 分支未与
restart/then_start 组合；RDEF-06 负向只测 visibility 非法与缺键）。探针已备好
（work/independent2/20260930-1500I/p2-B / p5-4 / p1-C / p10-K1d / p10-K4b）：

| # | 疑点 | 代码依据（HEAD=1906cce） | 潜在影响 |
|---|---|---|---|
| U1 | 工位目录**不存在**时 workspace_key 跳过 realpath/短名展开——先以 junction 别名绑定未建目录，目录实体化后同物理目录可再绑 | store.py workspace_key L272（exists 门控）、L274-286 | RDEF-07 唯一索引对绑定顺序不设防 |
| U2 | grace 停止 **unknown 分支**先删 kv 再 then_start；op_start_agent 把 stopping 计活槽→already_running，restart --grace 替身静默不启动 | scheduler.py run_grace_stops L228-241 + runtime_ops.py L47-51 | 宽限重启在停止结果不明时失效（恰是最需要它的场景） |
| U3 | 外置命令接管**无条件重跑 handler**：agent.send/wake 的消息投递不可合并——双执行者竞争可能同一文本投递两次 | service.py L355-363（接管无效果探测）；rdef01 仅证 start 可合并 | 幂等"不重复业务效果"对 send/wake 不成立（DESIGN §16） |
| U4 | port update --replace 对 patch **显式 null 删必持键**后直接取键→KeyError→INTERNAL_ERROR（RDEF-06 只修了缺键与非法枚举） | ports.py update_port L167-193（_merge null 删键 + L183/L190 直取） | 同类 INTERNAL_ERROR 残留路径 |
| U5 | `control system config --if-version` 未实现乐观并发（VERSION_CONFLICT 永不触发）——DESIGN CMD-022 声明该参数 | handlers_core.py h_system_config L108-145 | "参数有名无实"类（上轮 RDEF-09 同型） |

另有低危观察（不单列）：候选集顺序不稳定可能重置轮次 cap（scheduler.py L399-400 无 ORDER BY）；
op_stop_agent 仅捕获 HiveError、非 HiveError 仍可遗留 accepted 行；zsession 适配器 404 判定为
字符串包含；保存提示 wait_idle=False 在忙碌会话可能搁浅；create --start 失败以 exit0+warnings 表达；
幂等 payload_hash 在校验前计算（同语义异字面触发 CONFLICT）；迁移005 回填输家键永久 NULL。

## 5. 本审计未能完成项（执行层宕机所致）

- `git diff c409490..1906cce -- DESIGN/ACCEPTANCE/IMPLEMENTATION_PLAN` 的字节级复核
  （交付报告主张"仅 DESIGN §10.1 增量一行"；本审计已确认 HEAD 文本含该行，三份旧报告纯增量
  的主张与其文件现状一致，但历史对照需 git）。
- 上述 U1-U5 的运行时定性；独立探针 p1-p10 执行（脚本已静态自审并修复已知缺陷，
  连同执行序列 run_tasks.sh 一并留档）。
- netstat/进程/token 全库扫描等红线核验的独立复做（交付报告 §7 的自证已读，未独立复现）。

## 6. 对签收建议的审计意见

在交付证据范围内，"建议签收（有条件记录项）"成立且与证据相符。建议业主：
1. 接受 R2DEF-01/02 + 本附录 §3 两项证据质量瑕疵 + §4 五个 U 项作为**签收后小修轮清单**
   （其中 U3 若坐实将触碰 DESIGN §16 语义，建议优先跑 p1-C 探针定性）；
2. 后续任何执行轮可直接使用 `work/independent2/20260930-1500I/` 的探针套件与本附录的核验矩阵。

—— 审计流会话（无运行时副作用：未启动 daemon、未创建 zsession 会话、未修改任何既有文件；
本会话产物仅 work/independent2/20260930-1500I/** 与本文件，未 commit）。
