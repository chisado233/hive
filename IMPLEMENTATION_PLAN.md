# Hive V1 详细施工计划

版本：1.0 · 设计讨论：2026-09-29 · 整理交付：2026-09-30 · 交付对象：后续施工 Agent。

规范：[DESIGN.md](DESIGN.md)。验收：[ACCEPTANCE.md](ACCEPTANCE.md)。本文件是待执行计划，所有阶段初始状态均为“未实施”；不得把文档生成或fixture通过当作真实系统已上线。

## 1. 施工目标与不可改变的边界

在独立的 `valuable-projects/hive/` 实现一个可运行的通用脚手架：稳定Agent、多对多端口订阅、统一Task、三入口、任务树、终态/转交、可选回调、等待、评论@、最小harness、人工端口、trigger/外部生产者、可靠记账与恢复。

必须保留：

- Agent身份独立于实例；有效运行实例0或1。同配置并行创建不同Agent。
- CLI只有 `inbox / emit / control` 三个顶层。没有独立ask；人工询问是普通Task。
- work/notice/message/receipt共用Task。done/fail/trash是终态，forward是原Task转移。
- 任务树与等待图分开；无回调就不自动给来源发送失败通知。
- 工位根目录业务md + 系统agent.md + 配置生成prompt.md + 当前责任摘要。
- 允许缺省配置；harness只需五项最小能力。YOLO与Hive内部权限分别定义。
- trigger-platform现有进程、数据库、worker、审批资产全部保持运行现状；不得用Hive测试操作它们。

第一轮不建设角色模板平台、项目管理产品、复杂pipeline、模型智能分配器、分布式数据库或跨机器资产同步。人工页面只需完成核心待办和回答，外部通道只需定义的结果/通知投递。

## 2. 开工前准备

### 2.1 必读与工作区规则

1. 阅读适用的 AGENTS.md（工作区守则，未随本仓库发布）。
2. 完整阅读本目录三份新文档。旧HANDOVER只用于了解前身，冲突时以DESIGN为准。
3. 检查Hive目录是否已有别人进行中的代码和运行进程；保留既有改动，不重置工作区。
4. 若目录已在Git仓库中，记录当前分支与dirty状态；没有仓库时可按交付要求建立独立仓库，但不得把整个共享工作区擅自纳入。
5. 创建独立开发数据根，例如项目下 `.hive-dev/`，测试使用 `work/acceptance/<run-id>/` 下新的数据根。端口用19962或显式空闲端口，隔离验收默认29962。
6. 真实凭证仅放受保护配置/认证文件，提交中只放schema和无秘密样例。不得读取旧反代项目、旧worker或用户其他服务的凭据来凑接入。
7. 所有测试进程登记PID/句柄到当前run manifest；只停止自己创建的进程，不按程序名批量kill，不递归删除共享目录。

### 2.2 当前可参考的旧资产

以下路径已在文档准备时确认存在，但没有逐行完成可复用性审计；“参考”不等于可以不审查直接照搬。

| 旧资产 | 使用建议 | 必须改造的地方 |
|---|---|---|
| `../trigger-platform/daemon/core/store.py` | 参考SQLite事务、历史与领取测试思路 | 新Task/owner/claim_epoch/回调/等待/幂等模型，不能复用旧库 |
| `../trigger-platform/daemon/core/api.py` | 参考HTTP服务组织与响应封装 | 删除wait长轮询依赖，统一身份与命令网关 |
| `../trigger-platform/cli/trigger.py` | 参考Python CLI启动与文件输入 | 重做三入口和本文97个命令叶子，不继承过时参数 |
| `../trigger-platform/daemon/core/scheduler.py` | 参考周期扫描、状态持久化 | 调度任务期限/outbox/触发器，不为worker创建海量心跳 |
| `../trigger-platform/daemon/plugins/builtin/interval.py`、`cron.py` | 参考定时计算 | 使用新producer身份、事件幂等与明确missed策略 |
| `../trigger-platform/daemon/core/cloudsync.py`、`approvals-hub/hub.py` | 参考人工题目展示/同步 | ask表改为Task；不假设旧云端接口直接兼容，不在施工中部署旧站 |
| `../trigger-platform/web/` | 可提取基础页面样式/组件 | 前端从统一Task/人类回答API取数，不绑定旧角色 |
| `../trigger-platform/tests/test_core.py`、`test_v2.py` | 提取竞争领取/账本断言思路 | 断言迁移到新语义、新fixture；旧44测试不是新系统验收替代品 |

不要继承：crontime_slots、wait无限长轮询、角色自举律令、原生CronCreate改名技巧、旧五态、默认个人队列不允许work的规则。

