# ZSession第三轮：小范围合同补齐与联调支持

最新ACP_SECOND_FIX_ACCEPTANCE_REPORT已经确认ZSession协议核心实测通过。请保持已通过的输入真相、幂等、回放、水位、工具历史、标准模式与writer机制，本轮只做小范围合同补齐和Hive联调支持，不重新设计整个adapter。

## 1. 修改范围

你只改：

    D:\agent_workspace\projects\zcode-session-manager

Hive只读：

    D:\agent_workspace\valuable-projects\hive

读适用AGENTS.md和当前变更，再读：

- Hive/ACP_SECOND_FIX_ACCEPTANCE_REPORT.md。
- Hive/ACP_SECOND_FIX_ACCEPTANCE_DEFECTS.md。
- 当前ACP_INTEGRATION.md v0.6、ACP_SECOND_FIX_REPORT.md。
- Hive/HIVE_ACP_THIRD_FIX_PROMPT.md，了解联调目标。

证据：

    D:\agent_workspace\tmp\hive-acp-second-acceptance\2acc-20261001-200745-v1

核心通过项不无理由重写。主阻断SDEF-01、评分SDEF-02、writer消费者SDEF-03归Hive，不替对方修改其源码。

## 2. SDEF-05：回放元数据与合同一致

当前所有回放帧params级replay正确，文本帧缺update级标记，工具帧已有；v0.6合同声明双标。

最小修复：

- 所有user_message_chunk、agent_message_chunk、公开思考及工具回放，在params._meta.zsession与update._meta.zsession按合同一致带replay/replaySeq及需要的身份字段。
- 不改变正文、顺序、稳定ID和水位，不重新引入双投递。
- 有意保持不同层字段时说明范围；不要只删文档承诺掩盖这次小实现缺口。

复验多轮+工具15帧样本：所有承诺的双标实际存在，重复load全文一致，load后新消息仍无旧文，工具卡集合正确。

## 3. writer消费者接入支持

Hive将补ownerId/generation/writerToken。

提供最新合法示例与检查方法：

- 首次claim/load，writerKeyBound=true。
- adapter/Hive重连恢复同凭据的方式。
- 实例换代、key轮换、旧key拒绝和释放。
- 无key/错key/对key矩阵。
- 普通派发与显式owner override区别。

不要为了让Hive旧消费者立即工作而取消key校验、继续用header自称或放宽旧代际。消费者没传凭据是对方缺口；你保留明确错误和真实状态。

## 4. 本轮不扩大权限或取消范围

使用已有实际YOLO主线与用户已确认范围，文档准确区分未启用非YOLO能力。人类Task审批属于Hive，插件不代裁决。

原生取消限制继续如实说明：确认true才cancelled；未停止不能假成功。本轮Hive联合测试需要你给清楚的原生确认/错误信息和可复现的独立测试场景，但不通过kill全宿主改变结论。

若原生停止存在可安全修复的小缺陷，先保存复现并只修必要路径，补真实回归；否则标受限，不重开整个协议框架。

## 5. SDEF-06测试证据注记

验收工具respB被序列化成[object Object]是测试脚本/证据问题，不是产品缺陷。

不要改旧验收帧/结果来伪装修复。可在本项目测试驱动器中检查结构化JSON保存，生成fresh run结果，保证两个adapter的RPC响应、错误和marker次数都可读。

已有marker=1证明单次副作用；另补响应结构证据，不能互相替代。

## 6. 回归与交付

小范围测试：

- 回放双标、工具/文本集合、重复load。
- load后实时新回复与水位。
- busy同ID安全重试与错误不假end_turn。
- 同连接/跨重启done幂等。
- writer矩阵与Hive合法消费者联调。
- 标准SessionModeState、模式更新、stdout仅协议帧。

真实模型调用控制规模，夹具补边界；不动生产会话、旧挂起证据或Hive账本。

交付本项目ACP_THIRD_FIX_REPORT.md，按需更新ACP_INTEGRATION.md的版本/例子与准确状态。列明单侧通过和Hive联合待验，不代替Hive宣称主链已恢复。
