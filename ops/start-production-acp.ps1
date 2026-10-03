$ErrorActionPreference = 'Continue'

# SYSTEM scheduled task must use the real ZCode user's profile.
$env:USERPROFILE = 'C:\Users\Administrator'
$env:HOME = 'C:\Users\Administrator'
$env:APPDATA = 'C:\Users\Administrator\AppData\Roaming'
$env:LOCALAPPDATA = 'C:\Users\Administrator\AppData\Local'
$env:TEMP = 'C:\Windows\Temp'
$env:TMP = 'C:\Windows\Temp'
$env:Path = 'C:\Program Files\nodejs;C:\Program Files\nodejs\node_global;C:\Windows\System32;C:\Windows;' + $env:Path
$env:HIVE_ACP_ADAPTER = 'node D:/agent_workspace/projects/zcode-session-manager/plugins/session-manager/bin/zsession.mjs acp --stdio'
$env:HIVE_ACP_WRITER_KEY = '1'
$HiveCode = 'D:\agent_workspace\valuable-projects\hive'
$HiveHome = Join-Path $HiveCode '.hive'
$HiveCli = Join-Path $HiveCode 'cli\hive.py'
$LogOut = Join-Path $HiveHome 'logs\daemon.out.log'
$LogErr = Join-Path $HiveHome 'logs\daemon.err.log'
Set-Location -LiteralPath $HiveHome
& 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe' $HiveCli --home $HiveHome control system serve --listen 127.0.0.1:19962 >> $LogOut 2>> $LogErr
