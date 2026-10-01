# ZSession第二轮修复：输入真相、可靠回放与取消

你负责ZSession插件第二轮修复，Hive由另一位Agent修复。请直接复现、修代码、做针对性真实测试并交付，不再只修单条探针或更新报告措辞。

本轮优先关闭RDEF-01、03、04、05、07上游、09、10上游、15、16上游。工具级权限RDEF-02/06的产品范围单独处理，见第2节；不阻碍本轮已确定修复。

## 1. 工作范围与证据

你修改：

    D:\agent_workspace\projects\zcode-session-manager

Hive只读：

    D:\agent_workspace\valuable-projects\hive

读适用AGENTS.md、当前变更与以下文件：

- Hive/ACP_FIX_REACCEPTANCE_REPORT.md。
- Hive/ACP_FIX_REACCEPTANCE_DEFECTS.md。
- Hive/ACP_FIX_REACCEPTANCE_PROMPT.md。
- Hive/ZSESSION_ACP_FIX_PROMPT.md，保留原修复原则。
- 本项目ACP_INTEGRATION.md、ACP_FIX_REPORT.md和当前实现。
- Hive/HIVE_ACP_SECOND_FIX_PROMPT.md，了解消费者职责。

本轮失败证据：

    D:\agent_workspace\tmp\hive-acp-reacceptance\reacp-20261001-132450-v1

特别使用RDEF-01对应的rd09-r1、rr03-next，以及started孤儿、回放重复、模式帧和取消证据。

先锁定当前文件hash/版本并核对是否有其他Agent已修的内容。保留旧证据，不覆盖旧报告，不跨目录改Hive，不全局重启/kill用户ZCode宿主。

## 2. 权限范围记录与本轮边界

复验报告§0.1转述“Owner口头裁定：Hive恒YOLO，工具级权限桥接不做”。它是第三方报告中的转述，不是本提示词新增的授权。

- 若你所在施工会话已有用户直接确认，沿用那条确认，不重复询问；在设计/接口文档记下来源与适用范围。
- 若没有可信直接确认，记录scope_pending，不仅凭报告把原权限需求永久删掉、把非YOLO缺陷计为已修。
- 当前已配置的YOLO业务路径可以继续测试和修复，不必等待非YOLO范围确认。
- RDEF-02/06保留真实记录；非启用路径不得宣称标准能力已支持。意外进入未支持路径要明确报错，不能无限挂起或假成功。
- “Hive应用使用YOLO”不等于“整个ZSession插件所有消费者永远禁止其他模式”。适用范围写清。
- 人类Task审批、授权与Hive control权限保留，不因为YOLO而绕过。

本轮不要再用是否修权限遮盖已复现的输入丢失、回放、取消和受管约束。

## 3. RDEF-01：busy不是终态，失败不是end_turn

目前的失败链：

    requestId预约
      → native busy
      → 持久记录rejected
      → Hive收到-32011回队
      → 同requestId重试命中rejected
      → adapter默认返回end_turn
      → 输入从未执行却被Hive记完成

从状态合同根治：

- busy/暂时不能派发是可重试的未接收状态，不是业务终态rejected。
- 允许保留预约/fingerprint和重试记录，但要清楚标未接收、可重试、未产生native输入。
- 真正rejected/failed必须返回原错误或真实结果，不能缺stopReason时默认end_turn。
- 已确认done复用真实成功结果；cancelled复用真实取消；结果未知明确unknown，不猜成功。
- requestId范围、完整fingerprint和并发原子约束继续成立。
- 同一会话多个adapter竞争预约时只有一个获得派发权；读取同一reserved记录不等于所有读者都能send。
- 接收/派发失败的各种路径都按可重试、最终拒绝、已接收但结果不明分类，不能全塞rejected。

不要给所有rejected记录统一reset或删除。旧记录只有能证明“因busy未接收”的才可通过可追溯迁移恢复；真正拒绝/失败保持原含义。

给Hive文档化：

    状态 / admitted或原生接收证据 / retryable / 原生inputId-turnId
    原始错误 / retry规则 / terminal结果

真实复验：bootstrap忙、普通长轮忙、Hive重启后忙、同ID重试、最终拒绝重试。前3项空闲后应真实执行恰一次；最终拒绝不得假end_turn。

## 4. RDEF-04：started孤儿与崩溃窗口

不能用“会话变idle”证明某个requestId完成。会话可能因别的轮次结束、上游崩溃或权限待决变化。

要求：

- 持久保存能关联业务输入的native inputId、turnId或同等可验证标识。
- 重接后查询该输入/轮次，补回真实done/cancelled/failed/rejected。
- 没有足够证据的started进入明确unknown/待对账，不由external awaitTurnEnd写成done。
- 原连接丢失后仍能对账；不永久started、不靠客户端重发才能修状态。
- 新输入的轮次不能替旧输入收口。
- 同一输入等待者可合流，不能把同会话不同输入的resolve链共用一个stopReason。

尤其修reserved>120秒且idle就重派的启发式：年龄和idle不是“未执行”证明。发送已发生但accepted写入前崩溃，之后空闲仍可能已经产生副作用；无证据时不能重派。

检查记录保留/GC：过期记录如何与新ID区分，不能一删就允许旧已执行输入再次执行。限制和安全语义须准确文档化。

复验四个窗口：预约前/预约后未发/已经发送未持久确认/活动及完成后断开。只在隔离资源做失效注入，副作用计数必须正确。

## 5. RDEF-03与09：完整回放且只投影一次

