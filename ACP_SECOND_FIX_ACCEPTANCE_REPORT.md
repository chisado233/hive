# ACP 第二轮修复独立联合验收报告 — run 2acc-20261001-200745-v1

> 独立验收 Agent：ZCode 会话。日期：2026-10-01。
> 输入：ZSession `ACP_SECOND_FIX_REPORT.md`（v0.5.2/合同 v0.6）+ `ACP_INTEGRATION.md` v0.6；
> Hive `HIVE_ACP_SECOND_FIX_REPORT.md`（96b3c1a）。
> 证据根：`D:\agent_workspace\tmp\hive-acp-second-acceptance\2acc-20261001-200745-v1\`

---

## 0. 结论（先行）

| 维度 | 结论 |
|---|---|
| **ZSession 单侧** | **通过（协议核心）**。RDEF-01/03/04/05/07/09/15 全部实测关闭：输入状态机诚实（busy 可重试、失败不假成功、孤儿不臆断）、回放完整带标记不重复、writer 凭据强制、标准 SessionModeState。 |
| **Hive 单侧** | **不通过**。SDEF-01（阻断）：`_started_inputs` 挂错类，96b3c1a 下任何输入派发立即 AttributeError——会话链路 100% 不可用；二修报告的"实测"为旧记录清点，未做过一次活体验证。SDEF-02：双评分兜底无服务端校验（999 分入账）。 |
| **v0.6 真实联合链路** | **不通过（被 SDEF-01 阻断）**。adapter 侧新合同全部按 v0.6 行为验证通过；Hive 侧因派发崩溃无法完成任何联合输入用例（I01 网页版/I04-I07、恢复/取消/SSE watchdog 活体全部 BLOCKED）。 |
| 输入幂等/恢复 | adapter 侧 **CLOSED**（I01 adapter 版完整通过：busy→retryable_busy→空闲重试真实执行恰一次；I02 全矩阵 -32015/16/17/10 正确；O01/O03/O06 崩溃窗口/归属/并发单次执行正确）；Hive 侧联合 **BLOCKED**。 |
| 完整回放与实时 | **通过（adapter 侧）**：P01 工具+文本+思考回放（15 帧全标记）、P02 重复 load 一致、P03 load 后 live 零重复、P06 水位。Hive `_replaying` 窗口联合验证 BLOCKED。 |
| 实际 writer/代际 | **daemon/adapter 通过**（四场景矩阵+代际冲突 409）；**Hive 未接入 writerToken**（SDEF-03）——生产运行于"声明即通过"弱模式。 |
| 原生取消与准确状态 | 严格判定（confirmed===true）代码确认+上轮活体诚实语义；**取消能力本身仍受宿主限制**（不判通过，按合同文档化）。本轮活体取消因 SDEF-01 未能复跑。 |
| UI/兜底审批/性能 | 兜底 B/C 双表单实测渲染（含授权框/未评分态）；**服务端校验缺失（SDEF-02）**；会话流 UI 因 SDEF-01 无法活体验证；窄屏/星图截图已存待视觉评审。 |
| 权限 scope | **用户直接指示（本会话，2026-10-01）：Hive 恒 yolo、工具级权限不做**——来源可信（高于两报告的转述/沿用）。RDEF-02/06 维持 N/A·留档；YOLO 主线独立结论见上。 |
| RDEF-01~16 | **CLOSED 8**（01/03/04/05/07/09/15 单侧关闭 + 10 严格判定）· **PARTIAL 5**（08/11/12/13/16——部分验证或被 SDEF-01 阻断活体）· **OPEN 1**（14：修复本身引入 SDEF-01）· **N/A 2**（02/06 按用户裁定） |
| 原 55 项 | 本轮复测覆盖输入真相/回放/writer/模式/兜底审批核心；会话流/取消/SSE 活体受 SDEF-01 阻断；其余沿用需注明"联合层未复验"。 |
| **最重要阻断** | **SDEF-01（一行初始化错位，恢复 Hive 会话功能）**；其次 SDEF-02（兜底校验）。 |

## 1. 版本与合同对齐（§2 核实结果）

- ZSession：acp.mjs 41968B @17:49、manager 80629B @17:34、http 19605B @17:42；
  daemon PID 66020 @17:42:45（加载 manager/http 新码）；adapter 按 17:49 代码逐连接 spawn → **v0.5.2 全量生效**。
- Hive：git `96b3c1a`（17:38:43 提交）；hive_core/hive_web 文件 mtime ≤17:33 < daemon PID 56100 @17:35:02 → **生产 Hive 运行二修代码**。
- 结论：**两侧二修均已部署**。Hive 报告"上游二修尚未落地（13:17 快照）"为写作时点的过时表述，
  本轮联合测试全部连接同一新版本——版本错位问题不存在，真正的阻断是 SDEF-01。
- 合同：ACP_INTEGRATION.md v0.6 与实测行为一致（唯一偏差 SDEF-05：文本回放帧 update 级标记缺失）。

## 2. 关键实测结果

### 2.1 输入真相（RDEF-01 关闭链，adapter 级）
1. busy 窗口同 ID 输入 → 当场 `-32011` + 持久落账 `retryable_busy`（**不再终态化 rejected**）。
2. 空闲后同 ID 同内容重试 → **真实执行**（13.6 秒真实模型轮）→ marker 恰 1 次 → `done`。
3. 终态重试矩阵：done 复用原 stopReason；rejected→**-32015（带原错误）**；failed→-32016；
   unknown→**-32017（拒绝盲重派）**；同 ID 异内容→-32010。**失败路径永不返回 end_turn。**

### 2.2 崩溃恢复与归属（O 系列）
- started 孤儿（会话已 idle）重试 → -32017（不臆断 done、不重派）。
- 输入 B 运行期间孤儿 A 记录保持 started，**B 的完成不替 A 收口**；B 副作用恰 1 次。
- 双 adapter 并发同 ID → 单次执行（marker=1），两侧均得到结果。

### 2.3 回放与实时（P 系列）
- 多轮含工具会话 load：回放 15 帧（user 6/agent 6/**tool 3**），全部 params 级 `replay` 标记，
  `modes` 为标准 SessionModeState，响应 `_meta.watermarkSeq` 存在。
- 同会话第二次 load：全文 8741 字符 **完全一致**（无重复追加）。
- load 后发送新消息：增长内容仅"交接正常"四字——**零旧文重复**（RDEF-03 核心场景关闭）。

### 2.4 Writer 与标准（W 系列）
- claim 携带 writerToken → daemon 存 sha256（writerKeyBound=true）。
- 四场景矩阵：owner 无头直通 200 / 声明 acp 无 key **409** / 错 key **409** / 对 key **200**。
- 旧代际 owner 重复 claim → **409 claim_conflict**。
- session/set_mode → `_meta.zsession.modeState`（标准形状）+ `current_mode_update` 通知实测到达。
- **Hive 缺口**：ensure/send meta 仅 ownerId、无 writerToken → 生产会话为未绑 key 的弱声明模式（SDEF-03）。

### 2.5 Hive 侧（被 SDEF-01 阻断前完成的部分）
- SDEF-02 实测：兜底任务（request_type=dual_evaluation、无 ui.fields）POST `artifact_score:999` → **200 入账**。
- 兜底渲染：契约键（B）与 human_eval+intent（C）任务均渲染标准双评分表单（双滑块+判决+未评分态+授权框）；
  显式 fields（D）沿用上轮证据；非双评分任务（E）按代码判定不渲染。
- 截图：`browser/s3-fallback-B/C-1440.png`、`s3-narrow-390/320.png`（视觉评审见 §4）。

## 3. SDEF-01 详证与影响面

见 `ACP_SECOND_FIX_ACCEPTANCE_DEFECTS.md` SDEF-01（含复现与一行修复）。
被阻断用例清单：I01（网页版）/I04/I05/I06/I07/I08(Hive侧)、O02 联合、P08 交接联合、
取消活体复跑、SSE watchdog 活体、会话舱流式/状态 UI——均 BLOCKED-by-SDEF-01，不计通过也不计失败。

## 4. 视觉评审（glm-vision）

对 `browser/s3-fallback-B-1440.png`（兜底双评分表单）、`s3-narrow-390/320.png`（窄屏）执行评审。
结论与裁剪证据：`tmp_crops/`。要点：兜底表单结构/未评分态/授权框渲染成立；窄屏顶栏单行保持
（上轮修复未回归）。星图/会话流视觉因 SDEF-01 无新活体画面，沿用上轮 V 系基线。

## 5. 用户指示记录（§3 权限范围）

**本会话用户直接指示（2026-10-01）**："Hive 就是恒 yolo、工具级权限不做了，没必要做不是 yolo 的"，
并要求按此改判报告（已在上轮执行）。可信来源：用户原话（优于 ZSession 报告的 scope_pending 转述存疑
与 Hive 报告的"未获相反指示沿用"）。据此 RDEF-02/06 = N/A·留档；YOLO 主线结论独立给出（§0）。

## 6. 修复优先级建议

1. **SDEF-01**（一行）：恢复 Hive 会话派发——修完必须做至少一次真实派发回归（本轮证明二修报告未做过）。
2. **SDEF-02**：服务端兜底 schema 校验。
3. SDEF-03：Hive 接入 writerToken（v0.6 合同 W02）。
4. SDEF-05：文本回放帧补 update 级标记（对齐合同文字）。
5. 之后按上轮清单补 SDEF-01 阻断的联合用例（恢复/取消/SSE watchdog 活体、I04-I07）。

## 7. 交付与保留

- 本报告 + `ACP_SECOND_FIX_ACCEPTANCE_DEFECTS.md`（Hive 根目录）。
- run 目录保留全部帧/结果/截图/日志；测试 home `hive-home`（19990）与测试会话保留供复现。
- 生产 8765/19962 全程未触碰；本轮未登录生产 UI。
