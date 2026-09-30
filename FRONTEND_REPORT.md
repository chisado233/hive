# Hive 蜂群指挥台（前端）— 交付报告

日期：2026-10-01 · 版本：webui v1 · 面向：Hive V1（已签收底座）+ video-lab（23 同位体已注册）

## 1. 完成范围

在 Hive 项目内实现并验证了完整可用的可视化前端「蜂群星图 + 人类决策台」，
全部功能基于真实后端机制，无静态展示、无模拟数据、无假按钮。

- **蜂群星图**：同一 execution_root 的 Agent 聚合为一张图；Agent=圆形、资产库=方形、
  人=小人、端口=小菱形（仅在结构视图或有任务压 queue 时出现）；区域标题（项目负责人/
  研究节点/Skill 测试入库线/反馈进化线/正式生产线/共享资产库/人类决策台）为背景标注。
- **人类收件箱**：待处理/处理中/已回复/已结束 四态；未读徽标；进入消息保留任务上下文
  （父任务/回应对象/评论线程）；新消息不打断正在编辑的草稿。
- **富内容协议** `data.ui`（schema `hive-ui/1`）：Markdown/图片/视频/音频/文件块 +
  单选/多选/评分滑块/文本字段（必填、范围、步长、帮助说明），旧任务无 ui 描述时
  如实降级为正文+附件+自由回复。
- **双重评价界面**：识别 video-lab TASK_CONTRACT §3 模板键自动渲染产物/审核双评分 +
  判决 + 保留/修改/误判/遗漏 + 自主授权框（独立操作、默认不授权）。
- **回复与裁决分离**：评论走 emit.comment（不结算）；裁决走 control.human.answer
  （spec_version 校验、required 前后端双重校验、回执与等待恢复）。
- **资产库浏览**：项目 assets/ 目录树、README 内联、媒体预览、方法 candidates/releases
  与 active.methods 当前采用版本对照。
- **Agent 操作**：发消息/发任务/唤醒/启动/停止/重启/切换模型/退役（dry-run 列出未结
  责任并要求逐项处置）。受理与完成分态反馈（operation 长轮询）。

## 2. 关键设计

### 2.1 架构：同源适配层 + 零构建前端

```
浏览器 ──同源──> hive_core/webui.py（/ui 静态 + /ui/api/* 适配）
                      │ 全部业务效果走 Service.dispatch → 既有 handlers
                      ▼
                 Hive 账本（SQLite）不变
```

- **不加第二套真相源**：任务/审批/身份/评分全部复用 Hive 既有命令与账本；
  适配层只做聚合读模型、会话、文件存取与 UI 字段校验。
- **零第三方依赖、零构建**：前端为原生 ES modules + SVG，直接由 daemon 托管；
  Python 侧仍是标准库。Markdown 渲染器全程 DOM 构建（textContent），
  链接仅放行 http(s)，不执行消息中的脚本。
- **凭据不落地浏览器**：登录时粘贴 token → POST /ui/api/session → 服务端签发
  HttpOnly + SameSite=Strict 会话 cookie（kv 持久化，7 天滑动过期）。
  token 不进 JS 存储/URL/代码。变更请求要求 `X-Requested-With: hive-ui`
  自定义头（CSRF 防线）。Agent 凭据登录被明确拒绝。

### 2.2 适配层新增接口（均在 /ui/api/ 下，复用权限检查）