## 3. 推荐代码组织与契约交付

```text
hive/
  cli/hive.py                    单一CLI入口
  hive_core/
    contracts/                   配置/Task/命令/错误schema
    domain/                      Agent/Port/Task/Wait/Operation等服务
    store/                       SQLite迁移、事务、repositories
    auth/                        主体、授权、实例与claim fencing
    dispatch/                    outbox、通知、定时器、恢复
    runtime/                     最小HarnessAdapter、运行管理
    producers/                   manual/interval/cron、外部入口
    channels/                    human-web、webhook结果通道
    context/                     配置版本、工位、prompt生成
    api/                         统一命令网关/人工API
  web/human/                     最小人工处理页面
  schemas/                       可导出的JSON schema/命令清单
  templates/agent.md              系统工作协议
  tests/unit/                    领域与状态转换
  tests/integration/             HTTP/SQLite/CLI/并发恢复
  tests/fixtures/                模拟最小harness、时钟、Webhook、凭据
  scripts/acceptance.ps1         驱动验收并收集脱敏证据
  examples/                     无秘密配置与TaskSpec
  requirements.txt或pyproject.toml
  README.md
```

这是模块建议，允许合理调整文件名；公开CLI/API、字段、错误和语义不得因目录调整改变。

### 3.1 单一命令定义

将DESIGN第14节的97个命令整理成机器可读manifest，包含：命令路径、位置参数、选项、类型、默认、互斥、权限action、处理器、响应schema、测试编号。CLI parser、HTTP网关、help和参数测试都由此驱动或检查。

不能让CLI和HTTP各维护一套未同步的默认值。不能把全部命令映射成任意字符串反射调用内部函数。服务端只分发白名单manifest中的action。

### 3.2 贯穿实现的不变量

实现前先给以下不变量编号并落测试：

| 编号 | 不变量 |
|---|---|
| INV-01 | 一Agent一个有效实例槽位，unknown不当成0 |
| INV-02 | 同一Task同一时刻最多一个有效领取责任 |
| INV-03 | 旧实例和旧claim不可提交晚到结果 |
| INV-04 | 同请求同输入重放一次，同请求不同输入冲突 |
| INV-05 | Task终态与事件、回调/outbox记录一致 |
| INV-06 | done+dispatch、comment+@、create+wait都是声明范围内原子操作 |
| INV-07 | 无回调不产生来源终态通知 |
| INV-08 | forward不改ID、血缘、原回调，失败保留原责任 |
| INV-09 | 无等候丢唤醒；等待环拒绝；父终态不误终止子任务 |
| INV-10 | 角色名称、正文、metadata和@不能扩大权限 |
| INV-11 | 最小harness缺增强能力仍可接入，不伪造观测 |
| INV-12 | 生命周期停止/重启不抹掉责任和文件 |

## 4. 分阶段施工

每阶段按“实现 → 针对性验证 → 留证 → 记录下一阶段入口”推进。可以同一个施工Agent顺序做；本计划不要求创建多个Agent或把任务自动派给其他聊天。

### P00 · 隔离骨架与启动契约

依赖：无。对应验收：AC-ENV-01、AC-ENV-02、AC-CLI-01。

1. 完成目录、依赖声明、CLI三入口、统一JSON envelope、错误code/exit映射。
2. 实现home解析、system init/serve/start/stop/status，Windows隐藏后台启动、单写者锁、日志脱敏。
3. 初始化测试profile开关；生产环境没有可用test特权入口。
4. 提供health和最小command schema导出；未实现的命令明确返回NOT_IMPLEMENTED开发错误，不伪造成功。正式交付时不得残留此错误。
5. 将系统时间抽象为Clock、适配器抽象为接口，测试不依赖真实睡眠和付费模型。

完成门槛：全新空目录可初始化/启动/停止；已有home不被覆盖；第二个daemon明确拒绝；旧trigger端口与文件没有改变。

### P01 · 数据模型、迁移、命令manifest

依赖：P00。验收：AC-CLI-02、AC-STORE-01、AC-STORE-02。

1. 定义第2/3/5/6/8/9节数据对象与输入schema，集中枚举状态。
2. 建新数据库迁移，外键/唯一索引/条件更新，WAL与busy_timeout。
3. 事务helper必须能够一起保存领域变更、事件、outbox与幂等结果。
4. 完成命令manifest的全部参数与help，先让参数错误、互斥、默认补齐可测。
5. 定义规范化payload哈希：去掉传输超时/format，但包括影响行为的参数；文件输入按读取后的内容规范化。
6. 对existing idempotency key先重放已确认结果，再决定是否需要新的权限/状态检查；token已撤销仍不能无认证查询秘密结果，需先认证有效主体。

