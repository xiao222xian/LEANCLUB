param(
    [ValidateRange(1,65535)][int]$Port = 4180,
    [ValidateRange(1,65535)][int]$ApiPort = 5381,
    [string]$Database,
    [string]$Python = 'python',
    [string]$OperationsConfig,
    [string]$MemberOrdersConfig
)

$ErrorActionPreference = 'Stop'
$taskRepo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskPython = (Get-Command $Python -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
if ($Port -eq $ApiPort) { throw '网页与 API 端口必须不同。' }
$taskListeners = @(Get-NetTCPConnection -State Listen -LocalPort $Port,$ApiPort -ErrorAction SilentlyContinue)
if ($taskListeners.Count -gt 0) { throw "端口 $Port 或 $ApiPort 已被占用；现有服务未作修改。" }
$taskDatabase = if ($Database) { [IO.Path]::GetFullPath($Database) } else { Join-Path $taskRepo 'data\local.sqlite3' }
$taskLauncher = Join-Path $taskRepo 'scripts\start-local.py'
if (($taskLauncher + $taskDatabase).IndexOf('"') -ge 0) { throw '路径不能包含双引号。' }
$taskRuntime = Join-Path $taskRepo 'data\runtime'
New-Item -ItemType Directory -Path $taskRuntime -Force | Out-Null
$taskOutput = Join-Path $taskRuntime "site-$Port.out.log"
$taskError = Join-Path $taskRuntime "site-$Port.err.log"
$taskArguments = @(('"{0}"' -f $taskLauncher), '--port', "$Port", '--api-port', "$ApiPort", '--db', ('"{0}"' -f $taskDatabase))
if ($OperationsConfig) {
    $taskOperationsConfig = (Resolve-Path -LiteralPath $OperationsConfig).Path
    if ($taskOperationsConfig.Contains('"')) { throw '配置路径不能包含双引号。' }
    $taskArguments += @('--operations-config', ('"{0}"' -f $taskOperationsConfig))
}
if ($MemberOrdersConfig) {
    $taskMemberOrdersConfig = (Resolve-Path -LiteralPath $MemberOrdersConfig).Path
    if ($taskMemberOrdersConfig.Contains('"')) { throw '配置路径不能包含双引号。' }
    $taskArguments += @('--member-orders-config', ('"{0}"' -f $taskMemberOrdersConfig))
}
# A separate hidden process keeps serving after the launching terminal exits.
$taskProcess = Start-Process -FilePath $taskPython -ArgumentList $taskArguments -WorkingDirectory $taskRepo -WindowStyle Hidden -RedirectStandardOutput $taskOutput -RedirectStandardError $taskError -PassThru
$taskOrigin = "http://127.0.0.1:$Port"
$taskDeadline = [DateTime]::UtcNow.AddSeconds(20)
$taskReady = $false
while ([DateTime]::UtcNow -lt $taskDeadline) {
    $taskProcess.Refresh()
    if ($taskProcess.HasExited) { throw "本地服务启动失败，请查看 $taskError" }
    try {
        $taskHealth = Invoke-RestMethod "$taskOrigin/api/club/health" -TimeoutSec 1
        if ($taskHealth.status -eq 'ok') { $taskReady = $true; break }
    } catch { }
    Start-Sleep -Milliseconds 250
}
if (-not $taskReady) { throw "本地服务未就绪，请查看 $taskError" }
@{ pid = $taskProcess.Id; startTime = $taskProcess.StartTime.ToUniversalTime().ToString('O'); origin = $taskOrigin; apiPort = $ApiPort; database = $taskDatabase; launcher = $taskLauncher } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskRuntime "site-$Port.json") -Encoding utf8
Write-Output "本地服务已在后台运行：$taskOrigin/"
Write-Output "投票：$taskOrigin/city-vote"
Write-Output "管理：$taskOrigin/admin"
Write-Output "运行记录：$taskRuntime"
