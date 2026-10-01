# 给Hive Agent：第四轮修复提示词

请直接修复TDEF-02、03、04及相关浏览器残留，接入ZSession修正后的writer头转发，跑通强模式和此前被阻断的联合测试。

本轮优先关闭两条实质链：busy不会死锁，强writer实际可派发。不是只修几行再写“代码路径通过”；必须有fresh run真实证据。

## 1. 工作范围

只修改：

    D:\agent_workspace\valuable-projects\hive

ZSession只读：

    D:\agent_workspace\projects\zcode-session-manager

先读适用AGENTS.md、当前版本与改动，再读：

- ACP_THIRD_FIX_ACCEPTANCE_REPORT.md。
- ACP_THIRD_FIX_ACCEPTANCE_DEFECTS.md。
- ACP_THIRD_FIX_ACCEPTANCE_PROMPT.md。
- HIVE_ACP_THIRD_FIX_PROMPT.md、HIVE_ACP_THIRD_FIX_REPORT.md。
- 当前UI_API.md、DESIGN.md、UI_REDESIGN.md。
- 上游ACP_INTEGRATION.md及新ACP_FOURTH_FIX_REPORT.md。
- ZSESSION_ACP_FOURTH_FIX_PROMPT.md，了解对方范围。

证据：

    D:\agent_workspace\tmp\hive-acp-third-acceptance\3acc-20261001-225426-v1

主修TDEF-02/03/04；TDEF-01及adapter版本由ZSession修。保护已有通过项和他人工作，不跨目录改上游，不重建video-lab网络或改审美/方法/授权。

## 2. TDEF-02：busy异常路径不能再死锁

当前_fail_input调用inp.get("session_id")，inp实际是sqlite3.Row，异常处理器再次抛AttributeError，输入永卡dispatched。

修复：

- 明确输入类型契约：在边界统一转dict，或使用sqlite3.Row合法索引，处理None/空字段。
- 检查整个dispatch/error/reconcile路径，不能只替换这一处留下同类Row.get。
- 保持当前input_id贯穿失败处理，不能重新猜next_queued而错改后续输入。
- busy按新合同只回队当前未接收输入，同requestId，有限退避。
- 不把retryable_busy当rejected或failed，不假turn.started，不自动interrupt。
- 新合同不应触发旧orig#r<ts>兼容路径；任何换ID恢复需证明原输入未接收，避免重复副作用。

异常处理本身必须可靠：

- _fail_input中的查询/状态写/日志失败都有明确处置。
- 不用宽泛except:pass掩盖落账失败，然后永久留dispatched。
- 可恢复状态按真实证据写queued/failed/outcome_unknown。
- 持久存储不可用时明确暴露恢复计划/错误，调度恢复后可对账；未知不盲重发。
- _working释放与active_input门一致，线程退场不会把Agent留成无法接续。

对既有因该bug滞留dispatched的记录，先只读清点并查上游输入事实。只有可证明未接收才安全回队；不批量重发所有dispatched，不删除失败记录。

测试至少覆盖：

| 场景 | 预期 |
| --- | --- |
| sqlite3.Row真实输入遇busy | 无二次异常，正确回队 |
| bootstrap忙、普通长轮忙 | 空闲后同ID执行一次，marker=1 |
| busy连续多次 | 有限退避，输入不丢、无派生ID或假started |
| query_input_state异常/超时 | 稳定错误或未知，不死线程锁队列 |
| Generic Exception、写状态失败 | 诊断可见，当前身份正确，恢复后可接续 |
| 后续多条queued | 单调FIFO，错误不污染下一条 |
| busy/运行中重启 | 精准对账，不双派发，不长期卡dispatched |

夹具稳定复现错误，真实bootstrap/长轮必须各测一次。正常不busy的一次completed不能关闭本缺陷。

## 3. TDEF-03：以有效schema统一两种答复表示

目前顶层字段归一仅针对双评兜底；显式ui.fields顶层答复被忽略，误报未填写。

从effective_fields取得全部实际声明字段ID，统一解析：

    data.answers.<field>
    data.<field>

要求：

- 显式fields与兜底同样归一。
- 同字段两处同值合法，异值冲突拒绝。
- 根据字段类型规范化/校验，不能先忽略顶层导致错误必填提示。
- 不能只使用DUAL_EVAL_IDS白名单，丢掉显式schema的name/rating/date等合法字段。
- 普通无结构化表单的任务仍可自由答复，不误推断为双评。
- 原schema类型、范围、步长、选项、必填、spec_version与null/0语义保持。

测试：

- 显式step5的顶层77按步长拒绝、75通过，与answers表示一致。
- 显式非双评表单的自定义字段顶层/answers均合法保存。
- 双评三种兜底标记及显式双评。
- 0/100、可选null、缺必填、非法类型/布尔、冲突。
- 两种表示成功后result值、callback与Task终态一致。

## 4. TDEF-04：结构化结果不得原样夹带授权别名

当前_webui校验后又将raw_data的未知键补回，grant/authorize进入result.data。虽未实际提权，仍给下游误读留下脏字段。

