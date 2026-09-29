# Hive V1 详细验收清单

版本：1.0 · 设计讨论：2026-09-29 · 整理交付：2026-09-30 · 状态：待施工后执行，本文不代表已通过。

规范：[DESIGN.md](DESIGN.md)。实施顺序：[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)。验收必须以真实命令输出、状态查询与持久化证据为依据；不能仅看页面、help、模拟返回或施工Agent自述。

## 1. 验收分层与通过标准

1. **确定性验收**：隔离SQLite、HTTP、真实CLI与fixture harness、测试Clock、本地Webhook接收器，覆盖状态/并发/恢复/权限。
2. **真实harness验收**：至少一个实际可用的harness完成启动、发送消息、YOLO、选/切模型、终止。缺事件、inspect、原生工具注册等增强能力不构成失败。
3. **人工交互验收**：最小人工页面实际展示任务、提交答案、重复提交受控，与CLI/账本结果一致。
4. **文档/参数验收**：DESIGN的97个命令叶子与全部参数有对应实现和测试；不能只完成happy path而忽略文档参数。

所有阻断项必须通过；真实harness未提供/未执行则状态写“真实接入待验收”，不能写“系统全部验收通过”。增强能力可标“不提供”，不得把它伪造为idle、支持热更新或已压缩。

### 1.1 阻断项

- 重复实例、重复有效领取、旧实例/旧claim仍能提交。
- 无回调却向来源发送终态通知；回调或@通知因重试重复创建。
- 任务tree与wait混用，父done误当整树done，forward变成另一件任务。
- 结果先到导致永远等待；fail/trash不解除等待；等待环无防护。
- 配置/权限由提示词冒充；凭证进prompt/日志；跨主体越权。
- 最小harness被额外要求必须支持events/inspect/compact/MCP才可注册。
- 任务或责任在重启、暂停、退役时静默消失。
- 真实生产trigger环境被测试修改、删除或中止。

## 2. 隔离环境与执行约定

### 2.1 数据目录与服务

验收数据必须在新目录；示例用项目下 `work/acceptance/<run-id>/`，不得把旧trigger的data目录或生产Hive目录代入。服务端口默认29962，若被占用另选空闲端口，并在整个run保持一致。所有路径采用PowerShell LiteralPath等不解释通配符的方式处理。

```powershell
$HiveProject = 'D:\agent_workspace\valuable-projects\hive'
$HiveCli = Join-Path $HiveProject 'cli\hive.py'
$RunId = Get-Date -Format 'yyyyMMdd-HHmmss'
$EvidenceRoot = Join-Path $HiveProject ('work\acceptance\' + $RunId)
$HiveData = Join-Path $EvidenceRoot 'runtime'
$HiveEndpoint = 'http://127.0.0.1:29962'
New-Item -ItemType Directory -Path $EvidenceRoot -Force | Out-Null
python $HiveCli --home $HiveData control system init --listen 127.0.0.1:29962 --test-mode
python $HiveCli --home $HiveData control system start --listen 127.0.0.1:29962 --test-mode
$OwnerCredential = Join-Path $HiveData 'auth\owner.json'
```

预期：init返回独立数据根和凭据文件路径，stdout没有token正文；start返回唯一受管PID/endpoint；health ready。首次执行应保留这几条命令完整的脱敏输出。

### 2.2 标准fixture manifest

`control test seed --scenario <唯一名称> --out <文件>` 应真实走系统服务建立夹具，不通过绕过权限的公开--as-agent选项。

manifest结构：

```json
{
  "scenario":"示例",
  "agents":{"A":"agt_A","B":"agt_B","C":"agt_C"},
  "ports":{"main":"prt_MAIN","private":"prt_PRIVATE","human":"prt_HUMAN","external":"prt_EXTERNAL"},
  "producer":"prd_EXTERNAL",
  "channels":{"human":"chn_HUMAN","webhook":"chn_WEBHOOK"},
  "subjects":{"human":"subj_HUMAN","producer":"subj_PRODUCER"},
  "credentials":{"A":"受保护文件路径","B":"受保护文件路径","C":"受保护文件路径","human":"受保护文件路径","producer":"受保护文件路径","observer":"受保护文件路径"},
  "harness":"fixture-minimal",
  "files":{"workspaceA":"目录","workspaceB":"目录","sample":"可访问样本文件"},
  "webhook":{"endpoint":"本机测试接收器URL","received_file":"仅测试事件记录路径"}
}
```

fixture默认：A/B是enabled、auto_start=false、有唯一模拟活实例、订阅main、容量3，可互相通信并处理自己的责任。C有基本自身权限但无main领取权、无private读取权。A可读private。Human有该人工端口回答权；producer仅可投main并可回调已登记external目标。owner可管理全部夹具。所有对象名包含scenario前缀，case间不会复用同一任务或占用名额。

测试harness可以模拟状态，但必须经正式runtime service分配实例、凭据和generation；不能把actor名称直接当认证。夹具工具只在test profile+owner下开放。

### 2.3 建议PowerShell辅助函数

以下辅助函数只减少重复命令，不绕过CLI。施工方应提供同等功能的 `scripts/acceptance.ps1`，并保留每次实际argv、exit和JSON。遇到预期错误时H仍返回错误envelope，由断言判断，不能将其吞掉当成功。

```powershell
function Owner {
    param([string[]]$CommandArgv)
    $LaunchArgv = @($HiveCli, '--home', $HiveData, '--endpoint', $HiveEndpoint,
        '--credential-file', $OwnerCredential, '--format', 'json') + $CommandArgv
    $RawOutput = & python @LaunchArgv
    $script:LastHiveExit = $LASTEXITCODE
    ($RawOutput -join "`n") | ConvertFrom-Json
}

function Begin-Case {
    param([string]$CaseId)
    $ClearFaults = Owner @('control','test','fault','clear')
    if (-not $ClearFaults.ok) { throw 'fixture fault reset failed' }
    $script:CaseDir = Join-Path $EvidenceRoot $CaseId
    New-Item -ItemType Directory -Path $CaseDir -Force | Out-Null
    $script:ManifestPath = Join-Path $CaseDir 'fixture.json'
    $script:CaseScenario = $RunId + '-' + $CaseId
    $SeedResult = Owner @('control','test','seed','--scenario',$CaseScenario,'--out',$ManifestPath)
    if (-not $SeedResult.ok) { throw 'fixture creation failed' }
    $script:Fixture = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
}

function Invoke-HiveAs {
    param([string]$As, [string[]]$CommandArgv)
    if ($As -eq 'owner') { return Owner $CommandArgv }
    # 刷新同一夹具对象的当前凭据路径，不重置对象、权限或任务。
    $RefreshResult = Owner @('control','test','seed','--scenario',$CaseScenario,'--out',$ManifestPath)
    if (-not $RefreshResult.ok) { throw 'fixture refresh failed' }
    $script:Fixture = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
    $CredentialPath = $Fixture.credentials.$As
    if (-not $CredentialPath) { throw 'unknown fixture actor' }
    $LaunchArgv = @($HiveCli, '--home', $HiveData, '--endpoint', $HiveEndpoint,
        '--credential-file', $CredentialPath, '--format','json') + $CommandArgv
    $RawOutput = & python @LaunchArgv
    $script:LastHiveExit = $LASTEXITCODE
    ($RawOutput -join "`n") | ConvertFrom-Json
}

Set-Alias -Name H -Value Invoke-HiveAs -Scope Script -Force

function New-Task {
    param([string]$As='owner', [string]$To=('port:' + $Fixture.ports.main),
        [string]$Type='work', [string]$Parent='', [string[]]$Extra=@())
    $CommandArgv = @('emit','new','--to',$To,'--type',$Type,'--title','验收任务','--body','按本案例说明处理')
    if ($Parent) { $CommandArgv += @('--parent',$Parent) } else { $CommandArgv += '--root' }
    $Reply = H $As ($CommandArgv + $Extra)
    if (-not $Reply.ok) { throw ('task creation failed: ' + $Reply.error.code) }
    $Reply.data.task
}

