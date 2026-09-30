# Hive V1 系统设计与完整 CLI 契约

版本：1.0 · 设计讨论：2026-09-29 · 整理交付：2026-09-30 · 状态：施工基准，尚未实现。

配套文档：[施工计划](IMPLEMENTATION_PLAN.md)、[验收清单](ACCEPTANCE.md)。

## 0. 适用范围、优先级与阅读方法

本文件整理本次设计讨论中已确认的 Hive 底层脚手架，并给出施工所需的字段、默认值、命令、错误与边界。**从本版本开始，本文件及配套施工、验收文档优先于 2026-09-28 的 HANDOVER.md；旧文保留作历史，不可用旧规则覆盖新规则。** 对话已拍板的语义属于强约束；具体字段拼写、数值、文件名、CLI 参数和推荐模块划分是为落地补齐的 V1 工程默认。改变这些默认要同步三份文档、命令 schema 与验收，不能让代码另立一套隐含契约。

本次只交付设计文档，不表示下面的命令已经可运行，不表示任何真实 harness 已通过对接验收。文档中所有名称和ID示例均为示意，不是现存账号、密钥或线上对象。

### 0.1 已确认的核心决定

1. Hive 是「系统 + 稳定 Agent」的通用自动化脚手架。designer、worker、manager、项目组织、资产维护流程都是上层应用。
2. Agent 不是岗位、端口或会话。它有稳定身份；一个 Agent 同时最多一个有效运行实例，数量为 0 或 1。同配置并行执行要创建不同 Agent。
3. 任务端口与 Agent 通过多对多订阅连接。共享端口竞争领取；个人队列可接收指名工作、交流、通知、回执。
4. Agent 只有三个顶层入口：`inbox`、`emit`、`control`。独立 `ask` 删除；问人是向人工端口 emit 工作任务，可回调并等待。
5. 所有需要接收者处理的投递统一为 Task；类型规定处理规则。评论是任务附属记录，事件是系统事实，唤醒是运行信号，三者无需伪造为独立待办。
6. Task 终态为 `done / fail / trash`；`forward` 保留同一 Task ID、转移责任，不是终态。创建新工作才建立子任务。
7. 任务树记录血缘，不自动形成完成依赖；父任务结束不级联结束子任务。依赖由显式等待关系表达。
8. 回调是可选、独立的结果投递目标，不与来源绑定。Agent、内置 trigger、外部服务都可以产生任务。
9. 工位默认根目录业务 Markdown 是持久上下文；再加载系统 `agent.md`、配置生成的 `prompt.md` 和实时责任摘要。
10. Agent 配置为系统管理的 JSON，支持缺省、版本化与热更新。YOLO 是宿主执行模式，Hive 的对象权限仍由系统检查。
11. Harness 接入只要求启动、发消息、最高权限模式、选择/切换模型、终止。原生事件、状态查询、压缩、历史恢复、工具注册均不是接入门槛。
12. trigger-platform 保持维护态。Hive 新建存储、进程、端口和配置，不修改其在线数据与现有 worker。

### 0.2 与旧 HANDOVER 的主要差异

| 旧描述 | V1 采用的规则 |
|---|---|
| Hive 强依赖 ZCode P2 32 操作 | 多 harness，只有五项最小能力 |
| inbox / emit / ask | inbox / emit / control |
| 个人队列不放可执行任务 | 个人队列可放指名 work，仍需 claim |
| 五个混合 Agent 状态 | 生命周期、实例数量/观测、责任/等待分别记录 |
| 任务只有完成/废弃/下游输出 | done / fail / trash，加非终态 forward |
| 所有通知都不可丢 | Task 投递可靠保存；唤醒信号可丢并重发 |
| 超时和人工询问是独立特殊系统 | 统一任务期限、回调和等待 |
| 固定六角色、pipeline 是内核 | 可由上层配置；V1 不硬编码角色或项目流程 |
| 忙碌过久可凭摘要自动判断死循环 | 内核零 LLM，仅输出可验证事实与异常，不推断业务质量 |

## 1. 系统边界与组成

```text
内置 trigger / 外部服务 / 人 / Agent
                  │ 认证后的统一命令或任务投递
                  ▼
Hive daemon
  ├─ 身份与权限、配置版本、命令处理器
  ├─ 端口 / 订阅 / Task / 评论 / 血缘 / 等待 / 控制操作
  ├─ SQLite 账本、事件、outbox、幂等记录
  ├─ 规则调度器：通知、期限、恢复、对账
  ├─ 人工端口与外部通道适配器
  └─ HarnessAdapter：start / send / stop + model / yolo
                  │
                  ▼
具体运行实例 ── 读取工位文件 ── 调用 inbox / emit / control
```

推荐 V1 使用 Python 3.11+、SQLite、单个 daemon 写入服务、本机 HTTP API。可沿用 trigger-platform 的标准库 HTTP/CLI 风格，但新建领域模型和数据库。不得让 CLI、人工页、trigger 各自直接修改数据库。多客户端并发通过服务端事务处理；V1 不做多主数据库、分布式选主和跨机器文件同步。

CLI 统一入口 `python cli/hive.py`，安装包装器后可用 `hive`。默认服务地址 `http://127.0.0.1:19962`，与旧 trigger 的 19961 分开。示例使用 `hive` 作为该入口的缩写。

## 2. 命名、身份、时间和引用

### 2.1 标识

| 对象 | ID 前缀 | 不变量 |
|---|---|---|
| Agent | `agt_` | 永久稳定，改名/换模型/重启不改变 |
| 实例 | `ins_` | 每次启动新建；与宿主 session/进程 handle 映射 |
| 端口 | `prt_` | 稳定；可另有唯一路由名，如 `human/owner` |
| Task | `tsk_` | forward 不变；派生新工作新建 |
| 评论 | `cmt_` | 修改采用新记录修订，不抹去历史 |
| 等待 | `wtt_` | 归属一件责任任务 |
| 控制操作 | `op_` | 可异步查询、回执、等待 |
| 事件 | `evt_` | 消费去重键 |
| 生产者 / trigger / 通道 | `prd_ / trg_ / chn_` | 不占 Agent 实例 |
| 主体与授权 | `subj_ / gnt_` | 主体类型可为 owner、agent、human、producer、system |
| 领取凭据 | `clm_` | 每次领取新建，回收/转交/终态失效 |

ID 由系统生成。名称可改且不用于鉴权；Agent 显示名允许重复。端口路由名在一个 Hive 内唯一。输入目标使用 `agent:<agent_id>` 或 `port:<port_id-or-route>`；回调还允许 `self`、`none`。`self` 只能在可解析当前 Agent 身份时展开。用户文本中的自称、角色名、ID 均不是凭证。

### 2.2 时间与路径

- 对外时间使用带时区 RFC3339，服务端规范化为 UTC；无时区时间拒绝。截止条件为服务端 `now >= deadline`。
- 时长接受 `30s`、`5m`、`2h`、`1d`；任务期限必须正数，显式 `none` 清空可选期限。单纯 CLI 请求超时不取消业务任务。
- CLI 的 `--*-file` 由客户端读取 UTF-8（可带 BOM），不是让服务端读取调用者提交的任意路径。
- 附件/产物使用引用对象 `{uri, title?, media_type?, version?}`。`file:` 引用必须落在该主体获准访问的路径内；`https:` 是引用，服务端不默认下载执行。
- V1 本机共享路径可用；跨宿主接手必须先发布到双方可访问位置。系统不会把一个本机绝对路径伪装成可跨机器访问的附件。

## 3. Agent 定义、工位与热更新

### 3.1 Agent 配置 schema

`schema_version=1`；缺省允许注册。创建时允许只给 `{}`，但缺少默认 harness 时，启动明确返回 `HARNESS_NOT_CONFIGURED`，不影响 Agent 注册与收件。

| 字段 | 类型 / 默认 | 规则 |
|---|---|---|
| `schema_version` | integer / 1 | 不支持的主版本拒绝 |
| `agent_id` | string / 系统生成 | 创建输入不可指定；导入本体须与目标一致 |
| `revision` | integer / 系统维护 | 每次有效配置变更递增 |
| `name` | string / 自动名称 | 1–128 字符 |
| `description` | string / 空 | 公开简介，最长 4096 字符 |
| `instructions.text` | string / 空 | 个体说明，与系统协议分开 |
| `instructions.files` | array / [] | 可访问的补充说明文件 |
| `skills` | array / [] | 技能入口元数据；由提示词说明，非原生工具注册门槛 |
| `runtime.harness` | string or null / 系统默认 | 适配器注册名 |
| `runtime.model` | string or null / 宿主默认 | 空不阻止启动 |
| `runtime.yolo` | boolean / true | 此接入档默认最高宿主权限 |
| `runtime.auto_start` | boolean / true | 有待办时允许自动拉起；暂停状态仍禁止 |
| `runtime.restart_on_exit` | boolean / true | 仅在确认实例已退出且 Agent 可运行时生效 |
| `runtime.options` | object / {} | 适配器自己的配置，须由其校验 |
| `workspace.path` | string or null / 系统分配 | 相对路径按 Hive 根目录解析，最终保存绝对路径 |
| `workspace.context_glob` | string / `*.md` | V1 仅根目录，不递归 |
| `workspace.exclude` | array / [`agent.md`,`prompt.md`] | 协议与派生说明已单独加载，不重复 |
| `workspace.context_entries` | array / [] | 额外上下文入口；缺失情况明确列入启动报告 |
| `subscriptions` | array / [] | `{port_id, enabled:true}`；是配置的一部分 |
| `permissions` | array / 基础自身权限 | 显式 allow 的 action/resource 规则；修改单独鉴权 |
| `limits.max_inflight_work` | integer / 3 | 1–1000；等待中的 work 也计数，非 work 不计数 |
| `metadata` | object / {} | 应用标签与扩展，不自动产生授权 |

未列字段拒绝，扩展放 `metadata` 或适配器 `runtime.options`；“允许缺省”不等于静默忽略拼错字段。订阅修改也产生配置新版本，运行时 subscription 表是已发布版本的投影，不能独立成为另一套配置真相。

### 3.2 配置本体与发布

配置本体是 JSON。系统保存不可变版本文件，账本只发布有效版本指针；工位 `config.json` 是该有效 JSON 的只读副本/引用。

```text
验证身份、字段、版本和授权
→ 写入完整的新版本 JSON（临时文件 + 原子替换）
→ 事务发布 revision、权限/订阅投影与配置事件
→ 同步当前 config.json 与生成 prompt.md
```

发布前的孤立版本不生效；发布后副本生成失败可重建。所有操作以已发布版本为准。不要同时允许任意文件编辑与数据库字段各自决定有效权限。Owner 手改草稿后，通过 `control agent update --file` 激活。

热更新：名称/订阅/授权在发布后用于下一次系统请求；指令/上下文在下一次向实例发送输入时注入变化；模型/执行环境允许 stop+start 应用。记录 `desired_revision`、`applied_revision`、`pending_fields`。最小 harness 没有回合边界事件时，可在下一次 send 应用，或显式重启；不得强制要求宿主原地热更新。

### 3.3 工位

```text
workspaces/<agent_id>/
  agent.md       系统统一协议，只读；含三入口、恢复和记录规则
  config.json    有效配置只读视图，不含凭证正文
  prompt.md      配置生成的个体工作说明，只读
  CONTEXT.md     当前概况与接续信息（示例名）
  RULES.md       工作规则（示例名）
  EXPERIENCE.md  经验（示例名）
  history/       历史运行记录，按需读
```

只有前三文件名固定，其余业务 Markdown 命名自由。加载顺序为系统协议、个体说明、根目录业务 Markdown（按文件名稳定排序）、额外入口、实时责任摘要。业务资料是带来源的上下文，不因文字宣称而获得权限。

新回合由适配器检查文件变更并注入新内容；最小 harness 以首次/后续 send 完成。单次上下文工程默认上限 512 KiB UTF-8，可配置；超出要返回清晰的超量文件清单/状态，不静默截断或伪称完整加载。可让 Agent 整理后再启动。

复制 Agent 默认复制配置、另建身份/个人队列/工位，不复制凭据、在途责任或运行会话。工位历史和资产复制是显式应用动作。退役不删除工位。

## 4. 生命周期、实例与最小 HarnessAdapter

### 4.1 Agent 生命周期

| 状态 | 新工作/收件 | 自动拉起 | 说明 |
|---|---|---|---|
| `enabled` | 允许 | 受 auto_start 策略控制 | 实例可为 0 或 1 |
| `paused` | 可保留个人投递，不能新 claim work | 禁止 | stop 将其置为 paused |
| `retiring` | 拒绝新普通投递、停止新领取 | 禁止 | 正在处理责任与回调交接 |
| `retired` | 拒绝普通投递 | 禁止 | 身份与历史保留，V1 不复活 |

### 4.2 实例

实例记录 `instance_id, agent_id, harness, handle, config_revision, generation, phase, observation, started_at, ended_at, error`。有效实例表对 agent_id 建唯一约束；启动占位也计入唯一执行槽位，保证并发 start 合并。

`phase` 为 `starting / live / stopping / stopped / failed`；`observation` 可为 `idle / busy / unknown`。无原生事件时 `unknown` 是合法状态，不等于死亡或空闲。状态来源与更新时间随结果输出。

- start：enabled；已有实例/启动操作则返回该实例/操作，不重复创建。
- wake：enabled 才执行；0 实例时按策略 start，1 实例时 send。paused 返回未唤醒原因。
- stop：先置 paused，撤销新领取资格，尝试保存提示，再终止；成功后槽位释放，任务责任保留。
- restart：停止旧实例后以同一 Agent 新建实例，不自动 fail/trash 任务。
- retire：停止接单；须给未结 work、个人待办、已登记回调指定交接/结算方案，未处理完维持 retiring。
- 切模型：优先可选原地接口；没有则 stop+start；此前 paused 的 Agent 只更新配置，不擅自启动。
- 仅当旧实例确认结束，或运行环境已可靠阻止其继续作用于受管资源，才能启用替代实例。不能因为 get 不可用就当成 0。此责任由 Hive/进程包装器承担，不把原生 get 接口设为 harness 门槛。

### 4.3 最小接入：五项能力，三个必需操作

| 最小能力 | 可接受实现 |
|---|---|
| 启动会话 | API / CLI / 启动脚本，适配器持有可操作 handle |
| 向会话发消息 | API / stdin / 可用通道；允许由 Hive 排队 |
| 最高权限 / YOLO | 启动参数或配置项即可 |
| 选模型 / 切模型 | 启动选模型 + stop/start 即满足 |
| 终止会话 | API 或停止对应受管进程 |

```text
start(options) -> handle
send(handle, message, delivery_id) -> accepted | busy | failed | unknown
stop(handle, operation_id) -> stopped | stopping | failed | unknown
```

