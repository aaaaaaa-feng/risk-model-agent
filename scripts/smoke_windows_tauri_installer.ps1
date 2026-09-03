param(
    [string]$InstallerPath = "",
    [string]$LegacyInstallerPath = "",
    [string]$RepositoryRoot = ""
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RepositoryRoot)) {
    $RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
} else {
    $RepositoryRoot = (Resolve-Path $RepositoryRoot).Path
}
$PythonExecutable = (Get-Command "python.exe" -ErrorAction Stop).Source
$DesktopCookieReader = Join-Path $RepositoryRoot "scripts\read_webview_cookie.py"
if (-not (Test-Path $DesktopCookieReader -PathType Leaf)) {
    throw "缺少 WebView2 会话读取脚本，已阻止桌面安装包冒烟。"
}
if ([string]::IsNullOrWhiteSpace($InstallerPath)) {
    $Candidates = @(Get-ChildItem (Join-Path $RepositoryRoot "dist\installer") -Filter "RiskModelAgent-*-windows-x64-setup.exe" -File)
    if ($Candidates.Count -ne 1) {
        throw "正式产物目录必须恰好包含一个 Tauri Windows 安装包，当前找到 $($Candidates.Count) 个。"
    }
    $InstallerPath = $Candidates[0].FullName
} else {
    $InstallerPath = (Resolve-Path $InstallerPath).Path
}
if ([string]::IsNullOrWhiteSpace($LegacyInstallerPath)) {
    $LegacyInstallerPath = Join-Path $RepositoryRoot "dist\fixtures\legacy-inno\RiskModelAgent-1.1.2-windows-x64-setup.exe"
}
$LegacyInstallerPath = (Resolve-Path $LegacyInstallerPath).Path
$ExpectedLegacyHash = "b0d3ce62632a95ffd72e76ac27c49727af11d856ee74d22586190b5efaf27636"
$ActualLegacyHash = (Get-FileHash -Path $LegacyInstallerPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($ActualLegacyHash -ne $ExpectedLegacyHash) {
    throw "真实 1.1.2 Inno 安装器 SHA-256 不匹配，已阻止迁移冒烟。"
}

$Installer = Get-Item $InstallerPath
$ArtifactDirectory = $Installer.Directory.FullName
$ManifestPath = Join-Path $ArtifactDirectory "tauri-installer-manifest.json"
$ChecksumPath = "$InstallerPath.sha256"
$SizeReportPath = Join-Path $ArtifactDirectory "package-size-report.json"
if (-not (Test-Path $ManifestPath -PathType Leaf) -or -not (Test-Path $ChecksumPath -PathType Leaf) -or -not (Test-Path $SizeReportPath -PathType Leaf)) {
    throw "安装包缺少 Tauri 产物清单、SHA-256 或体积报告，已阻止冒烟。"
}
$Manifest = Get-Content $ManifestPath -Raw | ConvertFrom-Json
$SizeReport = Get-Content $SizeReportPath -Raw | ConvertFrom-Json
$ActualHash = (Get-FileHash -Path $InstallerPath -Algorithm SHA256).Hash.ToLowerInvariant()
$ChecksumText = (Get-Content $ChecksumPath -Raw).Trim()
if ($Manifest.schema_version -ne "risk-tauri-installer/v1" -or $Manifest.package_kind -ne "tauri-nsis") {
    throw "产物清单不能证明该安装包来自 Tauri NSIS。"
}
if ($Manifest.architecture -ne "windows-x64" -or [int64]($Manifest.bytes) -ne $Installer.Length) {
    throw "Tauri 安装包的架构或文件大小与产物清单不一致。"
}
if ($Manifest.file_name -ne $Installer.Name -or $Manifest.sha256 -ne $ActualHash -or $ChecksumText -notmatch "^$ActualHash\s+$([regex]::Escape($Installer.Name))$") {
    throw "Tauri 安装包的文件名或 SHA-256 与产物清单不一致。"
}
if ($SizeReport.schema_version -ne "risk-package-size-report/v1" -or $SizeReport.valid -ne $true -or [int64]($SizeReport.installer.bytes) -ne $Installer.Length) {
    throw "Tauri 安装包体积或依赖边界未通过发布门禁。"
}
$AllowedArtifactFiles = @(
    $Installer.Name,
    (Split-Path $ChecksumPath -Leaf),
    (Split-Path $ManifestPath -Leaf),
    (Split-Path $SizeReportPath -Leaf)
)
$UnexpectedArtifactFiles = @(
    Get-ChildItem -Path $ArtifactDirectory -File -Recurse |
        Where-Object {
            $_.Directory.FullName -ne $ArtifactDirectory -or $_.Name -notin $AllowedArtifactFiles
        }
)
if ($UnexpectedArtifactFiles.Count -gt 0) {
    throw "正式 Windows artifact 含未识别文件，已阻止发布：$($UnexpectedArtifactFiles.FullName -join ', ')。"
}

$Version = [string]$Manifest.version
$TauriConfigPath = Join-Path $RepositoryRoot "desktop\src-tauri\tauri.conf.json"
$TauriConfig = Get-Content $TauriConfigPath -Raw | ConvertFrom-Json
$ProductName = [string]$TauriConfig.productName
if ($TauriConfig.version -ne $Version -or [string]::IsNullOrWhiteSpace($ProductName)) {
    throw "Tauri 配置与正式安装包清单的产品名或版本不一致。"
}
if ($ProductName -notmatch '\s' -or $ProductName -notmatch '[^\x00-\x7F]') {
    throw "Windows 真实安装冒烟要求产品名同时覆盖中文和空格路径。"
}
$TemporaryRoot = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [System.IO.Path]::GetTempPath() }
$RunToken = [Guid]::NewGuid().ToString("N")
$InstallDirectory = Join-Path $env:LOCALAPPDATA $ProductName
$LegacyInstallDirectory = Join-Path $env:LOCALAPPDATA "Programs\RiskModelAgent"
$LegacyRuntimeDataDirectory = Join-Path $TemporaryRoot "RMA-Inno-Data-$RunToken"
$DataDirectory = $LegacyRuntimeDataDirectory
$MigrationEvidencePath = Join-Path $LegacyRuntimeDataDirectory "migration-evidence-$RunToken.json"
$WorkspaceSelectionDirectory = Join-Path $TemporaryRoot "风控 项目工作区-$RunToken"
# Tauri 2.11.5 的 Windows app_log_dir 使用 dirs::data_local_dir() 而非 roaming AppData。
$DesktopLogDirectory = Join-Path $env:LOCALAPPDATA "com.feng.riskmodelagent\logs"
$LegacyRegistryPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{4CE3329A-CF6F-49E0-86C7-BE5C38DB1474}_is1"
$TauriRegistryPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$ProductName"
$LegacyDataDirectory = Join-Path $env:LOCALAPPDATA "RiskModelAgent"
$MigrationSentinel = Join-Path $LegacyDataDirectory "migration-must-survive-$RunToken.txt"
$ClientStartedAt = $null
$ClientProcess = $null
$SmokeProcess = $null
$WorkspaceBackendProcess = $null
$RecoveryTriggerProcess = $null
$BackendProcessId = $null
$Uninstaller = $null
$LegacyUninstaller = Join-Path $LegacyInstallDirectory "unins000.exe"
$UninstallCompleted = $false
$CorruptFixtureCreated = $false
$ForgedInstallDirectory = $null
$DesktopWebSession = $null
$DesktopSessionCookie = $null
$WebViewDebugPort = $null
$Failure = $null

$BrowserNames = @("chrome", "msedge", "firefox", "brave", "opera", "vivaldi")
$BrowserIdsBefore = [System.Collections.Generic.HashSet[int]]::new()
Get-Process -ErrorAction SilentlyContinue |
    Where-Object { $BrowserNames -contains $_.ProcessName.ToLowerInvariant() } |
    ForEach-Object { [void]$BrowserIdsBefore.Add($_.Id) }
$ObservedNewBrowsers = [System.Collections.Generic.HashSet[int]]::new()

function Observe-NewSystemBrowsers {
    Get-Process -ErrorAction SilentlyContinue |
        Where-Object { $BrowserNames -contains $_.ProcessName.ToLowerInvariant() } |
        ForEach-Object {
            if (-not $BrowserIdsBefore.Contains($_.Id)) {
                [void]$ObservedNewBrowsers.Add($_.Id)
            }
        }
}

function Get-DescendantProcesses {
    param([Parameter(Mandatory = $true)][int]$RootProcessId)

    $Rows = @(Get-CimInstance Win32_Process)
    $Pending = [System.Collections.Generic.Queue[uint32]]::new()
    $Known = [System.Collections.Generic.HashSet[uint32]]::new()
    $Results = [System.Collections.Generic.List[object]]::new()
    $Pending.Enqueue([uint32]$RootProcessId)
    [void]$Known.Add([uint32]$RootProcessId)
    while ($Pending.Count -gt 0) {
        $Parent = $Pending.Dequeue()
        foreach ($Row in $Rows | Where-Object { $_.ParentProcessId -eq $Parent }) {
            $ProcessId = [uint32]$Row.ProcessId
            if ($Known.Add($ProcessId)) {
                $Results.Add($Row)
                $Pending.Enqueue($ProcessId)
            }
        }
    }
    return @($Results)
}