完成门槛：迁移可重复执行、从空库建立；并发唯一约束真实存在；参数schema和文档一致；没有生产数据依赖。

### P02 · 身份、权限、Agent配置与工位

依赖：P01。验收：AC-ID-01至AC-ID-05、AC-AUTH-01至AC-AUTH-04、AC-CTX-01至AC-CTX-03。

1. 实现owner初始化、agent/human/producer主体，受保护的凭据文件与验证数据。
2. 实现action+resource授权，自己的队列/创建/承担任务关系权限，单独的可授予范围。
3. Agent创建/复制/查询/更新：允许缺省；配置版本发布协议；投影订阅和权限。
4. 生成只读agent.md/config.json/prompt.md视图；权限正文与凭据不能混入prompt。
5. 工位根目录md稳定加载、排除重复文件、history不递归、超量明确报错。
6. 启动责任摘要由账本实时生成，不能从CONTEXT.md里的自称状态恢复owner。
7. 实现最小测试凭据发行与fixture注册槽位，为后续账本测试提供真实认证上下文，不使用公开--as-agent旁路。

完成门槛：空配置可注册；复制身份独立；权限修改与指令修改分离；缺上下文不阻塞基础运行、超量不静默丢失；旧实例凭据可被fence。

### P03 · 端口、订阅、Task核心与原子领取

依赖：P02。验收：AC-PORT-01至AC-PORT-04、AC-TASK-01至AC-TASK-08。

1. Port创建/更新/发现，handler/schema/defaults，个人目标与共享目标统一寻址。
2. Subscription多对多、暂停/取消与Agent配置版本一致。
3. Task创建明确root/parent、单父树、source鉴别、type规则与访问控制。
4. inbox聚合视图、筛选、分页、排序、详情；列表不claim，seen不done。
5. SQLite条件事务claim，批量逐项结果，领取代次缓存及旧claim拒绝。
6. 三终态与forward，根/子树查询；Task最终结果不可普通覆盖。
7. done+dispatch与emit batch，先全量验证再一次写入，不能先done后逐个emit。

完成门槛：20个并发请求竞争一单只有一个有效owner；重复请求不造单；批量领取部分成功可见；回收/转交后旧claim不能结单。

### P04 · 评论@、拒绝、期限与个人沟通

依赖：P03。验收：AC-MSG-01至AC-MSG-05、AC-TIME-01至AC-TIME-04。

1. 评论追加/修订、@解析与显式mention合并；读权限和评论权限一起检查。
2. 评论、系统生成notice和投递计划同事务；重复请求和重复@去重。
3. 个人轻量任务直接处理、reply_to关联；不会占满work容量阻塞回执。
4. decline按task+agent+spec_version记录；任务要求变化使旧拒绝不再压制。
5. Clock驱动领取超时fail和业务过期trash，和claim竞争使用同一事务边界。
6. 终态保留历史；不提供物理“垃圾桶清空”来删除正在处理的数据。

完成门槛：@有权者只生成一条notice；无权@整笔不写；评论不改变责任；期限优先级明确，无期限不自毁。

### P05 · 回调、outbox、等待与丢唤醒防护

依赖：P03/P04。验收：AC-CB-01至AC-CB-05、AC-WAIT-01至AC-WAIT-06、AC-REC-01/02。

1. 终态回执生成器，唯一semantic_key与recipient；receipt默认无callback。
2. Outbox发送、重试、dead、显式retry，错误不改源Task终态。
3. Wait登记/替换/取消、all/any、环检查、ready/timed_out；状态先到与后到一致。
4. create+wait组合提交；父终态取消本身等待但不取消依赖任务。
5. 父任务转交后等待通知跟随当前owner，原独立callback仍保留。
6. source为producer时self无效，显式目标可为agent或注册端口；无回调严格不主动通知来源。

完成门槛：在终态提交后模拟崩溃仍能补发一次逻辑回执；注册等待前结果已经到达不丢失；失败/废弃也解除相应等待。

### P06 · 运行控制与五项最小harness

依赖：P02/P03；完整联调依赖P05。验收：AC-RUN-01至AC-RUN-07、AC-HAR-01至AC-HAR-03。