`options` 包含可选 model、yolo=true、工作目录、启动说明、Hive CLI 使用入口与凭证文件位置（不含凭证正文）。配置缺失用系统/宿主默认。

能力声明来自注册配置即可，不要求原生握手。事件、inspect、steer、原生 compact、历史恢复、工具注册、原地换模型都可缺失。没有事件：以 send/进程事实和账本活动作保守观测；没有 busy 信息：标 unknown，合并低频提醒，不伪造 idle。没有原地更新：发送新说明或重启。不能确认 stop 时维持占位并报 `RUNTIME_OUTCOME_UNKNOWN`。

上下文和工具使用先由 prompt/CLI 说明实现，MCP/native tool 注册是增强。Hive API 权限仍校验；本接入档 **不承诺 YOLO 实例具有严格文件/网络沙箱隔离**。若应用另要求隔离，由部署环境额外提供，不能写成每个 harness 的必需能力。

## 5. 端口、订阅与发现

### 5.1 Port

字段：`port_id, route, name, description, handler, state, accepted_types, input_schema, defaults, visibility, revision, created_at`。

- `handler=agents|human|external`。agents 由订阅者竞争；human/external 由注册通道适配器接收，仍使用同一 Task 账本。
- `state=enabled|paused|retiring|retired`。paused 继续入队、停止新 claim；已有领取者可结算。retiring 拒绝新投递并清理原有责任；retired 只留历史。
- `accepted_types` 默认 work、notice、message、receipt；receipt 的创建权仍为系统专用。input_schema V1 支持对象字段 required、type、enum，未知 schema 关键字报错，不伪称完整 JSON Schema 实现。
- `defaults` 包含 urgency=normal、claim_within=null、expires_in=null。普通任务回调默认 none，不能由端口暗中添加 sender 回调。
- 人工端口只需要工作任务 payload 中描述 question/approval/options/attachments；没有独立 ask 表或状态机。

### 5.2 订阅与权限

Subscription 唯一键 `(agent_id, port_id)`，包含 enabled、created_at、created_by。关系属于稳定 Agent，不属于实例。不支持复杂订阅过滤 DSL，业务分流优先划分端口。

“有订阅”与“有领取权限”同时成立才可从共享 agent 端口 claim。取消订阅不改变已领任务、回调或讨论访问的既有合法关系。个人目标只允许目标主体处理；读取另行授权。

### 5.3 通知与竞争

共享 work 进入队列后，通知 enabled、有容量且未对当前任务版本 decline 的候选者。有已知 idle 的活实例时，群发短抖动提醒；发出前重新检查待领任务。只有 0 实例可用时，轮转启动一个，未接单再扩展。最小 harness 的 unknown 实例视为“活着但忙闲未知”，最多合并一个待发送提醒，不同时启动替身。

同 Agent 跨端口提醒合并。唤醒不预留任务，先被事务确认 claim 的 Agent 获得责任。任务被领后取消未发出的无用提醒；已收到的旧提示允许查看后发现没有任务。

所有候选者拒绝或长时间未领：产生 `task.unclaimed` 事件，不自动指派、不擅自 fail；有领取期限则到期按第 7 节结束。无回调不自动通知发送者，可在受权的系统观测中发现积压。

## 6. Task、评论与输出数据模型

### 6.1 Task 字段

| 字段 | 定义 |
|---|---|
| `task_id / parent_id / root_id` | 单父血缘树；根 parent=null、root=self；创建后不可换父 |
| `type` | work / notice / message / receipt |
| `title / summary / body` | 标题 1–256 字符，简介≤2048，正文可空；work 至少有 body 或 data |
| `data / attachments` | JSON 对象与引用列表 |
| `source` | 真实发起主体、producer/trigger/event_id；由认证入口注入 |
| `target` | 当前 `agent:<id>` 或 `port:<id>`；一个当前目标 |
| `state` | queued / claimed / done / fail / trash |
| `owner_subject / claim_id / claim_epoch` | 当前领取者及领取代次；只有 claimed work 必须存在 |
| `urgency` | urgent / normal / low；不自动获得打断权限 |
| `callback` | null，或 `{target: agent/port, pending_handover?...}`；self 在创建时展开 |
| `reply_to / references` | 回复的 Task、评论、操作等引用；不是多父关系 |
| `claim_deadline` | 当前一轮排队的领取截止；claim 后不再起作用 |
| `expires_at` | 业务有效期；过期也可能作用于已领任务，见下文 |
| `version / spec_version` | 普通对象修订与任务要求版本；评论不增加 spec_version |
| `result` | 终态 `{text?,data?,artifacts:[],reason?,reason_code?,ended_by,ended_at}` |
| `created_at / updated_at / ended_at` | 系统时间 |

结果只能结算一次；同一 request-id 的重放返回原结果，不同操作试图改变终态返回冲突。重试业务工作须创建新关联任务，可在 references 记录 retry_of。

### 6.2 类型与处理

| type | 领取与结束 | 工作容量 |
|---|---|---|
| work | 共享/个人均需 claim；done/fail/trash/forward 要有效领取凭据或管理权限 | 计入 |
| notice/message | 个人目标可直接 done/fail/trash；共享 agents 端口须先 claim 以明确处理者 | 不计入 |
| receipt | 系统生成；个人可直接处理；端口由其授权处理者消费 | 不计入 |

非 work 的普通创建也有血缘、权限、终态与过期规则，但不强制产出文件。回执默认不再回调。通知/回执结束不会自动结束原任务。

### 6.3 状态转移

```text
queued --claim--> claimed --done/fail/trash--> terminal
queued --个人非work处理/人工授权回答--> terminal
claimed --forward/reclaim--> queued（新 claim_epoch，无 owner）
queued --领取期限到--> fail
queued/claimed --业务失效--> trash（撤销领取资格）
```

业务失效若作用于 claimed work，系统撤销提交资格并通知当前执行者停止该项工作；这不承诺撤回已经发生的外部动作，也不停止其所有其他任务。任务期限由有权限者修改时记录 spec_version；运行中的执行者收到变化通知。

### 6.4 评论与 @

Comment 字段：`comment_id, task_id, author_subject, body, mentions[], created_at, revision_of?, checkpoint_refs[]`。一般追加不覆盖，修订用 revision_of 关联新记录。

规范 @ 语法为 `@agt_<完整ID>`，也可通过 CLI `--mention` 明确指定，两者去重合并。目标必须存在且能读原任务；不允许无权限 @ 自动授予任务访问权。整个评论提交在输入不合法时失败，避免一半 @ 已发、一半被忽略。

comment + @ 生成的 notice + outbox 在一个事务中保存。唯一键 `(comment_id,recipient_id)`。通知带 task/comment 引用，正文摘要只含接收者已有权读取的内容。评论不会 claim、结单、改变任务要求或解除等待。

## 7. emit、任务血缘、期限与回调

### 7.1 创建、协作与转交

- 新工作：必须显式 `--root` 或 `--parent T`，两者互斥。父任务须有读/派生权限；创建者承担的父任务不因 emit 自动结束。
- forward：保留 Task ID、父/根、来源、评论、附件、业务有效期和回调目标，只改目标、排队代次和领取关系。目标验证、清除 owner、入队、审计原子完成，失败保持原责任。
- 交付下一阶段：`emit done --dispatch-file` 提交一份 child TaskSpec 数组，当前 done 与全部新子任务同事务，全成功或全不变。
- parent 的 done/fail/trash 不递归结束子任务，tree 查询必须同时显示各终态计数与未结 work 数，不能把 parent done 显示成全树完成。
- 转交重算 claim_deadline：显式参数优先，否则目标端口默认，否则无期限；expires_at 保留。期限已经使任务失效时拒绝 forward。

### 7.2 领取期限与有效期

`claim_deadline` 到期且仍 queued → fail / `CLAIM_TIMEOUT`。`expires_at` 到期 → trash / `EXPIRED`。同一时刻都到期时业务失效优先 trash。缺省无自动结束；不得设置隐藏的统一销毁时间。

`inbox decline` 记录 `(task_id, agent_id, spec_version,reason)`，不结束 Task。要求变化后旧拒绝不再压制通知。个人指名 work 被拒绝仍留原队列并标 unclaimed，直到期限、发送者干预或管理操作；不私自转给别人。

所有到期结算使用同一事务与幂等终态操作。领取与到期竞态只允许一个成功。`trash` 是业务终态，不删除记录。

### 7.3 回调

回调目标可为 agent 个人地址，也可为注册端口（含外部结果通道）；端口必须允许 receipt。默认 none。Agent 指定 self 展开为其 ID；producer/trigger/human 的 self 不可自动猜测，返回 `CALLBACK_TARGET_REQUIRED`。

终态回执为新 Task：type=receipt、parent_id=源 Task、reply_to=源 Task、callback=null；携带 terminal_event_id、state、result、source_task_id。唯一键 `(terminal_event_id,callback_target)`。

终态 + 事件 + 回执任务/待投递 outbox 事务一致。投递失败不改写源任务结果。注册目标后来退役时，按显式回调交接配置重定向并审计；没有方案则保留待投递失败记录和结果，不伪称成功。

不回调：不因 fail/trash/超时自动通知原发送者。发送时未被系统接收的同步错误仍必须返回。发送者可以主动查询。外部服务从认证注册的 producer 获得 source，无需会话、订阅或个人队列。

## 8. 等待与人工/外部通道

### 8.1 Wait

Wait 字段：`wait_id, owner_task_id, dependencies[], mode, state, deadline, checkpoint_refs, created_by`。依赖引用 `task:<id>` 或 `operation:<id>`；原独立 ask 已成为普通 task。

`mode=all|any`；依赖的 done/fail/trash 或控制操作 succeeded/failed/cancelled 都是“结果已返回”，不是只等成功。状态 `pending|ready|timed_out|cancelled`。每件责任任务最多一个 pending Wait。

`inbox wait create` 是立即返回的登记操作。查询依赖终态与保存订阅必须一致，结果先到则直接 ready。登记会保存结果通知订阅，source Task 未配置 callback 也可被显式等待；已有同收件者回执时复用/合并唤醒，不能改写原 callback 目标。

等待对象不得是自己、祖先依赖的自身，也不得产生 task 等待环。登记时检查 waiter→dependency 图（独立于血缘树），产生环返回 `WAIT_CYCLE`。

父任务仍 claimed、责任和工作容量保留。ready/timed_out 只解除等待并通知当前责任人，不自动 done/fail。any 不取消其他依赖；解除等待不取消目标工作。

转交父任务时，Wait 仍挂 owner_task_id；无人领取期间结果入账，新的领取者读取依赖状态；已有人接手则通知新责任人。父任务先进入终态时取消其 pending Wait，不取消依赖任务，不撤销原有独立 callback。

### 8.2 人工端口

人工端口可有多个，如 human/owner、human/design；由通道适配器展示 work Task。一个逻辑问题可展示到多个通道，仍只有一个 Task ID；多人分别给意见则显式创建多个子任务并 all/any 等待。

人通过受权界面或 `control human answer` 提交结果。回答的主体、时间、选项与文字由可信通道记录；`done` 表示已回答，`data.decision=deny` 仍是 done，不等于 fail。无法答复用 fail，撤回/失效用 trash。接口校验 spec_version，过时界面不能覆盖新问题或已回答结果。

多通道重复回答只有第一次有效结算成功；重放同操作返回原结果。评论不算正式回答。允许代答时权限必须明确，审计保留真正的 Agent 回答者，不伪装成人。

### 8.3 外部生产者与外部接收通道

内置 trigger 和外部服务共同使用 Producer 主体与事件去重，输入通过同一个 create Task 处理器。外部服务使用 API/CLI 投递，不占 Agent 运行实例。

外部回调只发送到已注册通道，首次响应可表示已接收；默认 HTTP 2xx 后把该 receipt 投递任务记 done，其业务源任务早已结束。超时/非2xx重试同 delivery_id。外部服务重复消费须按 delivery_id 去重；平台只能保证至少一次传输，不承诺远端副作用 exactly-once。

## 9. 身份、权限与控制操作

### 9.1 身份边界

系统从认证上下文注入主体。生产者 token、实例 token、人工通道 token 分开。凭证只保存在受保护的 auth 文件/运行环境，**不进入 prompt、config.json、日志、报告或命令行参数正文**。CLI 用 `--credential-file`，不提供 `--as-agent` 冒充参数。

基础 agent 授权：读自己配置/队列/责任、读自己创建或持有的任务、处理个人轻量 Task、结算合法领取的 Task；公共端口投递/领取、直接联系其他 Agent、跨节点 control 需要显式 allow。读、发现、写、授权分别检查。

Action 词表：`agent.discover/read/create/update/start/stop/restart/retire/send`、`port.discover/read/create/update/retire`、`subscription.manage`、`task.read/create/claim/complete/fail/trash/forward/comment/manage/wait`、`human.answer`、`permission.grant/revoke`、`producer.manage`、`trigger.manage`、`channel.manage`、`system.observe/admin`。权限记录 action 列表 + resource 精确 ID/系统关系 own/created/assigned；metadata 标签不自动授权。

拥有某能力不自动拥有转授权权。创建/复制/修改 agent 中的 permissions 必须检查 grant 范围。所有请求还校验 lifecycle、instance generation、claim_epoch 等状态，不只查静态权限。

### 9.2 ControlOperation

字段：`operation_id, request_id, actor, action, target, expected_version, expected_instance, args, reason, state, result, error, callback, created_at, started_at, ended_at`。

状态 `accepted|running|succeeded|failed|cancelled`；观测结果不明时保持 running，设置 outcome_unknown，而不是伪造失败并盲重试。已受理与已完成分开。依赖冲突串行化或明确拒绝；执行前重新校验权限与版本。

cancel 只取消尚未发生的步骤，不保证回滚外部动作。已启动实例必须通过 stop 操作停止，不能仅把启动操作的状态改成 cancelled。

行政回收 claimed Task 时先失效 claim，再协调受管执行，保留动作与新去向。原实例不得提交旧结果，但可能已产生的文件/外部副作用仍需要明确处理。

## 10. 事件、outbox、观测与恢复

事件内容至少有 event_id、entity_type/id、entity_version、occurred_at、recorded_at、actor、cause_request_id、payload。只保存必要事实与引用，查询遵守对象访问权。

SQL 事务同时提交状态、事件、idempotency 和待发送 outbox。Outbox 状态 pending/sending/sent/retry/dead，包含唯一 semantic_key、attempts、next_attempt_at、last_error。dead 记录可受权 retry，不删除源结果。

允许事件重复/乱序；按 event_id 去重、对象版本和实例 generation 防止旧事实覆盖新事实。无宿主版本时以事件提示重新核实；最小 harness 没有查询时保留 unknown，不因此错误 kill 或替换。