function Claim-Task {
    param([string]$As,[string]$TaskId)
    $Reply = H $As @('inbox','claim',$TaskId)
    if (-not $Reply.data.results[0].ok) { throw 'expected claim success' }
    $Reply.data.results[0]
}
```

CLI若输出格式不同于上述data路径，属于契约不一致，须修复实现或正式同步文档，而不是验收脚本暗自适配。每例开始用Begin-Case创建全新夹具；并发测试用独立CLI进程和各自凭据，不能只在客户端串行模拟。每个实例的凭据文件使用不可复用的实例路径；实例替换后manifest指向新路径。旧凭据验收必须直接使用事先保存的旧路径调用CLI，不能走会刷新当前身份的H辅助函数。

### 2.4 证据保存

每例目录至少保存 `commands.jsonl`（脱敏argv/时间/exit）、`results.jsonl`、`assertions.json`、必要截图或日志引用。例结束记录Pass/Fail/Blocked/NotRun。只记录凭据文件路径和主体ID，不复制凭据正文。

父任务done、子任务queued、回调消息done等必须查不同Task ID确认；不能仅看“成功”文字。并发/恢复案例可追加只读数据库快照证明，但不能以直接SQL改库替代公开操作。

## 3. 逐步功能与异常验收

以下每例的步骤0均为执行 `Begin-Case '<案例ID>'`，确认seed成功、A/B实例有效、隔离时钟和故障点初始清空。涉及system init/restore/真实harness的案例按特别前置使用新的子目录。除明确复用外，case变量不跨例。

### AC-ENV-01 · 全新初始化、默认隔离与重复init

前置：独立夹具 `Begin-Case 'AC-ENV-01'`；检查没有上一例残留故障。

**步骤 1：** 在CaseDir的新子目录执行本地init。

```powershell
$FreshHome = Join-Path $CaseDir 'fresh-home'
python $HiveCli --home $FreshHome control system init --listen 127.0.0.1:29963
```

**预期结果：** 目录中创建独立配置、数据库和owner凭据；stdout只有凭据路径，无token；不连接旧19961。

**步骤 2：** 记录配置文件hash，再对同home执行相同init。

**预期结果：** 返回已初始化现状，不覆盖配置、身份或凭据；hash保持不变。

**步骤 3：** 检查旧trigger目录和端口的验收前后记录。

**预期结果：** 旧进程未被停止、配置与数据未被改写；仅本run目录产生新文件。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-ENV-02 · 单写者、生产禁用test与服务健康

前置：独立夹具 `Begin-Case 'AC-ENV-02'`；检查没有上一例残留故障。

**步骤 1：** 对验收home再次start。

```powershell
$r = Owner @('control','system','start','--test-mode')
```

**预期结果：** 返回已有服务PID；没有第二个写服务。

**步骤 2：** 在独立进程对同home运行serve，收集退出和错误。

**预期结果：** 明确单写者冲突，原服务仍health ready，账本可用。

**步骤 3：** 对另一个不带test-mode初始化的空home启动服务，并用其owner请求test seed。

**预期结果：** 返回TEST_ONLY/403，不能生成冒充主体；使用独立端口，随后只停止新建服务。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-CLI-01 · 全部命令help与输出协议

前置：独立夹具 `Begin-Case 'AC-CLI-01'`；检查没有上一例残留故障。

**步骤 1：** 按本文命令矩阵逐一执行每个叶子的--help，并执行hive --help、三个顶层help与--version。

**预期结果：** 97个叶子均有help；列出的每个本地/全局参数都出现；不存在独立ask或旧wait入口。

**步骤 2：** 分别运行成功查询、非法参数、权限拒绝和不存在对象。

**预期结果：** stdout各为一个可解析JSON对象；stderr日志不混入；exit分别符合0/2/3/4类别。

**步骤 3：** 比较CLI导出的命令schema与DESIGN命令索引。

**预期结果：** 命令集合、参数、默认、互斥、类型完全一致；缺少或额外改变必需接口均失败。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-CLI-02 · 每个参数的合法值、边界、互斥和文件输入

前置：独立夹具 `Begin-Case 'AC-CLI-02'`；检查没有上一例残留故障。

**步骤 1：** 执行第5节参数矩阵，遍历每个命令的每个参数。

**预期结果：** 合法值实际影响查询/写入/运行结果；不能只是parser接受。

**步骤 2：** 对enum给非法值，对int给类型错/上下界外，对必填省略，对未知参数与未知JSON字段提交。

**预期结果：** 按schema返回INVALID_ARGUMENT/REQUIRED_ARGUMENT/SCHEMA_UNSUPPORTED，状态不变。

**步骤 3：** 覆盖text/text-file、data/data-file、root/parent、claim-by/claim-within、expires-at/expires-in、model/default互斥。

**预期结果：** 每对同时传入都拒绝；不存在“后一个覆盖前一个”的静默行为。

**步骤 4：** 准备UTF-8中文、带空格路径、非法JSON、缺失文件和超过body上限输入。

**预期结果：** 合法文件客户端读取后原样入账；非法输入明确错误；服务端不尝试读取传来的任意本机路径。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-CLI-03 · 请求超时与异步结果区分

前置：独立夹具 `Begin-Case 'AC-CLI-03'`；检查没有上一例残留故障。

**步骤 1：** 以固定request-id启动fixture实例或执行异步重启。

**预期结果：** exit0仅表示受理，data包含operation_id。

**步骤 2：** 查询operation直到终态，再模拟失败操作并show。

**预期结果：** 查询成功仍ok=true；操作失败由operation.state/error表达，不把查询exit0解释成操作成功。

**步骤 3：** 设置rpc_response_drop，固定request-id创建Task，再用相同ID相同参数重试。

```powershell
Owner @('control','test','fault','set','--point','rpc_response_drop')
$argsNew = @('emit','new','--root','--to',('port:'+$Fixture.ports.main),'--title','response-drop','--body','one','--request-id','drop-001')
$r1 = H 'A' $argsNew
$r2 = H 'A' $argsNew
```

**预期结果：** 首次可能exit7；第二次返回原Task，不新增。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-ID-01 · 缺省Agent配置与个人队列

前置：独立夹具 `Begin-Case 'AC-ID-01'`；检查没有上一例残留故障。

**步骤 1：** 用空JSON文件创建Agent，暂不start。

```powershell
$cfg = Join-Path $CaseDir 'empty.json'
'{}' | Set-Content -LiteralPath $cfg -Encoding utf8
$r = Owner @('control','agent','create','--file',$cfg)
$id = $r.data.agent.agent_id
```

**预期结果：** 生成唯一ID、自动名称、个人地址和默认工位；订阅为空，实例数0。

**步骤 2：** 向新Agent投递一条notice。

**预期结果：** 即使没有业务订阅也能持久保存；按其生命周期/auto_start策略处理。

**步骤 3：** 分别验证有默认fixture harness和没有默认harness的启动。

**预期结果：** 前者采用默认模型启动；后者HARNESS_NOT_CONFIGURED，但注册与个人Task不丢失。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-ID-02 · 复制定义不复制身份和责任

前置：独立夹具 `Begin-Case 'AC-ID-02'`；检查没有上一例残留故障。

**步骤 1：** A领取一单后，owner clone A。

```powershell
$t=New-Task
$c=Claim-Task 'A' $t.task_id
$r=Owner @('control','agent','clone',$Fixture.agents.A,'--name','A-copy')
```

**预期结果：** 新agent_id、个人队列、工位均不同；原任务owner仍A。

**步骤 2：** 比较两者配置和运行记录。

**预期结果：** 可复制的配置一致；新Agent没有A的claim、回执、凭据和宿主会话。

**步骤 3：** 让低权主体尝试复制高权Agent。

**预期结果：** 不可借clone获得超出可授予范围的权限，返回GRANT_SCOPE_EXCEEDED或FORBIDDEN。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-ID-03 · 配置更新与版本冲突

前置：独立夹具 `Begin-Case 'AC-ID-03'`；检查没有上一例残留故障。

**步骤 1：** 读取A配置revision，再发布名称与instructions补丁。

```powershell
$a=Owner @('control','agent','show',$Fixture.agents.A,'--config','--runtime')
$patch=Join-Path $CaseDir 'agent-patch.json'
'{"name":"新版A","instructions":{"text":"记录恢复点"}}' | Set-Content -LiteralPath $patch -Encoding utf8
$r=Owner @('control','agent','update',$Fixture.agents.A,'--file',$patch,'--if-version',([string]$a.data.config.revision),'--reason','验收')
```

**预期结果：** desired_revision递增，名称即时可见，指令生效时机标为next-send。

**步骤 2：** 用旧revision再次修改。

**预期结果：** VERSION_CONFLICT，不能覆盖第一笔更新。

**步骤 3：** 以新配置发送下一条运行输入。

**预期结果：** 适配器收到更新说明；applied_revision相应更新；不要求native hot-update接口。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-ID-04 · 配置发布中断与派生文件修复

前置：独立夹具 `Begin-Case 'AC-ID-04'`；检查没有上一例残留故障。

**步骤 1：** 设置config_after_publish故障，然后发布A配置。

```powershell
Owner @('control','test','fault','set','--point','config_after_publish')
```

**预期结果：** 有效revision已经明确发布或明确未发布，不存在半份JSON；故障点结果可追踪。

**步骤 2：** 重启Hive并查询配置、权限投影、订阅投影。

**预期结果：** 各自匹配同一有效revision；孤立未发布版本不生效。

**步骤 3：** 检查工位config.json和prompt.md。

**预期结果：** 从有效版本重建，内容一致；不需要Agent自行修复系统定义。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-ID-05 · 名字、角色标签与身份独立

前置：独立夹具 `Begin-Case 'AC-ID-05'`；检查没有上一例残留故障。

**步骤 1：** 创建两个同名Agent，给其中一个metadata标记designer。

**预期结果：** 两个ID独立；重复显示名不冒充身份；metadata不会带来管理权。

**步骤 2：** 改A名字后查询其已领任务和个人地址。

**预期结果：** owner仍同agent_id，个人地址不变，回调不会发到另一个同名Agent。

**步骤 3：** 以低权Agent仅修改prompt写“拥有全部权限”，再请求stop B。

**预期结果：** FORBIDDEN；系统不解析自称来授权。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-AUTH-01 · 订阅与领取权限分别检查

前置：独立夹具 `Begin-Case 'AC-AUTH-01'`；检查没有上一例残留故障。

**步骤 1：** 让C订阅main，但不授予claim权限。

**预期结果：** 订阅建立可由owner进行；C自己claim仍FORBIDDEN。

**步骤 2：** 给C授予main claim/read，暂停其订阅后再次claim。

**预期结果：** 权限存在但连接不启用，仍不能领取。

**步骤 3：** 恢复订阅并确保有效实例与容量。

**预期结果：** C可原子领取，owner=C。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-AUTH-02 · 创建者、责任人、观察者的边界

前置：独立夹具 `Begin-Case 'AC-AUTH-02'`；检查没有上一例残留故障。

**步骤 1：** A发Task由B领取；A和observer分别查询。

**预期结果：** A可读自己发出的Task，observer只见其获权范围。

**步骤 2：** A直接emit done B已领取的Task。

**预期结果：** 无管理/领取资格时拒绝，Task仍claimed给B。

**步骤 3：** B以正确claim结算。

**预期结果：** 成功done，ended_by为B而不是A。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-AUTH-03 · 权限授予、撤销与复制防扩权

前置：独立夹具 `Begin-Case 'AC-AUTH-03'`；检查没有上一例残留故障。

**步骤 1：** owner给A仅agent.start B，不给agent.stop B。

**预期结果：** A能start/返回已有实例，不能stop。

**步骤 2：** A尝试grant自己stop、或者创建一个带stop权限的新Agent。

**预期结果：** 缺少delegable授权时拒绝，不产生高权新主体。

**步骤 3：** owner撤销A的start grant后再发请求。

**预期结果：** 下一次请求立即拒绝；此前已存在的任务责任不消失。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-AUTH-04 · 来源伪造与秘密不外泄

前置：独立夹具 `Begin-Case 'AC-AUTH-04'`；检查没有上一例残留故障。

**步骤 1：** producer向HTTP提交伪造source、owner、state字段。

**预期结果：** 400/INVALID_ARGUMENT，不能成为owner或任意Agent。

**步骤 2：** 用无凭据或已撤销凭据调用CLI/API。

**预期结果：** AUTH_REQUIRED/TOKEN_REVOKED，未写入任何任务。

**步骤 3：** 检查stdout、prompt.md、配置视图、日志、审计和报告。

**预期结果：** 没有token/密钥正文；只允许公开主体ID、文件位置或凭据引用。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-CTX-01 · 根目录md、排除生成文件与历史按需读

前置：独立夹具 `Begin-Case 'AC-CTX-01'`；检查没有上一例残留故障。

**步骤 1：** 在A工位写入不同标记的CONTEXT.md、RULES.md和history/old.md。

**预期结果：** 文件实际保存；不改系统生成的三份定义文件。

**步骤 2：** 显式重启A并查看fixture捕获的启动输入。

**预期结果：** 加载agent.md一次、prompt.md一次、根业务md各一次；history/old.md不被递归自动注入。

**步骤 3：** 增加额外context_entries指向old.md再启动。

**预期结果：** 该显式入口出现一次，加载列表与配置版本可查。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-CTX-02 · 实时责任覆盖过时记忆

前置：独立夹具 `Begin-Case 'AC-CTX-02'`；检查没有上一例残留故障。

**步骤 1：** 在A的CONTEXT.md写“所有任务已经完成”，同时让A实际持有未结Task并有pending wait。

**预期结果：** 文件内容与账本形成可观察差异，但系统账本不变。

**步骤 2：** 重启A。

**预期结果：** 启动摘要明确列出真实未结Task和等待；不因md自称已完成而清账。

**步骤 3：** Task已转给B后再读取A启动摘要。

**预期结果：** 当前责任归B；A保留历史/独立回调，不再被当作当前owner。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-CTX-03 · 超量、缺入口与工位保留

前置：独立夹具 `Begin-Case 'AC-CTX-03'`；检查没有上一例残留故障。

**步骤 1：** 令根业务md总大小超过配置context_bytes_limit。

**预期结果：** 启动返回CONTEXT_TOO_LARGE和相关文件列表，不静默截断。

**步骤 2：** 改回合规大小并删除/移动测试中自己创建的可选入口（用重命名保留证据）。

**预期结果：** 可选入口缺失给明确warning；必要工位不存在返回WORKSPACE_UNAVAILABLE。

**步骤 3：** 正常stop再start，并按空责任计划retire。

**预期结果：** 工位文件和历史仍存在；停止/退役不会物理删除资产。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-PORT-01 · 多对多订阅与发现

前置：独立夹具 `Begin-Case 'AC-PORT-01'`；检查没有上一例残留故障。

**步骤 1：** 创建第二agents端口，让A订阅两个端口，B订阅main。

**预期结果：** subscription list显示正确多对多关系，重复add不重复行。

**步骤 2：** 查询端口名片与订阅者。

**预期结果：** 用途、输入要求、类型、状态可见；只能看到获权的连接。

**步骤 3：** 向两个端口投递，A inbox available查询。

**预期结果：** 两端口任务聚合出现，每个Task只一条记录，无队列复制。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-PORT-02 · 暂停端口的接收与领取

前置：独立夹具 `Begin-Case 'AC-PORT-02'`；检查没有上一例残留故障。

**步骤 1：** 暂停main，然后投递work。

**预期结果：** emit成功，Task queued，端口继续保存输入。

**步骤 2：** A尝试claim。

**预期结果：** PORT_PAUSED，任务未被领取。

**步骤 3：** 恢复main并claim。

**预期结果：** 领取成功；若暂停前已有claimed任务，其结算始终允许。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-PORT-03 · 取消订阅不解除已有责任

前置：独立夹具 `Begin-Case 'AC-PORT-03'`；检查没有上一例残留故障。

**步骤 1：** A领取main一单，然后取消订阅main。

**预期结果：** 配置revision更新，已领Task仍由A负责。

**步骤 2：** 再创建main任务并让A领取。

**预期结果：** 无有效订阅而拒绝；B仍可领。

**步骤 3：** A结算原任务，再收到此前登记的个人回调。

**预期结果：** 结算允许；回调不依赖A仍订阅原端口。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-PORT-04 · 接收schema、handler限制与退役

前置：独立夹具 `Begin-Case 'AC-PORT-04'`；检查没有上一例残留故障。

**步骤 1：** 配置端口data要求必须有string字段query，提交不合规Task。

**预期结果：** TARGET_TYPE_REJECTED或schema错误；任务不存在。

**步骤 2：** 尝试给external handler允许work。

**预期结果：** SCHEMA_UNSUPPORTED；V1不伪造外部work执行协议。

**步骤 3：** 端口有未结任务时retire dry-run，再按明确方案转移并退役。

**预期结果：** 先列blockers；方案执行后历史保留，新投递PORT_RETIRED，原task按安排继续。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-TASK-01 · 根与子任务、血缘不等于等待

前置：独立夹具 `Begin-Case 'AC-TASK-01'`；检查没有上一例残留故障。

**步骤 1：** 创建根T1与两个子T2/T3。

```powershell
$t1=New-Task
$t2=New-Task -Parent $t1.task_id
$t3=New-Task -Parent $t1.task_id
```

**预期结果：** T1.parent=null/root=self；T2/T3.parent=T1、root=T1。

**步骤 2：** 查询tree，领取并done T1，子任务保持queued。

**预期结果：** T1结束不自动结束子任务；tree.open_work_count仍为2。

**步骤 3：** 尝试修改parent或构造树环。

**预期结果：** 不允许改系统血缘字段；拒绝，不损坏原树。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-TASK-02 · 20请求竞争领取一单

前置：独立夹具 `Begin-Case 'AC-TASK-02'`；检查没有上一例残留故障。

**步骤 1：** 创建一件main work，为A/B多个独立CLI请求准备各自有效凭据。

**预期结果：** 所有请求都指向同一Task，初始queued。

**步骤 2：** 同步放行20个领取请求；同一主体重试不得误计为多个新owner。

**预期结果：** 只有一次queued→claimed跃迁；赢家重复领取可返回already_owned，其余主体TASK_ALREADY_CLAIMED。

**步骤 3：** 查询Task、claim历史和事件。

**预期结果：** 恰好一个有效claim、一位owner、一条有效领取事件；记录真实并发证据。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-TASK-03 · 批量领取部分成功与容量原子性

前置：独立夹具 `Begin-Case 'AC-TASK-03'`；检查没有上一例残留故障。

**步骤 1：** 设置A容量1，准备两件可领任务与一件B已领任务。

**预期结果：** 前置容量配置生效，B的任务与A独立。

**步骤 2：** A批量claim三件。

**预期结果：** 逐项返回；最多一个新的work成功，另有CAPACITY_EXCEEDED/TASK_ALREADY_CLAIMED，exit6。

**步骤 3：** 另用并发请求竞争A最后一个名额。

**预期结果：** 容量统计与claim同事务，不能出现A在途2件超过上限1。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-TASK-04 · 个人work要领取，通知不占容量

前置：独立夹具 `Begin-Case 'AC-TASK-04'`；检查没有上一例残留故障。

**步骤 1：** 向A个人队列投work、notice各一条。

**预期结果：** 两条都在personal，work初始queued且无owner。

**步骤 2：** A未claim直接done该work，再正常claim。

**预期结果：** 前者拒绝；后者建立责任。

**步骤 3：** A工作名额满时读取并done notice。

**预期结果：** 允许，notice完成不改变work，也不需要额外工作名额。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-TASK-05 · 三种终态及原因

前置：独立夹具 `Begin-Case 'AC-TASK-05'`；检查没有上一例残留故障。

**步骤 1：** 分别准备并领取三个Task。

**预期结果：** 三个独立claim，任务可结算。

**步骤 2：** 分别done带结果、fail带受阻原因、trash带废弃原因。

**预期结果：** 三个状态及result/ended_by正确；fail/trash缺reason必须拒绝。

**步骤 3：** 用不同request-id尝试将已done改为fail。

**预期结果：** TASK_TERMINAL，原终态与原结果不变；同request-id重放原done则返回原结果。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-TASK-06 · forward保留身份并拒绝旧claim

前置：独立夹具 `Begin-Case 'AC-TASK-06'`；检查没有上一例残留故障。

**步骤 1：** A发任务要求回调A，B领取后forward到A个人队列。

**预期结果：** Task ID/parent/root/source/callback不变，state回queued、旧claim失效。

**步骤 2：** B用旧claim提交done。

**预期结果：** CLAIM_FENCED或相应责任冲突；不会产生回执。

**步骤 3：** A领取并done。

**预期结果：** 终态回调依然送配置中的A；forward阶段没有额外终态回执。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-TASK-07 · 转交失败与行政回收边界

前置：独立夹具 `Begin-Case 'AC-TASK-07'`；检查没有上一例残留故障。

**步骤 1：** B持有任务，forward到不存在或无权限目标。

**预期结果：** 操作失败，B仍是owner、原claim仍有效。

**步骤 2：** owner用control task forward直接移动claimed任务。

**预期结果：** RECLAIM_REQUIRED，不绕过执行者责任。

**步骤 3：** owner用reclaim显式转交，B随后提交旧结果。

**预期结果：** 回收有审计和新claim_epoch；旧结果拒绝，实际外部动作是否停止按stop-owner选择记录。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-TASK-08 · done+dispatch与batch原子性

前置：独立夹具 `Begin-Case 'AC-TASK-08'`；检查没有上一例残留故障。

**步骤 1：** 准备A持有的父任务；dispatch文件包含一个合法目标和一个无权目标。

**预期结果：** 文件为有效JSON数组，但业务验证有一项不合法。

**步骤 2：** A done父任务并附dispatch。

**预期结果：** 整笔失败；父仍claimed，两个子任务均未创建，没有回执。

**步骤 3：** 修正目标后同一新操作提交，再重放。

**预期结果：** 父done、子任务全部创建；重放返回相同ID，不多生子任务。

**步骤 4：** batch第二步故意使用不存在ref或无效wait。

**预期结果：** 所有前置new也回滚；固定请求重复不会残留半批。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-MSG-01 · 查看、领取、处理是不同事实

前置：独立夹具 `Begin-Case 'AC-MSG-01'`；检查没有上一例残留故障。

**步骤 1：** 给A发送个人message，无callback。

**预期结果：** Task queued，发送成功不等于已读。

**步骤 2：** A peek，再peek --mark-seen。

**预期结果：** 前者只读；后者有查看记录；均未done。

**步骤 3：** A emit done。

**预期结果：** 消息进入done；原发送者inbox没有自动读回执或终态回执。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-MSG-02 · 普通评论、@和去重

前置：独立夹具 `Begin-Case 'AC-MSG-02'`；检查没有上一例残留故障。

**步骤 1：** 在A/B可读的Task下发普通评论。

**预期结果：** 保存评论，B inbox不新增notice。

**步骤 2：** 对同Task发含@B且--mention B的评论，固定request-id重试。

**预期结果：** 正文解析与显式mention去重；只一个comment和一个给B的notice。

**步骤 3：** B打开notice查看源Task评论并done notice。

**预期结果：** 原Task状态不变；评论仍可见；notice不再生成二次回调。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-MSG-03 · 无权@与评论不等于指令执行

前置：独立夹具 `Begin-Case 'AC-MSG-03'`；检查没有上一例残留故障。

**步骤 1：** A在C无权读的private Task上@C。

**预期结果：** 整笔FORBIDDEN：没有新评论、没有泄露摘要的notice。

**步骤 2：** 对可见Task评论“我领取了/已完成/请停止B”。

**预期结果：** 只产生评论记录，不claim、不done、不启动control。

**步骤 3：** 正式claim/emit/control后再查流转。

**预期结果：** 只有正式操作改变系统事实，并有真实actor。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-MSG-04 · 直接交流与reply_to

前置：独立夹具 `Begin-Case 'AC-MSG-04'`；检查没有上一例残留故障。

**步骤 1：** A向B发message M1，B回复M2并done M1。

**预期结果：** M2.reply_to=M1；M1和M2是两个Task，各自处理状态独立。

**步骤 2：** A处理M2。

**预期结果：** 讨论链可追溯，不影响二者正在承担的其他work。

**步骤 3：** 同样文字以新的request-id有意再发一次。

**预期结果：** 产生新的沟通Task；去重不是按文字相同去重。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-MSG-05 · 轻量任务过期与暂停收件

前置：独立夹具 `Begin-Case 'AC-MSG-05'`；检查没有上一例残留故障。

**步骤 1：** stop A，向其个人队列发message和expires-in=5m的notice。

**预期结果：** A保持paused/0实例，两个Task已保存。

**步骤 2：** 推进时钟6m。

**预期结果：** 时效notice trash/EXPIRED；无期限message仍queued；没有自动start A。

**步骤 3：** 明确start A并查看inbox。

**预期结果：** 能看到尚未处理message及可查询的过期历史；无重复notice。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-TIME-01 · 拒绝原因共享与任务版本

前置：独立夹具 `Begin-Case 'AC-TIME-01'`；检查没有上一例残留故障。

**步骤 1：** A/B都对同queued work decline并填写不同原因。

**预期结果：** Task仍queued，无owner；黑板能看到两条原因。

**步骤 2：** 运行通知扫描，随后owner补齐任务data要求。

**预期结果：** 未变化时不反复提醒A/B；amend产生新spec_version后重新具备通知资格。

**步骤 3：** A对自己已经领取的另一个work decline。

**预期结果：** 拒绝，要求fail/trash/forward正式交代责任。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-TIME-02 · 无人领取超时fail及无回调约束

前置：独立夹具 `Begin-Case 'AC-TIME-02'`；检查没有上一例残留故障。

**步骤 1：** 创建claim-within=5m、callback=none的Task，A/B均不领取。

```powershell
$t=New-Task -As 'A' -Extra @('--claim-within','5m','--callback','none')
```

**预期结果：** queued保存，期限可查询。

**步骤 2：** 推进隔离时钟6m。

```powershell
Owner @('control','test','clock','advance','--by','6m')
```

**预期结果：** Task fail，reason_code=CLAIM_TIMEOUT；没有物理删除。

**步骤 3：** 查询来源A personal和Task历史。

**预期结果：** A不收到终态通知；事件和原Task结果仍可查询。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-TIME-03 · 已领取不受领取期限影响，有效期可废弃

前置：独立夹具 `Begin-Case 'AC-TIME-03'`；检查没有上一例残留故障。

**步骤 1：** 创建claim-within=2m、expires-in=10m的work并立即由A领取。

**预期结果：** claim建立，领取截止不再用于结束此claimed Task。

**步骤 2：** 推进3m。

**预期结果：** 仍claimed，不因CLAIM_TIMEOUT变fail。

**步骤 3：** 再推进8m。

**预期结果：** trash/EXPIRED，claim失效；记录停止本项工作通知，A旧claim不能done。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-TIME-04 · 期限竞态、同刻优先级与默认不销毁

前置：独立夹具 `Begin-Case 'AC-TIME-04'`；检查没有上一例残留故障。

**步骤 1：** 并发执行临界到期tick与claim。

**预期结果：** 只有一种合法结果：先claim则忽略领取超时，先超时则claim拒绝；不能双成功。

**步骤 2：** 给同Task设置相同claim与expires截止，推进到该刻。

**预期结果：** 业务失效优先，trash/EXPIRED，只有一个终态事件。

**步骤 3：** 创建无期限Task，推进长时间。

**预期结果：** 仍queued；可以有unclaimed事件，但不能自动fail/trash或删除。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-CB-01 · done/fail/trash分别自动回调

前置：独立夹具 `Begin-Case 'AC-CB-01'`；检查没有上一例残留故障。

**步骤 1：** A创建三件工作，callback=self，B分别领取。

**预期结果：** callback已展开为A的稳定ID，不依赖B或当前会话。

**步骤 2：** B分别done、fail、trash。

**预期结果：** 每件源Task各有一条receipt，含正确source_task_id、terminal_event_id、state和结果/原因。

**步骤 3：** A处理三个receipt，再查询其子树。

**预期结果：** 没有回执的回执；原Task终态不被通知处理改变。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-CB-02 · 回调目标独立于来源

前置：独立夹具 `Begin-Case 'AC-CB-02'`；检查没有上一例残留故障。

**步骤 1：** A创建Task，将callback指定到B，A不作为目标。

**预期结果：** source=A、callback=B分别保存。

**步骤 2：** 合法执行者done后查询A/B个人队列。

**预期结果：** 只有B得到终态receipt，A仍可主动查询原Task。

**步骤 3：** producer使用callback=self，随后改为显式agent或port目标。

**预期结果：** self返回CALLBACK_TARGET_REQUIRED；合法显式目标成功，不生成producer个人队列。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-CB-03 · 多次转交后的终态只回原目标

前置：独立夹具 `Begin-Case 'AC-CB-03'`；检查没有上一例残留故障。

**步骤 1：** A发送到main，callback=A；B领取后forward给A，A再按授权forward回main由B领取。

**预期结果：** 所有转交同一Task ID，每次claim代次变化。

**步骤 2：** 在转交阶段查询receipt。

**预期结果：** 没有终态回执；流转history有两个transfer。

**步骤 3：** B最终done，并重放同一done请求。

**预期结果：** A只得到一条逻辑receipt；当前owner清空，历史责任可追溯。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-CB-04 · 投递失败不改变源结果

前置：独立夹具 `Begin-Case 'AC-CB-04'`；检查没有上一例残留故障。

**步骤 1：** 让回调目标为外部结果端口，配置本地接收器暂时返回503。

**预期结果：** 源任务可正常领取执行。

**步骤 2：** done源任务并推进outbox重试。

**预期结果：** 源Task保持done；delivery=retry/dead，错误和attempts可见，receipt没有被重复创建。

**步骤 3：** 接收器恢复200，执行delivery retry。

**预期结果：** 沿用delivery_id，接收端可去重；成功后receipt投递完成，源Task仍原结果。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-CB-05 · 退役时未完回调交接

前置：独立夹具 `Begin-Case 'AC-CB-05'`；检查没有上一例残留故障。

**步骤 1：** 创建回调给A但尚未完成的任务，然后对A执行退役dry-run。

**预期结果：** blockers包括待回调安排，不能假装只有当前inbox才算待办。

**步骤 2：** 给出redirect到B的明确计划并完成退役。

**预期结果：** 保存回调重定向依据；A停止接新普通投递。

**步骤 3：** 结束原任务。

**预期结果：** 回执交给B并有redirect审计；如果没有有效方案则投递异常留账，不伪称sent。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-WAIT-01 · all等待包含失败结果

前置：独立夹具 `Begin-Case 'AC-WAIT-01'`；检查没有上一例残留故障。

**步骤 1：** A领取父任务，创建两个子任务并登记all等待。

**预期结果：** 父仍claimed，work容量仍计入；等待pending。

**步骤 2：** B完成一个子任务。

**预期结果：** 等待仍pending，结果已记录。

**步骤 3：** 另一个子任务fail。

**预期结果：** 等待ready；A收到恢复所需信息，父仍claimed，必须由A决定补救或结束。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-WAIT-02 · any等待不自动取消剩余工作

前置：独立夹具 `Begin-Case 'AC-WAIT-02'`；检查没有上一例残留故障。

**步骤 1：** 父任务登记两个依赖mode=any。

**预期结果：** 两条依赖都可查。

**步骤 2：** 其中一个trash。

**预期结果：** wait ready，明确包含trash结果；不是只等done。

**步骤 3：** 查看另一个依赖Task。

**预期结果：** 它继续原状态，没有被any恢复自动取消；需要另行明确处理。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-WAIT-03 · 结果先到、并发登记与重启无丢唤醒

前置：独立夹具 `Begin-Case 'AC-WAIT-03'`；检查没有上一例残留故障。

**步骤 1：** 先将子任务done，再登记父任务等待该子任务。

**预期结果：** 直接ready，返回既有结果，不永久pending。

**步骤 2：** 多轮并发执行子任务结束与wait create。

**预期结果：** 每轮最终ready；不存在终态已到但wait永不变化的记录。

**步骤 3：** 登记后停止Hive、使已保存结果待处理，再重启。

**预期结果：** 从账本补算wait和通知，不依赖某条瞬时事件刚好被监听到。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-WAIT-04 · 等待超时、取消与依赖任务独立

前置：独立夹具 `Begin-Case 'AC-WAIT-04'`；检查没有上一例残留故障。

**步骤 1：** 父等待一个仍在执行的子任务，wait-for=2m。

**预期结果：** 父claimed、子claimed，wait pending。

**步骤 2：** 推进3m。

**预期结果：** wait timed_out并提醒父责任人，父不自动fail，子不自动trash。

**步骤 3：** 另建pending wait并执行cancel。

**预期结果：** wait cancelled；父/依赖状态按原样保留，不产生假成功结果。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-WAIT-05 · 等待归任务、转交后跟随责任人

前置：独立夹具 `Begin-Case 'AC-WAIT-05'`；检查没有上一例残留故障。

**步骤 1：** A持有父任务并等待子任务，把父forward给B。

**预期结果：** 等待仍关联原父Task；A不再是当前责任人。

**步骤 2：** B领取父任务后，子任务done。

**预期结果：** 恢复通知送当前owner B；若原子任务独立callback=A，该回调仍保留。

**步骤 3：** 另一个父先终态，依赖后返回。

**预期结果：** 父pending wait取消，不重新唤醒它为工作责任；独立callback照常。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-WAIT-06 · 等待环、自等待与原子创建

前置：独立夹具 `Begin-Case 'AC-WAIT-06'`；检查没有上一例残留故障。

**步骤 1：** 尝试让T1等待自己；再建立T1等T2后，让T2等T1。

**预期结果：** 均WAIT_CYCLE，不保存循环边。

**步骤 2：** 提交batch：new child + wait parent，随后故意让wait参数无效。

**预期结果：** 整个batch回滚，没有孤立child。

**步骤 3：** 修正后提交并快速完成child。

**预期结果：** child、wait和结果订阅一致存在，最终ready，不丢回执或重复任务。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-RUN-01 · 同时start只产生一个实例

前置：独立夹具 `Begin-Case 'AC-RUN-01'`；检查没有上一例残留故障。

**步骤 1：** 新建auto_start=false的Agent，配置fixture harness，初始0实例。

**预期结果：** Agent存在、没有运行实例。

**步骤 2：** 同时发20个start请求。

**预期结果：** 同一有效instance_id/启动operation，最多一个受管执行进程或会话。

**步骤 3：** 查询实例与启动日志。

**预期结果：** 唯一槽位约束成立；重复请求返回already_running/已有操作，不出现第二有效凭据。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-RUN-02 · stop暂停且不消除任务责任

前置：独立夹具 `Begin-Case 'AC-RUN-02'`；检查没有上一例残留故障。

**步骤 1：** A领取任务并保存工位文件，然后stop。

**预期结果：** 生命周期paused；实例停止，已领Task仍归A，文件保留。

**步骤 2：** 投递个人message并触发wake。

**预期结果：** 消息持久保存；wake返回paused原因，不自动启动。

**步骤 3：** 明确start A。

**预期结果：** 新实例接回原Agent身份、任务与个人队列；原claim缓存需按新实例取回有效凭据。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-RUN-03 · restart更换实例并fence旧身份

前置：独立夹具 `Begin-Case 'AC-RUN-03'`；检查没有上一例残留故障。

**步骤 1：** 保存A旧实例凭据文件路径和claim，restart A。

**预期结果：** 旧实例停止，新instance_id生成，agent_id不变。

**步骤 2：** 用旧实例凭据调用inbox/emit/control。

**预期结果：** INSTANCE_FENCED或TOKEN_REVOKED，没有新写入。

**步骤 3：** 新实例查询已有责任并重新取得claim。

**预期结果：** 可接续；旧claim不能通过只填相同agent_id绕过。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-RUN-04 · 原生热换模型缺失时重启切换

前置：独立夹具 `Begin-Case 'AC-RUN-04'`；检查没有上一例残留故障。

**步骤 1：** 关闭fixture的model-hot能力，A当前运行模型m1。

**预期结果：** capability明确没有热切换，但满足model_at_start。

**步骤 2：** control agent model A --model m2 --apply auto。

**预期结果：** 停止原实例后start m2，配置desired/applied对应，只有一个有效实例。

**步骤 3：** 暂停A，再改模型m3。

**预期结果：** 只更新配置，无新实例；下次start使用m3。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-RUN-05 · 启动响应丢失与停止结果未知

前置：独立夹具 `Begin-Case 'AC-RUN-05'`；检查没有上一例残留故障。

**步骤 1：** 设置harness_start_after_launch故障，启动新节点。

**预期结果：** 宿主可能已创建，操作标记待核实并保留唯一槽位。

**步骤 2：** 重复start/重启Hive。

**预期结果：** 依启动请求和适配器受管记录找回原实例；不新建第二会话。

**步骤 3：** 设置harness_stop_unknown并stop，再请求start。

**预期结果：** 仍不能启用替身；模拟stop-confirm后才释放槽位并允许恢复。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-RUN-06 · 运行指令、普通消息与打断降级

前置：独立夹具 `Begin-Case 'AC-RUN-06'`；检查没有上一例残留故障。

**步骤 1：** 把A置busy，发送普通message到个人队列。

**预期结果：** 只保存并排队提醒，不自动中断当前工作。

**步骤 2：** 有权者control send mode=interrupt，fixture缺原生steer，fallback=reject。

**预期结果：** 明确不支持/拒绝，不伪装已实时插入。

**步骤 3：** 改fallback=restart并提交有原因的新请求。

**预期结果：** 明确重启后注入；返回degraded模式和操作轨迹。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-RUN-07 · 退出恢复策略与暂停区别

前置：独立夹具 `Begin-Case 'AC-RUN-07'`；检查没有上一例残留故障。

**步骤 1：** 对enabled且restart_on_exit=true的fixture模拟确认crash。

**预期结果：** 按退避策略恢复同Agent；责任不自动fail。

**步骤 2：** 在一小时内超过自动重启上限。

**预期结果：** 停止自动重启、输出异常，避免无限循环；任务仍可见。

**步骤 3：** 对paused节点制造旧实例迟到crash事件。

**预期结果：** 不自动拉起；旧generation不覆盖当前状态。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-HAR-01 · 最小适配器无增强能力也能接入

前置：独立夹具 `Begin-Case 'AC-HAR-01'`；检查没有上一例残留故障。

**步骤 1：** 注册声明只有start/send/yolo/model_at_start/stop的适配器。

**预期结果：** 注册成功，不要求inspect/events/compact/history/MCP。

**步骤 2：** 关闭fixture的inspect/events/model-hot，启动并发送内容。

**预期结果：** 实例可运行，观测缺失为unknown；基本Task/CLI调用可完成。

**步骤 3：** 改模型经stop+start，再终止。

**预期结果：** 五项能力均可组合兑现，不因缺高级接口判失败。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-HAR-02 · 真实harness五能力smoke

前置：独立夹具 `Begin-Case 'AC-HAR-02'`；检查没有上一例残留故障。

**步骤 1：** 使用操作者已登记的真实harness和独立测试工位；记录适配配置及真实版本，禁止记录凭证正文。

**预期结果：** 该对象不是fixture；有真实启动/send/stop通道和可用两个模型配置。

**步骤 2：** 执行显式smoke或逐步start/send/选另一模型重启/stop。

```powershell
# 将下列NAME与MODEL值替换为本机实际注册项，不假设任何厂商已支持
# hive control harness validate NAME --smoke --workspace ISOLATED_PATH --model MODEL1 --alternate-model MODEL2
```

**预期结果：** 能定位真实会话；消息产生可观察回复或工位文件；实际启动配置启用宿主最高权限；两次模型选择可核实；最后确实停止。

**步骤 3：** 分别记录五项passed与证据；没有真实环境则标Blocked。

**预期结果：** 不得以声明文件或fixture全绿代替本案例；事件等高级能力不参与五项判定。

证据：保存真实命令/适配器调用、会话handle、模型参数、YOLO配置、输入输出与终止证据；不保存token。

### AC-HAR-03 · 空配置默认、适配校验与不支持参数

前置：独立夹具 `Begin-Case 'AC-HAR-03'`；检查没有上一例残留故障。

**步骤 1：** 设系统default_harness，创建空配置Agent并启动。

**预期结果：** 使用默认harness/默认模型/自动工位/基础说明，skills空不阻塞。

**步骤 2：** 仅执行harness validate而不加smoke。

**预期结果：** 返回声明/结构校验级别，不声称真实五项已通过。

**步骤 3：** 注册缺send或stop能力、或argv模板不合法的定义。

**预期结果：** 明确拒绝不满足最小能力；不能靠prompt写“支持”通过。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-HUM-01 · 问人就是emit+wait

前置：独立夹具 `Begin-Case 'AC-HUM-01'`；检查没有上一例残留故障。

**步骤 1：** A持有父任务，emit子work到human端口，callback=A，登记等待。

**预期结果：** 没有ask实体或第四顶层命令；产生普通Task H与Wait。

**步骤 2：** 人工界面打开H并提交有权限的正式文字/选项答案。

**预期结果：** H done，answered_by为真实human主体；源父任务ready但仍需A处理。

**步骤 3：** A查看并处理receipt。

**预期结果：** 读到答案与原Task引用，不再生成回执的回执。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-HUM-02 · 人工否决、无法回答和撤回

前置：独立夹具 `Begin-Case 'AC-HUM-02'`；检查没有上一例残留故障。

**步骤 1：** 对审批题提交data.decision=deny。

**预期结果：** Task done，答案为deny；不得映射成fail。

**步骤 2：** 对另一题human fail并说明无法判断原因。

**预期结果：** Task fail并回调；父任务由Agent自己决定下一步。

**步骤 3：** 对第三题有权human trash并说明已撤回。

**预期结果：** Task trash，记录身份/原因；讨论评论中的“取消”本身不触发此动作。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-HUM-03 · 多人工通道与重复答案

前置：独立夹具 `Begin-Case 'AC-HUM-03'`；检查没有上一例残留故障。

**步骤 1：** 让同一人工Task展示在两个受权页面/通道。

**预期结果：** 两个展示记录引用同一个Task ID，没有两件重复问题。

**步骤 2：** 两个回答者同时提交不同答案。

**预期结果：** 只一个终态成功；另一个返回已处理/版本冲突，不能覆盖。

**步骤 3：** 重放赢家同request-id答案。

**预期结果：** 返回原结果；只有一条终态事件和一条对应receipt。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-HUM-04 · 答案版本、代答权限与附件

前置：独立夹具 `Begin-Case 'AC-HUM-04'`；检查没有上一例残留故障。

**步骤 1：** 打开问题页面后，owner修改问题spec_version；旧页面提交旧版本。

**预期结果：** TASK_SPEC_CHANGED/VERSION_CONFLICT，必须刷新问题。

**步骤 2：** 普通A尝试human answer；再授予明确代答权限后回答另一题。

**预期结果：** 第一次拒绝；第二次记录answered_by为Agent A，不伪装成人。

**步骤 3：** 提交含可访问附件引用的答案并检查界面。

**预期结果：** 附件可定位、权限正确；无权引用被拒绝或清晰标不可访问，不泄露本地任意文件。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-PROD-01 · 外部producer与内置trigger同样造Task

前置：独立夹具 `Begin-Case 'AC-PROD-01'`；检查没有上一例残留故障。

**步骤 1：** 以producer凭据POST /v1/tasks，带event_id和root TaskSpec。

**预期结果：** 生成普通Task，source包含producer与event_id；不创建Agent/会话/个人队列。

**步骤 2：** 用同event_id不同request-id重复提交相同内容。

**预期结果：** 返回原Task；不同内容则PRODUCER_EVENT_CONFLICT。

**步骤 3：** Agent领取处理该Task。

**预期结果：** 行为与内部emit任务一致，来源可追溯。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-PROD-02 · 外部范围、凭据轮换与无回调查询

前置：独立夹具 `Begin-Case 'AC-PROD-02'`；检查没有上一例残留故障。

**步骤 1：** producer投main以外未授权目标。

**预期结果：** FORBIDDEN，没有Task。

**步骤 2：** 允许目标投递callback=none并处理完成，producer主动GET其Task。

**预期结果：** 可读自己结果，没有默认回调或伪造收件箱。

**步骤 3：** owner轮换凭据并revoke previous，旧凭据再投递。

**预期结果：** 旧凭据拒绝，新凭据在原授权范围可用；已有任务不受停用影响。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-PROD-03 · manual与interval触发去重

前置：独立夹具 `Begin-Case 'AC-PROD-03'`；检查没有上一例残留故障。

**步骤 1：** 创建manual trigger，用同event-id fire两次。

**预期结果：** 只有一个根Task；source带同trigger和producer。

**步骤 2：** 创建interval=1m规则并enable，推进3m，按调度策略处理。

**预期结果：** 按各计划时间分别最多一单，重复tick不重复。

**步骤 3：** disable后再推进；手工fire一个明确新事件。

**预期结果：** 不再自动产生；有权的手工fire行为符合文档，不删除以前Task。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-PROD-04 · cron、时区与错过执行策略

前置：独立夹具 `Begin-Case 'AC-PROD-04'`；检查没有上一例残留故障。

**步骤 1：** 创建UTC与+08:00固定偏移的可确定cron规则，推进到各自触发时刻。

**预期结果：** 按配置计算，source计划时间唯一且存UTC。

**步骤 2：** 模拟daemon停机错过多个触发点，分别配置missed=skip与once后恢复。

**预期结果：** skip不补发；once只合并补一次，不一次性灌入所有历史槽位。

**步骤 3：** 提交非法表达式、未支持时区或callback=self模板。

**预期结果：** 在注册/更新时拒绝，不运行后才静默丢任务。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-PROD-05 · Webhook结果投递与至少一次语义

前置：独立夹具 `Begin-Case 'AC-PROD-05'`；检查没有上一例残留故障。

**步骤 1：** 源Task回调到external端口，本地接收器首次收到后故意断开响应。

**预期结果：** 源Task终态已保存，delivery结果待核实/重试。

**步骤 2：** 系统重试同delivery_id，接收器记录并去重，再返回200。

**预期结果：** 网络可能两次到达，但逻辑消费一次；同receipt不复制新Task。

**步骤 3：** 停止通道后再次结束另一个源Task，再恢复并retry。

**预期结果：** 结果留账；不会因通道停用把原done改fail；恢复后继续发送。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-SCHED-01 · 双订阅者群发与领取竞态

前置：独立夹具 `Begin-Case 'AC-SCHED-01'`；检查没有上一例残留故障。

**步骤 1：** 设置A/B均idle、有容量并订阅main，投一单。

**预期结果：** 两者进入候选，通知带抖动，不预分配owner。

**步骤 2：** 让A在B提醒发出前claim。

**预期结果：** B尚未发的无用提醒取消；Task只归A。

**步骤 3：** 在另一轮让两人都先收到信号再竞争。

**预期结果：** 只一个成功；落败者peek发现已领是正常结果，不产生副本。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-SCHED-02 · 跨端口合并和冷启动轮转

前置：独立夹具 `Begin-Case 'AC-SCHED-02'`；检查没有上一例残留故障。

**步骤 1：** A订阅多个端口，短时间进入10条Task。

**预期结果：** 保留10条Task，但唤醒合并，不产生10条新的提醒Task。

**步骤 2：** 创建两个enabled、auto_start=true、0实例订阅者，确保无其他活候选。

**预期结果：** 每轮仅拉起一个，不因一单启动整池。

**步骤 3：** 未领取后推进重试周期，重复多轮。

**预期结果：** 按轮转扩展可用候选，不永远固定第一只；始终无双实例。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-SCHED-03 · busy、unknown与不强制打断

前置：独立夹具 `Begin-Case 'AC-SCHED-03'`；检查没有上一例残留故障。

**步骤 1：** A busy，B idle，投共享work。

**预期结果：** 优先通知B，不把普通任务当interrupt。

**步骤 2：** 关闭宿主inspect/events，使A观测unknown。

**预期结果：** 不伪造idle或dead，不启动A第二实例。

**步骤 3：** 多次投递个人任务并推进调度。

**预期结果：** 队列全部保留，unknown实例只收到合并低频提醒，失败可重试。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-SCHED-04 · 全员拒绝和积压告警

前置：独立夹具 `Begin-Case 'AC-SCHED-04'`；检查没有上一例残留故障。

**步骤 1：** A/B对当前规格拒绝，Task无任何期限、callback=none。

**预期结果：** 记录两条原因，Task仍queued。

**步骤 2：** 推进超过unclaimed_alert_after。

**预期结果：** 产生可查询unclaimed事件；不fail、不trash、不自动给来源回调。

**步骤 3：** 增加有权新订阅者或修改任务要求。

**预期结果：** 新候选可被通知和领取；旧拒绝不变成永久全局封锁。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-SCHED-05 · 规则无LLM判断与状态维度

前置：独立夹具 `Begin-Case 'AC-SCHED-05'`；检查没有上一例残留故障。

**步骤 1：** 构造idle+有效等待、idle+可推进责任、running+无work三种情况。

**预期结果：** 分别展示等待事实、需关注事实和实际运行；不能一概判失职。

**步骤 2：** 延长运行时间但无足够业务证据。

**预期结果：** 最多产生长运行观测，不自动判断产物无效或清退Agent。

**步骤 3：** 检查事件、生命周期、instance observation与责任查询。

**预期结果：** 各维度独立可追踪；未实现高级观测标unknown。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-REC-01 · 提交前故障不留半笔

前置：独立夹具 `Begin-Case 'AC-REC-01'`；检查没有上一例残留故障。

**步骤 1：** 设置tx_before_commit，执行创建/结算/评论@/done+dispatch中至少各一种。

**预期结果：** 请求失败，相关事务整体回滚。

**步骤 2：** 查询所有涉及对象、事件、receipt和outbox。

**预期结果：** 没有部分创建、父提前done或孤立通知。

**步骤 3：** 清故障后使用原请求ID重试。

**预期结果：** 执行一次合法结果，不因失败缓存永久拒绝未提交请求。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-REC-02 · 提交后outbox故障可恢复

前置：独立夹具 `Begin-Case 'AC-REC-02'`；检查没有上一例残留故障。

**步骤 1：** 设置outbox_after_commit，完成带callback的Task。

**预期结果：** 源终态和待投递记录已可靠保存，即使暂未实际通知。

**步骤 2：** 停止并重启Hive，再运行恢复扫描。

**预期结果：** 发现原outbox并继续，恢复等待状态。

**步骤 3：** 重复扫描和重放终态事件。

**预期结果：** 逻辑receipt仍唯一，源Task终态不变。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-REC-03 · daemon重启不重复拉起Agent

前置：独立夹具 `Begin-Case 'AC-REC-03'`；检查没有上一例残留故障。

**步骤 1：** 记录A/B有效实例handle和已领任务，停止Hive服务但保留fixture受管实例。

**预期结果：** 服务停止不自动结束业务责任。

**步骤 2：** 重启Hive并核对受管记录。

**预期结果：** 重建原实例绑定；没有盲目start A/B第二个实例。

**步骤 3：** 原有效实例继续用凭据操作，再尝试旧已fenced代次。

**预期结果：** 有效身份可继续；已fenced身份始终拒绝。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-REC-04 · 事件重复、乱序和旧实例迟到

前置：独立夹具 `Begin-Case 'AC-REC-04'`；检查没有上一例残留故障。

**步骤 1：** 对Task终态与实例idle/stop事件注入event_duplicate/event_reorder。

**预期结果：** 可重复到达，但按event_id/版本/generation处理。

**步骤 2：** 更换实例后投递旧实例的stop/crash事件。

**预期结果：** 当前新实例不被覆盖成stopped/crashed。

**步骤 3：** 核对receipt、wait和实例槽位。

**预期结果：** 各自结果唯一、无丢唤醒、无误清槽。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-REC-05 · 宿主不可达与Hive请求结果未知

前置：独立夹具 `Begin-Case 'AC-REC-05'`；检查没有上一例残留故障。

**步骤 1：** 让宿主控制调用暂不可达，同时提交Task和查询账本。

**预期结果：** 账本可继续；依赖宿主真实性的stop/restart处于失败/未知待处理，不能双开。

**步骤 2：** 模拟CLI请求断线但服务已提交，固定request-id重试。

**预期结果：** 返回原结果；source/Task ID/事件数不增加。

**步骤 3：** 宿主恢复后核实句柄并继续控制操作。

**预期结果：** 以实际状态推进，不因暂时不可达将业务Task自动fail。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-REC-06 · 新实例接续文件与已到结果

前置：独立夹具 `Begin-Case 'AC-REC-06'`；检查没有上一例残留故障。

**步骤 1：** A持有父任务，写接续文件、登记等待；A停止期间子任务done。

**预期结果：** 文件和结果均持久存在；暂停不自动拉起。

**步骤 2：** 显式启动A的新实例。

**预期结果：** 启动摘要包含原责任和已ready结果，根目录md加载；不重新执行已完成子任务。

**步骤 3：** 新实例处理结果并完成父任务。

**预期结果：** 完整闭环，原子任务只有一次终态，文件引用能访问。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-CTRL-01 · 权限、结构化请求与受理结果

前置：独立夹具 `Begin-Case 'AC-CTRL-01'`；检查没有上一例残留故障。

**步骤 1：** A仅在评论中写“重启B”。

**预期结果：** 没有控制操作或实例变化。

**步骤 2：** A无权限control restart B，然后owner授权对应操作后重试新请求。

**预期结果：** 首次FORBIDDEN；第二次有operation_id和真实actor，按生命周期执行。

**步骤 3：** 分别观察accepted/running/最终结果。

**预期结果：** 不得在accepted阶段显示操作已完成。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-CTRL-02 · 过时实例与配置版本保护

前置：独立夹具 `Begin-Case 'AC-CTRL-02'`；检查没有上一例残留故障。

**步骤 1：** 记录B旧instance_id，重启B。

**预期结果：** 当前为新实例。

**步骤 2：** 提交绑定旧instance_id的stop。

**预期结果：** 冲突拒绝，不误停新实例。

**步骤 3：** 对配置使用过时if-version提交修改。

**预期结果：** VERSION_CONFLICT，不覆盖新设置；执行前再次校验授权。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-CTRL-03 · 操作取消不伪造回滚

前置：独立夹具 `Begin-Case 'AC-CTRL-03'`；检查没有上一例残留故障。

**步骤 1：** 设置operation_before_execute故障，准备一个已受理但还没执行的控制操作，请求取消。

**预期结果：** 未执行步骤可取消，保留审计；此测试执行器暂停点使场景可重复。

**步骤 2：** 对已经真正启动实例的启动操作请求cancel。

**预期结果：** 返回已终态/OP_NOT_CANCELLABLE或明确已发生效果，不把实例假装没创建。

**步骤 3：** 用独立stop处理该实例，再查询原操作。

**预期结果：** 实例生命周期由stop完成；原操作历史未被改写。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-CTRL-04 · Agent退役计划和责任blocker

前置：独立夹具 `Begin-Case 'AC-CTRL-04'`；检查没有上一例残留故障。

**步骤 1：** A有work、个人message和未完回调，提交未覆盖的退役计划dry-run。

**预期结果：** 列出全部blockers，无物理删除、无默认trash全部责任。

**步骤 2：** 补齐各项forward/fail/trash和callback redirect/cancel原因，执行。

**预期结果：** 逐项结果可查；未完成时retiring，全部交代后retired。

**步骤 3：** 尝试新普通投递和start A，检查工位与旧任务。

**预期结果：** 拒绝新工作/复活；历史与工位保留，责任按计划有明确去向。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-CTRL-05 · 系统查询、配置与投递重试权限

前置：独立夹具 `Begin-Case 'AC-CTRL-05'`；检查没有上一例残留故障。

**步骤 1：** owner查询event/audit/delivery和system log/config，observer查其允许范围。

**预期结果：** 数据可追溯；observer不能借查询看到私有payload或秘密。

**步骤 2：** 修改notify/reconcile策略，再发相关工作。

**预期结果：** 返回有效配置revision，新策略用于后续调度；数据库路径等不允许热改。

**步骤 3：** 对dead delivery执行retry两次。

**预期结果：** 同一语义投递被恢复，不创建两条新回执；记录真实操作人和原因。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-STORE-01 · 迁移、唯一索引与持久性

前置：独立夹具 `Begin-Case 'AC-STORE-01'`；检查没有上一例残留故障。

**步骤 1：** 在空测试home应用所有迁移并重复一次。

**预期结果：** schema版本正确，重复不破坏数据、不重复表内容。

**步骤 2：** 通过公开API产生Agent/Task/claim/回调/评论/Wait后停止服务。

**预期结果：** 所有确认成功的对象已提交。

**步骤 3：** 重启查询，并只读检查关键唯一索引。

**预期结果：** 数据齐全；实例槽位、订阅、幂等、回执语义唯一约束实际存在。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-STORE-02 · 并发幂等与不同内容冲突

前置：独立夹具 `Begin-Case 'AC-STORE-02'`；检查没有上一例残留故障。

**步骤 1：** 并发发送20次相同主体、命令、request-id、规范化内容的new。

**预期结果：** 一个Task，其余重放相同结果。

**步骤 2：** 同key改title或目标重新发送。

**预期结果：** IDEMPOTENCY_CONFLICT，不悄悄覆盖或造新任务。

**步骤 3：** 不同主体使用相同request-id。

**预期结果：** 按主体命名空间隔离；各自权限独立，不读取对方缓存响应。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-STORE-03 · 一致备份与恢复新home

前置：独立夹具 `Begin-Case 'AC-STORE-03'`；检查没有上一例残留故障。

**步骤 1：** 在有任务/配置/工作文件的运行服务执行backup --include-workspaces。

**预期结果：** 生成manifest/hash；SQLite使用一致备份，默认没有凭据正文。

**步骤 2：** restore到新的空目录，不覆盖原home。

**预期结果：** 配置、账本、工位对应同备份点；Agent先paused，旧绑定待核实，outbox冻结。

**步骤 3：** 检查恢复报告并核实外部事实后才显式启动需要的节点。

**预期结果：** 未核实时没有自动模型启动或外部Webhook重放；原环境未被更改。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-STORE-04 · 终态、归档与物理文件独立

前置：独立夹具 `Begin-Case 'AC-STORE-04'`；检查没有上一例残留故障。

**步骤 1：** 完成/废弃一些Task，另保留claimed和pending callback。

**预期结果：** 不同状态都可在history/control查询。

**步骤 2：** 停止或退役关联Agent。

**预期结果：** 不删除Task历史、工位、附件和等待/回调证据。

**步骤 3：** 检查系统没有自动清理未结记录的路径，测试备份目标已非空时再次backup。

**预期结果：** 拒绝覆盖旧备份；不通过“清空垃圾桶”删责任；无未经请求的目录清理。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-E2E-01 · 完整协作：拆分、@、回调、问人、交付

前置：独立夹具 `Begin-Case 'AC-E2E-01'`；检查没有上一例残留故障。

**步骤 1：** 外部producer创建根任务“形成一份可交付说明”，A领取。

**预期结果：** 来源和根血缘正确；执行与来源身份分离。

**步骤 2：** A用batch创建B资料子任务并等待；B评论@A补充信息后提交结果。

**预期结果：** 评论notice、work和receipt分别处理；结果解除等待。

**步骤 3：** A向人工端口发确认任务并等待，人工正式回答。

**预期结果：** 回答done，原任务可继续；人工deny也是正常返回而非系统失败。

**步骤 4：** A写入实际产物和接续md，done根任务并dispatch下一阶段。

**预期结果：** 父done、下游queued、整树仍有open_work；源配置的回调只发给指定目标。

**步骤 5：** 核对全部Task、comments、events、操作和文件引用。

**预期结果：** 每条责任有清晰结算，血缘/回复/等待不混用，没有孤儿队列项或重复回执。

证据：保存实际CLI/HTTP结果、相关Task/Agent/operation ID及关键状态查询。

### AC-E2E-02 · 真实最小harness端到端与可替换实例

前置：独立夹具 `Begin-Case 'AC-E2E-02'`；检查没有上一例残留故障。

**步骤 1：** 将一个独立验收Agent配置为已通过AC-HAR-02的真实harness，使用专用工位和最小Hive授权。

**预期结果：** 系统加载agent.md/prompt.md/业务md/实时摘要，不依赖原生MCP注册。

**步骤 2：** 投递一个真实可完成的任务：读取输入文件、输出一份说明、保存进度、通过CLI emit done。

**预期结果：** 真实模型通过三入口完成领取与结算，输出文件可打开，不能用fixture脚本替代这一证据。

**步骤 3：** 第二项工作中让它保存checkpoint后停止，切模型或新建实例继续。

**预期结果：** 同agent_id、唯一新实例、原文件/责任连续；旧实例凭据不能再操作。

**步骤 4：** 记录真实不足与最终证据，停止本轮受管测试实例。

**预期结果：** 五能力和接续行为分别报告；未运行项清楚标NotRun/Blocked，不虚报通过。

证据：真实运行输入/输出、可访问产物、Hive账本与调用记录、两个实例ID、停止证据；全部脱敏。

## 4. 命令覆盖矩阵

本表列出全部97个公开叶子。每项除所列业务案例外，还必须执行AC-CLI-01/02的逐参数检查。覆盖某个案例不等于该命令所有选项已自动通过，报告中必须填写该命令真实执行记录。

| CMD | 完整命令 | 本地参数/位置参数（另有全局参数） | 施工阶段 | 主要业务案例 |
|---|---|---|---|---|
| CMD-001 | `hive inbox list` | `--view`, `--agent`, `--port`, `--type`, `--state`, `--urgency`, `--include-declined`, `--sort`, `--limit`, `--cursor` | P03 | AC-TASK-01, AC-TASK-02, AC-TASK-03, AC-TASK-04, AC-MSG-01 |
| CMD-002 | `hive inbox peek` | `TASK`, `--comments`, `--history`, `--mark-seen` | P03 | AC-TASK-01, AC-TASK-02, AC-TASK-03, AC-TASK-04, AC-MSG-01 |
| CMD-003 | `hive inbox claim` | `TASK...`, `--spec-version` | P03 | AC-TASK-01, AC-TASK-02, AC-TASK-03, AC-TASK-04, AC-MSG-01 |
| CMD-004 | `hive inbox decline` | `TASK`, `--reason` | P04 | AC-TIME-01 |
| CMD-005 | `hive inbox tree` | `TASK`, `--depth`, `--include-terminal`, `--include-comments` | P03 | AC-TASK-01, AC-TASK-02, AC-TASK-03, AC-TASK-04, AC-MSG-01 |
| CMD-006 | `hive inbox wait create` | `TASK`, `--on`, `--mode`, `--wait-for`, `--until`, `--checkpoint`, `--replace`, `--claim` | P05 | AC-WAIT-01, AC-WAIT-03, AC-WAIT-04, AC-WAIT-06 |
| CMD-007 | `hive inbox wait list` | `--task`, `--state`, `--limit`, `--cursor` | P05 | AC-WAIT-01, AC-WAIT-03, AC-WAIT-04, AC-WAIT-06 |
| CMD-008 | `hive inbox wait show` | `WAIT` | P05 | AC-WAIT-01, AC-WAIT-03, AC-WAIT-04, AC-WAIT-06 |
| CMD-009 | `hive inbox wait cancel` | `WAIT`, `--reason` | P05 | AC-WAIT-01, AC-WAIT-03, AC-WAIT-04, AC-WAIT-06 |
| CMD-010 | `hive emit new` | `--root`, `--parent`, `--spec-file`, `--to`, `--type`, `--title`, `--summary`, `--body`, `--body-file`, `--urgency`, `--reply-to`, `--data`, `--data-file`, `--attach`, `--callback`, `--claim-by`, `--claim-within`, `--expires-at`, `--expires-in` | P03/P05 | AC-TASK-01, AC-TASK-05, AC-TASK-08, AC-CB-01, AC-STORE-02 |
| CMD-011 | `hive emit done` | `TASK`, `--dispatch-file`, `--claim`, `--text`, `--text-file`, `--data`, `--data-file`, `--attach` | P03/P05 | AC-TASK-01, AC-TASK-05, AC-TASK-08, AC-CB-01, AC-STORE-02 |
| CMD-012 | `hive emit fail` | `TASK`, `--code`, `--claim`, `--reason`, `--text`, `--text-file`, `--data`, `--data-file`, `--attach` | P03/P05 | AC-TASK-01, AC-TASK-05, AC-TASK-08, AC-CB-01, AC-STORE-02 |
| CMD-013 | `hive emit trash` | `TASK`, `--instruction`, `--claim`, `--reason` | P03/P05 | AC-TASK-01, AC-TASK-05, AC-TASK-08, AC-CB-01, AC-STORE-02 |
| CMD-014 | `hive emit forward` | `TASK`, `--to`, `--claim-by`, `--claim-within`, `--claim`, `--reason`, `--if-version` | P03 | AC-TASK-06, AC-TASK-07, AC-CB-03 |
| CMD-015 | `hive emit comment` | `TASK`, `--mention`, `--revision-of`, `--checkpoint`, `--text`, `--text-file` | P04 | AC-MSG-02, AC-MSG-03 |
| CMD-016 | `hive emit batch` | `--file` | P05 | AC-TASK-08, AC-WAIT-06 |
| CMD-017 | `hive control system init` | `--listen`, `--default-harness`, `--owner-credential-out`, `--test-mode` | P00/P08 | AC-ENV-01, AC-ENV-02, AC-REC-03 |
| CMD-018 | `hive control system serve` | `--listen`, `--test-mode`, `--log-level` | P00/P08 | AC-ENV-01, AC-ENV-02, AC-REC-03 |
| CMD-019 | `hive control system start` | `--listen`, `--test-mode` | P00/P08 | AC-ENV-01, AC-ENV-02, AC-REC-03 |
| CMD-020 | `hive control system stop` | `--reason` | P00/P08 | AC-ENV-01, AC-ENV-02, AC-REC-03 |
| CMD-021 | `hive control system status` | 无 | P00/P08 | AC-ENV-01, AC-ENV-02, AC-REC-03 |
| CMD-022 | `hive control system config` | `--file`, `--reason`, `--if-version` | P09 | AC-CTRL-05 |
| CMD-023 | `hive control system log` | `--tail`, `--level`, `--since` | P09 | AC-CTRL-05 |
| CMD-024 | `hive control system backup` | `--out`, `--include-workspaces`, `--include-credentials` | P09 | AC-STORE-03, AC-STORE-04 |
| CMD-025 | `hive control system restore` | `--from`, `--into`, `--with-credentials` | P09 | AC-STORE-03, AC-STORE-04 |
| CMD-026 | `hive control agent list` | `--query`, `--state`, `--harness`, `--port`, `--limit`, `--cursor` | P02 | AC-ID-01, AC-ID-02, AC-ID-03, AC-ID-04, AC-ID-05 |
| CMD-027 | `hive control agent show` | `AGENT`, `--config`, `--runtime` | P02 | AC-ID-01, AC-ID-02, AC-ID-03, AC-ID-04, AC-ID-05 |
| CMD-028 | `hive control agent create` | `--file`, `--name`, `--harness`, `--model`, `--workspace`, `--auto-start`, `--start`, `--reason` | P02 | AC-ID-01, AC-ID-02, AC-ID-03, AC-ID-04, AC-ID-05 |
| CMD-029 | `hive control agent clone` | `AGENT`, `--name`, `--workspace`, `--start`, `--reason` | P02 | AC-ID-01, AC-ID-02, AC-ID-03, AC-ID-04, AC-ID-05 |
| CMD-030 | `hive control agent update` | `AGENT`, `--file`, `--replace`, `--apply`, `--reason`, `--if-version` | P02 | AC-ID-01, AC-ID-02, AC-ID-03, AC-ID-04, AC-ID-05 |
| CMD-031 | `hive control agent start` | `AGENT`, `--callback` | P06 | AC-RUN-01, AC-RUN-02, AC-RUN-03, AC-RUN-04, AC-RUN-05, AC-RUN-06 |
| CMD-032 | `hive control agent wake` | `AGENT`, `--message` | P06 | AC-RUN-01, AC-RUN-02, AC-RUN-03, AC-RUN-04, AC-RUN-05, AC-RUN-06 |
| CMD-033 | `hive control agent send` | `AGENT`, `--mode`, `--fallback`, `--text`, `--text-file`, `--reason`, `--instance`, `--callback` | P06 | AC-RUN-01, AC-RUN-02, AC-RUN-03, AC-RUN-04, AC-RUN-05, AC-RUN-06 |
| CMD-034 | `hive control agent stop` | `AGENT`, `--grace`, `--force`, `--reason`, `--instance`, `--callback` | P06 | AC-RUN-01, AC-RUN-02, AC-RUN-03, AC-RUN-04, AC-RUN-05, AC-RUN-06 |
| CMD-035 | `hive control agent restart` | `AGENT`, `--grace`, `--force`, `--reason`, `--instance`, `--callback` | P06 | AC-RUN-01, AC-RUN-02, AC-RUN-03, AC-RUN-04, AC-RUN-05, AC-RUN-06 |
| CMD-036 | `hive control agent model` | `AGENT`, `--model`, `--default`, `--apply`, `--reason`, `--if-version` | P06 | AC-RUN-01, AC-RUN-02, AC-RUN-03, AC-RUN-04, AC-RUN-05, AC-RUN-06 |
| CMD-037 | `hive control agent retire` | `AGENT`, `--plan-file`, `--dry-run`, `--reason`, `--callback` | P09 | AC-CTRL-04, AC-CB-05 |
| CMD-038 | `hive control port list` | `--query`, `--handler`, `--state`, `--limit`, `--cursor` | P03 | AC-PORT-01, AC-PORT-02, AC-PORT-03, AC-PORT-04 |
| CMD-039 | `hive control port show` | `PORT`, `--subscribers` | P03 | AC-PORT-01, AC-PORT-02, AC-PORT-03, AC-PORT-04 |
| CMD-040 | `hive control port create` | `--route`, `--file`, `--name`, `--handler`, `--channel`, `--reason` | P03 | AC-PORT-01, AC-PORT-02, AC-PORT-03, AC-PORT-04 |
| CMD-041 | `hive control port update` | `PORT`, `--file`, `--replace`, `--reason`, `--if-version` | P03 | AC-PORT-01, AC-PORT-02, AC-PORT-03, AC-PORT-04 |
| CMD-042 | `hive control port pause` | `PORT`, `--reason` | P03 | AC-PORT-01, AC-PORT-02, AC-PORT-03, AC-PORT-04 |
| CMD-043 | `hive control port resume` | `PORT`, `--reason` | P03 | AC-PORT-01, AC-PORT-02, AC-PORT-03, AC-PORT-04 |
| CMD-044 | `hive control port retire` | `PORT`, `--plan-file`, `--dry-run`, `--reason`, `--callback` | P03 | AC-PORT-01, AC-PORT-02, AC-PORT-03, AC-PORT-04 |
| CMD-045 | `hive control subscription list` | `--agent`, `--port`, `--enabled`, `--limit`, `--cursor` | P03 | AC-PORT-01, AC-PORT-02, AC-PORT-03, AC-PORT-04 |
| CMD-046 | `hive control subscription add` | `--agent`, `--port`, `--reason` | P03 | AC-PORT-01, AC-PORT-02, AC-PORT-03, AC-PORT-04 |
| CMD-047 | `hive control subscription pause` | `--agent`, `--port`, `--reason` | P03 | AC-PORT-01, AC-PORT-02, AC-PORT-03, AC-PORT-04 |
| CMD-048 | `hive control subscription resume` | `--agent`, `--port`, `--reason` | P03 | AC-PORT-01, AC-PORT-02, AC-PORT-03, AC-PORT-04 |
| CMD-049 | `hive control subscription remove` | `--agent`, `--port`, `--reason` | P03 | AC-PORT-01, AC-PORT-02, AC-PORT-03, AC-PORT-04 |
| CMD-050 | `hive control task list` | `--source`, `--agent`, `--port`, `--root`, `--state`, `--type`, `--since`, `--limit`, `--cursor` | P03/P09 | AC-TASK-07, AC-TIME-01, AC-TIME-04, AC-CTRL-04 |
| CMD-051 | `hive control task show` | `TASK`, `--history` | P03/P09 | AC-TASK-07, AC-TIME-01, AC-TIME-04, AC-CTRL-04 |
| CMD-052 | `hive control task amend` | `TASK`, `--file`, `--reason`, `--if-version` | P03/P09 | AC-TASK-07, AC-TIME-01, AC-TIME-04, AC-CTRL-04 |
| CMD-053 | `hive control task reclaim` | `TASK`, `--to`, `--stop-owner`, `--reason`, `--if-version` | P03/P09 | AC-TASK-07, AC-TIME-01, AC-TIME-04, AC-CTRL-04 |
| CMD-054 | `hive control task forward` | `TASK`, `--to`, `--reason`, `--if-version` | P03/P09 | AC-TASK-07, AC-TIME-01, AC-TIME-04, AC-CTRL-04 |
| CMD-055 | `hive control task trash` | `TASK`, `--cascade`, `--dry-run`, `--reason`, `--if-version` | P03/P09 | AC-TASK-07, AC-TIME-01, AC-TIME-04, AC-CTRL-04 |
| CMD-056 | `hive control permission list` | `--subject`, `--limit`, `--cursor` | P02 | AC-AUTH-01, AC-AUTH-02, AC-AUTH-03, AC-AUTH-04 |
| CMD-057 | `hive control permission grant` | `--file`, `--expires-at`, `--reason` | P02 | AC-AUTH-01, AC-AUTH-02, AC-AUTH-03, AC-AUTH-04 |
| CMD-058 | `hive control permission revoke` | `GRANT`, `--reason` | P02 | AC-AUTH-01, AC-AUTH-02, AC-AUTH-03, AC-AUTH-04 |
| CMD-059 | `hive control operation list` | `--state`, `--target`, `--request`, `--limit`, `--cursor` | P09 | AC-CLI-03, AC-CTRL-01, AC-CTRL-02, AC-CTRL-03 |
| CMD-060 | `hive control operation show` | `OP`, `--wait-seconds` | P09 | AC-CLI-03, AC-CTRL-01, AC-CTRL-02, AC-CTRL-03 |
| CMD-061 | `hive control operation cancel` | `OP`, `--reason` | P09 | AC-CLI-03, AC-CTRL-01, AC-CTRL-02, AC-CTRL-03 |
| CMD-062 | `hive control event list` | `--entity`, `--kind`, `--since`, `--limit`, `--cursor` | P08/P09 | AC-SCHED-05, AC-REC-04, AC-CTRL-05 |
| CMD-063 | `hive control audit list` | `--actor`, `--target`, `--request`, `--since`, `--limit`, `--cursor` | P08/P09 | AC-SCHED-05, AC-REC-04, AC-CTRL-05 |
| CMD-064 | `hive control delivery list` | `--state`, `--task`, `--target`, `--limit`, `--cursor` | P05/P09 | AC-CB-04, AC-REC-02, AC-CTRL-05 |
| CMD-065 | `hive control delivery retry` | `DELIVERY`, `--reason` | P05/P09 | AC-CB-04, AC-REC-02, AC-CTRL-05 |
| CMD-066 | `hive control harness list` | 无 | P06 | AC-HAR-01, AC-HAR-02, AC-HAR-03 |
| CMD-067 | `hive control harness show` | `NAME` | P06 | AC-HAR-01, AC-HAR-02, AC-HAR-03 |
| CMD-068 | `hive control harness register` | `--file`, `--replace`, `--reason` | P06 | AC-HAR-01, AC-HAR-02, AC-HAR-03 |
| CMD-069 | `hive control harness validate` | `NAME`, `--smoke`, `--workspace`, `--model`, `--alternate-model` | P06 | AC-HAR-01, AC-HAR-02, AC-HAR-03 |
| CMD-070 | `hive control harness unregister` | `NAME`, `--reason` | P06 | AC-HAR-01, AC-HAR-02, AC-HAR-03 |
| CMD-071 | `hive control producer list` | `--limit`, `--cursor` | P07 | AC-PROD-01, AC-PROD-02 |
| CMD-072 | `hive control producer show` | `PRODUCER` | P07 | AC-PROD-01, AC-PROD-02 |
| CMD-073 | `hive control producer create` | `--name`, `--kind`, `--allow-target`, `--credential-out`, `--reason` | P07 | AC-PROD-01, AC-PROD-02 |
| CMD-074 | `hive control producer update` | `PRODUCER`, `--file`, `--reason`, `--if-version` | P07 | AC-PROD-01, AC-PROD-02 |
| CMD-075 | `hive control producer credential` | `PRODUCER`, `--out`, `--revoke-previous`, `--reason` | P07 | AC-PROD-01, AC-PROD-02 |
| CMD-076 | `hive control producer revoke` | `PRODUCER`, `--reason` | P07 | AC-PROD-01, AC-PROD-02 |
| CMD-077 | `hive control trigger list` | `--enabled`, `--limit`, `--cursor` | P07 | AC-PROD-03, AC-PROD-04 |
| CMD-078 | `hive control trigger show` | `TRIGGER` | P07 | AC-PROD-03, AC-PROD-04 |
| CMD-079 | `hive control trigger create` | `--name`, `--kind`, `--producer`, `--task-file`, `--every`, `--cron`, `--timezone`, `--enabled`, `--missed` | P07 | AC-PROD-03, AC-PROD-04 |
| CMD-080 | `hive control trigger update` | `TRIGGER`, `--file`, `--reason`, `--if-version` | P07 | AC-PROD-03, AC-PROD-04 |
| CMD-081 | `hive control trigger fire` | `TRIGGER`, `--event-id`, `--data` | P07 | AC-PROD-03, AC-PROD-04 |
| CMD-082 | `hive control trigger enable` | `TRIGGER`, `--reason` | P07 | AC-PROD-03, AC-PROD-04 |
| CMD-083 | `hive control trigger disable` | `TRIGGER`, `--reason` | P07 | AC-PROD-03, AC-PROD-04 |
| CMD-084 | `hive control channel list` | `--limit`, `--cursor` | P07 | AC-HUM-03, AC-PROD-05, AC-CB-04 |
| CMD-085 | `hive control channel show` | `CHANNEL` | P07 | AC-HUM-03, AC-PROD-05, AC-CB-04 |
| CMD-086 | `hive control channel register` | `--file`, `--replace`, `--reason` | P07 | AC-HUM-03, AC-PROD-05, AC-CB-04 |
| CMD-087 | `hive control channel update` | `CHANNEL`, `--file`, `--reason`, `--if-version` | P07 | AC-HUM-03, AC-PROD-05, AC-CB-04 |
| CMD-088 | `hive control channel disable` | `CHANNEL`, `--reason` | P07 | AC-HUM-03, AC-PROD-05, AC-CB-04 |
| CMD-089 | `hive control human list` | `--port`, `--state`, `--limit`, `--cursor` | P07 | AC-HUM-01, AC-HUM-02, AC-HUM-03, AC-HUM-04 |
| CMD-090 | `hive control human answer` | `TASK`, `--spec-version`, `--text`, `--text-file`, `--data`, `--data-file`, `--attach` | P07 | AC-HUM-01, AC-HUM-02, AC-HUM-03, AC-HUM-04 |
| CMD-091 | `hive control human fail` | `TASK`, `--spec-version`, `--reason`, `--text`, `--text-file`, `--data`, `--data-file` | P07 | AC-HUM-01, AC-HUM-02, AC-HUM-03, AC-HUM-04 |
| CMD-092 | `hive control human trash` | `TASK`, `--spec-version`, `--reason`, `--text`, `--text-file`, `--data`, `--data-file` | P07 | AC-HUM-01, AC-HUM-02, AC-HUM-03, AC-HUM-04 |
| CMD-093 | `hive control test seed` | `--scenario`, `--out` | P00/P06 | AC-ENV-02, AC-TIME-02, AC-REC-01, AC-REC-04, AC-RUN-05 |
| CMD-094 | `hive control test clock advance` | `--by` | P00/P06 | AC-ENV-02, AC-TIME-02, AC-REC-01, AC-REC-04, AC-RUN-05 |
| CMD-095 | `hive control test fault set` | `--point`, `--mode`, `--value` | P00/P06 | AC-ENV-02, AC-TIME-02, AC-REC-01, AC-REC-04, AC-RUN-05 |
| CMD-096 | `hive control test fault clear` | `--point` | P00/P06 | AC-ENV-02, AC-TIME-02, AC-REC-01, AC-REC-04, AC-RUN-05 |
| CMD-097 | `hive control test harness` | `AGENT`, `--action`, `--value` | P00/P06 | AC-ENV-02, AC-TIME-02, AC-REC-01, AC-REC-04, AC-RUN-05 |


## 5. 每个CLI参数必须执行的检查

### 5.1 全局参数

| 编号 | 参数 | 操作步骤 | 预期结果 |
|---|---|---|---|
| G01 | --home | 使用新路径、含空格路径、已有数据根；重复init | 正确定位且不覆盖已有定义；不误用旧trigger目录 |
| G02 | --endpoint | 在线命令指定测试endpoint，再指定不可达endpoint | 前者读写正确服务；后者明确通信错误；本地init/serve/restore不伪装远端调用 |
| G03 | --credential-file | 有权、无权、已撤销、缺文件四组 | 正确成功/拒绝，不读取或显示其他主体秘密 |
| G04 | --format | json与text各执行一次查询/变更 | json单对象且可解析；text人类可读但不改变业务行为 |
| G05 | --request-id | 同key同输入、同key不同输入、不同主体同key | 重放原结果、冲突、主体隔离，分别留证 |
| G06 | --timeout | 极短网络超时并查询原request | 只影响等待响应；业务不自动cancel，重试可找回原操作 |
| G07 | --help | 每一层与每个叶子执行 | 无凭据/无服务也能显示；不产生业务写入 |
| G08 | --version | 不连接服务执行 | 返回程序和协议版本，exit0 |
| G09 | 重复/未知 | 重复全局flag或加不存在flag | INVALID_ARGUMENT，不静默取后者或忽略 |
| G10 | 日志/编码 | 中文、空格、引号、JSON文件及错误堆栈 | 原文完整；stdout不混日志；没有凭据正文 |

### 5.2 对每个命令的每个本地参数执行以下程序

1. 从DESIGN对应CMD参数表取类型、默认、必填、互斥和权限；建立唯一测试记录键 `CMD-ID/parameter-name/test-kind`。
2. 构造该命令最小合法前置与输入，执行一次；读取对象/操作结果确认字段实际生效，不仅检查exit0。
3. 对可选参数省略再执行新对象，确认默认值；对必填参数省略，确认没有业务副作用。
4. enum逐个合法值至少一次，并给一个非法值；数值覆盖最小/最大/越界/非数字；duration覆盖s/m/h/d、none允许与不允许的位置；时间覆盖时区与无时区。
5. 引用字段覆盖合法、有权但已终态、不存在、无权四组；不能泄露不可见资源。
6. 文件字段覆盖合法UTF-8、BOM、中文空格路径、格式错误、路径不存在、超过上限；互斥字段覆盖单独有效与同时拒绝。
7. 可重复参数覆盖一次、多次、重复同值；检查去重规则而非默默复制。
8. --if-version、--spec-version、--instance、--claim各覆盖当前有效与过期值，确认旧写入不生效。
9. 写操作覆盖同request-id重放；读操作覆盖不改变责任/终态。管理命令验证权限边界及审计。
10. 输出参数验收明细：实际argv、expected、actual、state_before/after摘要、case引用、Pass/Fail。全部参数有记录才算AC-CLI-02通过。

### 5.3 特别需要防止“参数有名无实”的项目

- --callback none/self/agent/port：实际收件者与数量必须不同且符合设置。
- --dispatch-file / emit batch：事务回滚与幂等必须真实成立。
- --replace / --apply / --if-version：分别改变合并方式、生效时机和并发检查。
- --dry-run：必须无业务写入（查询审计可记录调用），之后正式执行才改变状态。
- --force / --grace：不能都只返回相同假成功；报告实际停止过程。
- --fallback：缺原生interrupt时reject与restart行为明确不同。
- --include-workspaces / --include-credentials / --with-credentials：备份内容和权限逐项检查；默认不复制秘密。
- --missed / --timezone：改变实际触发计划，不仅保存字符串。
- --mark-seen：只更新查看事实，不变成claim/done。
- --include-declined / --view / --sort / --cursor：集合、顺序和翻页符合约定。
- --cascade：普通取消只当前Task；显式级联列出每项处置与权限失败，不能隐式删除整个树。

## 6. 建议的真实并发执行方式

并发测试应使用独立客户端进程，两个主体竞争同Task，统计数据库里的有效责任，而不是只统计客户端“ok”。赢家自己的重复claim允许返回已有持有，因此20个ok也可能只有一个有效owner，不能只靠HTTP成功数断言。

可使用PowerShell Start-Job，每个job运行同一个CLI并传入独立凭据文件路径和不同request-id。所有job先等待同一屏障释放；30秒内未完成标超时并保存现场。只停止当前测试创建的job/进程，不按python或harness程序名批量结束。更推荐将相同逻辑放入项目tests/integration中的多进程测试，并保存每次实际请求和claim事件。

必须同时断言：Task.owner_subject唯一、有效claim唯一、claim_epoch符合一次获取、TaskClaimed有效事件只有一次、落败主体不能结算、没有错误的callback。

## 7. 验收报告与签收

### 7.1 每例报告

```json
{
  "case_id":"AC-TASK-02",
  "status":"NotRun",
  "started_at":null,
  "ended_at":null,
  "actual_commands_file":null,
  "assertions":[],
  "evidence":[],
  "related_ids":[],
  "notes":"此模板未执行，不代表通过"
}
```

状态只能是Pass/Fail/Blocked/NotRun；Blocked写明外部条件，不能自动算Pass。

### 7.2 总报告

包含版本/提交号、操作系统/Python版本、数据库schema版本、隔离home与endpoint、测试适配器和真实适配器版本、90例结果、97命令逐参数覆盖、失败与修复回归、已知增强能力缺失、证据路径、原trigger未受影响证明。

签收最低条件：

1. 90个案例均有实际状态，不留默认Pass；阻断项全部Pass。
2. 每个公开命令及其参数有实际检查记录，无NOT_IMPLEMENTED与静默忽略。
3. AC-HAR-02和AC-E2E-02有真实harness证据；缺少时只能签“核心fixture通过，真实接入待验收”。
4. 运行状态、Task结果、个人回执、文件产物互相可对照。
5. 施工方给出可复现README、恢复步骤与不含秘密的示例。

## 8. 收尾与保留

先保存所有证据，再明确停止当前run创建的Agent实例、trigger、mock webhook和测试daemon；用manifest核对ID与PID。保留测试数据和报告供复查，不自动删除目录。将未结测试任务的处置记录留在隔离账本，不能在退出脚本时静默清空。

验收不会执行旧trigger业务迁移、生产审批站部署或用户项目发布。通过之后如何上线与迁移，需要独立的施工范围。

