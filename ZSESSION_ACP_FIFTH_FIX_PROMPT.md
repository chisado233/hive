# 给ZSession Agent：恢复对账专项提示词

最新第四轮独立验证确认TDEF-01～05全部关闭，强writer、busy重试、幂等与评分核心已通过。本轮不重写这些能力，集中解决FDEF-01：adapter死亡后，原生已完成但持久输入仍started，Hive永久outcome_unknown。

请直接实施、测试并与Hive联调。保持原生执行只一次，不通过换requestId再调用模型“恢复”。

## 1. 范围和证据

只改：

    D:\agent_workspace\projects\zcode-session-manager

Hive只读：

    D:\agent_workspace\valuable-projects\hive

先读适用AGENTS.md、当前版本和改动，再读：

- Hive/ACP_FOURTH_FIX_ACCEPTANCE_REPORT.md。
- Hive/ACP_FOURTH_FIX_ACCEPTANCE_DEFECTS.md。
- Hive/ACP_FOURTH_FIX_ACCEPTANCE_PROMPT.md。
- 本项目ACP_INTEGRATION.md、ACP_FOURTH_FIX_REPORT.md。
- Hive/HIVE_ACP_FIFTH_FIX_PROMPT.md，了解消费者职责。

证据：

    D:\agent_workspace\tmp\hive-acp-fourth-acceptance\4acc-20261002-004613-v1

重点重放J05/fj05-long：adapter死亡，原生继续完成，持久记录不收口。

主修FDEF-01上游；FDEF-02面板恢复由Hive修。FDEF-03为现存取消限制，不因本轮恢复修复伪称已解决。FDEF-04仅提供可取得的真实内容类别信息，不按英文文字猜reasoning。

## 2. 输入结果必须脱离adapter生命周期

目前终态主要由adapter活动prompt收口。adapter死后这个收口者消失，daemon记录永久started。

设计并实现可接续的原生输入/轮次证据：

- requestId/sessionId/fingerprint与原生inputId、接收commandId、turnId等明确关联。
- 区分命令已接收、实际轮次已开始、工具/模型仍运行、原生终态。
- 已有持久输入登记仍是真源，adapter内存只是缓存/连接。
- 优先由常驻daemon依据真实事件/原生历史更新结果，或在查询时执行可重复的只读对账。
- daemon/adapter重启后都能恢复关联与最后处理的水位。
- 对账不触发session/prompt、原生send或工具，不产生新轮次。

不需要新增Hive业务实体。增强现有输入记录、状态查询与必要的_zsession扩展即可。

## 3. 归属证明：不能仅凭时间窗或idle猜done

FDEF-01报告建议用inputId/时间窗匹配历史。时间窗、原文和“看到答案”可以帮助定位，不能单独证明归属。

自动收口需强关联：

- 原生输入ID/轮次ID，或可验证且唯一的稳定关联链。
- 对应轮次的真实终态/stopReason/错误。
- 会话和代际一致，不能让后续B轮替旧A轮收口。

禁止：

- session idle就done。
- 找到相同正文/相近时间就done。
- 新轮结束给所有started一起done。
- adapter死亡或工具某一步完成就算整轮完成。
- 通过重发旧指令或新requestId产生一个新结果替旧结果。

遇到旧记录缺强关联，保留unknown并给明确reason、可查证据、可人工处理状态；不永远只有一个无说明的unknown。

记录准确的恢复来源、原生引用、对账时间与版本。已确认终态不能被迟到旧事件回写started。

## 4. 对Hive提供稳定恢复合同

扩展现有_zsession/get_input_state，或新增协商声明的_zsession/reconcile_input，文档化：

    sessionId / requestId / native inputId / native turnId
    state / stopReason / error
    matched或verified / evidence_refs / reconciliation_source
    recoverability（auto / manual_required / insufficient_evidence等）
    最后证据水位/epoch/时间

方法名/字段可以根据当前实现合理确定，但必须先固化ACP_INTEGRATION.md并让Hive按同一合同接入。

- 查询可以修正经证实的登记状态，但不重新执行输入。
- 已完成/失败/取消都正确回报，不都转end_turn。
- 仍运行的输入准确显示，活动轮结束后可自动收口。
- unknown不会伪装成功，失败也不以成功信封冒充正常完成。
- 多次查询/并发查询幂等，不重复写副作用或多份终态。

若提供人工对账确认接口，必须验证真实owner/代际、原输入与证据引用，保留原状态和操作审计；不能一个任意客户端传state=done就修改真源。Hive UI/control负责操作入口，你负责可校验的原生依据与登记。

## 5. 真实恢复测试

使用独立强writer会话和fresh run，不动生产/旧挂起证据。

| 场景 | 预期 |
| --- | --- |
| 长轮started时只终止本run adapter | 原生继续，结果收口不依赖旧adapter存活 |
| 原生结束后新adapter查询 | 有强关联则≤60秒内返回真实终态；无证据给可执行人工处置，不假done |
| 对账时原生仍running | 继续观察本轮，不提前收口，结束后可恢复 |
| 已完成A、同session运行B，再对账A | B不能替A证明完成 |
| 两条相同文本不同requestId/轮次 | 依原生身份区分，不按文本去重/错误配对 |
| 失败、取消、自然结束 | 各自真实结果，停止意图不冒充取消成功 |
| adapter/daemon重启与并发查询 | 恢复与终态幂等，无二次模型调用 |
| 证据缺失/保留范围外/非法会话或owner | unknown/明确拒绝，不猜、不泄露、不给任意改账 |

至少一条真实长轮和一次真实工具侧效应。核对marker/native输入/轮次次数，证明恢复没有重新执行。

严格fixture补故障/迟到/乱序边界，不能替代真实J05。恢复时限从“原生已结束且可取得证据”起算，不强制终止合法长轮。

## 6. 保持已通过能力

针对性回归：

- busy同ID安全重试，最终副作用一次。
- done跨重启幂等、失败/拒绝/unknown不假end_turn。
- 强key发送与取消的凭据头转发。
- 回放双标、水位、工具历史与实时不重不丢。
- 标准模式与版本可信。

不要为了扩大匹配成功率放宽旧代际、writer或不安全重试。

公开思考类别如果原生可取得，以真实channel/row类型提供给Hive；无法分类的assistantText保持原文，别用“英文开头”等规则删内容。

## 7. 交付与联调

本项目根目录写：

    ACP_FIFTH_FIX_REPORT.md

更新ACP_INTEGRATION.md的恢复合同、证据条件、状态、错误、人工处置和版本。新报告附fresh run完整帧/状态/marker/原生引用与命令。

与Hive共同重放原J05：旧adapter死→原生继续完成→新连接恢复→输入按证据收口→UI原位更新。

只有提供接口未联调时标pending_integration，不宣称FDEF-01联合关闭。无法自动匹配的旧记录保留真实限制，人工入口需可实际使用。

不部署生产，不全局重启，不清旧记录。生产当前仍旧代码/弱模式，切换由后续部署任务处理。