启动恢复：读取有效配置与凭证索引→检查 daemon 单写者锁→恢复账本→核对受管句柄/进程→恢复 outbox/操作/等待/期限→恢复通知。不能重启 Hive 就重启所有 Agent。宿主不可达只冻结依赖其真实状态的动作，已能确定的入队、查询、账本结算可继续。

### 10.1 工程默认策略（可配，不是角色判断）

| 参数 | 默认 | 行为 |
|---|---|---|
| reconcile_interval | 60s | 对账周期 |
| notify_jitter | 0–3s | 活跃候选短错峰 |
| notify_retry_after | 5m | 未领再核实，不无条件刷屏 |
| notify_max_rounds | 12 | 对账重唤醒轮次上限（1..1000）；达上限记一次 task.notify_exhausted 异常事件并可查询，任务规格/候选变化或调大上限后自动重置 |
| unclaimed_alert_after | 15m | 只记系统异常，无隐藏业务终态 |
| cold_start_batch | 1 | 无活实例时每轮拉起一个 |
| send_unknown_retry_after | 5m | busy/unknown 时合并低频提醒 |
| restart_backoff | 5s,15s,60s | 已确认退出的实例重启退避 |
| restart_max_per_hour | 3 | 超限停止自动重启，产生异常 |
| outbox_retry | 1s,5s,30s,2m,10m | 此后每 10m，最多 20 次后 dead |
| rpc_body_limit | 1 MiB | 更大产物通过引用 |
| context_bytes_limit | 512 KiB | 超出明确报错，不静默丢内容 |

无 native inspect/turn 事件也能通过最小接入验收。细粒度 stall/debt 只能在证据充分时产生 observation，不得仅凭没有宿主事件就认为 Agent 不工作。业务质量判断交给上层。

## 11. 存储、目录与事务不变量

推荐数据根 `.hive/`（可由 HIVE_HOME 或 --home 指定）：

```text
.hive/
  system.json
  data/hive.db
  definitions/agents/<id>/versions/<revision>.json
  definitions/ports/<id>/versions/<revision>.json
  workspaces/<agent_id>/
  auth/                  受保护，不备份到普通报告
  runtime/               实例绑定、适配器受管进程记录
  logs/                  脱敏结构化日志
  backups/               显式备份目标
```

建议表：agents、agent_config_versions、ports、port_config_versions、subscriptions、subjects、grants、credentials（只存验证数据）、instances、tasks、task_claims、task_transfers、task_views、task_declines、comments、waits、wait_dependencies、operations、events、outbox、idempotency、producers、producer_events、triggers、channels、channel_deliveries。具体拆表允许调整，但必须保持公开 schema 与下列不变量。

- agent_id 对有效实例槽位唯一；(agent_id,port_id) 订阅唯一。
- task_id 终态唯一；领取以条件更新/CAS 事务竞争，不能先 SELECT 再无条件 UPDATE。
- parent_id 不允许成环；task 等待图独立检查环。
- 幂等键按主体 + command + request_id 唯一；重复键且规范化参数不同返回 `IDEMPOTENCY_CONFLICT`。
- 回执、@通知、trigger事件与通道 delivery 各有语义唯一键。
- SQLite WAL + busy_timeout；关键写事务明确 commit 后再确认。禁止运行中只复制 `.db` 当完整备份，使用 SQLite backup API/一致快照。
- JSON 版本文件先持久化再发布 DB 指针；工作目录派生视图可重建。
- 备份包含一致账本、有效配置版本、可选工位、引用清单与恢复说明；凭证默认排除，恢复后按明确步骤重建/轮换。
- 物理清理独立于 done/fail/trash 和退役。V1 不提供自动删除未结记录或工位的功能。

## 12. CLI 总体约定

下一节给出 **V1 全部公开命令叶子与每个参数**。未列命令不得在实现中悄悄成为必需入口。`--help` 必须展示所在层命令、全部参数、默认值与互斥关系；`hive --version` 返回协议/程序版本。

全局参数可放在命令前后，但同一个参数重复赋值报错。命令参数不接受未知字段；JSON 文件使用下一节的对象 schema。所有变更生成 request_id；Agent/自动化重试应显式复用该 ID。

| 全局参数 | 类型 / 默认 | 说明 |
|---|---|---|
| `--home PATH` | path / HIVE_HOME，未设则项目 `.hive` | 数据根；不是用户 home |
| `--endpoint URL` | URL / HIVE_URL 或 localhost:19962 | 在线服务地址，CLI 以 HTTP 访问 |
| `--credential-file PATH` | path / HIVE_CREDENTIAL_FILE | 本地 owner 可使用 init 生成的 auth 文件；实例由系统注入位置 |
| `--format FORMAT` | json / text，默认json | json stdout 必须是一个完整对象 |
| `--request-id ID` | string / 自动 UUID | 变更的幂等键；查询仅作关联标识 |
| `--timeout SECONDS` | number / 15 | 网络请求超时；不改变业务期限/状态 |
| `--help` | flag | 不发请求，退出 0 |

响应 envelope：

```json
{"ok":true,"request_id":"req-example","data":{},"warnings":[]}
```

```json
{"ok":false,"request_id":"req-example","error":{"code":"TASK_ALREADY_CLAIMED","message":"任务已被领取","details":{},"retryable":false}}
```

Exit code：0 成功（可能只是受理）；1 非预期内部错误；2 参数/schema；3 鉴权/授权；4 不存在/不可见；5 状态/版本/幂等冲突；6 部分成功或异步操作最终失败；7 请求结果未知/通信超时；8 明确不可用/暂时忙。所有错误有机器 code，stderr 日志不混入 stdout JSON。异步受理返回 operation_id，不把 CLI exit 0 解释为宿主动作已完成。

通用文件参数互斥：`--text` 与 `--text-file`；`--data` 与 `--data-file`；文本与 JSON data 可以同时存在。`--attach` 可重复，保存引用。列表默认 limit=50，范围1–200，cursor 为服务端不透明游标；排序有 ID 次级键以稳定翻页。树默认 depth=4、最大20，超出返回截断标识与继续查询入口。

CLAIM 凭据：领取结果包含 claim_id，CLI 可写入当前实例的本地缓存；`emit` 可用 `--claim` 显式指定，也可读取该缓存。它不是身份 token，不能替代实例凭据。Owner 管理操作必须显式走 control，不能静默替 Agent 冒充业务提交。

## 13. 命令对象文件 schema

### 13.1 TaskSpec（emit new --spec-file / dispatch / trigger 模板）

```json
{
  "type": "work",
  "target": "port:example/work",
  "title": "示例任务",
  "summary": "一句话说明输入与产出",
  "body": "完成所述工作，提交结果",
  "data": {},
  "attachments": [],
  "urgency": "normal",
  "callback": "none",
  "claim_within": null,
  "expires_in": null,
  "reply_to": null
}
```

可替换的时间字段为 claim_by / claim_within、expires_at / expires_in，各对互斥。字段对应 CLI 参数，未知字段拒绝。TaskSpec 不允许注入 source、owner、state、task_id。`emit new --spec-file` 仍必须用 --root/--parent 明确血缘；spec-file 与其他任务内容字段互斥。dispatch 文件是 TaskSpec 数组（1–100项），系统强制 parent=当前完成任务；模板中的 callback=self 以真实调用者展开，producer 无 self。

### 13.2 原子 emit batch

```json
{
  "operations": [
    {"op":"new","ref":"child","parent":"tsk_PARENT","task":{"target":"port:example/work","type":"work","title":"查询","body":"查询资料","callback":"self"}},
    {"op":"wait","task":"tsk_PARENT","on":["$child"],"mode":"all","wait_for":"30m"}
  ]
}
```

V1 batch 仅允许 new、done、fail、trash、forward、comment、wait，所有写入同事务，引用按顺序解析；ref 在批内唯一，只能向前引用，不允许任意脚本、control、跨宿主动作或嵌套 batch。最大100操作。结果包含 refs 映射与每步对象结果。done 的 dispatch 与 batch 二选一即可表达相同组合，公开接口均须保持一致。

### 13.3 GrantSpec

```json
{"subject":"agent:agt_EXAMPLE","actions":["task.read","task.claim"],"resources":["port:prt_EXAMPLE"],"delegable":false}
```

主体、对象可用 ID；关系作用域为 self、created_tasks、assigned_tasks，仅在相应 action 适用时有效。系统 owner 的全部授权是系统初始化配置，不通过角色字符串推导。

### 13.4 HarnessSpec

```json
{
  "name":"example-harness",
  "adapter":"process",
  "start_argv":["example-cli","--model","{model}","--yolo"],
  "send_mode":"adapter-defined",
  "stop_mode":"process",
  "default_model":null,
  "capabilities":{"start":true,"send":true,"yolo":true,"model_at_start":true,"stop":true},
  "optional_capabilities":{}
}
```

配置是 argv 数组，不是拼接 shell 字符串；空 model 的省略方式由适配器 schema 定义。send_mode 必须由实际适配器实现，不能仅写 stdin 就假定任意交互 CLI 可收消息。元数据 validate 不等于真实五能力验收，二者分别记录。

### 13.5 ChannelSpec

`name, kind(human-web|webhook), endpoint?, credential_ref?, enabled=true, retry_policy?`。端口通过channel_ids绑定通道，通道不另存一份可独立修改的port归属。只保存凭证引用；webhook endpoint 必须经 owner/有权管理者登记。人工网页的会话身份由通道认证，不接受 body 里随意填写的回答者。

### 13.6 RetirePlan

`reason` 必填；`work` 数组列每件未结 work 的 task_id 与 forward/trash/fail 处置；`personal` 列个人轻量待办的 forward/trash；`callbacks` 指定登记给此 Agent 的未完回调的新 target 或显式取消；`stop=true`。每项有原因与合法目标。未覆盖的责任列为 blockers，Agent 保持 retiring，不自动丢弃。端口计划只覆盖该端口的 queued/claimed Task，不删除 agent 的其他订阅与任务。

## 14. 完整 CLI 参数索引

本节每一项都是公开命令叶子。所有命令另支持第12节全局参数；表中仅列本地参数。命令存在不代表任意主体有权调用。`control test` 只在隔离测试profile存在，生产必须拒绝。

### CMD-001 · `hive inbox list`

聚合查看个人、责任和可领任务。

```text
hive inbox list [--view VIEW] [filters]
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `--view VIEW` | all/personal/assigned/available/sent/history/declined | all | all 聚合前三类并按 task_id 去重；历史只在显式 view 出现。 |
| `--agent ID` | agent_id | 自己 | 查看别人必须有授权，不改变调用身份。 |
| `--port ID` | port ID/route | 不筛选 | 限定目标或来源端口；按可见性过滤。 |
| `--type TYPE` | work/notice/message/receipt | 不筛选 | 任务类型。 |
| `--state STATE` | queued/claimed/done/fail/trash | 视图默认 | assigned 默认 claimed，available 默认 queued。 |
| `--urgency LEVEL` | urgent/normal/low | 不筛选 | 优先级筛选。 |
| `--include-declined` | flag | false | available 默认排除对当前 spec_version 的本人拒绝。 |
| `--sort ORDER` | priority/created/deadline | priority | priority=紧急度降序、领取截止升序(null最后)、入队时间、ID。 |
| `--limit N` | int 1..200 | 50 | 返回条数。 |
| `--cursor TOKEN` | string | 无 | 使用前页 next_cursor，不手工解析。 |

执行规则：available 展示不构成预留；同一任务通过多个视图出现时只返回一次并列出 memberships。

返回：items[]、counts、next_cursor、snapshot_at。

### CMD-002 · `hive inbox peek`

查看任务详情。

```text
hive inbox peek TASK [--comments] [--history]
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `TASK` | task_id | 必填 | 仅能读有权访问的任务。 |
| `--comments` | flag | false | 包含最近50条可见评论及继续游标。 |
| `--history` | flag | false | 包含最近50条流转记录及继续游标。 |
| `--mark-seen` | flag | false | 显式记录当前主体已查看；仍不 claim 或结束。 |

执行规则：普通只读 peek 不改变处理状态。--mark-seen 只更新查看事实。

返回：task、可选 comments/history、permissions、wait_summary。

### CMD-003 · `hive inbox claim`

领取单个或多个任务。

```text
hive inbox claim TASK [TASK ...] [--spec-version N]
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `TASK...` | 1..100 task_id | 必填 | 去重后按输入顺序逐件竞争。 |
| `--spec-version N` | positive int | 无 | 仅单任务可用；任务要求已变则拒绝，评论变化不触发。 |

执行规则：逐项事务；一个失败不回滚其他成功。工作容量不足返回该项 CAPACITY_EXCEEDED。同一有效实例持有的重复 claim 返回已有 claim_id。新实例接续原 Agent 的责任时重新获取本次凭据并撤销旧缓存。

返回：results[{task_id,ok,task,claim_id,error}]；全部成功exit0，部分成功exit6，全失败按首个错误类别。

### CMD-004 · `hive inbox decline`

拒绝尚未领取的工作。

```text
hive inbox decline TASK --reason TEXT
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `TASK` | task_id | 必填 | 只支持当前可见 queued work。 |
| `--reason TEXT` | nonempty string | 必填 | 操作原因，最长4096字符，进入审计。 |

执行规则：不改变 Task 终态；记录拒绝和可见流转理由；已持有 work 不能 decline。权限缺失与没能力不同，不能通过 decline 获得读权限。

返回：decline_id、task_id、spec_version。

### CMD-005 · `hive inbox tree`

查询任务血缘树。

```text
hive inbox tree TASK [--depth N]
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `TASK` | task_id | 必填 | 查询当前任务子树，需读权限。 |
| `--depth N` | int 0..20 | 4 | 0只返回本节点。 |
| `--include-terminal` | bool true/false | true | false 仍保留必要祖先结构。 |
| `--include-comments` | flag | false | 每节点仅摘要，完整评论用 peek。 |

执行规则：权限不足的后代以受限占位表示，不泄露内容。父 done 不等于树完成。

返回：nodes、edges、counts_by_state、open_work_count、truncated。

### CMD-006 · `hive inbox wait create`

登记具体任务的等待。

```text
hive inbox wait create TASK --on REF [--on REF ...]
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `TASK` | task_id | 必填 | 当前主体持有的非终态 work。 |
| `--on REF` | repeated task:ID/operation:ID | 至少一个 | 每个依赖都必须有读/订阅结果权限，最多100个。 |
| `--mode MODE` | all/any | all | 返回包括失败、废弃和取消。 |
| `--wait-for D` | duration/none | none | 等待期限；与 --until 互斥，不修改被等待任务的期限。 |
| `--until TIME` | RFC3339 | 无 | 等待截止。 |
| `--checkpoint URI` | repeated reference | [] | 恢复资料入口。 |
| `--replace` | flag | false | 替换已有pending wait，旧记录置cancelled；不取消目标任务。 |
| `--claim ID` | claim_id | 当前实例领取缓存 | work 必需有效本次领取凭据；管理者请用 control，不能冒充 owner。 |

