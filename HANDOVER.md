# Hive 交接文档

> 写于 2026-09-28，hive 设计阶段。本文档是 hive 项目的**唯一基准**——设计讨论的
> 全部定稿沉淀于此，实现与后续讨论以此为准。
> 前身：trigger-platform（V1/V2，见其 HANDOVER.md 与 DESIGN-V2.md——进入维护态）。

---

## 一、项目是什么

**Hive = 基于 ZCode P2 会话编程能力的蜂群自动化操作系统。**

- 名字定案：hive（蜂巢/蜂群智能）。备选 neuros 落选（"养一张网"vs"网会思考"，owner 选前者）
- 一句话架构：**任务队列（岗位/业务/血缘）× 个人队列（投递/通知/状态）× 订阅（编制）× 五态 agent × keeper（蜂后守护进程）**
- 蜂群隐喻全映射：蜂后=keeper（不思考只维持巢序）、工蜂=worker（不知巢只知采蜜）、筑巢蜂=designer、监工蜂=manager、决策蜂=leader、蜂群意识=owner

### 与 trigger-platform 的关系

| | trigger-platform | hive |
|---|---|---|
| 状态 | **维护态**（V1 存量 13 worker + 玄枢命理/超级AI大玩家两项目继续跑，不动） | 绿地新建 |
| 宿主假设 | 原生 ZCode（会话不可编程——cron/wait 都是残疾补偿） | ZCode P2（32 种会话操作 + events + RBAC） |
| 运行时 | wait 长轮询 | 通知式领取（inbox） |
| 代码继承 | — | **约 60% 照抄**：store 账本内核/血缘/终态/原子 claim/emit 拦截器/pipeline 引擎/PROMPT.md 生成器/projects 注册/net 名片/cloudsync+approvals-hub 整套/前端骨架 |
| 位置 | `valuable-projects/trigger-platform/` | `valuable-projects/hive/`（新建） |

### 旧的残疾补偿 → P2 正解（迁移总表）

| 旧机制 | 补偿什么 | P2 正解 |
|---|---|---|
| v5 cron 六步心跳 | 会话不可自检 | keeper 事件驱动秒级自愈 |
| crontime 错峰 | 海量 cron 挤爆整点 | 无海量 cron，整表删除 |
| CronCreate title 改名 | 唯一改名通道 | `rename` 幂等 |
| 清退五步 worker 自办 | 会话只能自停 | keeper：rename 已清退→kill→archive |
| 待命池（人手开 20 会话） | 只有人能开会话 | `spawn` 批量拉起 |
| --front 等 wait | 无法直达干活中会话 | `send --interrupt` |
| 会话记忆膨胀 | 无解 | `compact` |
| manager 30min 轮询 | 会话层不可见 | events + get + messages 内容审计 |
| claim-then-ignore 盲区（等 debt 2h） | 黑盒 | P2 idle × 账本在途 = 分钟级捕捉 |

---

## 二、核心架构

```
┌─ ZCode P2（会话 OS）─────────────────────────────────────┐
│  32 操作：spawn/kill/rename/archive · send(--queue/--int) │
│  set-model/compact · queue ops · goal · events/webhook   │
│  RBAC: owner/staff/observer/none · audit                  │
└───────────────┬───────────────────────────────────────────┘
                │ events(webhook) ↓   API 调用 ↑
┌───────────────▼───────────────────────────────────────────┐
│  HIVE DAEMON                                              │
│  ┌─────────────┐  ┌───────────┐  ┌────────────────────┐  │
│  │ keeper      │  │ 业务核心   │  │ P2 client          │  │
│  │ (事件循环+  │  │ 任务队列/  │  │ (32操作封装,        │  │
│  │  状态机+    │  │ 账本/血缘/ │  │  owner凭据)         │  │
│  │  异常手册)  │  │ 原子claim/ │  │                    │  │
│  │             │  │ pipeline/  │  │                    │  │
│  │ 零LLM纯规则 │  │ net名片    │  │                    │  │
│  └─────────────┘  └───────────┘  └────────────────────┘  │
│  个人队列 × 订阅表 × agents 表（状态机挂载点）              │
└───────────────┬───────────────────────────────────────────┘
                │ cloudsync（照抄 trigger）
┌───────────────▼───────────────────────────────────────────┐
│  approvals-hub（HK，照抄复用）→ https://approval.codex888.vip │
└───────────────────────────────────────────────────────────┘
```