function Get-BackendLoopbackPorts {
    param([Parameter(Mandatory = $true)][int]$ProcessId)

    $Ports = @()
    try {
        $Ports = @(
            Get-NetTCPConnection -State Listen -OwningProcess $ProcessId -ErrorAction Stop |
                Where-Object { $_.LocalAddress -in @("127.0.0.1", "::1") } |
                Select-Object -ExpandProperty LocalPort -Unique
        )
    } catch {
        # Get-NetTCPConnection 在部分精简 Windows 环境不可用，退回系统 netstat。
        $Pattern = "^\s*TCP\s+127\.0\.0\.1:(?<port>\d+)\s+\S+\s+LISTENING\s+$ProcessId\s*$"
        $Ports = @(
            & netstat.exe -ano -p tcp |
                ForEach-Object {
                    if ($_ -match $Pattern) { [int]$Matches.port }
                } |
                Select-Object -Unique
        )
    }
    return @($Ports)
}

function Stop-ProcessTreeForCleanup {
    param([System.Diagnostics.Process]$Process)

    if ($null -eq $Process) { return }
    $Process.Refresh()
    if ($Process.HasExited) {
        $Process.WaitForExit()
        return
    }

    $TaskKillExitCode = -1
    $TaskKillError = $null
    try {
        $TaskKill = Start-Process -FilePath "taskkill.exe" -ArgumentList @(
            "/PID",
            "$($Process.Id)",
            "/T",
            "/F"
        ) -Wait -PassThru -NoNewWindow
        $TaskKillExitCode = $TaskKill.ExitCode
    } catch {
        $TaskKillError = $_.Exception.Message
    }
    if ($Process.WaitForExit(10000)) { return }

    $StopProcessError = $null
    try {
        Stop-Process -Id $Process.Id -Force -ErrorAction Stop
    } catch {
        $StopProcessError = $_.Exception.Message
    }
    if ($Process.WaitForExit(10000)) { return }

    $TaskKillDetail = if ($TaskKillError) { $TaskKillError } else { "退出码 $TaskKillExitCode" }
    $StopDetail = if ($StopProcessError) { $StopProcessError } else { "Stop-Process 已执行但进程未退出" }
    throw "无法在 20 秒内回收进程 $($Process.Id)。taskkill：$TaskKillDetail；Stop-Process：$StopDetail。"
}