执行规则：立即返回；已结束依赖立即计算 ready；检查循环。等候登记本身建立结果订阅。

返回：wait、dependency_results、ready。

### CMD-007 · `hive inbox wait list`

查看等待记录。

```text
hive inbox wait list [--task TASK] [--state STATE]
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `--task TASK` | task_id | 不筛选 | 按责任任务过滤。 |
| `--state STATE` | pending/ready/timed_out/cancelled | pending | 查询范围按读权限。 |
| `--limit N` | int 1..200 | 50 | 返回条数。 |
| `--cursor TOKEN` | string | 无 | 使用前页 next_cursor，不手工解析。 |

执行规则：不阻塞回合。

返回：items、next_cursor。

### CMD-008 · `hive inbox wait show`

查看等待详情。

```text
hive inbox wait show WAIT
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `WAIT` | wait_id | 必填 | 归属任务必须可读。 |

执行规则：含每个依赖结果，不因单个fail继续假等待。

返回：wait、dependencies、notification_status。

### CMD-009 · `hive inbox wait cancel`

解除等待。

```text
hive inbox wait cancel WAIT --reason TEXT
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `WAIT` | wait_id | 必填 | 当前责任人或有管理权限者。 |
| `--reason TEXT` | nonempty string | 必填 | 操作原因，最长4096字符，进入审计。 |

执行规则：不结束父任务、不取消依赖；重复同请求幂等。

返回：wait.state=cancelled。

### CMD-010 · `hive emit new`

创建并投递Task。

```text
hive emit new (--root | --parent TASK) [Task fields | --spec-file FILE]
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `--root` | flag | 与parent二选一 | 独立根任务。 |
| `--parent TASK` | task_id | 与root二选一 | 显式血缘；必须有派生权限。 |
| `--spec-file FILE` | TaskSpec JSON | 无 | 与NEW内容参数互斥，仍需root/parent。 |
| `--to TARGET` | agent:ID/port:ID-or-route | 必填 | 单一当前投递目标。 |
| `--type TYPE` | work/notice/message | work | receipt 只由系统生成。 |
| `--title TEXT` | string 1..256 | 必填 | 任务卡标题。 |
| `--summary TEXT` | string <=2048 | 空 | 列表摘要，由发起方填写，不由 keeper 使用 LLM 生成。 |
| `--body TEXT` | string | 空 | 与 --body-file 互斥；work 至少有 body 或非空 data。 |
| `--body-file FILE` | UTF-8 file | 无 | 客户端读取任务正文。 |
| `--urgency LEVEL` | urgent/normal/low | normal | 仅排序与提醒策略，不自动打断。 |
| `--reply-to TASK` | task_id | null | 关联正在回应的任务，不改变血缘。 |
| `--data JSON` | JSON object | {} | 结构化数据；与 --data-file 互斥。 |
| `--data-file FILE` | JSON object file | 无 | 客户端解析对象，非法 JSON 返回参数错误。 |
| `--attach URI` | repeated reference | [] | 可重复；保存引用，内容访问另外鉴权。 |
| `--callback TARGET` | none/self/agent:ID/port:ID | none | 注册终态投递目标；非 Agent 不能用 self。 |
| `--claim-by TIME` | RFC3339 | 端口默认或无 | 本轮领取截止，与 --claim-within 互斥。 |
| `--claim-within D` | duration/none | 端口默认或无 | 相对服务端接收时间；none 显式取消默认期限。 |
| `--expires-at TIME` | RFC3339 | 端口默认或无 | 业务有效期，与 --expires-in 互斥。 |
| `--expires-in D` | duration/none | 端口默认或无 | 正时长；none 显式无有效期限。 |

执行规则：入队、事件和幂等记录同事务；无回调时终态不通知发送者。

返回：task、delivery.accepted、source。

### CMD-011 · `hive emit done`

提交成功结果，可原子创建下游。

```text
hive emit done TASK [result] [--dispatch-file FILE]
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `TASK` | task_id | 必填 | work需claim；个人非work可直接处理。 |
| `--dispatch-file FILE` | TaskSpec array | 无 | 1..100下游任务，parent自动为当前Task，全有或全无。 |
| `--claim ID` | claim_id | 当前实例领取缓存 | work 必需有效本次领取凭据；管理者请用 control，不能冒充 owner。 |
| `--text TEXT` | string | 空 | 结果/评论文字；与 --text-file 互斥。 |
| `--text-file FILE` | UTF-8 file | 无 | 客户端读取文字，不把路径交服务端任意读取。 |
| `--data JSON` | JSON object | {} | 结构化数据；与 --data-file 互斥。 |
| `--data-file FILE` | JSON object file | 无 | 客户端解析对象，非法 JSON 返回参数错误。 |
| `--attach URI` | repeated reference | [] | 可重复；保存引用，内容访问另外鉴权。 |

执行规则：有产物要求的work必须给结果或附件；系统校验结构不判断业务质量；待办通知允许空结果。父结束取消自身pending wait，不级联子任务。

返回：task、children[]、receipt_ids[]、terminal_event_id。

### CMD-012 · `hive emit fail`

结束本次处理并说明失败。

```text
hive emit fail TASK --reason TEXT [result]
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `TASK` | task_id | 必填 | 失败受阻表示这次处理停止，不是正常等回执。 |
| `--code CODE` | string | FAILED | 业务原因码，不覆盖系统错误码。 |
| `--claim ID` | claim_id | 当前实例领取缓存 | work 必需有效本次领取凭据；管理者请用 control，不能冒充 owner。 |
| `--reason TEXT` | nonempty string | 必填 | 操作原因，最长4096字符，进入审计。 |
| `--text TEXT` | string | 空 | 结果/评论文字；与 --text-file 互斥。 |
| `--text-file FILE` | UTF-8 file | 无 | 客户端读取文字，不把路径交服务端任意读取。 |
| `--data JSON` | JSON object | {} | 结构化数据；与 --data-file 互斥。 |
| `--data-file FILE` | JSON object file | 无 | 客户端解析对象，非法 JSON 返回参数错误。 |
| `--attach URI` | repeated reference | [] | 可重复；保存引用，内容访问另外鉴权。 |

执行规则：允许附部分成果；按原回调设置出回执。

返回：task.state=fail、result、receipt_ids。

### CMD-013 · `hive emit trash`

废弃任务并说明原因。

```text
hive emit trash TASK --reason TEXT
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `TASK` | task_id | 必填 | 有结束此任务的权限。 |
| `--instruction REF` | task/comment/operation reference | 无 | 引用使任务失效的上级指示，必须可读。 |
| `--claim ID` | claim_id | 当前实例领取缓存 | work 必需有效本次领取凭据；管理者请用 control，不能冒充 owner。 |
| `--reason TEXT` | nonempty string | 必填 | 操作原因，最长4096字符，进入审计。 |

执行规则：保留记录；不自动取消子树；有回调则发trash回执。

返回：task.state=trash、result、receipt_ids。

### CMD-014 · `hive emit forward`

转交同一任务。

```text
hive emit forward TASK --to TARGET --reason TEXT
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `TASK` | task_id | 必填 | 当前持有的任务。 |
| `--to TARGET` | agent:ID/port:ID-or-route | 必填 | 校验目标、类型和调用者权限。 |
| `--claim-by TIME` | RFC3339 | 新目标默认 | 与claim-within互斥。 |
| `--claim-within D` | duration/none | 新目标默认 | 新一轮领取期限。 |
| `--claim ID` | claim_id | 当前实例领取缓存 | work 必需有效本次领取凭据；管理者请用 control，不能冒充 owner。 |
| `--reason TEXT` | nonempty string | 必填 | 操作原因，最长4096字符，进入审计。 |
| `--if-version N` | positive int | 接收时当前版本 | 有值时做乐观并发检查，过时返回 VERSION_CONFLICT。 |

执行规则：task_id不变，保留callback、expires_at与讨论；入队和解除旧claim原子执行；不发终态回调。

返回：task.state=queued、transfer_id、claim_epoch、wait_summary。

### CMD-015 · `hive emit comment`

追加评论并可@通知。

```text
hive emit comment TASK (--text TEXT | --text-file FILE)
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `TASK` | task_id | 必填 | 有读与评论权限。 |
| `--mention AGENT` | repeated agent_id | [] | 与正文规范@解析结果合并去重。 |
| `--revision-of COMMENT` | comment_id | 无 | 创建修订记录，不物理覆盖旧评论。 |
| `--checkpoint URI` | repeated reference | [] | 可挂接工作记录，最新引用进入恢复摘要。 |
| `--text TEXT` | string | 空 | 结果/评论文字；与 --text-file 互斥。 |
| `--text-file FILE` | UTF-8 file | 无 | 客户端读取文字，不把路径交服务端任意读取。 |

执行规则：正文非空；@权限不足时整笔失败；创建评论与通知复用emit事务。

返回：comment、notice_ids、mentions。

### CMD-016 · `hive emit batch`

原子提交多个业务操作。

```text
hive emit batch --file FILE
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `--file FILE` | EmitBatch JSON | 必填 | 第13.2节schema；最多100操作。 |

执行规则：任一步验证失败全部不写入；不能包含control/任意脚本；身份统一使用调用者。

返回：results[]、refs、event_ids。

### CMD-017 · `hive control system init`

初始化独立Hive数据根。

```text
hive control system init [--listen HOST:PORT]
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `--listen ADDRESS` | host:port | 127.0.0.1:19962 | 写初始system.json。 |
| `--default-harness NAME` | registry name | null | 可稍后配置。 |
| `--owner-credential-out FILE` | path | HOME/auth/owner.json | 私密文件保存凭据，stdout仅返回路径。 |
| `--test-mode` | flag | false | 创建隔离验收profile，不能把已有生产profile改为test。 |

执行规则：本地命令；目录已有有效Hive时返回现状，不覆盖。生产旧trigger目录/数据库不可作为home。

返回：home、schema_version、credential_file、initialized。

### CMD-018 · `hive control system serve`

前台运行daemon。

```text
hive control system serve [--listen HOST:PORT]
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `--listen ADDRESS` | host:port | system.json | 必须与home实例锁一致。 |
| `--test-mode` | flag | false | 仅已init为test的home有效。 |
| `--log-level LEVEL` | debug/info/warning/error | info | 始终脱敏。 |

执行规则：本地前台长期进程；取得单写者锁，第二服务拒绝；日志stderr，启动ready输出JSON事件。

返回：启动ready记录；进程持续运行。

### CMD-019 · `hive control system start`

后台启动daemon。

```text
hive control system start [--listen HOST:PORT]
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `--listen ADDRESS` | host:port | system.json | 检查监听冲突。 |
| `--test-mode` | flag | false | 只允许test profile。 |

执行规则：Windows后台进程隐藏窗口；已有同home活服务则返回它；不扫描/终止无关进程。

返回：service_pid、endpoint、ready。

### CMD-020 · `hive control system stop`

停止Hive服务。

```text
hive control system stop --reason TEXT
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `--reason TEXT` | nonempty string | 必填 | 操作原因，最长4096字符，进入审计。 |

执行规则：先停止接受变更并提交当前事务；保存恢复信息。默认不停止受管Agent实例，重启Hive时需核对；停全实例用显式agent stop。

返回：service stopping/已停止确认。

### CMD-021 · `hive control system status`

服务健康与能力概况。

```text
hive control system status 
```

无命令级参数，仅接受全局参数。

执行规则：服务不可达返回通信错误，不伪造所有Agent死亡。

返回：version、schema_version、uptime、home、pending_outbox、running_operations、warnings。

### CMD-022 · `hive control system config`

读取或更新系统策略。

```text
hive control system config [--file FILE] [--if-version N]
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `--file FILE` | SystemPolicy JSON patch | 无=只读 | 提交参数策略合并补丁；可更新默认harness/第10.1节参数。 |
| `--reason TEXT` | string | 更新时必填 | 只读时不用。 |
| `--if-version N` | positive int | 接收时当前版本 | 有值时做乐观并发检查，过时返回 VERSION_CONFLICT。 |

执行规则：不允许通过此接口改认证主体、数据库路径或直接写任务状态；重启类设置返回pending。

返回：effective_config、revision、pending_fields。

### CMD-023 · `hive control system log`

读取脱敏日志。

```text
hive control system log [--tail N] [--level LEVEL]
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `--tail N` | int 1..10000 | 100 | 有界读取，不默认follow。 |
| `--level LEVEL` | debug/info/warning/error | 不筛选 | 最低级别。 |
| `--since TIME` | RFC3339 | 无 | 时间筛选。 |

执行规则：需system.observe；文件路径不由用户任意指定。

返回：entries[]、truncated。

### CMD-024 · `hive control system backup`

创建一致备份。

```text
hive control system backup --out DIR [--include-workspaces]
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `--out DIR` | new directory path | 必填 | 目标必须不存在或为空；不覆盖已有备份。 |
| `--include-workspaces` | flag | false | 包含受管工位；外部资产仅列manifest引用。 |
| `--include-credentials` | flag | false | 默认排除；显式owner才可，产物必须按秘密处理。 |

执行规则：SQLite backup API；有效配置版本、manifest、hash同备份；不能复制运行中的db主文件假装完整备份。

返回：backup_id、manifest_path、files、warnings。

### CMD-025 · `hive control system restore`

恢复到新的数据根。

```text
hive control system restore --from DIR --into DIR
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `--from DIR` | backup directory | 必填 | 校验manifest/schema/hash。 |
| `--into DIR` | new empty directory | 必填 | 不允许原地覆盖，不自动停其他环境。 |
| `--with-credentials` | flag | false | 仅备份明确包含且owner授权时恢复凭据。 |

执行规则：本地离线动作；恢复后所有Agent先paused，旧实例绑定待核实，outbox冻结至显式恢复，避免备份启动复制外部动作。

返回：restored_home、recovery_report、required_actions。

### CMD-026 · `hive control agent list`

发现可见Agent。

```text
hive control agent list [filters]
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `--query TEXT` | string | 空 | 搜名称/简介。 |
| `--state STATE` | enabled/paused/retiring/retired | 不筛选 | 生命周期。 |
| `--harness NAME` | string | 不筛选 | 运行配置。 |
| `--port PORT` | port ID/route | 不筛选 | 订阅关系筛选。 |
| `--limit N` | int 1..200 | 50 | 返回条数。 |
| `--cursor TOKEN` | string | 无 | 使用前页 next_cursor，不手工解析。 |

执行规则：公开名片与完整配置分权。

返回：items、next_cursor。

### CMD-027 · `hive control agent show`

查看Agent定义和运行摘要。

```text
hive control agent show AGENT [--config] [--runtime]
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `AGENT` | agent_id | 必填 | 精确ID。 |
| `--config` | flag | false | 完整可读配置，凭证字段永不返回正文。 |
| `--runtime` | flag | false | 实例、配置生效版本、责任摘要与observability。 |