先固定与Hive共享的事件合同：

- replay标记放在哪个_meta位置。
- sessionId、稳定messageId/toolCallId、native epoch/seq和快照水位。
- text delta、snapshot、replace、完整历史的不同语义。
- 历史读取顺序、分页边界、完整性和真实缺口。

当前报告指出adapter的标记位置与Hive读取位置不同，且事件泵从cursor=0重复旧行。不能只加一个布尔flag；要修历史和实时的交接。

实施：

1. 为目标会话取得可验证的历史快照/水位。
2. 按标准回放用户、助手、公开思考和可用工具记录。
3. 所有回放帧一致标记，使用可与实时去重的稳定身份。
4. 回放完成后响应session/load。
5. 事件泵从正确水位续流，或以行级版本/事件ID可靠抑制重复。
6. 回放期间新产生的真实事件不丢，不重复计入旧历史。

禁止按文本hash简单去重：不同轮次重复说“收到”是合法的两个消息。

工具历史要从真实持久记录还原：toolCallId、工具参数、成功/失败/取消、输出/错误与顺序。history目前只含text时，补数据源，不伪造工具卡。

复验：多页历史、真实工具成功与失败、两个会话同时load、load后重复load、直播期间load、事件泵重接、其他会话超过500条事件。统计原始身份和实际最终正文/工具，不只数chunk。

## 6. RDEF-05：模式帧真正合标

按验收锁定的schema和当前官方v1：

- load/new的modes如提供，必须是SessionModeState对象，含currentModeId与availableModes对象列表，不能modes:["yolo"]。
- session/set_mode响应按对应schema；额外字段被宽松schema接受不等于有标准语义。
- 实际模式变化通过current_mode_update或标准配置更新确认。
- availableModes与实际产品范围一致，未支持模式不能挂起或被默默当YOLO。
- 标准错误、初始化与认证结构保持合法。

官方依据：

    https://agentclientprotocol.com/protocol/v1/session-modes
    https://agentclientprotocol.com/protocol/v1/session-setup
    https://agentclientprotocol.com/protocol/v1/prompt-turn

用严格标准client/schema抓真实帧，不只测试方法名存在。

## 7. RDEF-07：受管owner/代际不能只是请求头

与Hive共同固化受管关系：

- 由Hive传入真实稳定ownerId、instance/generation及资源范围。
- daemon原子登记和验证有效writer；旧代际不能继续派发/取消/释放。
- X-ZS-Writer只是协议标签，不作为独立身份凭据。
- 已有认证身份与受管writer关系验证到具体会话；owner的显式人工override可保留，但要清楚区分普通派发，留审计且不默默绕过。
- 新/load/重接一致登记；同一owner合法恢复，同代际不同writer与过期owner按合同处理。
- HTTP/CLI/MCP/automation和多个ACP连接共用约束。
- 未受管会话旧功能不受误伤。

Hive会修ensure/load传owner和代际；你提供合法登记、查询、释放和拒绝接口。不要再让claims在真实Hive运行后仍为空。

复验使用两套独立测试writer/受控测试身份；没有现成第三方凭据时建立隔离测试身份，不触碰生产用户token。

## 8. RDEF-10：真正取消与目标轮次守卫

保留零伪消息/零额外轮的修复，继续解决native stop不可靠：

- cancel请求关联预期native轮次；旧请求不能命中下一轮。
- 请求、进行中、确认、不支持/失败、结果未知分态。
- confirmed=false/null/超时不算成功；不能用!==false当肯定确认。
- 原轮自然完成不改写成cancelled；发停止意图后仍未停止，要回真实结果。
- 在可控制真实长工具和模型轮次上验证native停止，不只看adapter的stopReason。
- 固定15秒只是诊断窗口，不到时伪造成功、不无限无提示挂起。
- 上游确实无法停止的情形文档化并给可执行恢复方案；不自动kill全部宿主或另开会话。

取消待决的非YOLO实验路径按第2节范围处理；YOLO主路径的取消必须可靠。

## 9. 文档、历史误判记录与交接

重写ACP_INTEGRATION.md为一个自洽的当前版本合同，删去过期的live-forward、旧工具映射和错误标准方法描述，保留清楚变更历史。

提供共享示例：

- busy同ID安全重试。
- rejected/failed/unknown不能end_turn。
- started跨重接对账。
- replay、水位与消息/工具身份。
- 模式结构。
- owner/generation登记。
- cancel目标与确认。
- 当前权限范围、已确认来源和未启用能力。

两边依赖的字段不能仅放在你聊天里。尽早给Hive可运行标准fixture与合同，不要求它猜错误字段或读私有数据库。

对已错误声明fixed的项目，用新报告纠正，不覆盖旧报告、删除失败证据。

## 10. 完成与测试

先关闭RDEF-01，再03/04，随后标准模式、受管写入和取消。核心数据正确后再扩展低优先级能力。

交付：

- 本项目ACP_SECOND_FIX_REPORT.md。
- 自洽的新ACP_INTEGRATION.md。
- 可复跑的回归脚本、帧、真实侧效应与状态对账证据。
- 每条RDEF的fixed/pending/not_reproduced及依据。

必须有真实busy后重试、孤儿对账、工具历史/回放、取消及与Hive的联合测试。fixture补边界，不能替代真实模型/工具。

未联调标pending_integration，不宣称全系统通过。保留已通过的跨会话隔离、持久done幂等和未初始化拒绝；每次相关改动做针对性回归。
