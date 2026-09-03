param(
    [Parameter(Mandatory = $true)]
    [string]$ExecutablePath,
    [Parameter(Mandatory = $true)]
    [string]$DataDirectory,
    [string]$RepositoryRoot = ""
)

$ErrorActionPreference = "Stop"
$ExecutablePath = (Resolve-Path $ExecutablePath).Path
if ([string]::IsNullOrWhiteSpace($RepositoryRoot)) {
    $RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
} else {
    $RepositoryRoot = (Resolve-Path $RepositoryRoot).Path
}

function Get-AvailableLoopbackPort {
    $Listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    try {
        $Listener.Start()
        return ([System.Net.IPEndPoint]$Listener.LocalEndpoint).Port
    } finally {
        $Listener.Stop()
    }
}

function Stop-ApplicationProcessTree {
    param([System.Diagnostics.Process]$ApplicationProcess)

    if ($null -eq $ApplicationProcess) {
        return
    }
    $ApplicationProcess.Refresh()
    if ($ApplicationProcess.HasExited) {
        $ApplicationProcess.WaitForExit()
        return
    }

    & taskkill.exe /PID $ApplicationProcess.Id /T /F 2>$null | Out-Null
    $global:LASTEXITCODE = 0
    if (-not $ApplicationProcess.WaitForExit(10000)) {
        Stop-Process -Id $ApplicationProcess.Id -Force -ErrorAction SilentlyContinue
        if (-not $ApplicationProcess.WaitForExit(10000)) {
            throw "无法终止 Windows 冻结服务进程 $($ApplicationProcess.Id)。"
        }
    }
    # 无参数 WaitForExit 确保重定向的 stdout/stderr 已全部刷新到磁盘。
    $ApplicationProcess.WaitForExit()
}

$TemporaryRoot = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [System.IO.Path]::GetTempPath() }
$RunToken = [Guid]::NewGuid().ToString("N")
$RuntimeStdout = Join-Path $TemporaryRoot "risk-model-agent-$RunToken.stdout.log"
$RuntimeStderr = Join-Path $TemporaryRoot "risk-model-agent-$RunToken.stderr.log"
$Port = Get-AvailableLoopbackPort
$BaseUrl = "http://127.0.0.1:$Port"
$Process = $null
$Failure = $null
$LastProbeError = "尚未发起健康检查"

New-Item -ItemType Directory -Path $DataDirectory -Force | Out-Null
$env:RISK_AGENT_DATA_DIR = $DataDirectory
$env:RISK_AGENT_OPEN_BROWSER = "0"
$env:RISK_AGENT_PORT = [string]$Port

try {
    $Process = Start-Process -FilePath $ExecutablePath -RedirectStandardOutput $RuntimeStdout -RedirectStandardError $RuntimeStderr -PassThru
    $Ready = $false
    for ($Attempt = 1; $Attempt -le 90; $Attempt++) {
        if ($Process.HasExited) {
            throw "Windows 冻结服务在就绪前退出，退出码 $($Process.ExitCode)。"
        }
        try {
            $Health = Invoke-WebRequest -UseBasicParsing "$BaseUrl/api/v1/health"
            $RootPage = Invoke-WebRequest -UseBasicParsing "$BaseUrl/"
            if ($Health.StatusCode -eq 200 -and $Health.Content -match '"runtime":"local"' -and $RootPage.Content -match '<div id="root"></div>') {
                $Ready = $true
                break
            }
            $LastProbeError = "健康接口或首页返回内容不符合契约"
        } catch {
            $LastProbeError = $_.Exception.Message
        }
        Start-Sleep -Seconds 2
    }
    if (-not $Ready) {
        throw "Windows 冻结服务未在 180 秒内就绪；最后一次探测：$LastProbeError"
    }

    & python (Join-Path $RepositoryRoot "scripts\smoke_packaged_service.py") --url $BaseUrl
    if ($LASTEXITCODE -ne 0) {
        throw "Windows 冻结服务完整建模与评分冒烟失败，退出码 $LASTEXITCODE。"
    }
    if ($Process.HasExited) {
        throw "Windows 冻结服务在冒烟完成前退出，退出码 $($Process.ExitCode)。"
    }
} catch {
    $Failure = $_
} finally {
    try {
        Stop-ApplicationProcessTree -ApplicationProcess $Process
    } catch {
        if ($null -eq $Failure) {
            $Failure = $_
        } else {
            Write-Warning $_.Exception.Message
        }
    }
    $global:LASTEXITCODE = 0
}

$RuntimeLogs = @($RuntimeStdout, $RuntimeStderr) | Where-Object { Test-Path $_ }
$RuntimeError = if ($RuntimeLogs.Count -gt 0) {
    Select-String -Path $RuntimeLogs -Pattern "httptools|HttpParser|Traceback \(most recent call last\)|Exception in callback" -Quiet
} else {
    $false
}
if ($RuntimeError) {
    Get-Content $RuntimeLogs -ErrorAction SilentlyContinue
    throw "Windows 冻结服务日志出现 HTTP 解析器或未处理异常。"
}
if ($null -ne $Failure) {
    Get-Content $RuntimeLogs -ErrorAction SilentlyContinue
    throw $Failure
}

Write-Host "Windows frozen service startup, HTTP, modeling and scoring smoke passed on $BaseUrl."