1. 定义start/send/stop抽象及model/yolo选项；适配器声明字段可缺省，不增加事件/inspect等硬门槛。
2. 实现fixture harness：可收消息、记录模型/yolo、可停止、可切换“没有增强能力”。
3. 实现生命周期与实例槽位，start并发合并、stop暂停、restart同ID、model切换fallback。
4. 实例身份凭据、generation、claim接续；旧实例和旧领取缓存失效。
5. 配置desired/applied版本追踪、下次send注入变化、重启时加载新工位上下文。
6. stop未知保持占位；对于本地适配用自己创建的进程句柄核实，不扫描杀掉其他CLI。
7. 接一个用户实际可用的真实harness并运行显式五能力smoke；不核验不存在的高级能力、不将模拟适配器当真实已接入。

完成门槛：fixture在关闭inspect/events/model-hot后仍通过；真实适配有五项逐项证据；不支持原地换模型时stop+start可通过。

### P07 · 人工端口、内置trigger与外部服务

依赖：P05/P06。验收：AC-HUM-01至AC-HUM-04、AC-PROD-01至AC-PROD-05。

1. Producer身份、允许目标、受保护凭据发行/轮换与事件ID去重。
2. POST /v1/tasks与CLI复用创建服务，严禁信任body中的source/owner。
3. manual/interval/cron触发器，持久游标、UTC/固定偏移、missed skip/once。
4. 人工页面和human CLI共用answer服务；文本/选项/附件显示，版本冲突与真实回答者审计。
5. 多个人工端口、多展示通道，独立征询用多Task，不复制同一问题来混淆终态。
6. 外部Webhook结果通道、固定delivery_id、重试/去重、通道失效可观测。
7. 明确external handler V1只推送轻量结果/通知；不虚构外部worker回传协议。

完成门槛：外部producer不生成Agent；同event_id只一单；人工deny是done；无callback也可查结果；接口重试不会重答和重复回执。

### P08 · 调度、保守观测与恢复

依赖：P04–P07。验收：AC-SCHED-01至AC-SCHED-05、AC-REC-01至AC-REC-06。

1. task入队与个人待办唤醒规则，跨端口合并、已领取消待发提醒、decline抑制。
2. 活跃idle候选短错峰竞争；冷启动轮转每轮一个；unknown不能冒充idle也不能冒充死亡。
3. 事件去重/乱序、instance generation校验；定期对账不要求harness必须有原生events。
4. daemon重启加载配置、核实原句柄、接续outbox与operations，再恢复调度。
5. P2/宿主不可达时冻结依赖真实状态的控制动作，业务入队与查询不整体瘫痪。
6. 超时/无人承接/恢复超限只产生明确事件和状态，不能隐藏改变业务结果。

完成门槛：故障注入下没有重复实例、重复领取或逻辑回执；无回调不因监控异常偷偷通知来源。

### P09 · 管理控制、备份、CLI全覆盖与人工可用性

依赖：P08。验收：AC-CTRL-01至AC-CTRL-05、AC-STORE-03/04、AC-CLI-01/02/03。

1. 控制操作受理/执行/完成、取消边界、实例/配置预期版本检查和异步查询。
2. 退役计划dry-run、blockers、逐项恢复执行；未来回调交接；不删除工位。
3. 导出一致备份、离线恢复新home、凭据策略和恢复后paused/冻结投递。
4. 完成所有97命令叶子、全局参数、help、错误exit、文件参数与HTTP映射。
5. 人工界面实际能完成问答；界面中展示任务ID/状态/原问题/答案，避免把拒绝按钮映射成fail。
6. 记录不支持的增强能力、真实harness版本与smoke证据，不记录账号秘密。

完成门槛：没有NOT_IMPLEMENTED占位，没有静默忽略参数，没有模型拼接任意shell调用；文档、schema、CLI/API一致。

### P10 · 完整验收、交付与切换准备

依赖：P00–P09。验收：AC-E2E-01/02及全部案例、命令矩阵。

1. 在新建隔离目录按ACCEPTANCE逐步执行，完整保存结果。
2. 分别报告确定性fixture测试与真实harness五能力测试；真实接入未跑必须写未验收，不能全绿。
3. 审查worker=none/P2专属/独立ask/旧wait等过时语义没有残留到新接口。
4. 形成真实README启动教程、配置样例、接口schema、数据迁移/恢复说明与已知限制。
5. 修改施工状态表，写每阶段变更、命令、测试结果、证据路径和未解决项。
6. 停止本轮自己创建且不再需要的验收实例/服务，保留证据与数据；不清理旧trigger运行环境。
7. 是否将现有业务迁入Hive是下一项显式运营决定，本次交付不执行生产迁移。

## 5. 关键实现算法与检查点

### 5.1 领取

