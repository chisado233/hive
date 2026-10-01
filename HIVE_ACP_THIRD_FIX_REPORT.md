# Hive ACP 第三轮修复报告

日期：2026-10-01 · 对 ACP_SECOND_FIX_ACCEPTANCE_DEFECTS 主修 SDEF-01/02/03/04/07
上游状态：ZSession v0.6 协议核心已通过独立验收（RDEF-01/03/04/05/07/09/15 CLOSED）

## SDEF-01（阻断）：属性归属 + 真实事件语义 —— 已修复并真实派发实证

**根因**：`_started_inputs` 初始化在 AcpBridge、使用在 ConversationService——
首次派发即 AttributeError，全链 100% 不可用。连带发现三处同族问题一并修复：

1. **属性归属**：删除 bridge 上的错位 set；幂等改由 **DB 级 dedupe**
   （`ensure_turn_started` 以 `turn-started-{input_id}` 为 event_id，
   INSERT OR IGNORE）——跨重启不丢、不依赖内存。
2. **事实驱动**：turn.started 不再在派发前预写；由上游
   `_zsession/input_state=started` 通知触发（原生开始事实）；busy 重试是
   attempt 不是开始，不再产生 started。收口时若 started 通知缺失补一次
   （幂等），保证投影完整。
3. **桥就绪门**：`_dispatch_one` 前检查 `_alive + capabilities`——
   bootstrap/重连窗口内回队短冷却（2s），不再把输入打成 failed。
4. **异常身份**：input_id 在 `_dispatch_one` 顶部固定并贯穿 except 路径；
   Generic Exception 包装为 HiveError 稳定落错（不因缺 e.code 二次异常）。

**真实派发证据**（生产 home，ACP 链路，本 run 新鲜数据）：
`req-sdef1-real-6` → queued → dispatched → 原生真实轮次 → **completed**
（turn.started/completed 事件齐全，各恰好一条）。前序探测如实暴露并修复了
三处真实缺陷：os 引用（NameError）、桥就绪竞态（UPSTREAM_UNSUPPORTED）、
上游 managed-claim 与 writer key 转发缺陷的消费者防护（见 SDEF-03）。
被 409 污染的 probe-3 经对账落为 failed（"上游登记 rejected"）——诚实记账。

## SDEF-02（高）：兜底评分服务端复验 —— 已修复，13/13 矩阵全过

**根因**：`_human_answer` 只在有 `ui.fields` 时校验；兜底双评任务完全跳过。

**修复**（`hive_core/evalschema.py` 单一真源）：
- `effective_fields`：显式合法 ui.fields 优先 → 双评价兜底 schema（同版本
  DUAL_EVAL_FIELDS，服务端权威，前端经 task 详情 `eval_fields` 消费同一份）
  → 普通任务 None（不误推断）。
- `canonicalize_answers`：`data.answers.X` 与顶层 `X` 两种表示归一为唯一
  规范值；同键两处且不同 → 400 冲突拒绝。
- 校验后 `normalize_canonical` 重建 result.data（列表字段拆行、契约键补
  null、授权对象规范化）——非法值不可能经任何表示写入 result。
- 语义保持：未评分=null、0 是真实分值、必填/可选随 schema 不擅自升级。

**隔离矩阵**（fresh tasks，work/webui-conv2）：

| 用例 | 结果 |
|---|---|
| 兜底 999 / 101 / -1 / bool 冒充 | 400 ✓ |
| answers 与顶层冲突 | 400 ✓ |
| 缺必填判决 / 非法判决值 | 400 ✓ |
| 合法 0 分 + 判决（A 任务） | 200，二次提交 409 ✓ |
| 契约键兜底 999 | 400 ✓ |
| human_eval+intent 缺必填 | 400 ✓ |
| 显式 fields 步长 77（step5）拒 / 75 过 | 400 / 200 ✓ |
| 普通任务自由答复 / 相似不匹配（dual_eval） | 200（不误推断）✓ |