执行规则：配置和会话详情需额外权限；无原生观测标unknown。

返回：agent、可选config/runtime。

### CMD-028 · `hive control agent create`

注册Agent，允许缺省配置。

```text
hive control agent create [--file FILE] [overrides] [--start]
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `--file FILE` | AgentConfig JSON | {} | 缺字段按第3.1节补全。 |
| `--name TEXT` | string | 自动 | 覆盖文件对应字段。 |
| `--harness NAME` | registry name | 系统默认 | 可缺省。 |
| `--model NAME` | string | 宿主默认 | 可缺省。 |
| `--workspace PATH` | path | 系统分配 | 不自动复制旧资料。 |
| `--auto-start BOOL` | true/false | true | 之后收到待办的自动启动策略。 |
| `--start` | flag | false | 注册后请求启动，失败不回滚已注册身份。 |
| `--reason TEXT` | string | create agent | 审计原因。 |

执行规则：file+显式override允许；permissions额外鉴权；默认返回0实例，但之后调度可能因现有待办启动。

返回：agent、config_revision、personal_target、可选start_operation。

### CMD-029 · `hive control agent clone`

复制配置创建不同Agent。

```text
hive control agent clone AGENT [overrides]
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `AGENT` | agent_id | 必填 | 有读配置及创建权限。 |
| `--name TEXT` | string | 自动新名 | 新显示名。 |
| `--workspace PATH` | path | 新独立目录 | 不复制任务、凭据或会话。 |
| `--start` | flag | false | 是否请求新实例。 |
| `--reason TEXT` | string | clone agent | 审计原因。 |

执行规则：权限必须在调用者可授予范围内；不能从高权对象偷渡授权。

返回：new_agent、source_agent_id。

### CMD-030 · `hive control agent update`

发布配置补丁或完整定义。

```text
hive control agent update AGENT --file FILE --reason TEXT
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `AGENT` | agent_id | 必填 | 更新目标。 |
| `--file FILE` | AgentConfig/patch JSON | 必填 | 默认合并补丁：对象递归、数组替换、null删除可选项并回默认。 |
| `--replace` | flag | false | 完整配置替换；ID保持不变。 |
| `--apply MODE` | next-send/restart | next-send | 运行说明下次send更新；restart创建重启操作。 |
| `--reason TEXT` | nonempty string | 必填 | 操作原因，最长4096字符，进入审计。 |
| `--if-version N` | positive int | 接收时当前版本 | 有值时做乐观并发检查，过时返回 VERSION_CONFLICT。 |

执行规则：机器配置与权限即时检查；订阅投影同版本；配置文件热更新不要求宿主原生能力。

返回：desired_revision、applied_revision、pending_fields、operation_id?。

### CMD-031 · `hive control agent start`

启用并启动一个Agent。

```text
hive control agent start AGENT
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `AGENT` | agent_id | 必填 | retired不能启动。 |
| `--callback TARGET` | none/self/agent:ID/port:ID | none | 注册终态投递目标；非 Agent 不能用 self。 |

执行规则：paused改enabled；并发start合并；缺harness则清晰失败；其余缺配置用默认。

返回：operation_id、instance_id、already_running。

### CMD-032 · `hive control agent wake`

唤醒处理待办。

```text
hive control agent wake AGENT [--message TEXT]
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `AGENT` | agent_id | 必填 | paused不自动启用。 |
| `--message TEXT` | string | 请查看inbox | 运行信号，不能冒充新业务任务。 |

执行规则：重复唤醒可合并；无待办时手工wake仍允许，但不是任务责任。

返回：operation_id或coalesced/paused原因。

### CMD-033 · `hive control agent send`

给指定实例发送运行指令。

```text
hive control agent send AGENT --text TEXT --reason TEXT
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `AGENT` | agent_id | 必填 | 需agent.send，普通交流用emit。 |
| `--mode MODE` | queue/interrupt | queue | queue由Hive排队；interrupt若无原生支持须选择降级策略。 |
| `--fallback MODE` | reject/restart | reject | 仅interrupt有效；restart明确允许重启后送入指令。 |
| `--text TEXT` | string | 空 | 结果/评论文字；与 --text-file 互斥。 |
| `--text-file FILE` | UTF-8 file | 无 | 客户端读取文字，不把路径交服务端任意读取。 |
| `--reason TEXT` | nonempty string | 必填 | 操作原因，最长4096字符，进入审计。 |
| `--instance ID` | instance_id | 受理时绑定当前实例 | 提供时必须匹配，避免对后来的新实例执行旧请求。 |
| `--callback TARGET` | none/self/agent:ID/port:ID | none | 注册终态投递目标；非 Agent 不能用 self。 |

执行规则：不是任意人通过comment即可控制；paused默认拒绝，不暗中start。

返回：operation_id、delivery_mode、degraded。

### CMD-034 · `hive control agent stop`

暂停Agent并停止实例。

```text
hive control agent stop AGENT --reason TEXT
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `AGENT` | agent_id | 必填 | 停止目标。 |
| `--grace D` | duration | 15s | 正常保存提示宽限；强制停止不保证新checkpoint。 |
| `--force` | flag | false | 有额外权限者跳过宽限。 |
| `--reason TEXT` | nonempty string | 必填 | 操作原因，最长4096字符，进入审计。 |
| `--instance ID` | instance_id | 受理时绑定当前实例 | 提供时必须匹配，避免对后来的新实例执行旧请求。 |
| `--callback TARGET` | none/self/agent:ID/port:ID | none | 注册终态投递目标；非 Agent 不能用 self。 |

执行规则：持有任务保留；stop未知不释放槽位，不能启动替身。

返回：operation_id、lifecycle、instance_stop_result。

### CMD-035 · `hive control agent restart`

替换当前实例。

```text
hive control agent restart AGENT --reason TEXT
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `AGENT` | agent_id | 必填 | 身份与责任不变。 |
| `--grace D` | duration | 15s | 保存提示宽限。 |
| `--force` | flag | false | 跳过宽限，仍须确认旧实例停止。 |
| `--reason TEXT` | nonempty string | 必填 | 操作原因，最长4096字符，进入审计。 |
| `--instance ID` | instance_id | 受理时绑定当前实例 | 提供时必须匹配，避免对后来的新实例执行旧请求。 |
| `--callback TARGET` | none/self/agent:ID/port:ID | none | 注册终态投递目标；非 Agent 不能用 self。 |

执行规则：显式restart使节点enabled；若仅改paused节点配置而不启动应使用update。

返回：operation_id、old_instance_id、new_instance_id。

### CMD-036 · `hive control agent model`

选模型，允许用重启实现。

```text
hive control agent model AGENT (--model NAME | --default) --reason TEXT
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `AGENT` | agent_id | 必填 | 被更新对象。 |
| `--model NAME` | string | 与default二选一 | 适配器能识别的模型名。 |
| `--default` | flag | false | 清空显式模型，使用宿主默认。 |
| `--apply MODE` | auto/restart/next-start | auto | auto优先可选热切换，否则重启；paused只更新配置。 |
| `--reason TEXT` | nonempty string | 必填 | 操作原因，最长4096字符，进入审计。 |
| `--if-version N` | positive int | 接收时当前版本 | 有值时做乐观并发检查，过时返回 VERSION_CONFLICT。 |

执行规则：不能把不支持热切换当作不满足最小接入。

返回：operation_id?、desired_model、apply_mode、pending。

### CMD-037 · `hive control agent retire`

按计划退役节点。

```text
hive control agent retire AGENT --plan-file FILE --reason TEXT
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `AGENT` | agent_id | 必填 | 稳定身份保留。 |
| `--plan-file FILE` | RetirePlan JSON | 必填 | 覆盖当前责任、个人待办、未完回调。 |
| `--dry-run` | flag | false | 只返回blockers和预期动作。 |
| `--reason TEXT` | nonempty string | 必填 | 操作原因，最长4096字符，进入审计。 |
| `--callback TARGET` | none/self/agent:ID/port:ID | none | 注册终态投递目标；非 Agent 不能用 self。 |

执行规则：未覆盖保持retiring，禁止默认trash所有任务；不删除工位。

返回：operation_id、blockers、dispositions。

### CMD-038 · `hive control port list`

发现任务端口。

```text
hive control port list [filters]
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `--query TEXT` | string | 空 | 查路由名/名称/说明。 |
| `--handler TYPE` | agents/human/external | 不筛选 | 接收方式。 |
| `--state STATE` | enabled/paused/retiring/retired | 不筛选 | 端口状态。 |
| `--limit N` | int 1..200 | 50 | 返回条数。 |
| `--cursor TOKEN` | string | 无 | 使用前页 next_cursor，不手工解析。 |

执行规则：仅返回可见名片。

返回：items、next_cursor。

### CMD-039 · `hive control port show`

查看端口规则与积压摘要。

```text
hive control port show PORT [--subscribers]
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `PORT` | port ID/route | 必填 | 精确定位。 |
| `--subscribers` | flag | false | 仅返回有权发现的订阅者。 |

执行规则：包含默认期限和接收类型；忙闲/容量仅在可读范围展示。

返回：port、queue_counts、subscribers?。

### CMD-040 · `hive control port create`

创建端口。

```text
hive control port create --route NAME [--file FILE]
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `--route NAME` | unique string | 必填 | 长度1..128；可用斜杠分业务名。 |
| `--file FILE` | PortConfig JSON | {} | 包含描述/类型/schema/defaults等。 |
| `--name TEXT` | string | route | 显示名覆盖。 |
| `--handler TYPE` | agents/human/external | agents | 适配器类型。 |
| `--channel CHANNEL` | repeated channel_id | 人工/外部时必填 | human可绑定多个；external首版只绑定一个webhook；所有通道先注册。 |
| `--reason TEXT` | string | create port | 审计原因。 |

执行规则：接收类型/schema不合法拒绝；缺通道返回CHANNEL_REQUIRED，不能有假可用端口。

返回：port。

### CMD-041 · `hive control port update`

更新端口配置。

```text
hive control port update PORT --file FILE --reason TEXT
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `PORT` | port ID/route | 必填 | 路由名变更仍保持port_id。 |
| `--file FILE` | PortConfig patch JSON | 必填 | 字段规则同第5节。 |
| `--replace` | flag | false | 完整替换可选配置。 |
| `--reason TEXT` | nonempty string | 必填 | 操作原因，最长4096字符，进入审计。 |
| `--if-version N` | positive int | 接收时当前版本 | 有值时做乐观并发检查，过时返回 VERSION_CONFLICT。 |

执行规则：修改默认期限只作用于新入队/转交轮次，不批量改已有任务期限。

返回：port、revision、warnings。

### CMD-042 · `hive control port pause`

暂停新领取，继续保存投递。

```text
hive control port pause PORT --reason TEXT
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `PORT` | port ID/route | 必填 | enabled可暂停。 |
| `--reason TEXT` | nonempty string | 必填 | 操作原因，最长4096字符，进入审计。 |

执行规则：已claimed任务照常结算。

返回：port.state=paused。

### CMD-043 · `hive control port resume`

恢复端口领取。

```text
hive control port resume PORT --reason TEXT
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `PORT` | port ID/route | 必填 | paused转enabled；retired不可恢复。 |
| `--reason TEXT` | nonempty string | 必填 | 操作原因，最长4096字符，进入审计。 |

执行规则：触发待领任务的合并通知检查。

返回：port.state=enabled。

### CMD-044 · `hive control port retire`

退役并安排积压去向。

```text
hive control port retire PORT --plan-file FILE --reason TEXT
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `PORT` | port ID/route | 必填 | 停新投递。 |
| `--plan-file FILE` | RetirePlan JSON | 必填 | 安排queued及claimed责任。 |
| `--dry-run` | flag | false | 仅检查，不改变状态。 |
| `--reason TEXT` | nonempty string | 必填 | 操作原因，最长4096字符，进入审计。 |
| `--callback TARGET` | none/self/agent:ID/port:ID | none | 注册终态投递目标；非 Agent 不能用 self。 |

执行规则：未处理完维持retiring；历史路由不可重用冒充旧端口。

返回：operation_id、blockers。

### CMD-045 · `hive control subscription list`

查询Agent与端口连接。

```text
hive control subscription list [--agent ID] [--port ID]
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `--agent ID` | agent_id | 不筛选 | 至少限定agent或port之一。 |
| `--port ID` | port ID/route | 不筛选 | 双方都给时精确连接。 |
| `--enabled BOOL` | true/false | 不筛选 | 过滤启用状态。 |
| `--limit N` | int 1..200 | 50 | 返回条数。 |
| `--cursor TOKEN` | string | 无 | 使用前页 next_cursor，不手工解析。 |

执行规则：读取完整编制需要对应权限；名片可见不意味着所有连接可见。

返回：items、next_cursor。

### CMD-046 · `hive control subscription add`

建立或启用订阅。

```text
hive control subscription add --agent AGENT --port PORT --reason TEXT
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `--agent AGENT` | agent_id | 必填 | 关系归属稳定Agent。 |
| `--port PORT` | port ID/route | 必填 | 仅handler=agents可订阅。 |
| `--reason TEXT` | nonempty string | 必填 | 操作原因，最长4096字符，进入审计。 |

执行规则：发布Agent新配置版本；不影响原领取责任或个人回执；add相同连接幂等，remove不存在连接返回不存在。

返回：subscription、agent_revision。

### CMD-047 · `hive control subscription pause`

暂停订阅。

```text
hive control subscription pause --agent AGENT --port PORT --reason TEXT
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `--agent AGENT` | agent_id | 必填 | 关系归属稳定Agent。 |
| `--port PORT` | port ID/route | 必填 | 仅handler=agents可订阅。 |
| `--reason TEXT` | nonempty string | 必填 | 操作原因，最长4096字符，进入审计。 |

执行规则：发布Agent新配置版本；不影响原领取责任或个人回执；add相同连接幂等，remove不存在连接返回不存在。

返回：subscription、agent_revision。

### CMD-048 · `hive control subscription resume`

恢复订阅。

```text
hive control subscription resume --agent AGENT --port PORT --reason TEXT
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `--agent AGENT` | agent_id | 必填 | 关系归属稳定Agent。 |
| `--port PORT` | port ID/route | 必填 | 仅handler=agents可订阅。 |
| `--reason TEXT` | nonempty string | 必填 | 操作原因，最长4096字符，进入审计。 |

执行规则：发布Agent新配置版本；不影响原领取责任或个人回执；add相同连接幂等，remove不存在连接返回不存在。

返回：subscription、agent_revision。

### CMD-049 · `hive control subscription remove`

取消订阅。

```text
hive control subscription remove --agent AGENT --port PORT --reason TEXT
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `--agent AGENT` | agent_id | 必填 | 关系归属稳定Agent。 |
| `--port PORT` | port ID/route | 必填 | 仅handler=agents可订阅。 |
| `--reason TEXT` | nonempty string | 必填 | 操作原因，最长4096字符，进入审计。 |