在BEGIN IMMEDIATE等可确保写序的事务内：认证/授权→读取任务和最新规格→检查期限/类型/订阅/容量→条件更新queued到claimed→生成claim_id与epoch→写事件/幂等结果→commit。计容量与领取同事务，不能两个请求各看到剩一个名额而都成功。任务已经由同一有效实例持有时返回已有claim，不新增epoch。

### 5.2 终态

认证+当前claim→CAS非终态到目标终态→结果与terminal_event→按明确callback登记receipt/outbox→推进相关wait→保存幂等结果→commit。实际网络发送在commit之后。已确认结果未知时查request，不重跑业务动作。

### 5.3 转交

先验证新目标接收、回调目标保持可追溯、原有效期、当前claim、权限；一个事务解除旧claim并增加epoch、更新目标和新一轮claim期限、写transfer和事件。调用失败原owner保持；不得先释放owner再尝试emit新任务。

### 5.4 等待

登记和检查依赖使用一致事务；保存等待边后再次基于同一快照计算满足条件。等待检查环时同时考虑已有pending等待，不只看任务树。结果通知发送失败不改变ready事实；恢复从账本可重建提醒。

### 5.5 配置发布

先完成新版本文件，后发布有效指针、权限/订阅投影和事件，最后重建工位只读副本。启动只加载已发布revision；禁止把部分写入的JSON传给harness。修改提示词不顺便修改权限。

### 5.6 实例启动与未知结果

先占唯一槽位并登记operation/request correlation，再调用适配器。启动后未收到RPC响应时，利用适配器自己保存的handle/受管进程记录找回；无法核实时保留unknown，不能清槽再开。必要的进程管理能力由适配器包装，不变成对harness原生API的新增要求。

### 5.7 备份与恢复

SQLite使用backup API；冻结/标记相同备份点的配置引用，包含manifest与校验。恢复只写新空目录，所有Agent先paused、旧凭据默认不恢复、outbox冻结待人工核对远端事实。恢复命令不自动唤醒模型或重发外部副作用。

## 6. 测试与证据策略

- 单元层：对象校验、状态转移、授权、期限优先级、环检测、参数互斥。
- 集成层：真实SQLite/HTTP/CLI，不能只mock repository后宣称事务正确。
- 并发层：多客户端领取、启动、终态与期限竞争；检查数据库唯一结果。
- 故障层：commit前失败、commit后响应丢失、outbox暂停、重启恢复、旧实例晚到。
- 适配层：fixture故意缺增强能力；真实harness仅测五项门槛。
- 人工/外部层：本地测试接收器，真实HTTP交互和多页面重复提交；不连接用户线上审批站做破坏性实验。

每个测试记录case_id、开始/结束、实际命令或HTTP、exit/status、JSON结果、断言、相关ID、证据路径。脱敏不仅针对stdout，也包括异常堆栈、请求headers、环境变量和日志附件。

完成相关测试后不要无理由反复全量执行；接口变更、故障修复或跨模块影响出现时再扩展回归范围。

## 7. 交付清单与施工报告模板

最终交付至少有：可运行代码、依赖/启动说明、全部命令schema、数据库迁移、agent.md模板、无秘密示例、最小适配器接口与fixture、至少一个真实适配器的五项验收证据、人工页面、验收脚本与报告。

施工报告采用以下结构（状态不得预填通过）：

```text
阶段：Pxx
状态：未开始 / 进行中 / 已完成 / 有外部阻碍
对应设计条款与CMD：
修改文件：
完成的公开行为：
执行的验收AC：
实际结果与证据路径：
仍未完成/未实测：
与原计划的差异及理由：
下一个可执行步骤：
```

### 7.1 初始阶段状态

| 阶段 | 初始状态 | 允许结束该阶段的依据 |
|---|---|---|
| P00–P10 | 未实施 | 对应功能实现与AC证据，不是仅生成文件或help |

## 8. 禁止用以下方式交差

1. 只有demo或内存dict队列，没有持久账本和原子claim。
2. 一个HTTP200就声称实例已经启动或任务已经完成。
3. fixture支持的增强能力被误当成真实harness必须支持。
4. 所有错误统一变fail，或者把人工拒绝业务请求当任务执行失败。
5. 任务超时直接删记录；无回调仍向发送者推送异常。
6. 用最新角色提示词替代系统权限校验，或将token写入工位Markdown。
7. 工位md无限递归全读、静默截断、会话重开后责任丢失。
8. 复制/改动trigger-platform线上数据库来制造新系统“已有数据”。
9. 命令参数写在文档里但实现忽略；help存在但没有相应行为验收。
10. 未运行真实harness却写“全链路已通过”。

## 9. 命令实现归属与验收索引

