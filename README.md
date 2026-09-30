# Hive V1

通用自动化脚手架：**稳定 Agent + 统一 Task + 三入口 CLI**。依据
[DESIGN.md](DESIGN.md) / [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) /
[ACCEPTANCE.md](ACCEPTANCE.md) 施工，零第三方依赖（Python 3.11+ 标准库）。

## 项目搭建与使用技能

新项目搭建者先读本机 [Hive 使用技能](../../capability-library/skill-library/hive/SKILL.md)，或运行 `mycli skill-library search hive` 定位。启动者读 [DEPLOYMENT.md](DEPLOYMENT.md) 和技能中的完整 [DEPLOY.md](../../capability-library/skill-library/hive/DEPLOY.md)。

项目设计必须包含Agent运行守则，见 [DESIGN.md 第20节](DESIGN.md#20-项目搭建部署与-agent-使用守则)。共享运行根使用runtime.options.execution_root，每个Agent继续保留独立workspace.path；配置导入源与工位生成视图分别维护。

## 30 秒启动

```bash
cd D:\agent_workspace\valuable-projects\hive

# 1) 初始化独立数据根（生产模式，默认端口 19962）
python cli/hive.py --home .hive control system init

# 2) 后台启动 daemon（Windows 隐藏窗口；stdout 只含 PID/endpoint）
python cli/hive.py --home .hive control system start

# 3) 用 init 生成的 owner 凭据执行命令
python cli/hive.py --home .hive --credential-file .hive/auth/owner.json \
    control system status
```

验收/测试环境把 `--test-mode` 打开（test profile 才开放 `control test *`
夹具命令）：

```bash
python cli/hive.py --home work/acceptance/demo control system init \
    --listen 127.0.0.1:29962 --test-mode
python cli/hive.py --home work/acceptance/demo control system start \
    --listen 127.0.0.1:29962 --test-mode
```

## 最短操作路径

1. **启动**：如上 init + start。
2. **创建 Agent**：
   ```bash
   export HIVE="--home .hive --credential-file .hive/auth/owner.json"
   python cli/hive.py $HIVE control agent create --name worker1 \
       --harness fixture-minimal          # 或已注册的真实 harness
   ```
3. **创建端口并订阅**：
   ```bash
   python cli/hive.py $HIVE control port create --route work/general
   python cli/hive.py $HIVE control subscription add \
       --agent agt_XXXX --port work/general --reason 组队
   ```
4. **投递任务**：
   ```bash
   python cli/hive.py $HIVE emit new --root --to port:work/general \
       --title "写周报" --body "汇总本周进展" --claim-within 30m
   ```
5. **Agent 侧领取与结算**（用实例/静态凭据）：
   ```bash
   python cli/hive.py --home .hive \
       --credential-file <agent凭据文件> inbox list --view available
   python cli/hive.py ... inbox claim tsk_XXXX
   python cli/hive.py ... emit done tsk_XXXX --claim clm_XXXX --text 完成
   ```
6. **查看结果**：`inbox peek TSK` / `inbox tree TSK` / `control task show TSK`。

## 顶层入口（只有三个）

- `hive inbox …` 查看/领取/等待：list / peek / claim / decline / tree / wait
- `hive emit …` 投递与结算：new / done / fail / trash / forward / comment / batch
- `hive control …` 管理与观测：system / agent / port / subscription / task /
  permission / operation / event / audit / delivery / harness / producer /
  trigger / channel / human / test(仅test profile)

97 个命令叶子与全部参数见 `GET /v1/schema` 或 `python cli/hive.py <命令> --help`。

## 蜂群指挥台（Web UI）

daemon 启动后浏览器打开 `http://127.0.0.1:19962/ui/`：蜂群星图（Agent/资产库/
人类节点与任务流）、人类收件箱（Markdown/选项/评分/图片/视频/附件 + 双评分裁决）、
资产库浏览与 Agent 操作台。登录粘贴凭据 token（仅存服务端会话，不落地浏览器）。
详见 [FRONTEND_REPORT.md](FRONTEND_REPORT.md)。

## 架构速览

```
CLI(cli/hive.py) ──HTTP──> daemon(hive_core/server.py)
                            ├─ 命令网关（manifest 白名单 + 幂等 + 故障注入）
                            ├─ 领域：tasks/waits/agents/ports/…
                            ├─ SQLite(WAL, 每线程连接, 事务含事件+outbox+幂等)
                            ├─ 调度器：期限/通知合并/冷启动/outbox/trigger
                            └─ HarnessAdapter：fixture-minimal(内置) / process
```

关键语义（详见 DESIGN）：
- done/fail/trash 终态；forward 保留 Task ID 转移责任。
- 回调独立于来源；无 callback 不给发送者推终态通知。
- 等待登记立即返回；结果先到直接 ready；父终态不级联子任务。
- work 需 claim（CAS 原子，20 并发只产生一个 owner）；实例 0/1 槽位唯一。
- 凭据只存 sha256；不进 prompt/日志/报告。

## 真实 harness 接入（已验证：zcode-zsession）

主接入通道是本机 **zsession（ZCode 会话编排服务）**：`examples/harness-zcode-zsession.json`
把 ZCode 真实会话注册为 Hive harness——spawn=启动、/send=发消息（含 interrupt）、
mode=yolo、启动选模型（且支持 /model 热切换）、kill=终止，五项能力原生齐备，
另带 events/inspect 增强。凭据运行时读取 `~/.zcode/zsession/config.json` 的
owner-local token（或 `HIVE_ZSESSION_TOKEN` 环境变量），不进任何 spec/日志/prompt。

```bash
python cli/hive.py $HIVE control harness register \
    --file examples/harness-zcode-zsession.json
python cli/hive.py $HIVE control harness validate zcode-zsession --smoke \
    --workspace <隔离空目录> \
    --model bigmodel-api/GLM-5.3 \
    --alternate-model bigmodel-api/GLM-5.3-Flash
```

备选真实 harness：`examples/harness-opencode-run.json`（本机 opencode CLI 无头模式，
每次 send 为全新会话，连续性由工位文件承载）。

## 验收复现

```bash
python scripts/quicktest.py                 # 快速功能链路
python scripts/acceptance.py                # 88个确定性案例全量（存证 work/acceptance/<run-id>/）
python scripts/acceptance.py AC-TASK-02     # 单案例
# 真实harness五能力smoke + 真实端到端（zsession/ZCode）
HIVE_REAL_HARNESS=zcode-zsession \
HIVE_REAL_HARNESS_SPEC=examples/harness-zcode-zsession.json \
HIVE_SMOKE_MODEL1=bigmodel-api/GLM-5.3 \
HIVE_SMOKE_MODEL2=bigmodel-api/GLM-5.3-Flash \
python scripts/acceptance.py AC-HAR-02 AC-E2E-02
```

每例证据：`work/acceptance/<run-id>/<CASE>/commands.jsonl + results.jsonl +
assertions.jsonl`，汇总 `report.json`。

## 目录

```
cli/hive.py            CLI 单入口（manifest 驱动）
hive_core/             全部核心（store/auth/tasks/waits/…/handlers_*）
examples/              无秘密配置示例（AgentConfig/TaskSpec/HarnessSpec/…）
scripts/               quicktest / acceptance 驱动器
templates via code     系统协议 agent.md 在 contextgen.py 内置生成
work/acceptance/       验收证据（不入库）
```

## 已知限制（V1）

- 真实 harness 已验证两条通道：zcode-zsession（ZCode 会话编排，主通道，
  五能力+热切模型+interrupt）与 opencode-run（opencode 无头 CLI，备选）。
- YOLO 档不承诺操作系统级沙箱（DESIGN §18）。
- external 端口只承担结果/通知投递，不接受外部 work 执行。
- Windows 单机部署；不做多主/跨机同步。
- trigger-platform 线上环境全程未触碰（仅阅读参考）。