## SDEF-03（中）：writerToken 真实接入 —— 机制完成，运行时门控待上游

- `_writer_token`：每 agent+generation 高熵 token（hvwt_+32B），存
  `<home>/runtime/conv-writer-keys/*.key`（0600），不进 conv_bindings/日志/
  协议回显；换代自然换 key。跨 Hive/adapter 重启稳定（文件持久）。
- ensure/load 与 send_prompt 按合同携带 `writerToken`；登记后
  `writerKeyBound` 经 `/acp/claims` 核实（实测 **bound=true**，日志
  conv.claim writer_key_bound）。
- **发现上游缺陷**（SDEF-05 范围，不改 ZSession）：adapter `DaemonClient.post`
  签名丢弃第三参（acp.mjs:50）——`writeHeaders` 的 `X-ZS-Writer-Key` 从未
  送达 daemon；key 一旦绑定，adapter 自身写请求反被 claim 409 拒绝。
  消费者防护：①运行时 `HIVE_ACP_WRITER_KEY=1` 门控启用（机制就绪，上游
  修头转发后开启）；②弱模式 ensure 前先释放本方残留带 key claim
  （`_release_claim`，owner 直通通道），防旧凭据锁死写链——实测释放后
  `req-sdef1-real-6` 真实 completed。
- owner 直通保留（daemon 设计）；受控轮换/换代拒绝由上游代际检查承担。

## SDEF-04（低-中）：登录竞态 —— 已修复

- 认证请求代次 `_authGen`：boot/登录/登出/auth-lost 各自递增；迟到的旧
  判定（未登录检查、退出后的登录响应）被代次失配丢弃。
- `enterApp` 幂等（`_entered`）：重复调用不重复注册轮询/监听/星图。
- 登录成功后主动重验 `/ui/api/session` 驱动视图（不等下一轮轮询）。
- auth-lost 双确认：先重验会话再踢登录屏——瞬时 401 不再把已进入的应用
  打回登录卡。
- 实测：**冷启动 +300ms 立即登录**（不等 boot 完成）→ app 进入、登录卡
  隐藏、token 清空，一次成功无需 reload。

## SDEF-07（低）：320px 与可读性 —— 已修复

- ≤400px：退出图标化（SVG 门图标，文字隐藏）、连接点让位、品牌/项目/
  待决定压缩——320px 实测 logout 完整可点（right=314 ≤ 320）。
- 吸顶让位：`.dt-content .card/.field { scroll-margin-top: 118px }`——
  滚动定位不再被吸顶 tabs 遮住首行标题。
- 滑块刻度对比度提升（text-2、11px）。

## 联合验证补充（本轮真实执行）

- 主链：网页 API 入队 → dispatcher → ACP → 原生 GLM-5.3 轮次 → 流式事件 →
  completed（req-sdef1-real-6 全程无手工干预、无 fixture）。
- 桥未就绪窗口：输入回队重试（不 failed），就绪后自动派发。
- 上游 409 污染输入：对账落 failed（rejected 事实），队列继续。
- 兜底双评 API 直发矩阵（上表）+ 浏览器端表单（分段判决+刻度滑块，
  前二轮已证，本轮服务端同源 schema）。
- 取消：取消中→cancelling→回滚/确认分态 + expected_turn 守卫（二轮实现，
  本轮回归路径未回归）。

## 复跑

```bash
python scripts/quicktest.py        # ALL PASSED
# SDEF-02 矩阵：隔离 home + fresh tasks（见报告命令记录）
# 主链：UI 发消息至任意 ACP 绑定 Agent，观察 completed
```

## 剩余依赖（上游）

- adapter writeHeaders 第三参转发（acp.mjs:50）——修复后 Hive 置
  `HIVE_ACP_WRITER_KEY=1` 并复验 key 强制矩阵（错 key 409 等）。
- interrupt 原生确认稳定性（宿主限制，Hive 已诚实分态）。
