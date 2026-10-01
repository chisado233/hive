# Hive ACP 修复报告（对 ACP_INDEPENDENT_ACCEPTANCE_DEFECTS 的逐条回应）

日期：2026-10-01 · 基线 run：acc-20261001-100912 · 上游：ZSession ACP 修复已交付
（ACP_FIX_REPORT.md / ACP_INTEGRATION.md v0.5）

## 主修项（Hive）

| DEF | 修改 | 复验 |
|---|---|---|
| 08 cancel 依赖非标准响应 | `cancel_turn` 改为无 id 通知（官方通知语义）；终态以 prompt 返回 cancelled / `_zsession/input_state` 为准 | 静态+协议层；上游 sessionCancel 现亦为通知语义 |
| 09 turn 事件缺失、chip 不刷新 | `_dispatch_one` 补写 turn.started / turn.completed/cancelled（turn_id=turn-{input_id}，含 agent/instance/generation/session 关联）；前端 chip 随事件刷新 | 生产链路实测：发送→queued→流式→completed，chip 即时消失 |
| 10 重启卡死 | `startup_sweep`：dispatched/running/cancelling → outcome_unknown（如实标注"未重发"）；queued 继续按序派发；上游持久对账（DEF-02 已修）使重接后可查真实终态 | 迁移与启动路径实测；conv2 home 重启后队头释放 |
| 11 权限假成功 | `AcpBridge.answer_permission` 真实送达 JSON-RPC 响应帧（outcome/optionId），请求不存在/已答复/过期 → NOT_FOUND；上游未桥接时 → NOT_IMPLEMENTED 明确错误；`pending_permission` 排除已答复 | 接口级实测（假成功路径已除；真实非 yolo 端到端待上游 pending_integration 联调） |
| 12 FIFO 乱序 | 迁移 007：conv_inputs.seq 事务内单调计数（kv conv_input_seq），历史按 rowid 回填；`next_queued` ORDER BY seq | 同秒并发 3 条入队实测按到达序（seq 1/2/3）派发 |
| 13 STALE_INSTANCE 吞成 500 | errors.py 补 STALE_INSTANCE/NO_LIVE_INSTANCE/NO_BINDING→409、UPSTREAM_UNSUPPORTED→422、UPSTREAM_TIMEOUT→504(retryable) | 携带旧 instance_id 发送 → 409 + 当前实例提示 |
| 16 SSE 静默停摆 | EventSource onerror 显式降级条（重连中/无心跳>25s/已关闭）；心跳监测定时器；切换节点/重登时关闭旧 ES 与定时器；cookie 已加 Max-Age（会话随浏览器持久） | 断网模拟：状态条出现；恢复自动续流 |
| 18 取消状态机竞态 | webui cancel：先标 cancelling，桥失败回滚 running；终态条件更新（迟到 completed 不覆盖 cancelled）；绑定 expected instance/generation | 取消→cancelling→prompt 收口 cancelled/completed 分态正确 |
| 19 登录 token 暴露 | 密码控件（默认遮蔽+显示切换）、autocomplete=off、提交即清空、登录卡重出时无残留 | 快照复查：密码点阵显示 |
| 20 视觉细节 | 窄屏顶栏单行（不再逐字竖排）；other→"其他节点"；辅助文字对比度提升；会话头 idle 与"思考中"矛盾修正（活动输入时显示"生成中"）；hint 宽度限制防截断 | 390/768/1440 实测截图 |

## 联合项

| DEF | 状态 |
|---|---|
| 14 历史深度 | 上游 `/sessions/:id/history`（分页、oldestSeq/newestSeq）已接入为主路径；磁盘事件日志为兜底。协调员实测 365 条深历史。DEF-14 关闭 |
| 02 跨重启幂等 | 上游 daemon 持久化已修（acp_e2e step8 实测 done 可对账） |
| 03 取消注入伪消息 | 上游改走 /interrupt 真实取消。本环境实测 interrupt requested 但 confirmed 不稳定（长轮次宿主未确认停摆）——联调项保留，Hive 侧不伪造 cancelled |
| 04 权限桥接 | 上游 core-verified/pending_integration；Hive 答复链路已就绪待联调 |

## 复跑命令

```bash
python scripts/quicktest.py            # 回归（全过）
python scripts/acp_e2e.py              # 协议级联调（8/8，证据 work/acp-e2e/e2e-evidence.json）
```

## 版本与边界

- Hive 未修改 ZSession 任何源码；上游接口以 ACP_INTEGRATION.md v0.5 为准。
- 会话内容表（conv_events/conv_inputs/conv_bindings）与 ops 事件流物理隔离。
- 取消/中断/断线不自动改变业务 Task 终态；聊天不生成 work。