### 双层队列（本设计最核心的抽象）

```
任务队列（业务域：干什么）           个人队列（投递域：发给谁）
├ 视频制作.design                  ├ 小张@design（订阅:design）
├ 视频制作.produce                 ├ 老王@design+produce.review
├ 【worker】图书管理员              ├ 阿梅@图书管理员
│                                  │
· emit 的目标（业务语义不变）        · spawn 时自动建立（每个 agent 必有）
· 账本/血缘挂这层                   · 通知/指名消息投递到这层
· 原子竞争发生在任务队列             · inbox 是这层的聚合视图
· 永不过期直到被领/终态             · 通知可过期（任务被领走即作废）
```
订阅关系 = 编制本身。designer 的"派岗" = 建立订阅 + spawn。
个人队列**只放通知和指名消息**，可领任务永远只在任务队列（无积压脏状态）。

### 三张新表（在 trigger 七表基础上）

```
agents         agent_id · 个人队列名 · 会话句柄(P2) · 状态 · 模型 · 工位路径
subscriptions  agent_id · 任务队列名 · 角色(work/review) · 建立时间
tasks          原有全部列 + assigned_to（领取者，nullable）
```

---

## 三、消息类型学（个人队列里流动的六类）

| 类型 | 例子 | 投递 | 时效 | 来源 |
|---|---|---|---|---|
| ① task | "design 有单脚本" | **不直接投**——只在任务队列，投的是通知 | 永不过期 | emit 方 |
| ② notice | "你有 3 单待领(1紧急)" | 空闲订阅者群发(带抖动) | 可过期 | keeper |
| ③ directive | "停工"/"宪法已更新" | 指名单发(P2 send) | 不过期·幂等 | designer/owner/keeper |
| ④ receipt | 服务回执 | 指名(--to 个人队列) | 不过期 | 服务方 worker |
| ⑤ ask | 选题三选一 | 人(云端页)——不进个人队列 | 等人 | worker |
| ⑥ report | 体检/日报/阶段汇报 | 上级个人队列(异步批量看) | 不过期 | manager/leader/worker |

**通知可丢，队列是真相源**——自愈 = 重发通知，不依赖长连接。
紧急度三档：urgent（--front 抢占通知+置顶）/ normal / low（idle 超 N 分钟才通知）。

---

## 四、通知式领取协议（替代 wait）

```
① keeper：任务队列有货 × 订阅者 idle → 群发通知到个人队列
② agent：hive inbox list → 聚合视图（指名给我的 + 我订阅的可领的）
③ agent：hive inbox claim <id>... → 原子领取（单/复数）
④ agent：干完 → emit 下游 / done / trash（三动词只换 wait 一个）
```

- **agent 可以绕过通知直接领**（勤蜂不等闹钟；notified 也可弃权回 idle）
- **状态机约束 keeper 的投递，不约束 agent 的自由**
- 竞争领取保热备语义：群发通知 → 先 claim 先得 → 他人通知自动过期
- 防蜂拥：通知带 0-30s 抖动；大量同闲时按订阅顺序错峰

### inbox 视图（agent 的决策界面）