对结构化表单定义清楚结果合同：

- 有效schema声明字段进入规范answers/必要兼容字段。
- 明确允许的追踪元数据单独列白名单。
- 未声明顶层/answers键按合同拒绝或丢弃，不能校验完再原样合回。
- grant/authorize/_verified等没有声明和合法语义的字段不入规范结果。
- explicit_autonomy_grant只走原有明确对象/权限/范围流程，未选为null，高分不自动授权。
- 正文原话作为正文保留，不从其中提取隐式授权。

不要把白名单扩展到所有普通自由数据任务，误删它们合法业务metadata。结构化字段、跟踪metadata、自由data分别按已定义合同处理。

测试合法双评+grant/authorize、answers内未知键、顶层和嵌套冲突、显式自定义字段、未选择/非法自主授权。查实际result和callback载荷，不只HTTP码。

新服务器合同由前端同源schema消费，API/UI/可用CLI等同语义入口一致；更新UI_API说明兼容行为。

## 5. 强writer必须实际开启并闭合

上游Agent将修DaemonClient.post extraHeaders转发。你先完成独立busy与评分修复，再连接其新版本。

在独立Home开启：

    HIVE_ACP_WRITER_KEY=1

验证实际加载，不测试仍在跑的旧进程。

必须满足：

- ownerId/instance/generation正确，writerKeyBound=true。
- Hive→stdio adapter→daemon实际写请求带有效key。
- 网页真实输入、工具、忙时重试、取消、恢复正常。
- 无key/错key409，旧key/代际拒绝，合法新实例接续。
- adapter/Hive重启后安全恢复同代际凭据。
- control.agent.send和系统唤醒共用队列，不通过无头owner偷偷绕过。

修掉因旧上游缺陷设置的临时绕过：

- 不把自动release残留key claim作为普通ensure常规步骤。
- 不因强模式写失败自动解绑/退弱模式。
- 失配显示明确问题或走有效的受控轮换，保留审计。
- 开发演示/明确兼容弱模式如保留，应清楚标记，不默认成为真实生产成功路径。

独立强模式全过后，完成明确部署参数与现有服务的安全接续；保护已有会话与责任，不全局重启宿主或清claims。未完成真实部署/联合验证时如实标记，不写“机制已完、等上游”。

writerToken仍安全保存，不下发浏览器、不进日志/prompt/result；Windows文件权限按实际情况说明，不宣称0600形成OS沙箱。

## 6. 补被阻断的联合用例

最新验收中M02-M10、强模式Hive层、SSE watchdog等大量BLOCKED。本轮修后必须执行：

1. 网页→dispatcher→强key adapter→原生轮/工具→SSE→completed。
2. bootstrap/普通busy同ID回队，真实副作用一次。
3. 两Agent同requestId隔离，失败/未知/拒绝不假完成。
4. queued/dispatched/running/cancelling重启，准确对账与FIFO。
5. load期间live/replay/history/SSE不重不丢，短终态回复不被过滤。
6. 原生取消与expected-turn守卫，失败/未确认如实，不误杀下一轮。
7. SSE网络断/半开/心跳在但业务停流，watchdog自动补齐；不重发输入或泄漏订阅。
8. 真实审批双评分/字段校验/评论/讨论回卡/callback。
9. 模型控件、附件预览/能力提示、多身份授权与文件读取。

仍受宿主限制的原生取消单独标受限；不能用end_turn冒充cancelled，也不能把测试脚本无限挂着当正在验证。超时生成清楚结果并只处理本run进程。

## 7. 视觉与证据小项

不重新设计已经通过的静谧天文台：

- 320退出图标扩大有效触达区，标签/键盘焦点完整。
- 窄屏星区/下抽屉的节点滚动与选中可达，真实操作验证。
- 审批授权区完整入帧，未选态/评分刻度清楚。
- 保留登录竞态修复、正文/消息边界与布局稳定。

1920/1440/390/320实图检查。大量50节点/长历史性能用隔离fixture，真对话/工具/审批用真实链路，来源分别记录。

## 8. 执行与交付

顺序：

1. 独立复现TDEF-02与评分差异。
2. 修异常路径、显式schema归一、结果白名单。
3. 跑真实busy冒烟，不再只验证无busy。
4. 接上上游修头版本并启用强writer。
5. 完成旧BLOCKED联合测试、浏览器和针对性回归。

每条记录根因、修改、测试版本、原始证据和限制。保护旧失败记录，未知状态不猜成功，产物/测试分数不写进video-lab正式人评/方法库。

Hive根目录交付：

    HIVE_ACP_FOURTH_FIX_REPORT.md

并更新UI_API、部署、必要的Hive skill运行说明，提供fresh run脚本、marker/帧/队列记录、浏览器截图绝对路径和可复跑命令。

报告明确TDEF-02/03/04状态、强writer三层结果、旧BLOCKED补测、取消真实范围。源码存在/代码路径/生产旧completed清点都不算本轮实测。