执行规则：发布Agent新配置版本；不影响原领取责任或个人回执；add相同连接幂等，remove不存在连接返回不存在。

返回：subscription、agent_revision。

### CMD-050 · `hive control task list`

按权限查询任务账本。

```text
hive control task list [filters]
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `--source SUBJECT` | actor ID/type reference | 不筛选 | 来源筛选。 |
| `--agent AGENT` | agent_id | 不筛选 | 当前owner或个人目标筛选。 |
| `--port PORT` | port ID/route | 不筛选 | 当前目标端口。 |
| `--root TASK` | task_id | 不筛选 | 整棵树内可见任务。 |
| `--state STATE` | queued/claimed/done/fail/trash | 不筛选 | 终态也可查询。 |
| `--type TYPE` | work/notice/message/receipt | 不筛选 | 类型。 |
| `--since TIME` | RFC3339 | 无 | 创建时间起点。 |
| `--limit N` | int 1..200 | 50 | 返回条数。 |
| `--cursor TOKEN` | string | 无 | 使用前页 next_cursor，不手工解析。 |

执行规则：无callback的发送者仍可查询自己创建的任务。

返回：items、next_cursor。

### CMD-051 · `hive control task show`

管理视角查看任务。

```text
hive control task show TASK [--history]
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `TASK` | task_id | 必填 | 任务ID。 |
| `--history` | flag | false | 流转、领取代次与审计摘要。 |

执行规则：与inbox peek共用可见性服务；不绕过权限。

返回：task、history?。

### CMD-052 · `hive control task amend`

有权者修改任务要求或期限。

```text
hive control task amend TASK --file FILE --reason TEXT
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `TASK` | task_id | 必填 | 非终态任务。 |
| `--file FILE` | TaskAmend JSON patch | 必填 | 仅title/summary/body/data/attachments/urgency/claim_deadline/expires_at/callback；不改ID/source/parent/state/owner。 |
| `--reason TEXT` | nonempty string | 必填 | 操作原因，最长4096字符，进入审计。 |
| `--if-version N` | positive int | 接收时当前版本 | 有值时做乐观并发检查，过时返回 VERSION_CONFLICT。 |

执行规则：要求实质变化增加spec_version，通知当前责任人；清除旧版本拒绝抑制；已发生回调不能撤销。

返回：task、version、spec_version。

### CMD-053 · `hive control task reclaim`

行政回收已领任务。

```text
hive control task reclaim TASK --to TARGET --reason TEXT
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `TASK` | task_id | 必填 | 必须claimed。 |
| `--to TARGET` | agent:ID/port:ID | 必填 | 回收后的目标，不隐含丢弃。 |
| `--stop-owner` | flag | false | 明确请求暂停原Agent全部实例工作；可能影响其别的任务，记录影响范围。 |
| `--reason TEXT` | nonempty string | 必填 | 操作原因，最长4096字符，进入审计。 |
| `--if-version N` | positive int | 接收时当前版本 | 有值时做乐观并发检查，过时返回 VERSION_CONFLICT。 |

执行规则：先使原claim失效；没有stop-owner时只发任务回收通知，账本不声称旧外部动作已停止。

返回：operation_id、task、invalidated_claim。

### CMD-054 · `hive control task forward`

授权管理者转交任务。

```text
hive control task forward TASK --to TARGET --reason TEXT
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `TASK` | task_id | 必填 | 通常queued；claimed要用reclaim。 |
| `--to TARGET` | agent:ID/port:ID | 必填 | 保留Task身份和回调。 |
| `--reason TEXT` | nonempty string | 必填 | 操作原因，最长4096字符，进入审计。 |
| `--if-version N` | positive int | 接收时当前版本 | 有值时做乐观并发检查，过时返回 VERSION_CONFLICT。 |

执行规则：不冒充领取者；claimed返回RECLAIM_REQUIRED。

返回：task、transfer_id。

### CMD-055 · `hive control task trash`

授权取消任务，可显式处理子树。

```text
hive control task trash TASK --reason TEXT
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `TASK` | task_id | 必填 | queued/claimed均可按授权处理。 |
| `--cascade` | flag | false | 显式逐项处理可管理子树，产生控制操作；不是默认递归。 |
| `--dry-run` | flag | false | 返回将受影响对象和权限缺口。 |
| `--reason TEXT` | nonempty string | 必填 | 操作原因，最长4096字符，进入审计。 |
| `--if-version N` | positive int | 接收时当前版本 | 有值时做乐观并发检查，过时返回 VERSION_CONFLICT。 |

执行规则：无cascade只当前Task；cascade非跨外部动作原子，逐项结果必须可见；终态不改写。

返回：task或operation_id、affected、blockers。

### CMD-056 · `hive control permission list`

查看有效授权。

```text
hive control permission list --subject SUBJECT
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `--subject SUBJECT` | typed subject reference | 必填 | agent:ID/producer:ID/human:ID等。 |
| `--limit N` | int 1..200 | 50 | 返回条数。 |
| `--cursor TOKEN` | string | 无 | 使用前页 next_cursor，不手工解析。 |

执行规则：自己可读有效权限；查看他人授权需许可。

返回：grants、effective_scope。

### CMD-057 · `hive control permission grant`

授予明确能力。

```text
hive control permission grant --file FILE --reason TEXT
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `--file FILE` | GrantSpec JSON | 必填 | 第13.3节，所有字段校验。 |
| `--expires-at TIME` | RFC3339 | 无 | 到期不再允许新请求。 |
| `--reason TEXT` | nonempty string | 必填 | 操作原因，最长4096字符，进入审计。 |

执行规则：调用者须有相应可转授权范围；不能通过授予自己扩大到未获授权范围。

返回：grant_id、effective_revision。

### CMD-058 · `hive control permission revoke`

撤销授权。

```text
hive control permission revoke GRANT --reason TEXT
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `GRANT` | grant_id | 必填 | 不能猜ID绕过管理范围。 |
| `--reason TEXT` | nonempty string | 必填 | 操作原因，最长4096字符，进入审计。 |

执行规则：下一次操作立即生效；既有任务责任不自动消失。

返回：grant.revoked_at、affected_subject。

### CMD-059 · `hive control operation list`

查看控制操作。

```text
hive control operation list [filters]
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `--state STATE` | accepted/running/succeeded/failed/cancelled | 不筛选 | 结果未知保留running并有标记。 |
| `--target TARGET` | resource reference | 不筛选 | 目标对象。 |
| `--request REQUEST` | request_id | 无 | 找回超时请求。 |
| `--limit N` | int 1..200 | 50 | 返回条数。 |
| `--cursor TOKEN` | string | 无 | 使用前页 next_cursor，不手工解析。 |

执行规则：仅可见调用者有权读取的操作。

返回：items、next_cursor。

### CMD-060 · `hive control operation show`

查看或短等待控制结果。

```text
hive control operation show OP [--wait-seconds N]
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `OP` | operation_id | 必填 | 也可用request-id先list找回。 |
| `--wait-seconds N` | int 0..30 | 0 | 有界等待，不是Agent业务wait。 |

执行规则：超时仍返回当前operation；已succeeded/failed/cancelled立刻返回。

返回：operation、result/error、outcome_unknown。

### CMD-061 · `hive control operation cancel`

请求取消尚未完成步骤。

```text
hive control operation cancel OP --reason TEXT
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `OP` | operation_id | 必填 | 已终态不可改。 |
| `--reason TEXT` | nonempty string | 必填 | 操作原因，最长4096字符，进入审计。 |

执行规则：不撤回既有外部效果；无法安全取消返回OP_NOT_CANCELLABLE和当前进度。

返回：operation、completed_steps、remaining_steps。

### CMD-062 · `hive control event list`

查询系统事实事件。

```text
hive control event list [filters]
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `--entity REF` | typed resource reference | 不筛选 | 按对象权限过滤。 |
| `--kind NAME` | event kind | 不筛选 | 如task.terminal/agent.started。 |
| `--since TIME` | RFC3339 | 无 | 记录时间起点。 |
| `--limit N` | int 1..200 | 50 | 返回条数。 |
| `--cursor TOKEN` | string | 无 | 使用前页 next_cursor，不手工解析。 |

执行规则：不提供任意读内部敏感payload能力；无跨范围权限不泄露任务标题。

返回：items、next_cursor。

### CMD-063 · `hive control audit list`

查询审计轨迹。

```text
hive control audit list [filters]
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `--actor SUBJECT` | typed subject | 不筛选 | 发起者。 |
| `--target REF` | resource reference | 不筛选 | 对象。 |
| `--request ID` | request_id | 不筛选 | 操作链。 |
| `--since TIME` | RFC3339 | 无 | 时间起点。 |
| `--limit N` | int 1..200 | 50 | 返回条数。 |
| `--cursor TOKEN` | string | 无 | 使用前页 next_cursor，不手工解析。 |

执行规则：需相应审计范围；凭证正文永不返回。

返回：items、next_cursor。

### CMD-064 · `hive control delivery list`

查询待投递与投递异常。

```text
hive control delivery list [filters]
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `--state STATE` | pending/sending/sent/retry/dead | 不筛选 | outbox状态。 |
| `--task TASK` | task_id | 不筛选 | 源任务或回执关联。 |
| `--target TARGET` | route/subject | 不筛选 | 接收目标。 |
| `--limit N` | int 1..200 | 50 | 返回条数。 |
| `--cursor TOKEN` | string | 无 | 使用前页 next_cursor，不手工解析。 |

执行规则：无回调不构造sender通知，管理员从此观察投递异常。

返回：items、next_cursor。

### CMD-065 · `hive control delivery retry`

重试已有投递。

```text
hive control delivery retry DELIVERY --reason TEXT
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `DELIVERY` | delivery_id | 必填 | retry/dead状态，sent返回已完成。 |
| `--reason TEXT` | nonempty string | 必填 | 操作原因，最长4096字符，进入审计。 |

执行规则：沿用semantic_key与接收者去重键，不新建第二条相同回执。

返回：delivery、next_attempt_at。

### CMD-066 · `hive control harness list`

列出注册适配器。

```text
hive control harness list 
```

无命令级参数，仅接受全局参数。

执行规则：显示五项最小能力与可选能力，不把未核实增强能力当已有。

返回：items。

### CMD-067 · `hive control harness show`

查看适配配置与能力。

```text
hive control harness show NAME
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `NAME` | registry name | 必填 | 注册名。 |

执行规则：脱敏，只返回凭证引用。

返回：harness、validation_level、capabilities。

### CMD-068 · `hive control harness register`

注册或发布适配定义。

```text
hive control harness register --file FILE --reason TEXT
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `--file FILE` | HarnessSpec JSON | 必填 | 第13.4节。 |
| `--replace` | flag | false | 同名存在时必须显式替换。 |
| `--reason TEXT` | nonempty string | 必填 | 操作原因，最长4096字符，进入审计。 |

执行规则：只校验定义和适配器可加载；不自动启动真实会话、不宣称实测通过。

返回：harness、revision、validation_level=declared。

### CMD-069 · `hive control harness validate`

校验声明或显式执行五能力演练。

```text
hive control harness validate NAME [--smoke]
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `NAME` | registry name | 必填 | 待验适配器。 |
| `--smoke` | flag | false | 显式启动测试会话、发消息、选另一模型/默认再启动、终止。 |
| `--workspace PATH` | isolated directory | smoke时必填 | 不使用业务工位。 |
| `--model NAME` | string | 适配器默认 | 第一模型。 |
| `--alternate-model NAME` | string | smoke时必填 | 验证可选择不同模型；不依赖热切换。 |

执行规则：真实smoke结果逐项输出；元数据validate不是实际接入通过；无事件/history等仍可通过五能力。

返回：checks[capability,passed,evidence]、overall。

### CMD-070 · `hive control harness unregister`

注销未被使用的适配器。

```text
hive control harness unregister NAME --reason TEXT
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `NAME` | registry name | 必填 | 不物理删除外部程序。 |
| `--reason TEXT` | nonempty string | 必填 | 操作原因，最长4096字符，进入审计。 |

执行规则：存在活实例或enabled Agent引用时拒绝并列blockers。

返回：unregistered、blockers。

### CMD-071 · `hive control producer list`

列出任务生产入口。

```text
hive control producer list 
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `--limit N` | int 1..200 | 50 | 返回条数。 |
| `--cursor TOKEN` | string | 无 | 使用前页 next_cursor，不手工解析。 |

执行规则：内置trigger与外部服务共用身份模型。

返回：items、next_cursor。

### CMD-072 · `hive control producer show`

查看生产者范围。

```text
hive control producer show PRODUCER
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `PRODUCER` | producer_id | 必填 | 不返回token。 |

执行规则：显示可投递目标和事件去重概况。

返回：producer、allowed_targets。

### CMD-073 · `hive control producer create`

注册内置或外部生产者。

```text
hive control producer create --name NAME --kind KIND --allow-target TARGET [--allow-target TARGET]
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `--name NAME` | string | 必填 | 显示名。 |
| `--kind KIND` | builtin/external | 必填 | 来源类别。 |
| `--allow-target TARGET` | repeated target | 至少一个 | 允许投递目标。 |
| `--credential-out FILE` | protected path | external时必填 | 保存凭据文件，stdout仅路径。 |
| `--reason TEXT` | string | create producer | 审计原因。 |

执行规则：不创建Agent/inbox/session，不默认获得control权限。

返回：producer、credential_file?。

### CMD-074 · `hive control producer update`

更新生产者配置。

```text
hive control producer update PRODUCER --file FILE --reason TEXT
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `PRODUCER` | producer_id | 必填 | 目标。 |
| `--file FILE` | ProducerConfig patch | 必填 | name/allowed_targets/enabled/metadata；权限调整单独鉴权。 |
| `--reason TEXT` | nonempty string | 必填 | 操作原因，最长4096字符，进入审计。 |
| `--if-version N` | positive int | 接收时当前版本 | 有值时做乐观并发检查，过时返回 VERSION_CONFLICT。 |

执行规则：不改变历史source；权限收缩立即用于新投递。

返回：producer、revision。

### CMD-075 · `hive control producer credential`

生成或轮换生产者凭据。

```text
hive control producer credential PRODUCER --out FILE --reason TEXT
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `PRODUCER` | producer_id | 必填 | external入口。 |
| `--out FILE` | protected path | 必填 | 不打印秘密正文。 |
| `--revoke-previous` | flag | false | 显式使旧凭据失效。 |
| `--reason TEXT` | nonempty string | 必填 | 操作原因，最长4096字符，进入审计。 |

执行规则：凭据不进入任务、日志或prompt。

返回：credential_file、credential_id。

### CMD-076 · `hive control producer revoke`

停用生产者入口。

```text
hive control producer revoke PRODUCER --reason TEXT
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `PRODUCER` | producer_id | 必填 | 停用其新请求。 |
| `--reason TEXT` | nonempty string | 必填 | 操作原因，最长4096字符，进入审计。 |