| 端点 | 说明 |
|---|---|
| POST /ui/api/session · /logout · GET /session | 会话签发/销毁/查询 |
| GET /ui/api/overview?since= | 星图聚合：projects/agents(含runtime摘要+实际模型)/ports/subscriptions/flows(在途+15min内终态)/人工计数/事件增量。分区降级：缺权限的区返回错误说明而非整体 403 |
| GET /ui/api/agent/:id | agent.show(config+runtime) + 相关任务 + 等待 + 实际模型落点（agent.started 事件） |
| GET /ui/api/task/:id?mark_seen=1 | task.show(history) + 评论 + 上下文任务 + 已读登记 |
| GET /ui/api/human/inbox?state= | 人工队列水合列表（含 data/attachments/urgency/已读） |
| POST /ui/api/human/answer · /fail | 转发 control.human.*；先按 data.ui.fields 重新校验必填/范围/步长/选项集合 |
| POST /ui/api/comment | 转发 emit.comment（评论 ≠ 结算） |
| POST /ui/api/upload | 原生字节上传 → `<home>/webfiles/<file_id>/<name>`，返回 ref 与真实路径 |
| GET /ui/api/file/:id | 受权下载/预览，**支持 HTTP Range**（视频拖动/分段加载） |
| GET /ui/api/task-file?task=&uri= | 任务附件受权访问：uri 必须属于该任务（attachments/result.artifacts/ui.blocks/checkpoint_refs）且在受管目录（项目根/Hive home）内，否则 403/原因说明 |
| GET /ui/api/project/:key/assets?path= · /file · /info | 项目资产浏览，严格 containment，不开放任意本机路径 |
| POST /ui/api/command | 白名单命令转发（inbox/emit/agent 生命周期/operation 等 41 条；test/system 管理类不在列） |

幂等：所有变更由前端生成一次 request_id 随草稿固定，重试沿用——
同一 request_id 重放返回首次结果，不产生第二份答案（已实测）。

### 2.3 星图语义

- **身份稳定**：节点键 = agent_id；同位体各自独立；拖动位置按
  `用户×项目` 存 localStorage（视图偏好，非凭据），daemon 重启/浏览器刷新均保持；
  「恢复自动布局」一键还原。无实例的 Agent 以虚线低亮环保留在图中。
- **两种视图避免毛线团**：默认「当前任务」只画在途/近期任务流（实际方向：
  投递→领取；终态→回调目标的回执虚线）；「结构关系」画订阅（agent→port 菱形）
  与声明的资产读取（agent→资产库，虚线，来自 config context_entries 的真实推导）。
  无文件访问事件时只展示声明关系，不伪造实时读写动画。
- **选中一跳聚焦**：点击节点淡化无关边与节点；任务线可点击打开任务列表。
- **事件脉冲**：task.created/task.terminal 沿对应连线跑一次光点（SMIL animateMotion，
  一次性，非常驻）；agent 状态变化时外环闪烁一次；`prefers-reduced-motion` 下全部关闭。
- **诚实状态**：live 但无法观测 = 琥珀环 + "unknown"；观测 idle/busy 才上绿/青；
  无原生观测渠道明确显示"未知"，不冒充空闲。

### 2.4 双重评价与授权

- 检测到 `artifact_score`+`review_score` 键即渲染双栏：产物判决（通过/返工/放弃）、
  产物评分、审核评价（认可/部分认可/不认可）、审核评分，以及 keep/change/
  review_agreements/review_disagreements/review_omissions 列表与授权框。
- 提交结果按 TASK_CONTRACT 字段名入账（result.data），说明文字进 result.text，
  附件走 result.artifacts（真实路径，Agent 可直接读文件）。
- 滑块未触碰 = null（未评分），与 0 分严格区分；审核 Agent 预测分仅以紫色标注
  "仅供参考"，永不预填到人评。
- `explicit_autonomy_grant` 独立勾选 + 范围 + 依据，默认 null；分数再高也不产生授权。
- 拒绝提议 = done + artifact_decision=rework/abandon，不误映射为 fail。

## 3. 验证结果（全部在真实 video-lab home 上执行）

