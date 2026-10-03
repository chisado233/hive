# 2026-10-02 生产升级记录

本次电脑重启后的恢复与升级已完成。Hive 持久账本和原有 ZCode 会话保留，控制 daemon 重新接入了原 session_id；没有清理任务、claim、工位或历史。

## 当前状态

- Hive Home：`D:\agent_workspace\valuable-projects\hive\.hive`
- Hive endpoint：`http://127.0.0.1:19962`
- ZSession endpoint：`http://127.0.0.1:8765`
- Hive bridge：真实 ACP stdio，`HIVE_ACP_WRITER_KEY=1`
- ACP capabilities：loadSession=true；图片/音频/embedded context 以实际协商结果为准
- 账本：48 个可管理 Agent，47 个原有实例加 1 个恢复启动的 enabled Agent；1 个已暂停 Agent 仍保持暂停
- 项目：video-lab 23 个节点全部保留，game-line 20 个节点，xuanshu 5 个节点，acp-e2e 1 个测试节点
- ZCode 会话：原 session_id 保持，未批量 kill 或复制

## 模型路由

配置和现有会话均已切到 400k Auto 系列：

- Flash：负责人、研究、普通文案和轻量整理
- Mult：分镜、视频制作、审核
- Auto 400k：已有明确配置的辅助/暂停节点
- Free 路由没有强行替换到生产节点，避免把成本兜底模型误用于需要稳定质量或多模态的工作

当前模型配置分布记录在本次部署临时目录的 `models-applied.json`；模型切换保留会话上下文，未创建新 Agent。

## 备份

- 数据库与定义备份：`D:\agent_workspace\backups\hive\deploy-20261002-150421`
- 包含工位的完整备份：`D:\agent_workspace\backups\hive\deploy-20261002-150421-full`
- 部署检查、前后快照和claims记录：`D:\agent_workspace\tmp\hive-production-upgrade\deploy-20261002-150421`

备份不包含认证文件正文；原认证仍由受保护路径提供。

## 开机恢复

已创建 Windows 计划任务 `HiveProductionStart`，开机延迟两分钟启动：

    D:\agent_workspace\valuable-projects\hive\ops\start-production-acp.ps1

脚本显式设置 ZCode 用户 profile、真实 ACP adapter 和强 writer，然后启动 Hive daemon。ZSession 自己已有 `zsession-startup` / `zsession-health` 开机与自愈任务，Hive 任务会在其后启动并接入原会话。

## 仍需留意

- 本机单机部署，更新 Hive 控制 daemon时会有短暂控制面断线；原生 ZCode 会话继续由 ZSession 托管。
- 原生取消受宿主实际停止能力限制；Hive 保持 `cancelling`/未确认的诚实状态，不假报取消成功。
- 生产切换前应观察一次强 writer 主链：网页输入、busy 回队、工具 marker、完成回执和旧 session 不重复执行。

## 本次实际完成回执

- Hive ACP daemon 当前进程稳定监听 `127.0.0.1:19962`，bridge=`acp`、bridge_connected=true。
- ZSession daemon 继续监听 `127.0.0.1:8765`；未重启 ZCode 宿主。
- 48 个 live 实例对应的 claims 均已绑定 writer key；video-lab 23/23 均已绑定。
- 48 个 Agent 的原 session_id 保持；新增恢复启动的 Agent 为 game-line 反馈进化·蓝图审核，instance=`ins_d3a72bc201a3f00b86d5`。
- 玄枢·发布蜂仍是 paused，未被本次“恢复产线”误启动。
- 当前模型分布：`new-provider/auto-flash-400k` 31 个、`new-provider/auto-mult-400k` 15 个、`new-provider/auto-400k` 2 个；现有会话实际模型已逐项确认。
- 开机任务 `HiveProductionStart` 状态为 Ready；ZSession 原有开机/自愈任务仍在。