```
hive inbox list
  ┌ 指名给我的（个人队列）────────────────
  │ 📨 receipt  图书管理员  查询结果已回   2min
  │ 📣 directive designer  宪法更新请重读  10min
  └ 可领取（我订阅的队列）────────────────
    🔥 urgent  视频制作.design  紧急返工单  等8min
    📋 normal  视频制作.design  日常脚本单  等2min
  状态: idle · 在途1单 · 无欠账
```
命令族：`hive inbox list / claim <id>... / peek <id>`
（任务简介字段化——"任务要有简介说明条件"——owner 提出但细节未定，待聊）

---

## 五、Agent 状态机

### 五态 + 派生标记

```
idle ──→ notified ──→ working ──→ idle
  │          │            │
  │          └(过期/弃权)  └→ awaiting_human ─(ask 答复)→ working
  └────────(crashed 任意态)→ [respawn] → idle
```

**状态没有"声明"，只有"推导"**——agent 不上报状态，keeper 从双源事件 fold 出来：

| 源 | 看什么 |
|---|---|
| 会话源(P2) | get 状态/最近消息/回合/上下文用量 |
| 账本源(daemon) | claimed 在途/ask pending |

**会话源定"身体在干嘛"，账本定"责任了没有"**——冲突逐案裁决：

| 会话 | 账本 | 裁决 |
|---|---|---|
| running | 无在途 | working（干不领单的活） |
| idle | 在途<10min | working（收尾宽限） |
| idle | 在途>10min | **幽灵！debt 标记 → 异常手册#1** |
| idle | pending ask | awaiting_human |
| crashed | 任意 | crashed 赢 → respawn |

派生标记：debt（欠账）/ stall（卡死嫌疑）/ 额度受限 / 长活免打扰 / 新蜂保护期

---

## 六、Keeper 规格（蜂后）

**身份**：hive daemon 内置非智能守护进程（纯规则零 LLM），事件驱动+周期对账。
**职责一句话**：维持每只蜂的身体与账本一致。它是匹配器和执行器，不是分配器。

### 两个耳朵 + 慢通道

```
快：①P2 webhook(spawned/idle/message_done/crashed/archived/model_changed)
    ②daemon 内部事件(TaskEnqueued/Claimed/Disposed/AskCreated/AskAnswered)
慢：每 60s 三方对账（P2 get 全表 × 账本 × keeper 状态表）——事件丢失兜底
```

### 正常循环

| 触发 | 动作 |
|---|---|
| TaskEnqueued(Q) | 订阅者筛可领态 → 群发通知(抖动) → 记通知簿 |
| TaskClaimed | 通知过期；→ working |
| 在途清零 | → idle；订阅队列有货 → 再通知 |
| AskCreated | → awaiting_human（不打扰） |
| AskAnswered×会话idle | 答案重投 send --queue（wait 时代答案丢了就丢了，现在可重投） |
| P2.crashed | respawn 剧本 |
| 上下文>80% | compact + 通知"已压缩继续"（agent 无感） |

### 异常手册（全表）

| # | 检测 | 问（第一响应） | 裁（第二） | 保任务（第三） |
|---|---|---|---|---|
| 1 | idle+在途>10min | interrupt 三选一("交代/在干/做不了") | 15min 无响应→kill+respawn 附在途清单 | respawn 后 30min 无交代→任务回收重派+上报 |
| 2 | running>24h | get 回合摘要判读 | 长活→免打扰；循环→#1 流程 | 同#1 |
| 3 | claim 后从未起回合 | 直接 interrupt（跳过宽限） | 同#1 | 同#1 |
| 4 | ask已答+会话idle | 答案重投 | 重投后 15min 无动→#1 | 同#1 |
| 5 | crashed+在途 | — | respawn（宪法+在途清单+"读工位runs/捡起继续"） | 二死同单→任务有毒→回收报designer |
| 6 | archived+在途 | — | 不复活；任务回收重派 | 报 designer 补员 |
| 7 | crashed>3/h | — | 标额度受限：停通知 | 报 manager 提案换池（不自动换） |
| 8 | 通知5min无人领且有idle订阅者 | 补发(扩大范围) | 连续3轮→"有岗无人"报manager | — |

