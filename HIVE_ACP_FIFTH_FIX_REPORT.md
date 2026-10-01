# Hive ACP 第五轮修复报告

日期：2026-10-02 · 对 ACP_FOURTH_FIX_ACCEPTANCE_DEFECTS 主修 FDEF-01（联合）/FDEF-02/FDEF-04
上游：五修未落地（acp.mjs @00:00 为四修 v0.6.1）——FDEF-01 用 v0.6.1 已有原料
（`/acp/inputs` 持久登记 + `-32017` 指引 + `/sessions/:id/history`）在 Hive 侧完成联合关闭

## FDEF-01：unknown 有安全收口路径 —— 联合关闭（fresh run 实证）

**实现**（`convbridge.py`）：
1. **三层对账**（`reconcile_once`）：① 上游登记 done/cancelled/rejected/failed
   按事实收口；② **started/unknown + 会话空闲 → 原生归属对账**：在
   `/sessions/:id/history` 中精确匹配**本输入全文的 user 回显**（我方原文的
   1:1 身份链接——非正文相似/时间接近），其后存在 assistant 终稿 → 按事实补
   completed（`attribution: user-echo-exact-match` + registry_state +
   native_input_id 入证据）；③ 无强证据保持 unknown + 前端处置入口。
2. **周期对账**：存在 unknown 且桥活着时每 30s 低频重试（目标 ≤60s 收口；
   working 会话直接跳过——合法长轮不受影响、不提前 completed）。
3. **修复实测缺陷**：zsession 空闲态为 `inactive`（非 idle）——白名单补齐。
4. **幂等**：settle 事件 DB dedupe（`reconcile-settle-{input_id}`）；状态
   条件更新（终态不覆盖）；重复查询零副作用。

**J05 复刻联合证据**（strong-home，强 writer 模式，fresh run）：
- 长轮（4000 字散文，暗号灯塔-1024）running 中 kill adapter → 输入诚实落
  outcome_unknown → **原生轮次继续并完整产出 3375 字** → 周期对账按
  user-echo 精确归属 **自动收口 completed（turn 关联齐）**。
- **无重执行证明**：registry 仍 `unknown`（updatedAt 未变）、history 中
  fj05 user 回显**恰 1 次**、无新输入发送、零额外模型调用。
- 答案在 part 表落库有延迟——对账在此期间**正确地未猜**（has_answer
  false → 保留 unknown），落库后下一周期收口（诚实窗口）。
- 会话输入 completed 不改业务 Task/判决/授权（语义未动）。

**人工对账兜底**（`POST /ui/api/agent/:id/conversation/reconcile`）：
- `action=recheck`：只读重对账（conversation.read 即可），前端 unknown
  chip 旁「对账」按钮 + 状态条「重新对账」入口。
- `action=confirm`：人工收口**仅 owner/system**（观察者只读）；必须提供
  outcome + 原生引用 + 原因；写 audit_log（conv.reconcile.manual）；
  幂等（非 unknown 状态返回 already，不二次改账）；终态竞态由条件更新
  拒绝。聊天/评论不经此路径，不改账。

## FDEF-02：断线恢复不拆会话面板 —— 已修复（三态浏览器实证）

**根因定位**：旧 `_refreshTail` 调 `_renderHistory` **整树清空重建**
（flow.textContent=""）——恢复期间一旦重渲即表现为"面板内容消失"；
且无上游桥状态呈现，用户无从知道在恢复。

**修复**：
1. **增量归并 `_mergeTail`**：按稳定消息 id 就地更新（文本/状态），新增
   追加、缺失移除——**不清空整 Pane**：草稿/阅读位置/工具卡开合/思考
   折叠态全部保留（DOM 复用）。首次或空态才整渲染。
2. **异步代次守卫 `_tailGen`**：旧响应不覆盖新状态。
3. **连接与运行状态分离**（§5）：`/conversation` 返回
   `upstream: {bridge_alive, unknown_inputs}`；前端独立状态条：
   「上游连接中断，正在恢复——已显示内容与草稿保留」/「有待对账的历史
   输入」+ 重新对账按钮；与 SSE 传输状态条互不覆盖。
4. watchdog 先查上游状态再决定 resync（心跳活着≠桥活着）。

**浏览器三态实证**（1440px，strong-home）：
- **故障前**：面板 28 条消息，chip `running`；
- **故障中**（kill adapter +12s）：**面板不消失、28 条消息完整保留**、
  状态条「有待对账的历史输入…重新对账」出现；
- **恢复后**：自动收口，队列条消失，内容原位（无需关面板重开）。

## FDEF-03：取消限制 —— 维持诚实结论

UI cancel → cancelling 分态 → 原生未停则自然 end_turn（不假 cancelled）
——行为与四修验收一致（宿主限制），本轮小量回归无变化。不 kill 宿主。

## FDEF-04：视觉精修

- 思考折叠态在增量归并时保留（DOM 复用）；展开态文本更新跟随滚动。
- 320 退出按钮补 `aria-label`（title 已有），图标形态维持（触达区前轮已
  达标 right=314/320）。
- 未分类 assistantText 保持原文（不删英文/不伪清洗）——源限制继续登记。

## 复跑

```bash
python scripts/quicktest.py   # ALL PASSED
# J05 复刻：strong-home 长轮 → taskkill adapter（仅本 run node 进程）→
#   观察 ≤60s 自动收口 + history echo=1（命令记录于本 run shell 历史）
```

## 生产部署状态（pending_deployment，未自动切换）

生产 19962 仍为四修前代码 + 弱模式。接续步骤（后续部署任务执行）：
停止 → `HIVE_ACP_WRITER_KEY=1 HIVE_ACP_ADAPTER=… start` → 现有会话/责任
不受影响；TDEF 已关的强 key/FIFO/评分行为不回归（恢复改动不自动 release
key/不退弱——弱模式 release 仅 HIVE_ACP_WRITER_KEY≠1 时路径）。
