# ACP 第三轮修复独立验收报告 — run 3acc-20261001-225426-v1

> 独立验收 Agent：ZCode 会话。日期：2026-10-01。
> 输入：Hive `HIVE_ACP_THIRD_FIX_REPORT.md`（a5da2b9）+ ZSession `ACP_THIRD_FIX_REPORT.md`
> （自称 v0.5.3，源码版本值仍 0.5.0）+ 合同 ACP_INTEGRATION.md v0.6.1。
> 证据根：`D:\agent_workspace\tmp\hive-acp-third-acceptance\3acc-20261001-225426-v1\`

---

## 0. 结论（先行）

| 维度 | 结论 |
|---|---|
| **Hive 真实派发恢复** | **部分恢复，不通过**。不 busy 时真实派发可用（SDEF-01 属性错位已修）；但**首次 busy 冲突即队列死锁**（TDEF-02：`sqlite3.Row.get` 崩线程→active_input 门永挡）。修复报告的"真实派发实证"未覆盖 busy 路径。 |
| **兜底评分服务端校验** | **通过（主体）**。SDEF-02 关闭：范围/类型/冲突/选项/步长/必填全拒，0≠null，评论不结单，fresh 任务不误终态。残留 TDEF-03（顶层表示仅兜底归一）与 TDEF-04（恶意键入账）。 |
| **弱模式真实会话** | **受限**。不 busy 窗口可真实对话；busy 窗口死锁（TDEF-02）。 |
| **强 writer 联合写入** | **不通过**。三层判定：daemon ✓（对 key 200）/ **adapter ✗（TDEF-01：post() 丢第三参，writerKey 头从未到达 daemon，adapter 被自己绑定的 claim 409 拒之门外）** / Hive 层默认关闭（HIVE_ACP_WRITER_KEY 未启用）且本轮被 TDEF-02 阻断活体验证。**强模式主线未跑通，完整通过不成立。** |
| **历史/实时/恢复** | **通过（adapter 侧）**：SDEF-05 关闭（5/5 帧双标、重载一致、水位续流）；恢复对账沿用二轮关闭证据。Hive 联合（_replaying 窗口等）BLOCKED-by-TDEF-02。 |
| **原生取消与准确状态** | **沿用二轮活体证据**（严格判定/零伪消息/未停如实 end_turn；宿主停止能力仍受限）。本轮 T3 复跑脚本挂起未收，标 NOT_RETESTED-live，不因此改判。 |
| **登录/响应式/审批/性能** | **通过**：SDEF-04（冷启动立即登录）、SDEF-07（320 退出完整可点 right=314）关闭；兜底表单（分段判决/双滑块刻度/未评分态）实测渲染；视觉评审 4/4 PASS。性能 trace 未重跑（沿用前注）。 |
| **SDEF-01~07 关闭状态** | **CLOSED 6**（01/02/04/05/06/07）· **OPEN 1**（03＝TDEF-01+Hive 未接入）· 新增 **TDEF-01~05**（2 阻断/1 中/2 低） |
| **旧 BLOCKED 用例本轮结果** | M01 实测→暴露 TDEF-02；M02-M10、强模式 Hive 层、SSE watchdog 活体仍 **BLOCKED-by-TDEF-02**；评分/登录/窄屏/回放类完成。 |
| **实际可用范围** | 弱模式不撞 busy 的对话 + 审批全链（含兜底评分）+ 视觉响应式可用；**任何 busy 窗口会死锁该 Agent 队列**；强 writer 模式不可用。 |

## 1. 版本锁定

- Hive：git `a5da2b9`（21:38 前提交，文件 ≤21:23:38）；生产 daemon PID 70160 @21:24:21 → 运行三修代码。
- ZSession：acp.mjs @21:03（md5 `b15e3596…`，含 SDEF-05 修复）；daemon PID 66020（manager/http 未变，
  沿用二修）；**adapter 源码内部版本值 '0.5.0'，报告称 v0.5.3**（TDEF-05）。
- 测试环境：隔离 home `hive-home`（19995，弱模式默认）+ 2 测试会话 + 12 fresh 评分任务；
  生产 8765/19962 只读未触碰。

## 2. 关键实测

### 2.1 弱模式冒烟（M01）→ TDEF-02
首条输入（含 marker 工具任务）撞 bootstrap busy：-32011 到达后 `_fail_input` 崩溃
（`AttributeError: 'sqlite3.Row' object has no attribute 'get'`，convbridge.py:821），
输入永卡 dispatched、队列死锁、零日志。完整 traceback 存证。**SDEF-01 的"真实派发实证"
只在非 busy 路径成立。**

### 2.2 强 writer 三层（T1）→ TDEF-01
adapter `session/new` 带 writerToken → claim 绑定 sha256 成功（`writerKeyBound`）→
同 adapter `session/prompt` → **409 managed_session**；对照 daemon 直连同 key → 200。
根因一行：`post(path, body)` 丢弃 extraHeaders（acp.mjs:50）。Hive 三修报告自己发现并以
默认关闭规避——**SDEF-03 不能以"机制完成/待上游"关闭**（验收提示词 §2 明示）。

### 2.3 评分矩阵（E 系列，12 fresh 任务）
拒：999/101/-1（顶层与 answers）、bool/字符串/数组/空、两处冲突、未知选项、
显式 step5 的 77、缺必填；过：75、合法 0 分（result 保留 0 非 null）、普通任务自由文本；
评论不结单；被拒任务保持 queued 可重试。**SDEF-02 主体 CLOSED**。
残留：显式 fields 顶层表示不归一（TDEF-03）；恶意 grant/authorize 键原样入账（TDEF-04，
`explicit_autonomy_grant` 正确为 null，无实际授权）。

### 2.4 回放（T2）
双标记 5/5 帧（user2/agent2/tool1 全部 params+update 双标）、重复 load 全文一致、
load 后新回复实时到达。**SDEF-05 CLOSED**（与 ZSession tx1 报告一致）。

### 2.5 浏览器与视觉
冷启动 +300ms 立即登录成功（SDEF-04 CLOSED）；320px 退出 right=314 完整可点、
390 底部导航完整（SDEF-07 CLOSED）；兜底表单分段判决/双滑块刻度/未评分态实测渲染；
glm-vision 评审 4/4 PASS（三条低风险项见缺陷清单）。截图：`browser/t4-*.png` ×4。

## 3. 修复优先级

1. **TDEF-02**（一行）：`inp["session_id"]`——恢复 busy 路径；修完必须以 busy 窗口真实输入回归
   （本轮再次证明施工报告的实证没有覆盖关键路径）。
2. **TDEF-01**（一行）：`post(path, body, extraHeaders)` 透传——强模式 adapter 层打通后，
   Hive 开 `HIVE_ACP_WRITER_KEY=1` 复跑 T1/W 矩阵联合。
3. TDEF-03/04（评分归一与白名单）、TDEF-05（版本号与文档对齐）。
4. 之后补 TDEF-02 阻断的全部联合用例（M02-M10、SSE watchdog 活体、取消复跑）。

## 4. 交付与收尾

- 本报告 + `ACP_THIRD_FIX_ACCEPTANCE_DEFECTS.md`（TDEF-01~05，含行号/复现/复验条件）。
- run 目录保留全部证据；测试 home（19995）与测试会话保留供复现。
- 收尾：停止本 run 测试 daemon；清理挂起的验收脚本进程（仅本 run 起的 node）；生产未动。