**三铁律**：先问后杀（🟡必给15min自辩）；杀蜂不杀任务（任务永远回队或换蜂）；
人可以停账不能烂（停顿不消灭责任，责任了结只有三种：agent emit / keeper 代 trash
带原因 / designer 决策回收）。

### keeper 不做（判断权边界）

不选通知发给谁（群发+竞争）；不决定换池/加岗/清退（报 manager/designer 提案）；
不代批 ask（leader）；不碰业务数据内容；不修 P2/ZCode（它自己也归看门狗管）。

### 策略参数（全部可配）

```
宽限10min·自辩窗15min·respawn观察30min·抖动0-30s·补发轮5min·对账60s
上下文阈值80%·长活免打扰24h·crashed循环线3/h·新蜂保护期15min
```

### P2 依赖降级（三档都要）

① P2 不可达 → keeper 冻结全部处置（只记账，恢复后补执行）
② 业务层无损降级（任务照常入队，agent 仍可 inbox list 自领——主动性兜底）
③ 看门狗双向（daemon 看门狗照旧 + P2 健康探测）

---

## 七、角色编制（六位，按职责原子×权限划分）

| 角色 | P2 权限 | 职责 | 对应旧角色 |
|---|---|---|---|
| **worker 工蜂** | none（会话层）+ 业务三动词 | wait→inbox 族/emit/ask，按宪法干活 | worker/review |
| **keeper 蜂后** | staff（宿主进程） | 会话监护/资源维护/对账/生命周期执行/拦截 —— 全自动 | cron六步+supervisor+manager机械半 |
| **manager 监工蜂** | observer 只读 | 体检报告 + 加减岗提案权（不执行） | manager判断半 |
| **designer 筑巢蜂** | staff | 组网设计/文档维护/改网/编制决策/给keeper下工单 | designer |
| **leader 决策蜂** | observer+answer | TASTE 学习/白名单代批/日报 | leader |
| **owner 蜂群意识** | 全部 | 终裁（终审放行/方向/花钱）+ 一切否决 | owner |

关键拆分：
- manager 拆两半：机械半→keeper（零LLM零成本），判断半→manager（读报告出提案）
- D2 拆发起与执行：designer 决定"清谁/换什么"（判断），keeper 执行 spawn/kill/rename（机械）
  ——designer 通过**给 keeper 下工单**动手，自己不碰会话 API
- worker 彻底纯化：无自举无心跳，宪法只剩五区（身份/语境/岗位/纪律/安全），
  上岗区整段删除——出生带名带宪法（spawn 注入），死了蜂后补（crashed→respawn）

---

## 八、命令族总表（hive.py）

```
三动词层（业务，继承改造）：
  hive inbox list / claim <id>... / peek <id>      ← 替代 wait
  hive emit <队列>... --task --to <agent> --urgency --data/--file --prompt --free --reason
    （--to=指名进个人队列；--front 并入 --urgency urgent）
  hive ask ...（原样继承，含 --attach）
  hive answer <ask_id> --text --by                  ← leader 代批

查询观测（原样继承）：
  hive tasks --worker / health / lineage <id> / task <id> / net / whois
  hive asks --answered-since/--pending-older-than
  hive agents [--filter 状态/模型/队列]              ← 新：状态机查询
  hive subs <队列>|<agent>                           ← 新：订阅查询

编制管理（designer 域）：
  hive spawn --queue <任务队列> --model <池> --n <数> --name <模板> ...（→keeper 工单）
  hive retire / dismiss / reassign                   ← 清退（keeper 执行）
  hive project register/list/show/enable/disable     ← 原样
  hive pipeline install/...                          ← 原样

keeper 运维：
  hive keeper status / params / audit / events
```

---

## 九、继承清单（trigger → hive 照抄/改造/抛弃）