function Find-UniqueUninstaller {
    param(
        [Parameter(Mandatory = $true)][string]$Directory,
        [Parameter(Mandatory = $true)][string]$Filter,
        [Parameter(Mandatory = $true)][string]$ProductName
    )

    if (-not (Test-Path $Directory -PathType Container)) { return $null }
    $Candidates = @(Get-ChildItem -Path $Directory -Filter $Filter -File -Recurse -ErrorAction SilentlyContinue)
    if ($Candidates.Count -gt 1) {
        throw "$ProductName 目录中发现多个卸载程序，无法安全选择：$($Candidates.Name -join ', ')。"
    }
    if ($Candidates.Count -eq 1) { return $Candidates[0].FullName }
    return $null
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

function New-AuthenticatedDesktopWebSession {
    param(
        [Parameter(Mandatory = $true)][string]$BaseUrl,
        [Parameter(Mandatory = $true)][int]$DebugPort,
        [Parameter(Mandatory = $true)][string]$PythonPath,
        [Parameter(Mandatory = $true)][string]$ReaderScript
    )

    # 成功时 Python 只输出 Cookie 值。这里必须捕获到变量，
    # 不能让 HttpOnly 会话出现在 GitHub Actions 日志中。
    $CookieOutput = @(
        & $PythonPath $ReaderScript `
            --debug-port ([string]$DebugPort) `
            --backend-url $BaseUrl `
            --cookie-name "risk_agent_desktop_session" `
            --timeout "45"
    )
    if ($LASTEXITCODE -ne 0 -or $CookieOutput.Count -ne 1) {
        throw "无法从真实 WebView 取得唯一桌面会话，已阻止冒烟。"
    }
    $CookieValue = [string]$CookieOutput[0]
    if ($CookieValue -notmatch '^[0-9a-f]{64}$') {
        throw "WebView 返回的桌面会话格式无效，已阻止冒烟。"
    }

    $Session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
    $SessionCookie = [System.Net.Cookie]::new(
        "risk_agent_desktop_session",
        $CookieValue,
        "/",
        "127.0.0.1"
    )
    $SessionCookie.HttpOnly = $true
    [void]$Session.Cookies.Add([Uri]$BaseUrl, $SessionCookie)
    $RootPage = Invoke-WebRequest `
        -UseBasicParsing `
        -Uri "$BaseUrl/" `
        -WebSession $Session `
        -TimeoutSec 10
    if ($RootPage.StatusCode -ne 200 -or $RootPage.Content -notmatch '<div id="root"></div>') {
        throw "WebView 会话无法访问受保护的应用首页。"
    }
    return [PSCustomObject]@{
        WebSession = $Session
        CookieValue = $CookieValue
    }
}

function Wait-ForLocalService {
    param(
        [Parameter(Mandatory = $true)][System.Diagnostics.Process]$Process,
        [Parameter(Mandatory = $true)][string]$BaseUrl,
        [int]$TimeoutSeconds = 180
    )

    $Deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    $LastProbe = "尚未发起健康检查"
    while ([DateTime]::UtcNow -lt $Deadline) {
        $Process.Refresh()
        if ($Process.HasExited) {
            throw "冻结后端在本地服务就绪前退出，退出码 $($Process.ExitCode)。"
        }
        try {
            $Health = Invoke-RestMethod "$BaseUrl/api/v1/health" -TimeoutSec 3
            if ($Health.status -eq "ok" -and $Health.runtime -eq "local" -and $Health.version -eq $Version) {
                return
            }
            $LastProbe = "健康接口返回内容不符合本地运行契约"
        } catch {
            $LastProbe = $_.Exception.Message
        }
        Start-Sleep -Milliseconds 500
    }
    throw "冻结后端未在 $TimeoutSeconds 秒内就绪；最后一次探测：$LastProbe"
}

function Invoke-NativePickerCancelSmoke {
    param(
        [Parameter(Mandatory = $true)][System.Diagnostics.Process]$BackendProcess,
        [Parameter(Mandatory = $true)][string]$BaseUrl
    )

    # 请求必须在后台等待，因为被测 API 会一直阻塞到真实 Windows Forms
    # 文件夹对话框返回。主脚本只对该后端派生的 PowerShell 窗口发送关闭，
    # 不触碰 runner 上的其他窗口或进程。
    $PickerJob = Start-Job -ScriptBlock {
        param([string]$TargetUrl)
        $Response = Invoke-RestMethod `
            -Uri $TargetUrl `
            -Method Post `
            -ContentType "application/json" `
            -Body "{}" `
            -TimeoutSec 45
        $Response | ConvertTo-Json -Depth 4 -Compress
    } -ArgumentList "$BaseUrl/api/v1/workspace/native-picker"

    try {
        $Deadline = [DateTime]::UtcNow.AddSeconds(20)
        $DialogClosed = $false
        while ([DateTime]::UtcNow -lt $Deadline) {
            $BackendProcess.Refresh()
            if ($BackendProcess.HasExited) {
                throw "打开系统文件夹选择器时，本地服务意外退出。"
            }
            $PickerChildren = @(
                Get-DescendantProcesses -RootProcessId $BackendProcess.Id |
                    Where-Object { $_.Name -in @("powershell.exe", "pwsh.exe") }
            )
            foreach ($PickerChild in $PickerChildren) {
                $PickerProcess = Get-Process -Id $PickerChild.ProcessId -ErrorAction SilentlyContinue
                if ($PickerProcess -and $PickerProcess.MainWindowHandle -ne 0) {
                    if (-not $PickerProcess.CloseMainWindow()) {
                        throw "系统文件夹选择器已经出现，但无法发送取消操作。"
                    }
                    $DialogClosed = $true
                    break
                }
            }
            if ($DialogClosed) { break }
            Start-Sleep -Milliseconds 250
        }
        if (-not $DialogClosed) {
            throw "系统文件夹选择器 20 秒内没有出现可关闭窗口，可能仍会在 Windows 首次选择时卡住。"
        }

        $Completed = Wait-Job -Job $PickerJob -Timeout 20
        if ($null -eq $Completed) {
            throw "取消系统文件夹选择器后，API 20 秒内没有返回。"
        }
        if ($PickerJob.State -ne "Completed") {
            $Reason = $PickerJob.ChildJobs[0].JobStateInfo.Reason
            throw "系统文件夹选择器请求失败：$Reason"
        }
        $PayloadText = @(Receive-Job -Job $PickerJob -ErrorAction Stop)[-1]
        $Payload = $PayloadText | ConvertFrom-Json
        if ($Payload.cancelled -ne $true -or $null -ne $Payload.path) {
            throw "系统文件夹选择器取消后没有返回 cancelled=true。"
        }
    } finally {
        if ($PickerJob.State -notin @("Completed", "Failed", "Stopped")) {
            Stop-Job -Job $PickerJob -ErrorAction SilentlyContinue
        }
        Remove-Job -Job $PickerJob -Force -ErrorAction SilentlyContinue
    }
}

function Wait-ForCurrentRunLogMarker {
    param(
        [Parameter(Mandatory = $true)][string]$Marker,
        [int]$TimeoutSeconds = 20
    )

    $Deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $Deadline) {
        $Logs = @(Get-CurrentRunLogs)
        if ($Logs.Count -gt 0 -and (Select-String -Path $Logs.FullName -SimpleMatch $Marker -Quiet)) {
            return
        }
        Start-Sleep -Milliseconds 250
    }
    throw "未在本次桌面日志中找到必要证据：$Marker"
}

function Get-RunningInstalledClients {
    param([Parameter(Mandatory = $true)][string]$ExecutablePath)

    return @(
        Get-CimInstance Win32_Process -ErrorAction Stop |
            Where-Object {
                $_.ExecutablePath -and [string]::Equals(
                    $_.ExecutablePath,
                    $ExecutablePath,
                    [System.StringComparison]::OrdinalIgnoreCase
                )
            }
    )
}

function Get-CurrentRunLogs {
    if ($null -eq $ClientStartedAt) { return @() }
    $CutoffUtc = $ClientStartedAt.ToUniversalTime().AddSeconds(-2)
    return @(
        Get-ChildItem -Path $DesktopLogDirectory -Filter "*.log" -File -ErrorAction SilentlyContinue |
            Where-Object { $_.LastWriteTimeUtc -ge $CutoffUtc }
    )
}

function Get-ProductUninstallEntries {
    $RegistryRoot = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall"
    if (-not (Test-Path $RegistryRoot -PathType Container)) {
        return @()
    }
    return @(
        Get-ChildItem -Path $RegistryRoot -ErrorAction Stop |
            ForEach-Object { Get-ItemProperty -Path $_.PSPath -ErrorAction Stop } |
            Where-Object { $_.DisplayName -eq $ProductName }
    )
}

function Get-ProductShortcuts {
    $ShortcutRoots = @(
        (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"),
        [Environment]::GetFolderPath([Environment+SpecialFolder]::DesktopDirectory)
    ) | Where-Object { $_ -and (Test-Path $_ -PathType Container) }
    $ShortcutFiles = @(
        foreach ($Root in $ShortcutRoots) {
            Get-ChildItem -Path $Root -Filter "*$ProductName*.lnk" -File -Recurse -ErrorAction Stop
        }
    )
    if ($ShortcutFiles.Count -eq 0) { return @() }

    $Shell = New-Object -ComObject WScript.Shell
    try {
        return @(
            foreach ($ShortcutFile in $ShortcutFiles) {
                $Shortcut = $Shell.CreateShortcut($ShortcutFile.FullName)
                [PSCustomObject]@{
                    Path = $ShortcutFile.FullName
                    TargetPath = [string]$Shortcut.TargetPath
                }
            }
        )
    } finally {
        [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($Shell)
    }
}

function Test-ShortcutTargetsPath {
    param(
        [Parameter(Mandatory = $true)][object[]]$Shortcuts,
        [Parameter(Mandatory = $true)][string]$TargetPath
    )

    return @(
        $Shortcuts | Where-Object {
            [string]::Equals($_.TargetPath, $TargetPath, [System.StringComparison]::OrdinalIgnoreCase)
        }
    ).Count -gt 0
}

function Assert-NoVisibleBackendTerminal {
    param([Parameter(Mandatory = $true)][int]$ClientProcessId)

    $Descendants = @(Get-DescendantProcesses -RootProcessId $ClientProcessId)
    $ConsoleHosts = @($Descendants | Where-Object { $_.Name -ieq "conhost.exe" })
    if ($ConsoleHosts.Count -gt 0) {
        throw "桌面客户端进程树出现 conhost.exe，后台建模或 Notebook 可能弹出终端。"
    }
    foreach ($Descendant in $Descendants) {
        if ($Descendant.Name -ieq "msedgewebview2.exe") { continue }
        $DescendantProcess = Get-Process -Id $Descendant.ProcessId -ErrorAction SilentlyContinue
        if ($DescendantProcess -and $DescendantProcess.MainWindowHandle -ne 0) {
            throw "后台子进程 $($Descendant.Name) 出现了可见窗口。"
        }
    }
}

function Wait-ForDesktopWindow {
    param(
        [Parameter(Mandatory = $true)][System.Diagnostics.Process]$Client,
        [int]$TimeoutSeconds = 20
    )

    $Deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $Deadline) {
        Observe-NewSystemBrowsers
        $Client.Refresh()
        if ($Client.HasExited) {
            throw "Tauri 客户端在主窗口显示前退出，退出码 $($Client.ExitCode)。"
        }
        $WebViewProcesses = @(
            Get-DescendantProcesses -RootProcessId $Client.Id |
                Where-Object { $_.Name -ieq "msedgewebview2.exe" }
        )
        if ($Client.MainWindowHandle -ne 0 -and $WebViewProcesses.Count -gt 0) {
            return
        }
        Start-Sleep -Milliseconds 250
    }
    throw "本地服务已启动，但没有在 $TimeoutSeconds 秒内检测到 Tauri 主窗口或 WebView2 进程。"
}

function Wait-ForOwnedTauriBackend {
    param(
        [Parameter(Mandatory = $true)][System.Diagnostics.Process]$Client,
        [int]$RejectedProcessId = 0,
        [int]$RejectedPort = 0,
        [int]$TimeoutSeconds = 180
    )

    $Deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    $LastProbe = "尚未发现桌面客户端后端"
    while ([DateTime]::UtcNow -lt $Deadline) {
        Observe-NewSystemBrowsers
        $Client.Refresh()
        if ($Client.HasExited) {
            throw "Tauri 客户端在本地服务就绪前退出，退出码 $($Client.ExitCode)。"
        }

        $Descendants = @(Get-DescendantProcesses -RootProcessId $Client.Id)
        $BackendCandidates = @($Descendants | Where-Object { $_.Name -ieq "risk-model-agent.exe" })
        if ($BackendCandidates.Count -gt 1) {
            throw "桌面客户端启动了多个后端进程，已阻止发布。"
        }
        if ($BackendCandidates.Count -eq 1) {
            $Candidate = $BackendCandidates[0]
            $CandidateProcessId = [int]$Candidate.ProcessId
            if ($CandidateProcessId -eq $RejectedProcessId) {
                $LastProbe = "仍然只发现恢复前的旧后端进程"
                Start-Sleep -Milliseconds 250
                continue
            }
            if ([int]$Candidate.ParentProcessId -ne $Client.Id) {
                throw "本地服务不是桌面客户端的直属子进程，进程所有权不符合契约。"
            }
            if (-not $Candidate.ExecutablePath -or -not $Candidate.ExecutablePath.StartsWith($InstallDirectory, [System.StringComparison]::OrdinalIgnoreCase) -or $Candidate.ExecutablePath -notmatch '[\\/]backend[\\/]risk-model-agent\.exe$') {
                throw "桌面客户端启动的后端不在安装包 backend 资源目录内。"
            }
            foreach ($Port in @(Get-BackendLoopbackPorts -ProcessId $CandidateProcessId)) {
                if ($Port -eq $RejectedPort) {
                    $LastProbe = "恢复后的后端仍占用恢复前端口"
                    continue
                }
                try {
                    $CandidateUrl = "http://127.0.0.1:$Port"
                    $Health = Invoke-RestMethod "$CandidateUrl/api/v1/health" -TimeoutSec 3
                    if ($Health.status -eq "ok" -and $Health.runtime -eq "local" -and $Health.desktop -eq $true -and $Health.version -eq $Version) {
                        $Process = Get-Process -Id $CandidateProcessId -ErrorAction Stop
                        Assert-NoVisibleBackendTerminal -ClientProcessId $Client.Id
                        return [PSCustomObject]@{
                            Process = $Process
                            ProcessId = $CandidateProcessId
                            Port = [int]$Port
                            BaseUrl = $CandidateUrl
                        }
                    }
                    $LastProbe = "监听端口返回的桌面健康契约不匹配"
                } catch {
                    $LastProbe = $_.Exception.Message
                }
            }
        }
        Start-Sleep -Milliseconds 500
    }
    throw "Tauri 客户端后端未在 $TimeoutSeconds 秒内就绪；最后一次探测：$LastProbe"
}

function Invoke-SilentUninstall {
    param([Parameter(Mandatory = $true)][string]$UninstallerPath)

    $Result = Start-Process -FilePath $UninstallerPath -ArgumentList @("/S") -Wait -PassThru
    if ($Result.ExitCode -ne 0) {
        throw "Tauri 卸载程序失败，退出码 $($Result.ExitCode)。"
    }
}

function Invoke-LegacySilentUninstall {
    param([Parameter(Mandatory = $true)][string]$UninstallerPath)

    $Result = Start-Process -FilePath $UninstallerPath -ArgumentList @(
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART"
    ) -Wait -PassThru
    if ($Result.ExitCode -ne 0) {
        throw "旧版 Inno 卸载程序失败，退出码 $($Result.ExitCode)。"
    }
}

function Assert-RejectedTauriMigration {
    param([Parameter(Mandatory = $true)][string]$Scenario)

    $Result = Start-Process -FilePath $InstallerPath -ArgumentList @("/S") -Wait -PassThru
    if ($Result.ExitCode -eq 0) {
        throw "$Scenario 时 Tauri NSIS 仍返回成功，迁移边界已失效。"
    }
    if ((Test-Path $InstallDirectory) -or (Test-Path $TauriRegistryPath) -or (@(Get-ProductShortcuts).Count -gt 0)) {
        throw "$Scenario 虽被拒绝，但已写入新客户端目录、卸载项或快捷方式。"
    }
}

$OriginalDataDirectory = [Environment]::GetEnvironmentVariable("RISK_AGENT_DATA_DIR", "Process")
$OriginalWorkspaceDirectory = [Environment]::GetEnvironmentVariable("RISK_AGENT_WORKSPACE_DIR", "Process")
$OriginalOpenBrowser = [Environment]::GetEnvironmentVariable("RISK_AGENT_OPEN_BROWSER", "Process")
$OriginalPort = [Environment]::GetEnvironmentVariable("RISK_AGENT_PORT", "Process")
$OriginalBackendLogPath = [Environment]::GetEnvironmentVariable("RISK_AGENT_BACKEND_LOG_PATH", "Process")
$OriginalAutoMigrate = [Environment]::GetEnvironmentVariable("RISK_AGENT_AUTO_MIGRATE", "Process")
$OriginalWebViewArguments = [Environment]::GetEnvironmentVariable("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "Process")
$OriginalSmokeDesktopCookie = [Environment]::GetEnvironmentVariable("RISK_AGENT_SMOKE_DESKTOP_COOKIE", "Process")

if (Test-Path $InstallDirectory) {
    throw "默认 Tauri 安装目录已存在，为避免覆盖现有客户端，已拒绝运行冒烟：$InstallDirectory"
}
if (Test-Path $LegacyInstallDirectory) {
    throw "旧版官方默认安装目录已存在，为避免覆盖真实程序，已拒绝运行冒烟：$LegacyInstallDirectory"
}
if (Test-Path $LegacyRegistryPath) {
    throw "当前用户已存在旧 Inno 卸载项，为避免覆盖真实安装，已拒绝运行冒烟。"
}
if (Test-Path $TauriRegistryPath) {
    throw "当前用户已存在 Tauri 卸载项，为避免覆盖真实安装，已拒绝运行冒烟。"
}
if (Test-Path $LegacyDataDirectory) {
    throw "当前用户已存在旧版默认数据目录，为避免改写真实工作区指针，已拒绝运行冒烟：$LegacyDataDirectory"
}
$PreexistingEntries = @(Get-ProductUninstallEntries)
$PreexistingShortcuts = @(Get-ProductShortcuts)
if ($PreexistingEntries.Count -gt 0 -or $PreexistingShortcuts.Count -gt 0) {
    throw "检测到现有风控建模 Agent 卸载项或快捷方式，已阻止可能破坏真实安装的冒烟。"
}

try {
    Write-Host "[迁移冒烟] 用完全空的固定旧 Inno 注册项验证损坏迁移必须失败…"
    New-Item -Path $LegacyRegistryPath -Force | Out-Null
    $CorruptFixtureCreated = $true
    Assert-RejectedTauriMigration -Scenario "固定旧 Inno 注册项完全为空"
    Remove-Item -Path $LegacyRegistryPath -Recurse -Force
    $CorruptFixtureCreated = $false

    Write-Host "[迁移冒烟] 验证字段齐全但产品和发布者伪造的固定旧键必须失败…"
    New-Item -Path $LegacyRegistryPath -Force | Out-Null
    $CorruptFixtureCreated = $true
    New-ItemProperty -Path $LegacyRegistryPath -Name "DisplayName" -Value "伪造建模工具" -PropertyType String | Out-Null
    New-ItemProperty -Path $LegacyRegistryPath -Name "Publisher" -Value "Unknown Publisher" -PropertyType String | Out-Null
    New-ItemProperty -Path $LegacyRegistryPath -Name "DisplayVersion" -Value "1.1.2" -PropertyType String | Out-Null
    New-ItemProperty -Path $LegacyRegistryPath -Name "InstallLocation" -Value (Join-Path $TemporaryRoot "forged-product-$RunToken") -PropertyType String | Out-Null
    Assert-RejectedTauriMigration -Scenario "固定旧 Inno 键的产品或发布者被伪造"
    Remove-Item -Path $LegacyRegistryPath -Recurse -Force
    $CorruptFixtureCreated = $false

    Write-Host "[迁移冒烟] 验证元数据正确但卸载路径无官方卸载器的固定旧键必须失败…"
    $ForgedInstallDirectory = Join-Path $TemporaryRoot "forged-inno-path-$RunToken"
    New-Item -ItemType Directory -Path $ForgedInstallDirectory -Force | Out-Null
    New-Item -Path $LegacyRegistryPath -Force | Out-Null
    $CorruptFixtureCreated = $true
    New-ItemProperty -Path $LegacyRegistryPath -Name "DisplayName" -Value $ProductName -PropertyType String | Out-Null
    New-ItemProperty -Path $LegacyRegistryPath -Name "Publisher" -Value "Risk Model Agent" -PropertyType String | Out-Null
    New-ItemProperty -Path $LegacyRegistryPath -Name "DisplayVersion" -Value "1.1.2" -PropertyType String | Out-Null
    New-ItemProperty -Path $LegacyRegistryPath -Name "InstallLocation" -Value $ForgedInstallDirectory -PropertyType String | Out-Null
    Assert-RejectedTauriMigration -Scenario "固定旧 Inno 键的卸载路径没有官方卸载器"
    Remove-Item -Path $LegacyRegistryPath -Recurse -Force
    $CorruptFixtureCreated = $false

    Write-Host "[迁移冒烟] 验证指向其他软件卸载器的完整伪造记录必须失败…"
    Copy-Item -Path $env:ComSpec -Destination (Join-Path $ForgedInstallDirectory "unins000.exe") -Force
    Copy-Item -Path $env:ComSpec -Destination (Join-Path $ForgedInstallDirectory "risk-model-agent.exe") -Force
    New-Item -Path $LegacyRegistryPath -Force | Out-Null
    $CorruptFixtureCreated = $true
    New-ItemProperty -Path $LegacyRegistryPath -Name "DisplayName" -Value $ProductName -PropertyType String | Out-Null
    New-ItemProperty -Path $LegacyRegistryPath -Name "Publisher" -Value "Risk Model Agent" -PropertyType String | Out-Null
    New-ItemProperty -Path $LegacyRegistryPath -Name "DisplayVersion" -Value "1.1.2" -PropertyType String | Out-Null
    New-ItemProperty -Path $LegacyRegistryPath -Name "InstallLocation" -Value $ForgedInstallDirectory -PropertyType String | Out-Null
    New-ItemProperty -Path $LegacyRegistryPath -Name "UninstallString" -Value '"C:\OtherProduct\unins000.exe"' -PropertyType String | Out-Null
    New-ItemProperty -Path $LegacyRegistryPath -Name "QuietUninstallString" -Value '"C:\OtherProduct\unins000.exe" /SILENT' -PropertyType String | Out-Null
    Assert-RejectedTauriMigration -Scenario "固定旧 Inno 键交叉指向其他软件卸载器"
    Remove-Item -Path $LegacyRegistryPath -Recurse -Force
    $CorruptFixtureCreated = $false

    New-Item -ItemType Directory -Path $DataDirectory -Force | Out-Null
    $Sentinel = Join-Path $DataDirectory "must-survive-uninstall.txt"
    "user-data" | Set-Content -Path $Sentinel -Encoding ascii

    New-Item -ItemType Directory -Path $LegacyDataDirectory -Force | Out-Null
    "legacy-user-data" | Set-Content -Path $MigrationSentinel -Encoding ascii

    Write-Host "[迁移冒烟] 静默安装固定哈希的真实 1.1.2 Release…"
    $LegacyInstallResult = Start-Process -FilePath $LegacyInstallerPath -ArgumentList @(
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/SP-"
    ) -Wait -PassThru
    if ($LegacyInstallResult.ExitCode -ne 0) {
        throw "真实 1.1.2 Inno 安装失败，退出码 $($LegacyInstallResult.ExitCode)。"
    }
    $LegacyExecutable = Join-Path $LegacyInstallDirectory "risk-model-agent.exe"
    $DiscoveredLegacyUninstaller = Find-UniqueUninstaller -Directory $LegacyInstallDirectory -Filter "unins*.exe" -ProductName "真实 1.1.2 Inno"
    if ($DiscoveredLegacyUninstaller) { $LegacyUninstaller = $DiscoveredLegacyUninstaller }
    if (-not (Test-Path $LegacyExecutable -PathType Leaf) -or -not $DiscoveredLegacyUninstaller -or -not (Test-Path $LegacyRegistryPath)) {
        throw "真实 1.1.2 安装后，旧程序、卸载器或固定 Inno 注册项不完整。"
    }
    # 这两个哈希来自已校验 Release 安装器的真实安装结果，用于生成
    # 下一轮安全迁移白名单。它们不包含用户数据或密钥，可作为 CI 产物证据。
    $LegacyExecutableHash = (Get-FileHash -LiteralPath $LegacyExecutable -Algorithm SHA256).Hash.ToLowerInvariant()
    $LegacyUninstallerHash = (Get-FileHash -LiteralPath $LegacyUninstaller -Algorithm SHA256).Hash.ToLowerInvariant()
    Write-Host "[1.1.2 迁移哈希] risk-model-agent.exe=$LegacyExecutableHash"
    Write-Host "[1.1.2 迁移哈希] unins000.exe=$LegacyUninstallerHash"
    $LegacyEntry = Get-ItemProperty -Path $LegacyRegistryPath -ErrorAction Stop
    $LegacyEntries = @(Get-ProductUninstallEntries)
    $LegacyShortcuts = @(Get-ProductShortcuts)
    if ($LegacyEntry.DisplayVersion -ne "1.1.2" -or $LegacyEntries.Count -ne 1) {
        throw "真实 1.1.2 安装后未形成唯一、版本正确的旧卸载项。"
    }
    if (-not (Test-ShortcutTargetsPath -Shortcuts $LegacyShortcuts -TargetPath $LegacyExecutable)) {
        throw "真实 1.1.2 安装后未找到指向旧程序的开始菜单或桌面入口。"
    }

    Write-Host "[迁移冒烟] 运行真实 1.1.2 冻结服务、Notebook、建模与评分基线…"
    & (Join-Path $RepositoryRoot "scripts\smoke_windows_service.ps1") `
        -ExecutablePath $LegacyExecutable `
        -DataDirectory $LegacyRuntimeDataDirectory `
        -EvidenceOutputPath $MigrationEvidencePath `
        -RepositoryRoot $RepositoryRoot
    if (-not (Test-Path $MigrationEvidencePath -PathType Leaf)) {
        throw "真实 1.1.2 冒烟未生成项目与 Provider 持久化证据。"
    }
    $MigrationEvidence = Get-Content $MigrationEvidencePath -Raw | ConvertFrom-Json
    if ($MigrationEvidence.schema_version -ne "risk-windows-migration-evidence/v1" -or @($MigrationEvidence.projects).Count -lt 2) {
        throw "真实 1.1.2 生成的持久化证据格式无效。"
    }

    Write-Host "[迁移冒烟] 安装 Tauri NSIS，并由预安装 Hook 卸载旧 Inno…"
    $InstallResult = Start-Process -FilePath $InstallerPath -ArgumentList @("/S") -Wait -PassThru
    if ($InstallResult.ExitCode -ne 0) {
        throw "Tauri NSIS 安装失败，退出码 $($InstallResult.ExitCode)。"
    }
    for ($Attempt = 1; $Attempt -le 30; $Attempt++) {
        if (-not (Test-Path $LegacyRegistryPath) -and -not (Test-Path $LegacyExecutable) -and -not (Test-Path $LegacyUninstaller)) {
            break
        }
        Start-Sleep -Milliseconds 500
    }
    if (Test-Path $LegacyRegistryPath) {
        throw "Tauri 迁移后仍存在旧 Inno 卸载注册项。"
    }
    if (Test-Path $LegacyExecutable) {
        throw "Tauri 迁移后仍存在旧版程序。"
    }
    if (Test-Path $LegacyUninstaller) {
        throw "Tauri 迁移后仍存在旧 Inno 卸载程序。"
    }
    if (-not (Test-Path $MigrationSentinel)) {
        throw "Tauri 迁移过程中删除了旧版用户数据。"
    }

    $ClientExecutables = @(Get-ChildItem -Path $InstallDirectory -Filter "risk-model-agent-desktop.exe" -File -Recurse)
    $Uninstaller = Find-UniqueUninstaller -Directory $InstallDirectory -Filter "*uninstall*.exe" -ProductName "Tauri NSIS"
    if ($ClientExecutables.Count -ne 1 -or -not $Uninstaller) {
        throw "安装后未找到唯一桌面客户端或卸载程序。客户端=$($ClientExecutables.Count)，卸载器=$(if ($Uninstaller) { 1 } else { 0 })。"
    }
    $ClientExecutable = $ClientExecutables[0].FullName
    $TauriEntries = @(Get-ProductUninstallEntries)
    $TauriShortcuts = @(Get-ProductShortcuts)
    if (-not (Test-Path $TauriRegistryPath) -or $TauriEntries.Count -ne 1 -or $TauriEntries[0].DisplayVersion -ne $Version) {
        throw "Tauri 迁移后未形成唯一、版本正确的新卸载项。"
    }
    if ([string]$TauriEntries[0].UninstallString -notlike "*$InstallDirectory*") {
        throw "Tauri 新卸载项未指向默认中文空格安装目录。"
    }
    if (-not (Test-ShortcutTargetsPath -Shortcuts $TauriShortcuts -TargetPath $ClientExecutable)) {
        throw "Tauri 迁移后未找到指向新桌面客户端的开始菜单或桌面入口。"
    }
    if ((Test-ShortcutTargetsPath -Shortcuts $TauriShortcuts -TargetPath $LegacyExecutable) -or (Test-ShortcutTargetsPath -Shortcuts $TauriShortcuts -TargetPath $LegacyUninstaller)) {
        throw "Tauri 迁移后仍存在指向旧程序或旧卸载器的入口。"
    }

    $PackagedBackendExecutables = @(
        Get-ChildItem -Path $InstallDirectory -Filter "risk-model-agent.exe" -File -Recurse |
            Where-Object { $_.FullName -match '[\\/]backend[\\/]risk-model-agent\.exe$' }
    )
    if ($PackagedBackendExecutables.Count -ne 1) {
        throw "安装后未找到唯一的 Tauri 内置后端，无法验证首次工作区。"
    }
    $PackagedBackendExecutable = $PackagedBackendExecutables[0].FullName

    Write-Host "[首次工作区冒烟] 验证真实系统文件夹对话框可见且可取消，再验证中文空格路径选择与重启持久化…"
    [Environment]::SetEnvironmentVariable("RISK_AGENT_DATA_DIR", $null, "Process")
    [Environment]::SetEnvironmentVariable("RISK_AGENT_WORKSPACE_DIR", $null, "Process")
    $env:RISK_AGENT_OPEN_BROWSER = "0"
    $env:RISK_AGENT_AUTO_MIGRATE = "0"
    $WorkspacePort = Get-AvailableLoopbackPort
    $WorkspaceBaseUrl = "http://127.0.0.1:$WorkspacePort"
    $env:RISK_AGENT_PORT = [string]$WorkspacePort
    $env:RISK_AGENT_BACKEND_LOG_PATH = Join-Path $TemporaryRoot "workspace-first-run-$RunToken.log"
    $WorkspaceBackendProcess = Start-Process `
        -FilePath $PackagedBackendExecutable `
        -WorkingDirectory (Split-Path $PackagedBackendExecutable -Parent) `
        -PassThru
    Wait-ForLocalService -Process $WorkspaceBackendProcess -BaseUrl $WorkspaceBaseUrl
    Invoke-NativePickerCancelSmoke `
        -BackendProcess $WorkspaceBackendProcess `
        -BaseUrl $WorkspaceBaseUrl
    $WorkspaceBefore = Invoke-RestMethod "$WorkspaceBaseUrl/api/v1/workspace" -TimeoutSec 5
    if ($WorkspaceBefore.workspace.needs_setup -ne $true -or $WorkspaceBefore.workspace.configured -ne $false) {
        throw "首次启动没有返回 needs_setup=true，无法证明工作区选择流程。"
    }
    $WorkspaceSelectBody = @{ path = $WorkspaceSelectionDirectory } | ConvertTo-Json -Compress
    $WorkspaceSelected = Invoke-RestMethod `
        "$WorkspaceBaseUrl/api/v1/workspace/select" `
        -Method Post `
        -ContentType "application/json" `
        -Body $WorkspaceSelectBody `
        -TimeoutSec 30
    if ($WorkspaceSelected.workspace.configured -ne $true -or $WorkspaceSelected.workspace.needs_setup -ne $false) {
        throw "首次工作区 API 选择后仍显示未配置。"
    }
    if (-not [string]::Equals($WorkspaceSelected.workspace.path, $WorkspaceSelectionDirectory, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "首次工作区 API 返回的路径与中文空格目标目录不一致。"
    }
    Stop-ProcessTreeForCleanup -Process $WorkspaceBackendProcess
    $WorkspaceBackendProcess = $null

    $WorkspaceRestartPort = Get-AvailableLoopbackPort
    $WorkspaceRestartBaseUrl = "http://127.0.0.1:$WorkspaceRestartPort"
    $env:RISK_AGENT_PORT = [string]$WorkspaceRestartPort
    $env:RISK_AGENT_BACKEND_LOG_PATH = Join-Path $TemporaryRoot "workspace-restart-$RunToken.log"
    $WorkspaceBackendProcess = Start-Process `
        -FilePath $PackagedBackendExecutable `
        -WorkingDirectory (Split-Path $PackagedBackendExecutable -Parent) `
        -PassThru
    Wait-ForLocalService -Process $WorkspaceBackendProcess -BaseUrl $WorkspaceRestartBaseUrl
    $WorkspaceAfterRestart = Invoke-RestMethod "$WorkspaceRestartBaseUrl/api/v1/workspace" -TimeoutSec 5
    if ($WorkspaceAfterRestart.workspace.configured -ne $true -or $WorkspaceAfterRestart.workspace.needs_setup -ne $false) {
        throw "重启后首次工作区选择状态没有持久化。"
    }
    if (-not [string]::Equals($WorkspaceAfterRestart.workspace.path, $WorkspaceSelectionDirectory, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "重启后没有恢复到已选择的中文空格工作区。"
    }
    Stop-ProcessTreeForCleanup -Process $WorkspaceBackendProcess
    $WorkspaceBackendProcess = $null
    if (-not (Test-Path (Join-Path $WorkspaceSelectionDirectory ".risk-model-agent-workspace.json") -PathType Leaf)) {
        throw "首次工作区选择后缺少项目级工作区标记。"
    }

    # 故意把父环境设为开启浏览器，桌面监督器必须用 RISK_AGENT_OPEN_BROWSER=0
    # 覆盖它；后续也会观测是否产生新的系统浏览器进程。
    $env:RISK_AGENT_DATA_DIR = $DataDirectory
    [Environment]::SetEnvironmentVariable("RISK_AGENT_WORKSPACE_DIR", $null, "Process")
    $env:RISK_AGENT_OPEN_BROWSER = "1"
    # 仅在本次 CI 安装冒烟的进程树内开启 WebView2 CDP。
    # 正式客户端没有默认调试端口，finally 会恢复父进程环境。
    $WebViewDebugPort = Get-AvailableLoopbackPort
    $env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS = "--remote-debugging-address=127.0.0.1 --remote-debugging-port=$WebViewDebugPort"
    [Environment]::SetEnvironmentVariable("RISK_AGENT_SMOKE_DESKTOP_COOKIE", $null, "Process")
    Write-Host "[桌面冒烟] 启动无终端桌面客户端…"
    $ClientStartedAt = Get-Date
    $ClientProcess = Start-Process -FilePath $ClientExecutable -PassThru

    $BackendProcess = $null
    $BaseUrl = $null
    $LastProbe = "尚未发现桌面客户端后端"
    for ($Attempt = 1; $Attempt -le 120; $Attempt++) {
        Observe-NewSystemBrowsers
        $ClientProcess.Refresh()
        if ($ClientProcess.HasExited) {
            throw "Tauri 客户端在本地服务就绪前退出，退出码 $($ClientProcess.ExitCode)。"
        }

        $Descendants = @(Get-DescendantProcesses -RootProcessId $ClientProcess.Id)
        $BackendCandidates = @($Descendants | Where-Object { $_.Name -ieq "risk-model-agent.exe" })
        if ($BackendCandidates.Count -gt 1) {
            throw "桌面客户端启动了多个后端进程，已阻止发布。"
        }
        if ($BackendCandidates.Count -eq 1) {
            $Candidate = $BackendCandidates[0]
            $BackendProcessId = [int]$Candidate.ProcessId
            if ([int]$Candidate.ParentProcessId -ne $ClientProcess.Id) {
                throw "本地服务不是桌面客户端的直属子进程，进程所有权不符合契约。"
            }
            if (-not $Candidate.ExecutablePath -or -not $Candidate.ExecutablePath.StartsWith($InstallDirectory, [System.StringComparison]::OrdinalIgnoreCase) -or $Candidate.ExecutablePath -notmatch '[\\/]backend[\\/]risk-model-agent\.exe$') {
                throw "桌面客户端启动的后端不在安装包 backend 资源目录内。"
            }
            $BackendProcess = Get-Process -Id $BackendProcessId -ErrorAction Stop
            foreach ($Port in @(Get-BackendLoopbackPorts -ProcessId $BackendProcessId)) {
                try {
                    $CandidateUrl = "http://127.0.0.1:$Port"
                    $Health = Invoke-RestMethod "$CandidateUrl/api/v1/health" -TimeoutSec 3
                    if ($Health.status -eq "ok" -and $Health.runtime -eq "local" -and $Health.desktop -eq $true -and $Health.version -eq $Version) {
                        $BaseUrl = $CandidateUrl
                        break
                    }
                    $LastProbe = "监听端口返回的桌面健康契约不匹配"
                } catch {
                    $LastProbe = $_.Exception.Message
                }
            }
        }
        if ($BaseUrl) { break }
        Start-Sleep -Milliseconds 1500
    }
    if (-not $BaseUrl -or $null -eq $BackendProcess) {
        throw "Tauri 客户端后端未在 180 秒内就绪；最后一次探测：$LastProbe"
    }

    $DesktopWindowReady = $false
    for ($Attempt = 1; $Attempt -le 40; $Attempt++) {
        Observe-NewSystemBrowsers
        $ClientProcess.Refresh()
        $WebViewProcesses = @(
            Get-DescendantProcesses -RootProcessId $ClientProcess.Id |
                Where-Object { $_.Name -ieq "msedgewebview2.exe" }
        )
        if ($ClientProcess.MainWindowHandle -ne 0 -and $WebViewProcesses.Count -gt 0) {
            $DesktopWindowReady = $true
            break
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not $DesktopWindowReady) {
        throw "本地服务已启动，但没有检测到 Tauri 主窗口或 WebView2 进程。"
    }

    $DesktopAuth = New-AuthenticatedDesktopWebSession `
        -BaseUrl $BaseUrl `
        -DebugPort $WebViewDebugPort `
        -PythonPath $PythonExecutable `
        -ReaderScript $DesktopCookieReader
    $DesktopWebSession = $DesktopAuth.WebSession
    $DesktopSessionCookie = $DesktopAuth.CookieValue
    $DesktopAuth = $null

    $BackendProcess.Refresh()
    if ($BackendProcess.MainWindowHandle -ne 0) {
        throw "后台建模服务出现了可见窗口，CREATE_NO_WINDOW 契约失效。"
    }
    Assert-NoVisibleBackendTerminal -ClientProcessId $ClientProcess.Id

    Write-Host "[迁移冒烟] 先验证 1.1.2 创建的项目与非秘密 Provider 配置仍可读取…"
    if (-not (Test-Path $Sentinel) -or -not (Test-Path $MigrationEvidencePath)) {
        throw "Tauri 启动后，旧版共用数据根中的保留文件或迁移证据丢失。"
    }
    $ProjectsAfterUpgrade = Invoke-RestMethod `
        "$BaseUrl/api/v1/projects?include_archived=true" `
        -WebSession $DesktopWebSession `
        -TimeoutSec 10
    foreach ($ExpectedProject in @($MigrationEvidence.projects)) {
        $Matches = @(
            $ProjectsAfterUpgrade.projects |
                Where-Object { $_.id -eq $ExpectedProject.id -and $_.name -eq $ExpectedProject.name }
        )
        if ($Matches.Count -ne 1) {
            throw "升级后无法读取旧版项目证据：$($ExpectedProject.name)。"
        }
    }
    $ProviderAfterUpgrade = Invoke-RestMethod `
        "$BaseUrl/api/v1/providers/settings" `
        -WebSession $DesktopWebSession `
        -TimeoutSec 10
    $ExpectedProvider = $MigrationEvidence.provider
    $ActualSettings = $ProviderAfterUpgrade.settings
    foreach ($Field in @("active_profile_id", "provider", "api_format", "base_url", "model", "reviewer_model")) {
        if ([string]$ActualSettings.$Field -ne [string]$ExpectedProvider.$Field) {
            throw "升级后 Provider 非秘密配置字段 $Field 与旧版证据不一致。"
        }
    }
    if ($ActualSettings.llm_enabled -ne $false -or $ActualSettings.api_key_configured -ne $false) {
        throw "升级后 Provider 启用状态或无密钥边界与旧版证据不一致。"
    }

    Write-Host "[恢复冒烟] 强制终止已就绪后端，再通过单实例回调触发恢复…"
    $OriginalBackendProcessId = $BackendProcessId
    $OriginalBackendPort = ([Uri]$BaseUrl).Port
    $OriginalBaseUrl = $BaseUrl
    Stop-Process -Id $OriginalBackendProcessId -Force -ErrorAction Stop
    if (-not $BackendProcess.WaitForExit(10000)) {
        throw "模拟运行期崩溃后，旧后端进程 10 秒内仍未退出。"
    }
    $BackendProcess.WaitForExit()
    Wait-ForCurrentRunLogMarker -Marker "runtime backend failure:" -TimeoutSeconds 20
    $ClientProcess.Refresh()
    if ($ClientProcess.HasExited) {
        throw "后端运行中断后，Tauri 客户端也意外退出，无法向用户展示恢复状态。"
    }
    $OldEndpointClosed = $false
    for ($Attempt = 1; $Attempt -le 20; $Attempt++) {
        try {
            Invoke-RestMethod "$OriginalBaseUrl/api/v1/health" -TimeoutSec 1 | Out-Null
        } catch {
            $OldEndpointClosed = $true
            break
        }
        Start-Sleep -Milliseconds 250
    }
    if (-not $OldEndpointClosed) {
        throw "旧后端进程退出后，原随机端口仍然返回健康响应。"
    }

    $RecoveryTriggerProcess = Start-Process -FilePath $ClientExecutable -PassThru
    if (-not $RecoveryTriggerProcess.WaitForExit(15000)) {
        throw "第二次启动同一客户端后，单实例触发进程未在 15 秒内退出。"
    }
    $RecoveryTriggerProcess.WaitForExit()
    if ($RecoveryTriggerProcess.ExitCode -ne 0) {
        throw "单实例恢复触发进程退出码不是 0：$($RecoveryTriggerProcess.ExitCode)。"
    }
    $RecoveredBackend = Wait-ForOwnedTauriBackend `
        -Client $ClientProcess `
        -RejectedProcessId $OriginalBackendProcessId `
        -RejectedPort $OriginalBackendPort `
        -TimeoutSeconds 180
    $BackendProcess = $RecoveredBackend.Process
    $BackendProcessId = [int]$RecoveredBackend.ProcessId
    $BaseUrl = [string]$RecoveredBackend.BaseUrl
    if ($BackendProcessId -eq $OriginalBackendProcessId -or $BaseUrl -eq $OriginalBaseUrl) {
        throw "恢复后没有获得新的后端进程和随机端口。"
    }
    Wait-ForDesktopWindow -Client $ClientProcess
    $RecoveredAuth = New-AuthenticatedDesktopWebSession `
        -BaseUrl $BaseUrl `
        -DebugPort $WebViewDebugPort `
        -PythonPath $PythonExecutable `
        -ReaderScript $DesktopCookieReader
    $DesktopWebSession = $RecoveredAuth.WebSession
    $DesktopSessionCookie = $RecoveredAuth.CookieValue
    $RecoveredAuth = $null
    $InstalledClientProcesses = @(Get-RunningInstalledClients -ExecutablePath $ClientExecutable)
    if ($InstalledClientProcesses.Count -ne 1 -or [int]$InstalledClientProcesses[0].ProcessId -ne $ClientProcess.Id) {
        throw "运行期恢复后存在多个长期运行的 Tauri 客户端进程。"
    }
    Observe-NewSystemBrowsers
    if ($ObservedNewBrowsers.Count -gt 0) {
        throw "运行期恢复过程中检测到新的系统浏览器进程：$($ObservedNewBrowsers -join ', ')。"
    }

    Write-Host "[桌面冒烟] 本地服务已就绪：$BaseUrl；执行完整 Notebook、建模与评分，并持续监控后台窗口…"
    $SmokeScript = Join-Path $RepositoryRoot "scripts\smoke_packaged_service.py"
    try {
        # 只让冒烟 Python 子进程继承会话；立即恢复父环境，
        # 避免后续 Tauri/后端进程意外获得测试 Cookie。
        [Environment]::SetEnvironmentVariable(
            "RISK_AGENT_SMOKE_DESKTOP_COOKIE",
            $DesktopSessionCookie,
            "Process"
        )
        $SmokeProcess = Start-Process -FilePath $PythonExecutable -ArgumentList @(
            "`"$SmokeScript`"",
            "--url",
            $BaseUrl
        ) -PassThru -NoNewWindow
    } finally {
        [Environment]::SetEnvironmentVariable(
            "RISK_AGENT_SMOKE_DESKTOP_COOKIE",
            $null,
            "Process"
        )
    }
    $DesktopSessionCookie = $null
    $DesktopWebSession = $null
    while ($true) {
        Observe-NewSystemBrowsers
        Assert-NoVisibleBackendTerminal -ClientProcessId $ClientProcess.Id
        if ($ObservedNewBrowsers.Count -gt 0) {
            throw "完整建模期间检测到新的系统浏览器进程：$($ObservedNewBrowsers -join ', ')。"
        }
        $SmokeProcess.Refresh()
        if ($SmokeProcess.HasExited) { break }
        Start-Sleep -Milliseconds 300
    }
    $SmokeProcess.WaitForExit()
    if ($SmokeProcess.ExitCode -ne 0) {
        throw "桌面客户端完整建模与评分冒烟失败，退出码 $($SmokeProcess.ExitCode)。"
    }
    $ClientProcess.Refresh()
    $BackendProcess.Refresh()
    if ($ClientProcess.HasExited -or $BackendProcess.HasExited) {
        throw "完整建模冒烟结束前，桌面客户端或后端已经退出。"
    }
    Observe-NewSystemBrowsers
    if ($ObservedNewBrowsers.Count -gt 0) {
        throw "启动桌面客户端后检测到新的系统浏览器进程：$($ObservedNewBrowsers -join ', ')。"
    }

    Write-Host "[桌面冒烟] 通过主窗口正常关闭客户端，并验证后台服务回收…"
    if (-not $ClientProcess.CloseMainWindow()) {
        throw "无法向 Tauri 主窗口发送正常关闭请求。"
    }
    if (-not $ClientProcess.WaitForExit(15000)) {
        throw "Tauri 客户端收到正常关闭请求后 15 秒内未退出。"
    }
    $ClientProcess.WaitForExit()
    for ($Attempt = 1; $Attempt -le 30; $Attempt++) {
        if (-not (Get-Process -Id $BackendProcessId -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Milliseconds 500
    }
    if (Get-Process -Id $BackendProcessId -ErrorAction SilentlyContinue) {
        throw "Tauri 客户端退出后，本地服务仍在运行，进程回收契约失效。"
    }
    foreach ($Port in @(Get-BackendLoopbackPorts -ProcessId $BackendProcessId)) {
        throw "Tauri 客户端退出后，本地服务端口 $Port 仍在监听。"
    }

    $RuntimeLogs = @(Get-CurrentRunLogs)
    if ($RuntimeLogs.Count -eq 0) {
        throw "没有找到本次桌面启动日志，无法完成异常扫描。"
    }
    $RuntimeError = Select-String -Path $RuntimeLogs.FullName -Pattern "httptools|HttpParser|Traceback \(most recent call last\)|Exception in callback" -Quiet
    if ($RuntimeError) {
        Get-Content $RuntimeLogs.FullName -ErrorAction SilentlyContinue
        throw "桌面客户端日志出现 HTTP 解析器或未处理异常。"
    }
    if (-not (Select-String -Path $RuntimeLogs.FullName -SimpleMatch "desktop graceful shutdown completed" -Quiet)) {
        Get-Content $RuntimeLogs.FullName -ErrorAction SilentlyContinue
        throw "正常关闭后缺少 Python 后端优雅退出证据。"
    }
    if (-not (Select-String -Path $RuntimeLogs.FullName -SimpleMatch "backend stopped gracefully" -Quiet)) {
        Get-Content $RuntimeLogs.FullName -ErrorAction SilentlyContinue
        throw "正常关闭后缺少 Tauri 监督器优雅回收证据。"
    }

    Write-Host "[桌面冒烟] 再次启动客户端并强制终止，单独验证 Windows Job Object 兜底回收…"
    $GracefulBackendProcessId = $BackendProcessId
    $GracefulBackendPort = ([Uri]$BaseUrl).Port
    $ClientProcess = Start-Process -FilePath $ClientExecutable -PassThru
    $ForcedBackend = Wait-ForOwnedTauriBackend `
        -Client $ClientProcess `
        -RejectedProcessId $GracefulBackendProcessId `
        -RejectedPort $GracefulBackendPort `
        -TimeoutSeconds 180
    $BackendProcess = $ForcedBackend.Process
    $BackendProcessId = [int]$ForcedBackend.ProcessId
    $ForcedBackendPort = [int]$ForcedBackend.Port
    $ForcedBaseUrl = [string]$ForcedBackend.BaseUrl
    Wait-ForDesktopWindow -Client $ClientProcess
    Observe-NewSystemBrowsers
    Assert-NoVisibleBackendTerminal -ClientProcessId $ClientProcess.Id
    if ($ObservedNewBrowsers.Count -gt 0) {
        throw "Job Object 兜底冒烟期间检测到新的系统浏览器进程：$($ObservedNewBrowsers -join ', ')。"
    }

    Stop-Process -Id $ClientProcess.Id -Force -ErrorAction Stop
    if (-not $ClientProcess.WaitForExit(10000)) {
        throw "强制终止 Tauri 客户端后，客户端进程 10 秒内仍未退出。"
    }
    $ClientProcess.WaitForExit()
    for ($Attempt = 1; $Attempt -le 40; $Attempt++) {
        if (-not (Get-Process -Id $BackendProcessId -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Milliseconds 250
    }
    if (Get-Process -Id $BackendProcessId -ErrorAction SilentlyContinue) {
        throw "强制终止 Tauri 客户端后，Job Object 未回收本地服务进程。"
    }
    $ForcedEndpointClosed = $false
    for ($Attempt = 1; $Attempt -le 20; $Attempt++) {
        try {
            Invoke-RestMethod "$ForcedBaseUrl/api/v1/health" -TimeoutSec 1 | Out-Null
        } catch {
            $ForcedEndpointClosed = $true
            break
        }
        Start-Sleep -Milliseconds 250
    }
    if (-not $ForcedEndpointClosed) {
        throw "强制终止 Tauri 客户端后，本地服务端口 $ForcedBackendPort 仍可访问。"
    }
    if (@(Get-RunningInstalledClients -ExecutablePath $ClientExecutable).Count -ne 0) {
        throw "强制终止测试结束后仍存在 Tauri 客户端进程。"
    }

    Write-Host "[桌面冒烟] 静默卸载并验证项目数据保留…"
    Invoke-SilentUninstall -UninstallerPath $Uninstaller
    $UninstallCompleted = $true
    for ($Attempt = 1; $Attempt -le 30; $Attempt++) {
        if (-not (Test-Path $ClientExecutable) -and -not (Test-Path $Uninstaller)) { break }
        Start-Sleep -Seconds 1
    }
    if (Test-Path $ClientExecutable) {
        throw "Tauri 客户端卸载后主程序仍然存在。"
    }
    if (Test-Path $Uninstaller) {
        throw "Tauri 客户端卸载后卸载程序未清理。"
    }
    if (Test-Path $InstallDirectory) {
        throw "Tauri 客户端卸载后默认程序目录仍然存在。"
    }
    if ((Test-Path $TauriRegistryPath) -or (@(Get-ProductUninstallEntries).Count -gt 0)) {
        throw "Tauri 客户端卸载后仍存在卸载注册项。"
    }
    if (@(Get-ProductShortcuts).Count -gt 0) {
        throw "Tauri 客户端卸载后仍存在开始菜单或桌面入口。"
    }
    if (-not (Test-Path $Sentinel)) {
        throw "Tauri 客户端卸载时删除了用户项目数据。"
    }
    if (-not (Test-Path $MigrationSentinel)) {
        throw "Tauri 客户端卸载时删除了从旧版保留的用户数据。"
    }
    if (-not (Test-Path $MigrationEvidencePath -PathType Leaf)) {
        throw "Tauri 客户端卸载时删除了旧版项目与 Provider 持久化证据。"
    }
    if (-not (Test-Path (Join-Path $LegacyDataDirectory "workspace-selection.json") -PathType Leaf)) {
        throw "Tauri 客户端卸载时删除了首次工作区选择指针。"
    }
    if (-not (Test-Path (Join-Path $WorkspaceSelectionDirectory ".risk-model-agent-workspace.json") -PathType Leaf)) {
        throw "Tauri 客户端卸载时删除了已选择的中文空格工作区。"
    }
    if (-not ($RuntimeLogs | Where-Object { Test-Path $_.FullName })) {
        throw "Tauri 客户端卸载时删除了运行日志。"
    }
} catch {
    $Failure = $_
} finally {
    try {
        Stop-ProcessTreeForCleanup -Process $RecoveryTriggerProcess
    } catch {
        if ($null -eq $Failure) { $Failure = $_ } else { Write-Warning $_.Exception.Message }
    }
    try {
        Stop-ProcessTreeForCleanup -Process $WorkspaceBackendProcess
    } catch {
        if ($null -eq $Failure) { $Failure = $_ } else { Write-Warning $_.Exception.Message }
    }
    try {
        Stop-ProcessTreeForCleanup -Process $SmokeProcess
    } catch {
        if ($null -eq $Failure) { $Failure = $_ } else { Write-Warning $_.Exception.Message }
    }
    try {
        Stop-ProcessTreeForCleanup -Process $ClientProcess
    } catch {
        if ($null -eq $Failure) { $Failure = $_ } else { Write-Warning $_.Exception.Message }
    }
    if ($BackendProcessId) {
        $OrphanBackend = Get-Process -Id $BackendProcessId -ErrorAction SilentlyContinue
        if ($OrphanBackend) {
            try {
                Stop-ProcessTreeForCleanup -Process $OrphanBackend
            } catch {
                if ($null -eq $Failure) { $Failure = $_ } else { Write-Warning $_.Exception.Message }
            }
        }
    }
    if (-not $Uninstaller) {
        try {
            $Uninstaller = Find-UniqueUninstaller -Directory $InstallDirectory -Filter "*uninstall*.exe" -ProductName "Tauri NSIS"
        } catch {
            if ($null -eq $Failure) { $Failure = $_ } else { Write-Warning $_.Exception.Message }
        }
    }
    if (-not $UninstallCompleted -and $Uninstaller -and (Test-Path $Uninstaller)) {
        try {
            Invoke-SilentUninstall -UninstallerPath $Uninstaller
        } catch {
            if ($null -eq $Failure) { $Failure = $_ } else { Write-Warning $_.Exception.Message }
        }
    }
    if (-not (Test-Path $LegacyUninstaller -PathType Leaf)) {
        try {
            $DiscoveredLegacyUninstaller = Find-UniqueUninstaller -Directory $LegacyInstallDirectory -Filter "unins*.exe" -ProductName "真实 1.1.2 Inno"
            if ($DiscoveredLegacyUninstaller) { $LegacyUninstaller = $DiscoveredLegacyUninstaller }
        } catch {
            if ($null -eq $Failure) { $Failure = $_ } else { Write-Warning $_.Exception.Message }
        }
    }
    if ($LegacyUninstaller -and (Test-Path $LegacyUninstaller -PathType Leaf)) {
        try {
            Invoke-LegacySilentUninstall -UninstallerPath $LegacyUninstaller
        } catch {
            if ($null -eq $Failure) { $Failure = $_ } else { Write-Warning $_.Exception.Message }
        }
    }
    if ($CorruptFixtureCreated -and (Test-Path $LegacyRegistryPath)) {
        try {
            Remove-Item -Path $LegacyRegistryPath -Recurse -Force
        } catch {
            if ($null -eq $Failure) { $Failure = $_ } else { Write-Warning $_.Exception.Message }
        }
    }
    if ($ForgedInstallDirectory -and (Test-Path $ForgedInstallDirectory -PathType Container)) {
        try {
            Remove-Item -Path $ForgedInstallDirectory -Recurse -Force
        } catch {
            if ($null -eq $Failure) { $Failure = $_ } else { Write-Warning $_.Exception.Message }
        }
    }
    Remove-Item -Path $MigrationSentinel -Force -ErrorAction SilentlyContinue
    [Environment]::SetEnvironmentVariable("RISK_AGENT_DATA_DIR", $OriginalDataDirectory, "Process")
    [Environment]::SetEnvironmentVariable("RISK_AGENT_WORKSPACE_DIR", $OriginalWorkspaceDirectory, "Process")
    [Environment]::SetEnvironmentVariable("RISK_AGENT_OPEN_BROWSER", $OriginalOpenBrowser, "Process")
    [Environment]::SetEnvironmentVariable("RISK_AGENT_PORT", $OriginalPort, "Process")
    [Environment]::SetEnvironmentVariable("RISK_AGENT_BACKEND_LOG_PATH", $OriginalBackendLogPath, "Process")
    [Environment]::SetEnvironmentVariable("RISK_AGENT_AUTO_MIGRATE", $OriginalAutoMigrate, "Process")
    [Environment]::SetEnvironmentVariable("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", $OriginalWebViewArguments, "Process")
    [Environment]::SetEnvironmentVariable("RISK_AGENT_SMOKE_DESKTOP_COOKIE", $OriginalSmokeDesktopCookie, "Process")
    $DesktopSessionCookie = $null
    $DesktopWebSession = $null
}

if ($null -ne $Failure) {
    @(Get-CurrentRunLogs) | ForEach-Object { Get-Content $_.FullName -ErrorAction SilentlyContinue }
    throw $Failure
}

Write-Host "[桌面冒烟] 真实 1.1.2 迁移、Tauri 安装、无浏览器/无后端终端、完整建模评分、退出回收、卸载与数据保留全部通过。"