| # | 验收链路 | 结果 |
|---|---|---|
| 1 | 同项目根 23 个 Agent 聚合一张图 | ✅ video-lab 一图 25 节点（含资产库/人类节点），execution_root 规范化分组 |
| 2 | 同位体身份独立；重启后身份与布局不变 | ✅ 多次 daemon 重启（含代码升级）节点身份不变；拖动位置重载后保持 |
| 3 | 点击 Agent 看真实任务/模型/工位/状态 | ✅ 概览含 agent_id/项目标识/运行根/工位/实例/配置模型路由/实际模型落点(new-provider/auto-flash-400k，取自 agent.started 事件) |
| 4 | 创建发给人的任务，前端收到并显示 | ✅ emit.new→人工端口→收件箱列表/徽标/图节点徽标实时出现 |
| 5 | 单任务含 Markdown+选项+双滑块+图片+视频 | ✅ tsk_7474791564f3b2fe8489 全要素渲染（表格/引用/代码、缩略图灯箱、视频原生播放+Range 拖动） |
| 6 | 回复文字+上传附件，Agent 能收到 | ✅ 上传→answer→result.artifacts 含真实路径；评论走 checkpoint_refs |
| 7 | 双评分+裁决入账并触发回执 | ✅ tsk_d14573192d472c1b8286 done，契约字段全入账；回执 tsk_0b0d82f674f5c6f226c4 → 项目负责人 |
| 8 | 普通评论不误结束审批 | ✅ 评论后任务仍 queued |
| 9 | 视频时间码批注可追溯版本 | ✅ video_notes[{t, text, artifact, version:"v1"}] 入账 |
| 10 | 重复提交/断线重连/版本冲突/权限拒绝 | ✅ 同 request_id 重放返回同一评论（库中仅 1 条）；断线显示"连接中断，重试中"+恢复后无重复；旧 spec_version 提交 → 409 TASK_SPEC_CHANGED 草稿保留；Agent 凭据登录被拒、缺 CSRF 头被拒、越权路径 403 |
| 11 | 桌面与窄窗口可用 | ✅ ≤980px 详情变底部抽屉，选中高亮保留，文字清晰 |
| 12 | 无虚假状态/假评分/样子按钮 | ✅ 观测未知显示"未知"；预测分仅标注；不可用功能显示真实错误 |

额外真实验证：投递给「Skill测试·视频制作」的演示任务被真实 Agent 领取并完成
（result 为 ffprobe 实测数据：9.0s/1280x720/H.264/3,587,541B——与生成文件精确一致）；
「唤醒」操作返回真实语义（已完成·唤醒请求已合并）。

## 4. 启动命令与访问路径

```bash
cd D:\agent_workspace\valuable-projects\hive
python cli/hive.py --home .hive control system start     # daemon 127.0.0.1:19962
# 浏览器打开
http://127.0.0.1:19962/ui/
# 登录：粘贴 .hive/auth/owner.json 中的 token（仅建立会话，不落地浏览器）
```

dev/测试环境：`--home work/webui-dev --listen 127.0.0.1:29963 --test-mode` 同样可用。

## 5. 受环境限制 / 未实现

- **浏览器内文件选择器**：IAB 自动化不支持驱动原生 file chooser，
  上传控件的 UI 路径经接口等价验证（curl 实测上传+入账），XHR  wiring 为真实代码。
- **声音通知**：默认静默（按设计要求）；未做开关 UI（事件带与徽标承担提醒）。
- **多项目同图**：项目切换器已支持多注册项目；当前环境仅 video-lab 一个真实项目。
- **外部渠道回执（owner 非 Agent）**：owner 不能登记 callback=self（Hive V1 语义），
  人发给 Agent 的任务结果由指挥台主动查看（任务详情/事件带），不向来源推终态通知。
- Agent 观测值依赖 harness observe；zsession 当前回报 unknown，界面如实显示。

## 6. 文件清单

```
hive_core/webui.py        后端适配层（约 950 行）
hive_core/server.py       +/ui 路由接入（GET/POST 委托）
hive_core/ids.py          +file_ 前缀
hive_web/index.html       页面骨架
hive_web/styles.css       设计系统
hive_web/js/              util / api / markdown / store / graph / content /
                          composer / inbox / detail / assets / main
```

回归：`python scripts/quicktest.py` 全过（ALL QUICK TESTS PASSED）。
