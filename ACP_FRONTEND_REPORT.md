# ACP 会话舱与星辰大海升级 — 最终报告

日期：2026-10-01 · FRONTEND_ACP_COSMOS_PROMPT 施工完成 · 上游 ACP_INTEGRATION.md 已联调

## 结论

**真实 ZSession → ACP → Hive → 浏览器 的对话、事件、取消与断线对账全部端到端验证通过。**
协议级 8/8 用例 PASS（scripts/acp_e2e.py，证据 work/acp-e2e/e2e-evidence.json）；
生产链路（浏览器 UI 发消息 → 真实 GLM-5.3 流式回答 → 会话舱实时渲染）实测通过；
`scripts/quicktest.py` 回归全过。

## 1. 真实链路证据

### 1.1 协议级（scripts/acp_e2e.py，独立测试会话，隔离 cwd）

| 步骤 | 结果 | 证据 |
|---|---|---|
| initialize | PASS | protocolVersion=1；`_meta.zsession.methods` 声明三个扩展；loadSession=true |
| session/new | PASS | 真实 sessionId `sess_e6e1d5f7…`（bootstrap:false，不消耗首轮） |
| 中文对话（真实流式） | PASS | stopReason=end_turn；5 个字符级 agent_message_chunk；GLM-5.3 真实回答 |
| 幂等重放 | PASS | 同 requestId 重放 0.0s 返回既有 stopReason，0 新 chunk（不重发） |
| 取消 | PASS | 长轮次中途 session/cancel → stopReason=cancelled（原生 interrupt 确认） |
| input_state 对账 | PASS | req-1=done、req-2=cancelled（不触发模型调用） |
| 断开不终止会话 | PASS | adapter stdin 关闭后 daemon 侧会话仍可查、未归档 |
| 重接 load + 对账 | PASS | session/load loaded:true（live-forward）；跨 adapter 重启 input state **如实 unknown**（契约 §6；Hive 侧标 outcome_unknown 不盲目重发） |

### 1.2 生产链路（video-lab 同一 Hive home，独立测试 Agent `acp-e2e · 桥接测试员`）

- daemon 以 `HIVE_ACP_ADAPTER="node …/bin/zsession.mjs acp --stdio"` 启动，
  bootstrap 日志 `acp.connected {loadSession:true…}`。
- UI API 入队（0.03s 即返，受理-流式分离）→ ConversationService 派发 →
  ensure()（session/load live-forward 接入实例现有会话 `sess_5deec110…`）→
  session/prompt → 真实轮次 → completed。
- 浏览器会话舱（ACP 标签 + 真实 session id）实测：
  - UI 发送「请用两句话介绍你能做什么。」→ 用户回显（乐观气泡与 SSE 事件去重）→
    真实 GLM-5.3 字符级流式回答 + 公开思考行（斜体）实时渲染 → 轮次收口 completed。
  - 轮次结束后行状态落终态（finalize_turn），历史自愈（新输入前流式行补 completed）。
- 忙冲突路径实测：spawn 引导轮未结束时 prompt → 上游 -32011 → Hive 按契约
  「排队由 Hive 决策」回队重试（10s 冷却），不抢占不失败吞没。

## 2. 桥实现（hive_core/convbridge.py AcpBridge）

- **帧协议**：ndjson JSON-RPC over stdio；专用读线程（逐字符缓冲、不因长 prompt
  阻塞 cancel）；并发请求 id 匹配；通知分发（session/update、_zsession/input_state）；
  未实现的反向请求明确 -32601 拒绝不悬挂。
- **投影**：`user_message_chunk`→user.message（并作消息分组边界）；
  `agent_message_chunk`/`agent_thought_chunk`→按会话+序号聚合行，payload.append
  字符级增量（投影层与前端双侧累加）；`tool_call(_update)`→状态映射
  pending/in_progress→running、completed→completed，终态 content 进 result/error；
  轮次收口 finalize_turn 落 agent.message.final/thought.final。
- **生命周期**：initialize 协商；ensure() 以实例 handle 的真实 sess:* 经
  session/load live-forward 接入（不复制/不新建/不 kill）；adapter 重启后按
  `_loaded_sessions` 重新 load；STALE_INSTANCE 代际检查拒绝旧面板写入。
- **可靠性**：断线→全部未决输入 outcome_unknown + supervisor 15s 退避重启 +
  重新 load；忙(-32011)→回队+冷却；超时→outcome_unknown；幂等 requestId 全链。
- **harness.py**：移除「queue 等 20s 静默升级 interrupt」——超时如实返回
  `queued-not-delivered`，抢占只能由用户显式选择 interrupt。

## 3. 星辰大海视觉（Phase A，上一轮已交付，本轮延续）

宇宙设计 token、nebula.js 背景 Canvas（reduce-motion/隐藏页暂停）、恒星节点
呼吸光晕（仅真实 busy）、玻璃面板、项目选择记忆。截图：星图全景、会话舱
（真实 ACP 流式+思考行）、审批台、窄屏，见 ZCode 会话工件目录。

## 4. 已知限制（如实）

1. **全量历史 replay 未实现**（上游 live-forward 语义）：session/load 从当前水位
   续流，已落盘历史经 `_zsession/get_session_state`/消息查询读取；Hive 会话舱的
   历史来自 conv_events 自身留存（环保留 5000/会话），请求过旧游标返回
   `gap_before:true`。
2. **权限请求桥接未实现**（上游 request_permission ❌）：yolo 会话权限自动处理；
   非 yolo 待上游补齐后再接 Hive 待决权限卡。
3. **input state 跨 adapter 重启为 unknown**（上游契约）：Hive 以 outcome_unknown
   呈现，不盲目重发；用户可显式重发（新 request_id）。
4. 视频直接输入未启用（无真实链路验证，不虚报）；图片/音频 ContentBlock 待
   实际需要时按上游 ContentBlock 能力接入。
5. GLM-5.3 部分思考内容落在 assistantText 行（上游如实转发，非桥缺陷）。

## 5. 部署与启动

```bash
# 生产（ACP 真实链路）
HIVE_ACP_ADAPTER="node D:/agent_workspace/projects/zcode-session-manager/plugins/session-manager/bin/zsession.mjs acp --stdio" \
  python cli/hive.py --home .hive control system start
# 缺省（无该环境变量）自动回退 fixture 演示桥，UI 明确标注「演示模式」
python cli/hive.py --home .hive control system start
# 协议级回归
python scripts/acp_e2e.py
```

## 6. 验收矩阵对照（提示词 §10）

全项已验：1-24。其中 2/3/4/5/6/7/8/9/10/11/12/14/16/17/20 由 §1.1+§1.2 证据覆盖；
13（向上阅读不强制滚底+新输出按钮）、15（附件预览/能力如实）、18（既有审批链路）、
19（服务端鉴权/无 token 下发）、21-24 见前轮报告与 quicktest。
性能：23 节点项目实测交互流畅（SVG 增量更新 + 背景粒子 ≤260、DPR≤1.5）。
