# Hive 项目部署与启动入口

维护日期：2026-10-01。给负责把已经设计好的项目接入真实Hive的启动者。

完整操作、PowerShell示例与故障接续见本机技能库的 [DEPLOY.md](../../capability-library/skill-library/hive/DEPLOY.md)。项目设计与配置编写先读 [Hive 使用技能](../../capability-library/skill-library/hive/SKILL.md)。

## 1. 带齐本项目参数

从项目DESIGN/DEPLOYMENT/台账读取：PROJECT_ROOT、Hive home和endpoint、凭据文件位置、Agent配置源与独立工位、harness与模型、端口/订阅/权限、人工通道、激活范围及第一项任务。凭据正文不写在这些文件中。

使用 `python -X utf8 D:/agent_workspace/valuable-projects/hive/cli/hive.py`，或已实际登记且核对过的包装入口。mycli skill-library负责检索技能，不等于已存在mycli hive业务命令。

## 2. 按依赖接线

| 顺序 | 工作 | 完成依据 |
|---|---|---|
| 1 | 复用或按已有配置启动目标Hive服务 | 实际home/endpoint与状态明确 |
| 2 | 建立人工/外部通道及项目端口 | 返回真实channel_id/port_id，接收规则正确 |
| 3 | 导入或复用Agent | 真实agent_id、配置revision、独立工位可追溯 |
| 4 | 解析绑定并授权 | 实际订阅存在；应用selector已转换为真实resource |
| 5 | 按要求启动节点 | 控制操作完成，实例唯一，实际cwd/工位分开 |
| 6 | 领取并完成初始化任务 | Agent以自己的身份回报接续信息与READY产物 |
| 7 | 投递本项目首项工作 | 输入、产物、回调/等待和预算有明确约定 |

新项目不使用测试fixture冒充真实运行。已存在ID和对象应复用，不因重启/重复部署创建一批同名替身。

## 3. 执行目录与工位

当前实现支持：

```text
runtime.options.execution_root → 会话实际运行根目录，可由本项目多个Agent共用
workspace.path                 → 当前Agent独立工位，不能被其他Agent共用
--home                         → Hive账本与运行数据根，必须指向实际服务
```

系统生成工位agent.md/prompt.md/config.json；导入源独立保存在项目目录。通过context_entries加载必要共同规则。不要把个体定义文件、旧STARTUP.md或长历史写到共享运行根反复加载。

## 4. 启动与待命

用户明确要求部署/启动即是本项目范围内必要注册、接线与启动的依据。只要求文件时不启动。较多节点按项目计划分批启动；无任务时保持0实例或空闲，按用户/项目策略决定。

初始化报告真实agent_id、instance_id、cwd、工位、模型配置、输入端口和未完成绑定。操作accepted或CLI exit0不等于宿主动作已完成。

周期触发器与对外发布按项目明确授权启用。审美、方法、评分未形成时保留真实空状态，不将初始化成功当作业务已成熟。

## 5. 留下可接手的报告

在项目根记录STARTUP_REPORT.md：实际连接、ID映射、版本、启动状态、初始化证据、人工入口、首项Task、未完成项以及停止/恢复方式。

普通启动只确认本次接线，不重复全套系统验收。停止结果未知时先核实原实例，不新开替身；退役需交代任务和回调，不默认删除工位或历史。
