# Hive ACP 第二轮修复报告（对 ACP_FIX_REACCEPTANCE_DEFECTS 的逐条回应）

日期：2026-10-01 · 基线 run：reacp-20261001-132450-v1
上游状态：ZSession 二修（RDEF-01/02/03/04/07 上游侧）**尚未落地**
（acp.mjs 13:17 快照仍含 busy→rejected 终态写入）——本报告所有上游依赖项
标 待联调，Hive 消费者防御先行落地。

## 权限范围（§2）

RDEF-02/06（非 yolo 权限桥接）按 Owner 设计裁定维持 N/A·留档；本会话用户
未给出相反指示，沿用裁定，scope 来源：复验报告 §0.1（2026-10-01）。
Hive 人类 Task 审批、控制权限、自主授权不受影响，本轮未触碰授权记录。

## Hive 主修项

| RDEF | 修改 | 实测 |
|---|---|---|
| 01 busy 假成功 | 三层防御：① send_prompt 返回后用 `_zsession/get_input_state` 核实——state=rejected 即按失败落账（不写 completed），协议成功≠业务完成；② -32011 回队前查持久状态，发现 requestId 已被污染（rejected）→ 派生新 id `orig#r<ts>` 重置预约（保留血缘，error 记录原 id）；③ `_dispatch_one` 全路径固定 input_id（异常处理不再用 next_queued 重新猜输入错改后续消息）；Generic Exception 包装为 HiveError 稳定落错（不因 e.code 缺失二次异常挂死队列） | 代码路径+清点：生产 6 条 completed 全部有真实输出（0 疑似）；复验 home 的 2 条假账存在于其隔离 home，未批量改写（按提示词只清点） |
| 03 replay 双写 | ensure() 在 session/load 调用前后设置 `_replaying` 窗口，窗口内 chunk 一律丢弃；同时兼容 replay 标记的 params 级与 update 级两种位置（上游标记位置与 Hive 读取位置不一致的消费端修复）。历史唯一来源 = `/sessions/:id/history` 分页 | 静态+协议层；上游标记补齐后窗口防御自然退化为兜底 |
| 04 恢复对账 | `reconcile_once()`：桥（重）连后对全部 outcome_unknown 输入查持久 get_input_state——done/cancelled/rejected/failed 按事实补账并写 `reconciled:true` 的 turn.completed；started/accepted/unknown 保留 outcome_unknown（上游 idle ≠ 旧输入成功，不猜）。startup_sweep 仍先落 unknown 释放队头 | 代码路径；上游持久对账已可用（e2e step8 done 可查） |
| 07 owner 登记 | ensure() 的 load `_meta.zsession` 携带 `ownerId=hive/<agent_id>`、instanceId、generation、agentId；send_prompt meta 同带 ownerId。上游 v0.5.1 仅在显式 ownerId 时启用受管锁 | 待上游二修后 `/acp/claims` 验证（本环境 claims 仍空——上游 claim 链路对 load 路径未生效，联调项） |
| 08 停流死链 | 前端 watchdog：活动输入存在且 >18s 无业务事件时低频校对 `/conversation`（权威 active_input 状态+60s 强制校对）；不一致 → 从游标重建 EventSource + 重渲尾屏（"实时通道已自动校对恢复"）。合法长工具安静期不误报（仅活动输入时触发）。ES onerror/心跳缺失/校对恢复三类分态提示 | 模拟验证（本地断流）；真实长流式窗口已具备 |
| 10 取消守卫 | 前端取消携带 instance_id/generation/expected_turn_id；服务端 expected_turn 与活动轮不符 → 409 拒绝（防竞态误杀下一轮）；取消后 toast 改为"等待原生确认…"（不假称已取消） | 接口级 |
| 11 窄屏/审批 | 判决改分段按钮组（≤4 选项；通过/返工/放弃语义着色，未选空心描边）；滑块加 0/中值/100 刻度 + 当前值 `n / 100` 实时显示；抽屉窄屏模态化（遮罩+关闭提示+Esc/外点）；抽屉内选择节点/资产后自动收起并定位 | 390 DOM+截图；1440 真实任务截图 |
| 12 星图叠字 | 边计数标签默认稀疏（flow 边隐藏，hover 或选中相关子图时显示）；分组标题移到左缘独立留白（不再被中央连线穿过）；hover 生成的标签回落隐藏 | 1920 截图复验：组标题左侧清晰、无叠字 |
| 13 消息边界/meta | conv-head 各项 nowrap + sid 截断（孤词/断行消除）；中英混排为上游原生输出内容——UI 如实保留原文（不删不裁），源问题已在上游 RDEF 登记 | DOM 验证 |

## 真实产品缺口（本轮发现并修复）

业务 Agent 的真实人评任务（穷途岛 S2 等）不带 `ui.fields`，只声明
`request_type: dual_evaluation`——旧 UI 退化为自由文本回复，用户无法
结构化评分。现 `isDualEvalData` 兼容三种声明：契约键 /
`request_type=dual_evaluation` / `human_eval+review 类 intent`，均渲染
标准双评分裁决表单（实测：真实任务出现 通过/返工/放弃 + 双滑块 + 刻度）。

## 待上游联调（不阻塞，登记）

- RDEF-01 上游侧：busy 不应写终态 rejected（Hive 已有防御，上游修复后
  派生 id 路径自然不触发）
- RDEF-03 上游侧：回放帧补 replay 标记 + 事件泵水位对齐
- RDEF-07 上游侧：load 路径 claim 实际登记（claims 目前为空）
- RDEF-10 上游侧：interrupt 在部分宿主场景 confirmed 不稳定
- RDEF-09/05：回放含工具行 / modes 形状（上游）

## 复跑

```bash
python scripts/quicktest.py     # 全过
# 停流恢复：打开会话舱等待 >18s（有活动输入）观察自动校对 toast
```

## 边界

未重建视频网络、未改审美/方法/授权、未删失败记录；复验 home 未动；
生产 completed 记录只读清点未修改。
