# 给ZSession Agent：第四轮修复提示词

请直接修复TDEF-01和TDEF-05的插件部分，并配合Hive跑通强writer联合链路。保留已经通过的输入真相、幂等、回放、工具历史和标准模式，本轮不要重新设计整个adapter。

## 1. 范围与证据

只修改：

    D:\agent_workspace\projects\zcode-session-manager

Hive只读参考：

    D:\agent_workspace\valuable-projects\hive

先读适用AGENTS.md、当前改动与：

- Hive/ACP_THIRD_FIX_ACCEPTANCE_REPORT.md。
- Hive/ACP_THIRD_FIX_ACCEPTANCE_DEFECTS.md。
- Hive/ACP_THIRD_FIX_ACCEPTANCE_PROMPT.md。
- 本项目ACP_INTEGRATION.md、ACP_THIRD_FIX_REPORT.md。
- Hive/HIVE_ACP_FOURTH_FIX_PROMPT.md，了解消费者修复范围。

原始证据：

    D:\agent_workspace\tmp\hive-acp-third-acceptance\3acc-20261001-225426-v1

核对当前代码，已修部分独立复验而不重复改。保护其他Agent改动，旧报告/帧/日志不覆盖。

## 2. TDEF-01：真正转发writer凭据头

当前根因是DaemonClient.call支持extraHeaders，但post(path,body)忽略调用方第三参。send/interrupt等已经传writeHeaders，实际却丢失。

修复起点：

    async post(path, body, extraHeaders) {
      return this.call('POST', path, body, extraHeaders);
    }

同时检查全部包装方法和写调用链，确保发送、取消及其它受管写操作的凭据真实传到daemon。不能只修send、漏interrupt，不能让某条路径继续无头owner直通。

保持：

- Authorization与writer身份各司其职。
- 绑定key后缺key/错key拒绝，正确key正常。
- key不会被错误请求头覆盖或泄露。
- owner显式override可保留，普通adapter派发使用受管writer。
- 旧代际/key拒绝，新实例合法接续。

不要取消key校验、清claim、退弱模式或杀宿主来让测试通过。

## 3. 必须通过三层验证

使用独立run、会话、工作目录与测试凭据。禁止触碰生产其它会话、全局claims/clear或全局重启。

1. daemon直连：无key/错key409，对key正常。已有通过证据可作对照。
2. 同一个stdio adapter：session/new或load带writerToken→writerKeyBound=true→session/prompt真正调用模型和工具→marker实际落位。
3. Hive最新修复消费者：开启强writer后沿真实网页/队列派发，原生工具副作用正确，非owner直连旁路。

第二层是本次必测，不能再以daemon直接HTTP矩阵证明adapter已通。

用隔离代理/严格fake daemon记录头存在与是否匹配，不记录秘密值；再用真实绑定key会话证明。

回归：

| 场景 | 预期 |
| --- | --- |
| 强key会话经adapter发送 | 无managed_session 409，原生真实执行 |
| 强key会话经adapter取消 | key正确到达；原生结果如实，未确认不假cancelled |
| 无key/错key | 拒绝，不能自动退无头owner |
| adapter重启load同凭据 | 合法恢复，同requestId不重复执行 |
| key轮换/实例换代 | 旧key/低代拒绝，新合法写可用 |
| busy再同ID重试 | retryable_busy→空闲后真实执行恰一次 |
| rejected/failed/unknown重试 | 原错误/未知，不假end_turn |
| 回放双标/水位/工具历史 | 现有通过行为不回归 |

## 4. TDEF-05：可信版本与证据

源码this.version仍0.5.0，报告自称0.5.3。建立单一adapter版本值，让initialize agentInfo、启动诊断和新报告反映实际实现。

插件包版本与adapter版本如果是不同概念，可分别标明，不必为了对齐改无关包；但相同组件不能三种版本。

保留旧报告作为历史，新报告明确本轮版本/hash与运行进程实际加载情况。不要只改文档标题。

取消测试脚本需有进程级超时与结构化结果。真实取消仍受宿主限制时写受限/未确认，不挂无限脚本、不把自然end_turn当取消成功。

## 5. 接口与Hive交接

更新ACP_INTEGRATION.md，明确本轮头转发修复已生效、准确版本和最小强模式命令。

Hive Agent负责busy处理、评分与运行启用强key；你不代改Hive。提供新版本、可复跑adapter强模式证据和实际接口，双方共同完成联合测试。

保持YOLO业务路径，不重新开启工具权限实验；人类Task审批与Hive控制权限不受影响。

## 6. 交付

本项目根目录写：

    ACP_FOURTH_FIX_REPORT.md

报告包含TDEF-01/05的根因、修改文件、最新版本、daemon/adapter/Hive三层结果、真实marker与帧证据、取消的真实限制、回归与启动步骤。

只通过单侧时标pending_integration，不宣称整套已通过。测试资源安全停止/保留，旧证据不删除。