执行规则：已创建任务仍然有效；不得因此自动trash。

返回：producer.enabled=false。

### CMD-077 · `hive control trigger list`

列出内置触发器。

```text
hive control trigger list [--enabled BOOL]
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `--enabled BOOL` | true/false | 不筛选 | 开关状态。 |
| `--limit N` | int 1..200 | 50 | 返回条数。 |
| `--cursor TOKEN` | string | 无 | 使用前页 next_cursor，不手工解析。 |

执行规则：只管理Hive新触发器，不读取/接管旧trigger在线实例。

返回：items、next_cursor。

### CMD-078 · `hive control trigger show`

查看触发规则与游标。

```text
hive control trigger show TRIGGER
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `TRIGGER` | trigger_id | 必填 | 目标。 |

执行规则：输出最近触发、下次时间、持久去重游标。

返回：trigger、status。

### CMD-079 · `hive control trigger create`

建立内置任务生产规则。

```text
hive control trigger create --name NAME --kind KIND --producer PRODUCER --task-file FILE
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `--name NAME` | string | 必填 | 规则名。 |
| `--kind KIND` | manual/interval/cron | 必填 | 首版支持三种。 |
| `--producer PRODUCER` | producer_id | 必填 | enabled builtin主体。 |
| `--task-file FILE` | TaskSpec JSON | 必填 | 每次产生root Task；无agent身份不能callback=self。 |
| `--every D` | positive duration | interval必填 | 与cron互斥。 |
| `--cron EXPR` | five-field string | cron必填 | 分时日月周，支持*、列表、范围、步长。 |
| `--timezone TZ` | UTC/±HH:MM | UTC | V1固定偏移，无DST/IANA依赖；其他值拒绝。 |
| `--enabled BOOL` | true/false | false | 先注册再显式启用。 |
| `--missed MODE` | skip/once | skip | 重启错过多轮时跳过或合并补发一次。 |

执行规则：触发事件ID按规则ID+计划时间唯一；记录游标与入队一致，重复tick不重复Task。

返回：trigger。

### CMD-080 · `hive control trigger update`

更新触发器规则。

```text
hive control trigger update TRIGGER --file FILE --reason TEXT
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `TRIGGER` | trigger_id | 必填 | 规则。 |
| `--file FILE` | TriggerConfig patch | 必填 | 与create同字段；kind相关字段重新校验。 |
| `--reason TEXT` | nonempty string | 必填 | 操作原因，最长4096字符，进入审计。 |
| `--if-version N` | positive int | 接收时当前版本 | 有值时做乐观并发检查，过时返回 VERSION_CONFLICT。 |

执行规则：新revision影响未来触发；不改变已有任务。

返回：trigger、revision。

### CMD-081 · `hive control trigger fire`

显式触发一次。

```text
hive control trigger fire TRIGGER --event-id ID
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `TRIGGER` | trigger_id | 必填 | manual或有权限的其他规则。 |
| `--event-id ID` | string | 必填 | 同producer/trigger内唯一，重放返回原Task。 |
| `--data JSON` | JSON object | {} | 合并进模板data，不能覆盖target/source/state。 |

执行规则：manual即使enabled=false也能有权fire；定时enabled只控制调度。

返回：task_id、deduplicated。

### CMD-082 · `hive control trigger enable`

设置触发器启用状态。

```text
hive control trigger enable TRIGGER --reason TEXT
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `TRIGGER` | trigger_id | 必填 | 规则。 |
| `--reason TEXT` | nonempty string | 必填 | 操作原因，最长4096字符，进入审计。 |

执行规则：不追溯删除已产生任务。

返回：trigger.enabled=true。

### CMD-083 · `hive control trigger disable`

设置触发器启用状态。

```text
hive control trigger disable TRIGGER --reason TEXT
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `TRIGGER` | trigger_id | 必填 | 规则。 |
| `--reason TEXT` | nonempty string | 必填 | 操作原因，最长4096字符，进入审计。 |

执行规则：不追溯删除已产生任务。

返回：trigger.enabled=false。

### CMD-084 · `hive control channel list`

列出人工/外部通道。

```text
hive control channel list 
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `--limit N` | int 1..200 | 50 | 返回条数。 |
| `--cursor TOKEN` | string | 无 | 使用前页 next_cursor，不手工解析。 |

执行规则：同一人工任务可展示多个通道但不能复制Task身份。

返回：items、next_cursor。

### CMD-085 · `hive control channel show`

查看通道配置与投递概况。

```text
hive control channel show CHANNEL
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `CHANNEL` | channel_id | 必填 | 精确对象。 |

执行规则：隐藏凭证正文。

返回：channel、delivery_counts。

### CMD-086 · `hive control channel register`

注册人工或外部接收通道。

```text
hive control channel register --file FILE --reason TEXT
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `--file FILE` | ChannelSpec JSON | 必填 | 第13.5节。 |
| `--replace` | flag | false | 按name替换需显式指定。 |
| `--reason TEXT` | nonempty string | 必填 | 操作原因，最长4096字符，进入审计。 |

执行规则：外部endpoint由有权者登记；不是任意emit正文都能让服务端访问URL。

返回：channel。

### CMD-087 · `hive control channel update`

修改通道配置。

```text
hive control channel update CHANNEL --file FILE --reason TEXT
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `CHANNEL` | channel_id | 必填 | 目标。 |
| `--file FILE` | ChannelSpec patch JSON | 必填 | 凭证只引用，目标变更须重新授权。 |
| `--reason TEXT` | nonempty string | 必填 | 操作原因，最长4096字符，进入审计。 |
| `--if-version N` | positive int | 接收时当前版本 | 有值时做乐观并发检查，过时返回 VERSION_CONFLICT。 |

执行规则：重试已有delivery保留语义ID；修改endpoint进入审计。

返回：channel、revision。

### CMD-088 · `hive control channel disable`

停用通道但保留待投递。

```text
hive control channel disable CHANNEL --reason TEXT
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `CHANNEL` | channel_id | 必填 | 通道。 |
| `--reason TEXT` | nonempty string | 必填 | 操作原因，最长4096字符，进入审计。 |

执行规则：不销毁关联Task或结果，记录不可投递状态。

返回：channel.enabled=false。

### CMD-089 · `hive control human list`

人工身份查询待答任务。

```text
hive control human list [--port PORT]
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `--port PORT` | human port ID/route | 全部有权端口 | handler必须human。 |
| `--state STATE` | queued/claimed/done/fail/trash | queued | 默认待答。 |
| `--limit N` | int 1..200 | 50 | 返回条数。 |
| `--cursor TOKEN` | string | 无 | 使用前页 next_cursor，不手工解析。 |

执行规则：CLI和人工网页共用此权限与模型。

返回：items、next_cursor。

### CMD-090 · `hive control human answer`

对人工任务正式回答。

```text
hive control human answer TASK --spec-version N [answer]
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `TASK` | task_id | 必填 | 指向人工端口的work。 |
| `--spec-version N` | positive int | 必填 | 防旧页面答新问题。 |
| `--text TEXT` | string | 空 | 结果/评论文字；与 --text-file 互斥。 |
| `--text-file FILE` | UTF-8 file | 无 | 客户端读取文字，不把路径交服务端任意读取。 |
| `--data JSON` | JSON object | {} | 结构化数据；与 --data-file 互斥。 |
| `--data-file FILE` | JSON object file | 无 | 客户端解析对象，非法 JSON 返回参数错误。 |
| `--attach URI` | repeated reference | [] | 可重复；保存引用，内容访问另外鉴权。 |

执行规则：有human.answer权限的真实主体原子结算；队列任务可原子接受+done，无需伪造Agent实例。data.decision=deny仍是done。

返回：task.state=done、answered_by、receipt_ids。

### CMD-091 · `hive control human fail`

人工无法回答或废弃问题。

```text
hive control human fail TASK --spec-version N --reason TEXT
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `TASK` | task_id | 必填 | 需人工处理权限。 |
| `--spec-version N` | positive int | 必填 | 当前问题版本。 |
| `--reason TEXT` | nonempty string | 必填 | 操作原因，最长4096字符，进入审计。 |
| `--text TEXT` | string | 空 | 结果/评论文字；与 --text-file 互斥。 |
| `--text-file FILE` | UTF-8 file | 无 | 客户端读取文字，不把路径交服务端任意读取。 |
| `--data JSON` | JSON object | {} | 结构化数据；与 --data-file 互斥。 |
| `--data-file FILE` | JSON object file | 无 | 客户端解析对象，非法 JSON 返回参数错误。 |

执行规则：复用统一Task终态；不根据一条评论自动执行。

返回：task.state=fail、answered_by、receipt_ids。

### CMD-092 · `hive control human trash`

人工无法回答或废弃问题。

```text
hive control human trash TASK --spec-version N --reason TEXT
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `TASK` | task_id | 必填 | 需人工处理权限。 |
| `--spec-version N` | positive int | 必填 | 当前问题版本。 |
| `--reason TEXT` | nonempty string | 必填 | 操作原因，最长4096字符，进入审计。 |
| `--text TEXT` | string | 空 | 结果/评论文字；与 --text-file 互斥。 |
| `--text-file FILE` | UTF-8 file | 无 | 客户端读取文字，不把路径交服务端任意读取。 |
| `--data JSON` | JSON object | {} | 结构化数据；与 --data-file 互斥。 |
| `--data-file FILE` | JSON object file | 无 | 客户端解析对象，非法 JSON 返回参数错误。 |

执行规则：复用统一Task终态；不根据一条评论自动执行。

返回：task.state=trash、answered_by、receipt_ids。

### CMD-093 · `hive control test seed`

创建隔离验收夹具。

```text
hive control test seed --scenario NAME --out FILE
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `--scenario NAME` | unique string | 必填 | 本次case前缀；已存在则返回同一批对象的当前manifest并刷新实例凭据路径，不重置数据。 |
| `--out FILE` | path | 必填 | 只写ID/凭据文件位置，不写秘密正文。 |

执行规则：仅test profile+owner开放；创建main/private/human/external端口，A/B/C Agent和human/producer主体，fixture harness活实例、权限与附件样本。详见验收文档。

返回：manifest_path、scenario、objects。

### CMD-094 · `hive control test clock advance`

推进隔离测试时钟。

```text
hive control test clock advance --by D
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `--by D` | positive duration | 必填 | 只影响此test daemon的时钟源与调度tick。 |

执行规则：必须持久偏移供重启测试；生产profile返回TEST_ONLY。

返回：now、processed_deadlines、processed_triggers。

### CMD-095 · `hive control test fault set`

设置可控故障点。

```text
hive control test fault set --point NAME [--mode MODE]
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `--point NAME` | enum | 必填 | tx_before_commit/rpc_response_drop/outbox_after_commit/notification_drop/harness_start_after_launch/harness_stop_unknown/config_after_publish/event_duplicate/event_reorder/operation_before_execute。 |
| `--mode MODE` | once/always | once | once在命中后自动撤销。 |
| `--value JSON` | object | {} | 目标entity/request过滤等可选值，未知键拒绝。 |

执行规则：仅测试进程内部故障注入，不修改生产文件或网络配置。

返回：fault。

### CMD-096 · `hive control test fault clear`

清除测试故障点。

```text
hive control test fault clear [--point NAME]
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `--point NAME` | enum | 全部 | 同fault set词表。 |

执行规则：只清测试内存/持久夹具故障配置。

返回：cleared。

### CMD-097 · `hive control test harness`

控制模拟宿主状态。

```text
hive control test harness AGENT --action ACTION
```

| 参数 | 类型 | 默认 / 必填 | 含义 |
|---|---|---|---|
| `AGENT` | fixture agent_id | 必填 | 仅fixture adapter。 |
| `--action ACTION` | idle/busy/crash/stop-confirm/inspect-off/events-off/model-hot-off | 必填 | 模拟状态或缺增强能力。 |
| `--value JSON` | object | {} | 仅该动作schema允许的可选参数。 |

执行规则：不得指向真实生产harness；crash模拟已确认退出，stop-confirm用于恢复未知停止。

返回：fixture_state、events。


## 15. 补充输入 schema、API 与接入协议

### 15.1 配置文件字段全集

以下 schema 与第3/5/13节组合使用。所有配置允许省略可选字段、应用默认值；未知字段拒绝，业务扩展进入 metadata。

| 文件对象 | 允许字段及默认 |
|---|---|
| PortConfig | route（创建时CLI可提供），name=route，description=""，handler=agents，channel_ids=[]，accepted_types按handler补全，input_schema={}，defaults={urgency:normal,claim_within:null,expires_in:null}，visibility=authenticated，metadata={} |
| ProducerConfig | name必填，kind=builtin/external，allowed_targets必填，enabled=true，metadata={}；ID和凭据由系统管理 |
| TriggerConfig | name、kind、producer_id、task、every或cron、timezone=UTC、enabled=false、missed=skip、metadata={}；task使用TaskSpec，系统按事件新建根 |
| ChannelSpec | name必填，kind=human-web或webhook，endpoint对webhook必填，credential_ref=null，enabled=true，retry_policy=null，metadata={}；ID由系统生成 |
| TaskAmend | title、summary、body、data、attachments、urgency、claim_deadline、expires_at、callback；null可清空可选期限/回调，不能移除必填标题 |
| SystemPolicy | default_harness、default_model、第10.1节策略键、context_bytes_limit、rpc_body_limit、recovery_hold=false；数据库路径、home、凭据和监听地址不是在线合并字段 |
| HarnessSpec | name、adapter、start_argv、send_mode、stop_mode、default_model、capabilities、optional_capabilities、options={}、metadata={} |

PortConfig.accepted_types：agents默认四种；human默认work；external默认receipt/notice/message。human的channel_ids允许多个human-web通道，同一Task展示多处但只有一个终态；external V1绑定一个webhook通道，多接收方可用多个端口和独立轻量投递表达。**V1 external handler只承担外部结果/通知投递，不能以HTTP 2xx把外部work擅自标成done。** 对外部work执行接入可后续扩展，当前外部服务创建任务完全支持。显式给external添加work在V1返回SCHEMA_UNSUPPORTED。

Port.input_schema只校验任务data，不校验source或Task内部系统字段。支持对象properties中的string/number/integer/boolean/object/array及required、enum；嵌套不超过8层。未知关键词（如未实现的任意组合逻辑）必须拒绝。人类问题可用data.kind=question/approval、question、options[{id,label}]、required_answer_fields等业务字段；无模板仍可提交文字问答。