**照抄（~60%）**：store 账本七表+V2列 / 原子claim / 血缘终态disposition /
emit 拦截器+防忘400+系统队列 / pipeline 引擎(join/审查/max_rework/run) /
PROMPT.md 生成器(删上岗区) / projects 目录注册 / net·whois / cloudsync+approvals-hub 整套 /
前端骨架 / 44 个单测大部分

**改造**：CLI 更名 trigger.py→hive.py / wait→inbox 族 / --front→--urgency /
emit 加 --to / 新三表(agents/subscriptions/assigned_to) / 前端改数据源

**抛弃**：wait 长轮询 / crontime 整表 / v5 心跳 / 统一待命提示词+待命池薄派岗 /
清退五步模板 / 解散模板 / 特殊角色自举序言律令 / 大面积掉线预案(人版，机器版替代)

**保留不动**：daemon 看门狗（Windows 计划任务，keeper 也归它管）/
云端审批页 / RBAC 对应的 worker=none 铁律

---

## 十、已定决策记录

| # | 决策 | 日期 | 备选落选原因 |
|---|---|---|---|
| 1 | 项目名 hive | 09-28 | neuros(更贴但owner选"养网"意象)/bees(单复数别扭) |
| 2 | trigger 维护态，hive 绿地新建 | 09-28 | 边跑边换心脏风险大 |
| 3 | 双层队列（任务/个人） | 09-28 | 解开"通知发给谁"与指名通道 |
| 4 | 通知式领取替代 wait | 09-28 | 不依赖后台任务稳定性；自愈=重发通知 |
| 5 | 状态是推导不是声明（双源fold） | 09-28 | 杜绝谎报军情 |
| 6 | keeper 住 daemon 内置 | 09-28 | 事件零延迟+RBAC在宿主侧 |
| 7 | manager 拆机械/判断两半 | 09-28 | 机械半零LLM，判断半才需要智能 |
| 8 | worker=none 权限+宪法瘦身五区 | 09-28 | RBAC硬约束替代提示词自觉 |
| 9 | 异常三铁律（先问后杀/杀蜂不杀任务/账不能烂） | 09-28 | 全部来自 test 实战教训 |
| 10 | 消息六类型学（task/notice/directive/receipt/ask/report） | 09-28 | 投递策略按类型分 |
| 11 | 竞争领取（群发通知+先claim先得） | 09-28 | 保热备；keeper 是匹配器不是分配器 |
| 12 | P2 降级三档 | 09-28 | 冻结处置/业务无损/看门狗双向 |

## 十一、待聊清单（按优先级）

1. **P2 对接层契约**——keeper 依赖的 ~8 操作的调用契约与失败模式（ keeper 规格已定，
   下一个自然话题）
2. **任务简介字段化**——owner 提出"任务要有简介说明条件"（领单决策的数据），
   细节未展开：字段（标题/条件/预期产出/预估难度？）/emit 时填还是 keeper 生成/
   inbox 展示规格
3. **designer↔keeper 工单协议**——spawn/retire/reassign 的请求格式与回执
4. **manager/leader 在 hive 的重塑**——新体检内容（含 messages 内容审计）/TASTE 接入状态机
5. **V1→hive 存量迁移策略**——13 worker 双轨自然死亡 or 主动迁（未拍板）
6. **goal 系统与 queue followupMode**——P2 新能力，后置试水
7. **前端**——本地看板改数据源 + 与 P2 web UI 的关系（互补不合并已初定）
8. **approval 云端页是否嵌 P2 会话观看**——待聊

---

## 十二、当前状态

- trigger-platform：维护态运行（存量正常，test 项目已清退归档，资产保留）
- hive：**设计阶段**，本文档=已定稿部分；代码未动工
- P2：ZCode 侧开发中（32 操作清单已由 owner 提供，需求提示词已交付）
- 下一步：按"待聊清单"逐项定稿 → 写 DESIGN 定稿 → 动工

---

*本文档由 2026-09-28 设计讨论沉淀。执行中若发现与现实冲突，回来改本文，不改口头记忆。*
