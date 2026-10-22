param([ValidateRange(1,65535)][int]$Port = 4180)
$ErrorActionPreference = 'Stop'
$taskRepo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskRecord = Join-Path $taskRepo "data\runtime\site-$Port.json"
if (-not (Test-Path -LiteralPath $taskRecord)) { throw "没有找到端口 $Port 的后台运行记录。" }
$taskInfo = Get-Content -LiteralPath $taskRecord -Raw | ConvertFrom-Json
$taskLauncher = Join-Path $taskRepo 'scripts\start-local.py'
$taskProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$taskInfo.pid)"
if (-not $taskProcess) { Write-Output '本地服务已经停止。'; exit 0 }
if (-not $taskProcess.CommandLine.Contains($taskLauncher)) { throw '进程与运行记录不一致，未停止任何服务。' }
$taskApi = Join-Path $taskRepo 'backend\server.py'
$taskPreview = Join-Path $taskRepo 'scripts\preview.py'
$taskChildren = Get-CimInstance Win32_Process -Filter "ParentProcessId=$([int]$taskInfo.pid)" | Where-Object {
    $_.CommandLine -and ($_.CommandLine.Contains($taskApi) -or $_.CommandLine.Contains($taskPreview))
}
foreach ($taskChild in $taskChildren) { Stop-Process -Id $taskChild.ProcessId -ErrorAction SilentlyContinue }
# The launcher notices its owned child exited and cleans up the remaining child.
Write-Output "本次后台运行的本地服务已停止：端口 $Port。"
