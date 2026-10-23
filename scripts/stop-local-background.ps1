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
if ($taskInfo.startTime -and (Get-Process -Id $taskProcess.ProcessId).StartTime.ToUniversalTime().Ticks -ne ([DateTime]$taskInfo.startTime).ToUniversalTime().Ticks) {
    throw '运行进程已变化，未停止任何服务。'
}
$taskApi = Join-Path $taskRepo 'backend\server.py'
$taskPreview = Join-Path $taskRepo 'scripts\preview.py'
# Windows venv python.exe is a redirector: the actual launcher and services can be
# grandchildren. Walk only this verified process tree, never global Python PIDs.
$taskSnapshot = @(Get-CimInstance Win32_Process)
$taskOwned = [System.Collections.Generic.List[object]]::new()
$taskOwned.Add($taskProcess)
for ($taskIndex = 0; $taskIndex -lt $taskOwned.Count; $taskIndex++) {
    foreach ($taskChild in $taskSnapshot | Where-Object { $_.ParentProcessId -eq $taskOwned[$taskIndex].ProcessId }) {
        if ($taskChild.Name -eq 'conhost.exe' -and $taskChild.ExecutablePath -eq (Join-Path $env:WINDIR 'System32\conhost.exe')) { continue }
        if (-not $taskChild.CommandLine -or -not (
            $taskChild.CommandLine.Contains($taskLauncher) -or
            $taskChild.CommandLine.Contains($taskApi) -or
            $taskChild.CommandLine.Contains($taskPreview))) { throw '运行树包含未知进程，未停止服务。' }
        $taskOwned.Add($taskChild)
    }
}
for ($taskIndex = $taskOwned.Count - 1; $taskIndex -ge 0; $taskIndex--) {
    $taskChild = $taskOwned[$taskIndex]
    $taskCurrent = Get-CimInstance Win32_Process -Filter "ProcessId=$($taskChild.ProcessId)"
    if (-not $taskCurrent) { continue }
    if ($taskCurrent.CreationDate.ToUniversalTime().Ticks -ne $taskChild.CreationDate.ToUniversalTime().Ticks) {
        throw '进程ID已复用，停止操作中止。'
    }
    Stop-Process -Id $taskChild.ProcessId -ErrorAction SilentlyContinue
}
Write-Output "本次后台运行的本地服务已停止：端口 $Port。"
