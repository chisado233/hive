# ACP 修复轮与静谧天文台 — 独立复验报告

> 复验 Agent：ZCode 会话（独立复验，非施工会话）。日期：2026-10-01。
> 复验基线：`ACP_INDEPENDENT_ACCEPTANCE_PROMPT.md`（原 55 项）+ `ACP_FIX_REACCEPTANCE_PROMPT.md`（本轮）。
> 被验版本：Hive git `6aa4089`；ZSession 非 git 仓库（快照 md5 记录在 run 目录），
> 实际运行：ZSession daemon PID 59140（13:17:20 启动，确认为新代码）、生产 Hive PID 16832（12:41 启动，修复后）。
> 本 run 全部证据：`D:\agent_workspace\tmp\hive-acp-reacceptance\reacp-20261001-132450-v1\`
> 旧验收基线：`D:\agent_workspace\tmp\hive-acp-acceptance\acc-20261001-100912\`

---

## 0. 总结论（先行）

| 维度 | 结论 |
|---|---|
| **ZSession 标准 ACP** | **不通过**。initialize/agentInfo/pre-init/load 错误路径已合标；但 load 响应 modes 形状非法（RDEF-05）、回放帧无 replay 标记且与泵重复投递（RDEF-03），严格标准 client 无法完整互操作。权限帧结构问题（RDEF-06）已随恒 yolo 裁定降级留档。 |
| **ZSession 原需求** | **部分实现**。幂等隔离（DEF-01/02）真实闭合；取消不再注入伪消息但仍不可靠（confirmed=false 时如实返回 end_turn，原生不停止）；load 有回放但不完整（工具缺失）且有双写风险；工具级权限桥接**按设计裁定不做**（见下）。 |
| **Hive 后端与恢复** | **部分实现**。队列 FIFO（seq）、stale-instance 409、启动对账（sweep→outcome_unknown）实测成立；但恢复路径撞上 RDEF-01（假完成）——比上一轮"卡死"更危险（静默丢数据）。 |
| **真实两端联调** | **受限**。真实 YOLO 对话端到端仍可用；但本轮引入的"busy→rejected 终态→重试假成功"会把忙冲突变成静默丢单，生产使用存在数据风险。 |
| **非YOLO权限** | **按设计不做（N/A）**。Owner 裁定（2026-10-01，见 §0.1）：Hive 恒 yolo，工具级权限桥接不实现，人类控制保留在任务层（人工审批台/授权）。原"待联调"项改判 N/A；适配器侧已写的权限代码存在 RDEF-02 缺陷，仅在未来启用非 yolo 时才需修复（降级留档）。 |
| **静谧天文台视觉** | **需改进**。方向达标（暗、安静、收敛、决策金克制、中文可读）；顶栏逐字竖排已修复；残留：生产 26 节点星图连线/标签叠字、会话流中英 chunk 无分隔、审批判决控件形态不符（竖排 radio 非分段）+滑块无刻度、390 抽屉遮节点。 |
| 旧 20 项 | **CLOSED 9 / PARTIALLY_FIXED 9 / OPEN 1（DEF-15）/ N/A·按设计 1（DEF-04）** |
| 新风险用例 R01-R14 | PASS 3（R03 恢复、R12 触发面已被 RDEF-01 覆盖定位、R08 代码级）/ FAIL 6 / BLOCKED 2 / NOT_TESTED 3 |
| 原 55 项 | 复测覆盖 Z01-Z20 与 H01-H20 相关路径 + B/V 系关键项；深度长历史/50 节点/性能 trace 仍为 BLOCKED/NOT_TESTED（环境限制）。 |
| **最重要阻断** | RDEF-01（假完成丢输入）、RDEF-03（回放双写）、RDEF-04（started 孤儿）；RDEF-02 已按设计裁定降级（恒 yolo，见 §0.1） |

### 0.1 设计裁定记录（本轮验收期间，Owner 口头裁定）

> **2026-10-01：Hive 会话恒 yolo；工具级权限桥接（session/request_permission 链路）不做。**
> 理由：Hive 的人类控制点在任务层（人工审批台、授权），不在工具调用层；23 节点生产流水线
> 不允许每个工具调用阻塞等待人工放行。
> 影响：DEF-04 由"待联调"改判 **N/A（按设计不适用）**；DEF-11 接口层修复实测成立、联合层 N/A；
> RDEF-02/RDEF-06 降级为低优先级留档（适配器侧已写的权限代码带缺陷、当前为不启用路径的死代码，
> 若未来需要"守卫型 Agent"再修）；ZSession spawn 硬编码 yolo 不再视为缺陷。
> 该裁定与原需求 §5.8/§9（"模型和 yolo 由 Hive 配置决定"）存在范围收窄，以本裁定为准。

---

## 1. 修复有效区（实测通过）

| 项 | 证据 |
|---|---|
| 幂等隔离闭合 | `protocol/d-results-P3.json`：跨会话同 requestId 各自执行、副作用计数独立、get_input_state 串号修复 |
| 跨重启幂等 | `protocol/d-results-P4.json`：重启后 done 可查、重发零副作用、终态短路返回 |
| pre-init 拒绝 | `protocol/d-results-P1.json`：-32002；load 不存在 → -32001（非 loaded:false 成功信封） |
| FIFO 序号 | `api/d12.json` + 派发序列映射：同秒 6 条按到达序（rd12-r0→r5） |
| 陈旧实例 | `api/d13.json`：409 STALE_INSTANCE（输入与取消都正确返回码） |
| 启动对账 | `api/r03-*.json` + daemon 日志：running→outcome_unknown、queued 续派（虽然下一条被 RDEF-01 撞车） |
| 历史分页 | `api/d14-*.json`：`/history` 分页稳定、`before` 游标正确、`/events?sessionId=` 过滤生效 |
| 登录安全 | 浏览器实测 password/autocomplete=off/切换；`browser/` |
| 审批完整链 | 浏览器实测双评分 85/90 + 通过 → 提交 200 "答复已入账" → task done；`browser/v04-approval-1440.png` |
| 窄屏顶栏 | 390/320 单行收拢、底部导航横排可用；`browser/v06-narrow-*.png` |
| 生产 23 节点只读 | `browser/v07-prod-starmap-1920.png`：26 节点、中文分组、三种节点形态可辨 |

## 2. 主要未通过/新缺陷（详见 ACP_FIX_REACCEPTANCE_DEFECTS.md）

- **RDEF-01（阻断·回归）**：busy 被写成终态 rejected → 重试命中终态短路返回假 end_turn → Hive 假完成；
  两条实测输入（rd09-r1/rr03-next）从未执行。比旧版"启动卡死"更隐蔽。
- **RDEF-02（已降级·低，按设计裁定不启用）**：request_permission 的 selected/cancelled/超时答复均不到原生（pendingApproval 挂起 30+ 分钟，/interrupt 也无效）。因 Hive 恒 yolo（§0.1），此链路不在产品路径上——保留缺陷记录，仅当未来启用非 yolo 时修复。
- **RDEF-03（高）**：load 回放帧无 replay 标记 + 事件泵 cursor=0 重发 → 同内容双份；Hive 当前过滤基于错误的 _meta 位置。
- **RDEF-04（高）**：注册表 started 孤儿跨重启不落账，重试可被误写 done。
- **RDEF-05（高）**：load 响应 modes 形状非法（标准客户端不可互操作）；RDEF-06 已随权限链路降级（不启用路径）。
- **RDEF-07（中）**：受管写入真实链路从未生效（Hive 不传 ownerId），writer 凭 X-ZS-Writer 头自称。
- **RDEF-08（中）**：SSE 静默死链在流式中段复现，无降级条、不自动恢复，切节点才补齐。
- **RDEF-09（中）**：load 回放缺工具行。
- **RDEF-10（中）**：取消不可靠（无伪消息但 native 可能不停止；confirmed=false 如实回 end_turn——诚实但语义仍是"没取消成"）。
- **RDEF-11/12/13（低）**：390 抽屉遮节点/判决控件形态/生产星图叠字/中英 chunk 拼接。
- **RDEF-15/16（文档）**：ACP_INTEGRATION.md v0.5 自冲突；两份修复报告的 fixed 与实测不一致。

## 3. 原 55 项回归摘要

- 复测/补齐：B08/V04 审批双评分+结论+提交链路 PASS；B09 字段校验未全复测（沿用旧 PASS，UI/校验逻辑未变）；D14 历史补齐；B10 模型/模式 UI 未测（文档待补）。
- 新测补项：标准 initialize/load/set_mode/权限帧结构（schema 对照实测）；取消真实原生副作用计数。
- 仍 NOT_TESTED：V07 拖拽/缩放操作交互的像素证据；50 节点性能 trace（隔离数据不足）；H18 多身份矩阵（无第三方凭据）；生产 23 节点上的写操作（按要求只读）。
- 复验未触发类：`acp_e2e.py`（8 步协议夹具）不驱动浏览器，本轮所有浏览器证据均为真实链路（真实 Agent + 真实会话 + 真实审批）。**只有 fixture 用于：R14 GC 语义静态评估（未跑 4000 轮）**。

## 4. 施工报告可信度（本轮）

- 两侧修复报告在"自述修复点"与"自述限制"之间存在继续的不一致：ZSession 报告把
  DEF-03/DEF-04 标 fixed/core-verified，但实测 cancel 不可靠、权限答复不送达（DEF-04 后按设计裁定
  N/A，见 §0.1，但"报告声称与实测不符"的事实保留）；Hive 报告在
  承认 confirmed 不稳定的同时仍把"答复链路已就绪"当结论。
- `ACP_INTEGRATION.md` v0.5 的自冲突说明施工在修文档时没有同步所有旧段落。
- 本次没有证据表明 `quicktest.py`/`acp_e2e.py` 被用来替代独立验收——但双方对
  "已修复"的宣告仍缺乏严格的回归脚本支撑（fx 系列是施工自定义探针）。

## 5. 使用范围与修复优先级

1. **不要先上生产**。RDEF-01 是数据级缺陷（忙冲突→静默丢单）。
2. 先修 RDEF-01（busy→不终态 + 重试不失真），其次 RDEF-03/04（回放双写与对账语义）。
3. 然后补齐 RDEF-05 标准帧（load modes 形状），再做 D16 SSE 静默死链与视觉残留（RDEF-11/12/13）。
4. RDEF-02/06（权限链）按 §0.1 裁定不启用，留档不排期；若未来要"守卫型 Agent"再启用并修复。
5. 取消的 15s 确认窗口建议改为"请求→进行中→原生确认"显式分态 UI，避免假 cancelled 假设。

## 6. 收尾

- 测试 daemon 19980 已停止；生产 8765/19962 未触碰；生产 UI 登录态已退出。
- 残留运行项：P6 挂起会话 sess_68119062（pendingApproval，为 D04 现场证据，保留不复位）。
- 测试资源与本 run 证据全部保留；`resources.json` 记录清单。
- 已交付：`ACP_FIX_REACCEPTANCE_REPORT.md`、`ACP_FIX_REACCEPTANCE_DEFECTS.md`（本两份）。