按以下表建立施工任务。每个CMD都实现其全部参数；共用处理器可以复用，但不能缺少公开入口或悄悄采用其他语义。

| CMD | 命令 | 主施工阶段 | 验收案例 |
|---|---|---|---|
| CMD-001 | `inbox list` | P03 | AC-TASK-01, AC-TASK-02, AC-TASK-03, AC-TASK-04, AC-MSG-01 |
| CMD-002 | `inbox peek` | P03 | AC-TASK-01, AC-TASK-02, AC-TASK-03, AC-TASK-04, AC-MSG-01 |
| CMD-003 | `inbox claim` | P03 | AC-TASK-01, AC-TASK-02, AC-TASK-03, AC-TASK-04, AC-MSG-01 |
| CMD-004 | `inbox decline` | P04 | AC-TIME-01 |
| CMD-005 | `inbox tree` | P03 | AC-TASK-01, AC-TASK-02, AC-TASK-03, AC-TASK-04, AC-MSG-01 |
| CMD-006 | `inbox wait create` | P05 | AC-WAIT-01, AC-WAIT-03, AC-WAIT-04, AC-WAIT-06 |
| CMD-007 | `inbox wait list` | P05 | AC-WAIT-01, AC-WAIT-03, AC-WAIT-04, AC-WAIT-06 |
| CMD-008 | `inbox wait show` | P05 | AC-WAIT-01, AC-WAIT-03, AC-WAIT-04, AC-WAIT-06 |
| CMD-009 | `inbox wait cancel` | P05 | AC-WAIT-01, AC-WAIT-03, AC-WAIT-04, AC-WAIT-06 |
| CMD-010 | `emit new` | P03/P05 | AC-TASK-01, AC-TASK-05, AC-TASK-08, AC-CB-01, AC-STORE-02 |
| CMD-011 | `emit done` | P03/P05 | AC-TASK-01, AC-TASK-05, AC-TASK-08, AC-CB-01, AC-STORE-02 |
| CMD-012 | `emit fail` | P03/P05 | AC-TASK-01, AC-TASK-05, AC-TASK-08, AC-CB-01, AC-STORE-02 |
| CMD-013 | `emit trash` | P03/P05 | AC-TASK-01, AC-TASK-05, AC-TASK-08, AC-CB-01, AC-STORE-02 |
| CMD-014 | `emit forward` | P03 | AC-TASK-06, AC-TASK-07, AC-CB-03 |
| CMD-015 | `emit comment` | P04 | AC-MSG-02, AC-MSG-03 |
| CMD-016 | `emit batch` | P05 | AC-TASK-08, AC-WAIT-06 |
| CMD-017 | `control system init` | P00/P08 | AC-ENV-01, AC-ENV-02, AC-REC-03 |
| CMD-018 | `control system serve` | P00/P08 | AC-ENV-01, AC-ENV-02, AC-REC-03 |
| CMD-019 | `control system start` | P00/P08 | AC-ENV-01, AC-ENV-02, AC-REC-03 |
| CMD-020 | `control system stop` | P00/P08 | AC-ENV-01, AC-ENV-02, AC-REC-03 |
| CMD-021 | `control system status` | P00/P08 | AC-ENV-01, AC-ENV-02, AC-REC-03 |
| CMD-022 | `control system config` | P09 | AC-CTRL-05 |
| CMD-023 | `control system log` | P09 | AC-CTRL-05 |
| CMD-024 | `control system backup` | P09 | AC-STORE-03, AC-STORE-04 |
| CMD-025 | `control system restore` | P09 | AC-STORE-03, AC-STORE-04 |
| CMD-026 | `control agent list` | P02 | AC-ID-01, AC-ID-02, AC-ID-03, AC-ID-04, AC-ID-05 |
| CMD-027 | `control agent show` | P02 | AC-ID-01, AC-ID-02, AC-ID-03, AC-ID-04, AC-ID-05 |
| CMD-028 | `control agent create` | P02 | AC-ID-01, AC-ID-02, AC-ID-03, AC-ID-04, AC-ID-05 |
| CMD-029 | `control agent clone` | P02 | AC-ID-01, AC-ID-02, AC-ID-03, AC-ID-04, AC-ID-05 |
| CMD-030 | `control agent update` | P02 | AC-ID-01, AC-ID-02, AC-ID-03, AC-ID-04, AC-ID-05 |
| CMD-031 | `control agent start` | P06 | AC-RUN-01, AC-RUN-02, AC-RUN-03, AC-RUN-04, AC-RUN-05, AC-RUN-06 |
| CMD-032 | `control agent wake` | P06 | AC-RUN-01, AC-RUN-02, AC-RUN-03, AC-RUN-04, AC-RUN-05, AC-RUN-06 |
| CMD-033 | `control agent send` | P06 | AC-RUN-01, AC-RUN-02, AC-RUN-03, AC-RUN-04, AC-RUN-05, AC-RUN-06 |
| CMD-034 | `control agent stop` | P06 | AC-RUN-01, AC-RUN-02, AC-RUN-03, AC-RUN-04, AC-RUN-05, AC-RUN-06 |
| CMD-035 | `control agent restart` | P06 | AC-RUN-01, AC-RUN-02, AC-RUN-03, AC-RUN-04, AC-RUN-05, AC-RUN-06 |
| CMD-036 | `control agent model` | P06 | AC-RUN-01, AC-RUN-02, AC-RUN-03, AC-RUN-04, AC-RUN-05, AC-RUN-06 |
| CMD-037 | `control agent retire` | P09 | AC-CTRL-04, AC-CB-05 |
| CMD-038 | `control port list` | P03 | AC-PORT-01, AC-PORT-02, AC-PORT-03, AC-PORT-04 |
| CMD-039 | `control port show` | P03 | AC-PORT-01, AC-PORT-02, AC-PORT-03, AC-PORT-04 |
| CMD-040 | `control port create` | P03 | AC-PORT-01, AC-PORT-02, AC-PORT-03, AC-PORT-04 |
| CMD-041 | `control port update` | P03 | AC-PORT-01, AC-PORT-02, AC-PORT-03, AC-PORT-04 |
| CMD-042 | `control port pause` | P03 | AC-PORT-01, AC-PORT-02, AC-PORT-03, AC-PORT-04 |
| CMD-043 | `control port resume` | P03 | AC-PORT-01, AC-PORT-02, AC-PORT-03, AC-PORT-04 |
| CMD-044 | `control port retire` | P03 | AC-PORT-01, AC-PORT-02, AC-PORT-03, AC-PORT-04 |
| CMD-045 | `control subscription list` | P03 | AC-PORT-01, AC-PORT-02, AC-PORT-03, AC-PORT-04 |
| CMD-046 | `control subscription add` | P03 | AC-PORT-01, AC-PORT-02, AC-PORT-03, AC-PORT-04 |
| CMD-047 | `control subscription pause` | P03 | AC-PORT-01, AC-PORT-02, AC-PORT-03, AC-PORT-04 |
| CMD-048 | `control subscription resume` | P03 | AC-PORT-01, AC-PORT-02, AC-PORT-03, AC-PORT-04 |
| CMD-049 | `control subscription remove` | P03 | AC-PORT-01, AC-PORT-02, AC-PORT-03, AC-PORT-04 |
| CMD-050 | `control task list` | P03/P09 | AC-TASK-07, AC-TIME-01, AC-TIME-04, AC-CTRL-04 |
| CMD-051 | `control task show` | P03/P09 | AC-TASK-07, AC-TIME-01, AC-TIME-04, AC-CTRL-04 |
| CMD-052 | `control task amend` | P03/P09 | AC-TASK-07, AC-TIME-01, AC-TIME-04, AC-CTRL-04 |
| CMD-053 | `control task reclaim` | P03/P09 | AC-TASK-07, AC-TIME-01, AC-TIME-04, AC-CTRL-04 |
| CMD-054 | `control task forward` | P03/P09 | AC-TASK-07, AC-TIME-01, AC-TIME-04, AC-CTRL-04 |
| CMD-055 | `control task trash` | P03/P09 | AC-TASK-07, AC-TIME-01, AC-TIME-04, AC-CTRL-04 |
| CMD-056 | `control permission list` | P02 | AC-AUTH-01, AC-AUTH-02, AC-AUTH-03, AC-AUTH-04 |
| CMD-057 | `control permission grant` | P02 | AC-AUTH-01, AC-AUTH-02, AC-AUTH-03, AC-AUTH-04 |
| CMD-058 | `control permission revoke` | P02 | AC-AUTH-01, AC-AUTH-02, AC-AUTH-03, AC-AUTH-04 |
| CMD-059 | `control operation list` | P09 | AC-CLI-03, AC-CTRL-01, AC-CTRL-02, AC-CTRL-03 |
| CMD-060 | `control operation show` | P09 | AC-CLI-03, AC-CTRL-01, AC-CTRL-02, AC-CTRL-03 |
| CMD-061 | `control operation cancel` | P09 | AC-CLI-03, AC-CTRL-01, AC-CTRL-02, AC-CTRL-03 |
| CMD-062 | `control event list` | P08/P09 | AC-SCHED-05, AC-REC-04, AC-CTRL-05 |
| CMD-063 | `control audit list` | P08/P09 | AC-SCHED-05, AC-REC-04, AC-CTRL-05 |
| CMD-064 | `control delivery list` | P05/P09 | AC-CB-04, AC-REC-02, AC-CTRL-05 |
| CMD-065 | `control delivery retry` | P05/P09 | AC-CB-04, AC-REC-02, AC-CTRL-05 |
| CMD-066 | `control harness list` | P06 | AC-HAR-01, AC-HAR-02, AC-HAR-03 |
| CMD-067 | `control harness show` | P06 | AC-HAR-01, AC-HAR-02, AC-HAR-03 |
| CMD-068 | `control harness register` | P06 | AC-HAR-01, AC-HAR-02, AC-HAR-03 |
| CMD-069 | `control harness validate` | P06 | AC-HAR-01, AC-HAR-02, AC-HAR-03 |
| CMD-070 | `control harness unregister` | P06 | AC-HAR-01, AC-HAR-02, AC-HAR-03 |
| CMD-071 | `control producer list` | P07 | AC-PROD-01, AC-PROD-02 |
| CMD-072 | `control producer show` | P07 | AC-PROD-01, AC-PROD-02 |
| CMD-073 | `control producer create` | P07 | AC-PROD-01, AC-PROD-02 |
| CMD-074 | `control producer update` | P07 | AC-PROD-01, AC-PROD-02 |
| CMD-075 | `control producer credential` | P07 | AC-PROD-01, AC-PROD-02 |
| CMD-076 | `control producer revoke` | P07 | AC-PROD-01, AC-PROD-02 |
| CMD-077 | `control trigger list` | P07 | AC-PROD-03, AC-PROD-04 |
| CMD-078 | `control trigger show` | P07 | AC-PROD-03, AC-PROD-04 |
| CMD-079 | `control trigger create` | P07 | AC-PROD-03, AC-PROD-04 |
| CMD-080 | `control trigger update` | P07 | AC-PROD-03, AC-PROD-04 |
| CMD-081 | `control trigger fire` | P07 | AC-PROD-03, AC-PROD-04 |
| CMD-082 | `control trigger enable` | P07 | AC-PROD-03, AC-PROD-04 |
| CMD-083 | `control trigger disable` | P07 | AC-PROD-03, AC-PROD-04 |
| CMD-084 | `control channel list` | P07 | AC-HUM-03, AC-PROD-05, AC-CB-04 |
| CMD-085 | `control channel show` | P07 | AC-HUM-03, AC-PROD-05, AC-CB-04 |
| CMD-086 | `control channel register` | P07 | AC-HUM-03, AC-PROD-05, AC-CB-04 |
| CMD-087 | `control channel update` | P07 | AC-HUM-03, AC-PROD-05, AC-CB-04 |
| CMD-088 | `control channel disable` | P07 | AC-HUM-03, AC-PROD-05, AC-CB-04 |
| CMD-089 | `control human list` | P07 | AC-HUM-01, AC-HUM-02, AC-HUM-03, AC-HUM-04 |
| CMD-090 | `control human answer` | P07 | AC-HUM-01, AC-HUM-02, AC-HUM-03, AC-HUM-04 |
| CMD-091 | `control human fail` | P07 | AC-HUM-01, AC-HUM-02, AC-HUM-03, AC-HUM-04 |
| CMD-092 | `control human trash` | P07 | AC-HUM-01, AC-HUM-02, AC-HUM-03, AC-HUM-04 |
| CMD-093 | `control test seed` | P00/P06 | AC-ENV-02, AC-TIME-02, AC-REC-01, AC-REC-04, AC-RUN-05 |
| CMD-094 | `control test clock advance` | P00/P06 | AC-ENV-02, AC-TIME-02, AC-REC-01, AC-REC-04, AC-RUN-05 |
| CMD-095 | `control test fault set` | P00/P06 | AC-ENV-02, AC-TIME-02, AC-REC-01, AC-REC-04, AC-RUN-05 |
| CMD-096 | `control test fault clear` | P00/P06 | AC-ENV-02, AC-TIME-02, AC-REC-01, AC-REC-04, AC-RUN-05 |
| CMD-097 | `control test harness` | P00/P06 | AC-ENV-02, AC-TIME-02, AC-REC-01, AC-REC-04, AC-RUN-05 |

全部命令还受AC-CLI-01/02约束。共享CLI/schema工作贯穿P01至P09；不能等业务完成后才发现参数定义不一致。