RetirePlan完整例：

```json
{
  "reason": "节点退出并交接",
  "stop": true,
  "work": [{"task_id":"tsk_W","action":"forward","target":"agent:agt_B","reason":"接任"}],
  "personal": [{"task_id":"tsk_N","action":"forward","target":"agent:agt_B","reason":"接任"}],
  "callbacks": {"action":"redirect","target":"agent:agt_B"}
}
```

work/personal中的action可为forward/fail/trash；terminal所需reason必填；forward需target。callbacks.action为redirect或cancel，cancel需reason。未登记方案的未来指名普通任务仍拒收；已承诺的callback按方案处理。计划可分步执行并记录进度；它不是跨宿主、跨文件、跨任务的巨大原子事务。重复同operation从进度继续。

### 15.2 本地调用与文件边界

`control system init/serve/start/restore` 是本地主机操作；生产者和普通Agent不允许远程调用。其他管理命令由服务鉴权。system backup的out是服务所在主机授权目录；producer credential、init凭据输出只允许owner在本机受保护路径写入，stdout仅有路径/ID。CLI输入文件全部先在客户端读取解析。

bootstrap只产生owner凭据文件，不在终端展示秘密。Agent通过受保护的凭据文件路径/受控工具连接调用Hive；配置生成的prompt只包含命令入口、公开地址和使用方法。JSON响应、审计、验收报告不得收集token正文。

### 15.3 HTTP API

CLI与人工页面均调用同一domain service。HTTP与CLI错误code一致，状态码只是传输映射。

| 方法与路径 | 输入 | 输出 / 说明 |
|---|---|---|
| GET /v1/health | 无 | 最小ready/version；不公开数据根/凭据；详细状态需认证 |
| POST /v1/commands | {command,arguments,request_id?} | CLI统一网关；command是命令路径以点连接，如emit.new |
| GET /v1/schema | 已认证 | 协议版本、公开命令、参数schema、配置schema；test命令仅test profile返回 |
| POST /v1/tasks | {event_id?,root或parent,spec} | producer或agent投递；走同一emit.new处理器 |
| GET /v1/tasks/{task_id} | 已认证 | 有权限者读Task；producer只能读自己产生或明确获权的内容 |
| GET /v1/human/tasks | 已认证human/owner，分页 | 人工待办列表 |
| POST /v1/human/tasks/{id}/answer | {spec_version,result,request_id} | 复用control human answer，身份来自认证通道 |
| GET /human/ | 人工界面入口 | 最小可用列表、详情、选项/文字回答；经认证访问 |

CLI规范化参数名为snake_case；位置参数按命令schema命名。`--text-file/--body-file/--data-file/--spec-file/--dispatch-file/--plan-file`分别转为text/body/data/spec/dispatch/plan。通用`--file`规范化为input，具体命令按本文规定对象schema解析。服务端不把arguments中的file字符串当成本机读取指令。`--attach`规范化为attachments引用数组。

认证优先使用Authorization携带注册主体凭据，由本地客户端读取受保护文件注入；该header不写日志。`Idempotency-Key`与body.request_id若同时存在必须相同。外部producer的event_id用于事件语义去重，和RPC request_id不同：不同请求ID但相同producer/event_id也只产生一件Task；同事件不同内容返回PRODUCER_EVENT_CONFLICT。

外部服务投递例（为协议示例，不包含真实凭据）：

```json
{
  "event_id": "order-123-created",
  "root": true,
  "spec": {
    "target": "port:incoming/work",
    "type": "work",
    "title": "处理一条外部事件",
    "body": "根据data处理",
    "data": {"external_id": "123"},
    "callback": "port:external/results"
  }
}
```

服务从producer身份确定source；body不得覆盖source、owner、state。没有callback时仍保存结果，但不为producer虚构个人inbox。

### 15.4 Webhook结果通道

外发请求体：delivery_id、receipt_task_id、source_task_id、terminal_event_id、state、result、occurred_at；必要时按已登记凭据引用附认证。接收端2xx表示接收成功；超时/5xx保持原delivery_id重试。4xx默认重试到策略上限并留dead，可由管理员修正配置重试；不把源业务Task从done改成fail。

channel_deliveries用(task_id,channel_id)唯一。人工任务多通道、多界面展示使用展示记录，绝不复制业务Task。终态后其他页面提交返回TASK_TERMINAL，不造成第二次回调。V1外部结果端口只绑定单一webhook，接收2xx后结算对应receipt投递；不会因为某一通道成功就擅自完成其他独立投递。

## 16. 错误码与重试规则

| code | exit / HTTP | 含义与调用方动作 |
|---|---|---|
| INVALID_ARGUMENT / INVALID_JSON / SCHEMA_UNSUPPORTED | 2 / 400 | 修正参数；不能原样无限重试 |
| MUTUALLY_EXCLUSIVE / REQUIRED_ARGUMENT | 2 / 400 | 参数组合或必填项错误 |
| DEADLINE_INVALID | 2 / 400 | 期限无时区、非正数或已无效 |
| CALLBACK_TARGET_REQUIRED | 2 / 400 | 非Agent使用self，或目标不明确 |
| AUTH_REQUIRED / TOKEN_INVALID / TOKEN_REVOKED | 3 / 401 | 重新取得有效凭据，不能在prompt编造身份 |
| FORBIDDEN / GRANT_SCOPE_EXCEEDED | 3 / 403 | 需要明确授权；不得换CLI绕过 |
| INSTANCE_FENCED / CLAIM_FENCED | 5 / 409 | 旧实例/旧领取代次已失效，停止旧结果提交 |
| NOT_FOUND / NOT_VISIBLE | 4 / 404 | 对外不泄露不可见资源内容 |
| VERSION_CONFLICT / TASK_SPEC_CHANGED | 5 / 409 | 重新读取并决定是否再次提交 |
| IDEMPOTENCY_CONFLICT / PRODUCER_EVENT_CONFLICT | 5 / 409 | 相同键不同内容；修正调用逻辑 |
| TASK_ALREADY_CLAIMED / TASK_TERMINAL | 5 / 409 | 不抢写，不改已有终态 |
| CAPACITY_EXCEEDED | 5 / 409 | 先处理已有责任；回执和通知不应触发work容量限制 |
| AGENT_PAUSED / AGENT_RETIRED | 5 / 409 | 不自动越过生命周期；有权者明确start或改投 |
| PORT_PAUSED / PORT_RETIRED | 5 / 409 | paused允许入队但claim拒绝；retired投递也拒绝 |
| TARGET_TYPE_REJECTED / CHANNEL_REQUIRED | 2 / 422 | 目标类型/schema/通道配置不满足 |
| WAIT_EXISTS / WAIT_CYCLE / INVALID_DEPENDENCY | 5 / 409 | 调整等待，不能形成自循环或不可见依赖 |
| RECLAIM_REQUIRED / RESPONSIBILITY_BLOCKED | 5 / 409 | 必须走明确回收/交接，不直接改owner |
| OP_NOT_CANCELLABLE | 5 / 409 | 已有效果不能简单回滚；读取进度 |
| HARNESS_NOT_CONFIGURED / HARNESS_UNAVAILABLE | 8 / 503 | 注册默认或修复适配；Agent注册/收件不丢失 |
| HARNESS_BUSY | 8 / 409 | 由Hive排队或稍后发，不能创建替身 |
| RUNTIME_OUTCOME_UNKNOWN | 7 / 503 | 未能确认调用结果时返回；已可靠受理的操作用202及operation_id，查询仍显示running/outcome_unknown |
| CONTEXT_TOO_LARGE / WORKSPACE_UNAVAILABLE | 2或8 / 422或503 | 报具体入口与大小，整理/修复后再启动 |
| STORAGE_UNAVAILABLE / TRANSPORT_UNAVAILABLE | 8或7 / 503 | 未确认提交时使用同request-id核查，不重新造单 |
| TEST_ONLY | 3 / 403 | 测试接口在生产环境禁用 |
| INTERNAL_ERROR | 1 / 500 | 返回request-id供查审计，隐藏凭据与内部栈细节 |

操作accepted用HTTP202，普通完成/查询200，新建Task201；CLI成功统一exit0。operation.show看到failed时返回ok=true（查询成功）、operation.state=failed；需要脚本判最终操作成败，不能仅看查询exit code。批量claim部分成功用ok=true+partial=true且CLI exit6，逐项含错误。

## 17. 关键组合时序与并发边界

### 17.1 根任务 → 子任务 → 回调 → 等待恢复

1. producer/owner创建根Task T1并投到端口。
2. A claim得到claim C1；用batch创建T2并注册T1等待T2，callback=self。
3. B claim T2；done时事务保存终态、事件和回执R。
4. Wait根据账本得ready；回执和等待唤醒可合并，但R仍有独立处理记录。
5. A读R与T1依赖结果，done R，再推进T1；T1不会被系统自动done。

### 17.2 转交与回调

A发T2给端口并要求回调A；B领取后forward到C，Task ID和callback不变。B旧claim不能再done。C领取后fail也给A终态回执。B的forward不产生终态回执，不生成另一个“同内容子任务”。

### 17.3 结束与派生下游

A done T1并dispatch=[T2,T3]；一个目标无权限则整笔拒绝，T1仍claimed、两个child都不存在。成功后T1=done、T2/T3=queued，整树仍有open_work。callback只按T1自己的配置生成。

### 17.4 实例替换、旧请求与失联

系统暂停新claim，停止旧实例，旧实例凭据被fence；新实例继续同agent_id的责任。旧请求即使晚到也不能修改任务；新实例对旧责任重新取得当前claim代次。最小适配器没有inspect时，停止确认来自stop实现/受管进程证据；无法确认就保留unknown占位，不能猜测然后双开。

### 17.5 无回调 + 到期 + 观测

T配置callback=null，queued超过claim_deadline，系统fail并存CLAIM_TIMEOUT。来源inbox不新增任何回执。事件、原任务、审计仍可查；端口积压告警是独立系统观测，不绕过callback设置给原发送者发消息。

### 17.6 评论、@ 与人工回答

评论@生成一条notice；读该notice不构成人工回答。人回答通过human.answer提交工作Task的done，data.decision=deny是正常答复。多通道重复提交只认一个terminal_event；其他页面明确显示已处理。

## 18. 必须保留的范围界线

- V1内核无designer/manager/leader专用分支，无自动审美/质量判定，无强制pipeline模板。
- 不要求所有harness实现状态查询、事件、MCP、压缩、原地热换模型；五项最小能力必须能组合兑现。
- 默认缺少个体配置仍能注册；有系统默认harness时能用基础prompt运行。缺模型用宿主默认，缺技能不阻塞。
- 最小YOLO档保留Hive API鉴权，但不虚构操作系统级隔离；对此文档、界面、验收应一致。
- 不从CLI调用结束推断业务Task完成，不从无日志推断会话死亡，不从父done推断全树完成。
- 不在此阶段迁移trigger-platform线上worker、复用其数据库、终止其daemon或发布外网服务。
- 下阶段实现若需要改变本文件公开语义，先记录变更原因、影响的CMD编号与AC编号，再同时更新三份文档。


## 19. 施工补充：恢复开关、batch字段和测试夹具

### 19.1 备份恢复后的管理入口

restore默认撤销快照中的旧凭据验证记录，生成目标新home的owner凭据文件（stdout仅返回位置），Agent/producer等随后显式重新发行或在start时注入凭据。--with-credentials只有显式owner才可恢复既有秘密，仍必须报告跨环境复用风险与实际授权状态。

恢复目标设置system.recovery_hold=true，禁止自动运行trigger、投递outbox与启动Agent。操作者核对外部状态后，可用control system config提交{"recovery_hold":false}和原因解除。Agent仍paused，需要逐个明确start；不因解除全局hold而一键重放所有会话。原始运行目录完全不被restore修改。

### 19.2 EmitBatch操作字段

| op | 允许字段（op之外） |
|---|---|
| new | ref可选，root或parent二选一，task=TaskSpec |
| done | task、claim可选缓存、result={text,data,artifacts}可选、dispatch可选 |
| fail | task、claim、reason必填、code=FAILED、result可选 |
| trash | task、claim、reason必填、instruction可选 |
| forward | task、claim、to、reason、claim_by或claim_within、if_version可选 |
| comment | task、text、mentions=[]、revision_of、checkpoint_refs=[] |
| wait | task、claim、on数组、mode=all、wait_for或until、checkpoint_refs=[]、replace=false |

work需要的claim可由CLI在发送batch前从本实例缓存补全，服务端始终验证有效代次。on中的$ref解析为前面new生成的task依赖；显式已有依赖用task:ID/operation:ID。每个op仍检查自己的权限，不因batch统一请求而扩大范围。comment引用原Task，@通知与普通创建同事务；raw source/owner/state字段一律不接受。

### 19.3 Fixture实现要求

control test仅在test profile+owner开放。seed必须经正常registry/runtime/auth服务创建A/B/C及主体凭据；可以使用轻量模拟harness，但不能在公共接口接受任意actor字符串来绕过认证。fixture角色和返回manifest结构以ACCEPTANCE第2.2节为准。

Clock advance推进隔离时钟并触发到期/调度扫描，不调整操作系统时间。fault set可按entity/request过滤；operation_before_execute暂停受理后执行前的操作供取消测试，fault clear后可继续未取消项。receipt生成默认不继承端口的隐藏过期时间；要废弃回执必须显式操作或明确配置。

测试profile不得读取、创建或终止旧trigger worker；真实harness smoke只操作专用测试会话与工位。fixture必须能切换关闭增强能力，证明最小接入不是依赖完整模拟P2才成立。

### 19.4 控制结果、等待恢复与身份补充

控制操作的callback同样是可选目标，默认none；希望跨回合得到结果可设self/显式目标或登记等待。控制结果receipt的data使用source_operation_id，不把operation ID伪装成task_id；有明确关联责任任务时parent指向该Task，否则该receipt作为系统根Task，reply_to为空。

等待进入ready/timed_out时产生一条wait-resume notice，语义键为wait_id+状态+当前责任代次；其parent为等待所属Task，callback为空。尚无领取者时只保存恢复事实，后续claim时把该事实提供给新责任人。它与单项终态receipt是不同记录，运行唤醒可以合并。父任务已终态则不再产生继续工作的提醒。

幂等缓存按稳定主体分区，认证始终检查当前有效身份。新实例重放旧claim请求得到历史结果不等于恢复旧凭据有效性；要接续已有责任，应以新请求重新claim自己的在途Task，取得当前实例可用的claim代次，容量不重复计算。

YOLO/共享系统账号接入档中的Hive权限是正规API调用的授权边界，不承诺抵御同宿主进程直接读取管理员文件或篡改账本。只读工位定义文件属于正常运行约束，不等同于操作系统隔离。若要求恶意进程隔离，应另加执行环境；当前五项接入验收不因此加码。
