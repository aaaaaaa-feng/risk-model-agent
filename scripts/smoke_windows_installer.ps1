param(
    [string]$InstallerPath = ""
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

if ([string]::IsNullOrWhiteSpace($InstallerPath)) {
    $Installers = @(Get-ChildItem -Path (Join-Path $Root "dist\installer") -Filter "RiskModelAgent-*-windows-x64-setup.exe")
    if ($Installers.Count -ne 1) {
        throw "Expected exactly one Windows installer, found $($Installers.Count)."
    }
    $Installer = $Installers[0]
    $InstallerPath = $Installer.FullName
} else {
    $InstallerPath = (Resolve-Path $InstallerPath).Path
}

$TemporaryRoot = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [System.IO.Path]::GetTempPath() }
$InstallDirectory = Join-Path $TemporaryRoot "RiskModelAgent-installed"
$DataDirectory = Join-Path $TemporaryRoot "RiskModelAgent-installed-data"
$InstallLog = Join-Path $TemporaryRoot "risk-model-agent-install.log"
$UninstallLog = Join-Path $TemporaryRoot "risk-model-agent-uninstall.log"

$InstallArguments = @(
    "/VERYSILENT",
    "/SUPPRESSMSGBOXES",
    "/NORESTART",
    "/SP-",
    "/LANG=chinesesimp",
    "/DIR=$InstallDirectory",
    "/LOG=$InstallLog"
)
$Install = Start-Process -FilePath $InstallerPath -ArgumentList $InstallArguments -Wait -PassThru
if ($Install.ExitCode -ne 0) {
    Get-Content $InstallLog -ErrorAction SilentlyContinue
    throw "Installer exited with code $($Install.ExitCode)."
}

$Executable = Join-Path $InstallDirectory "risk-model-agent.exe"
$Uninstaller = Join-Path $InstallDirectory "unins000.exe"
if (-not (Test-Path $Executable) -or -not (Test-Path $Uninstaller)) {
    throw "Installed executable or uninstaller is missing."
}

New-Item -ItemType Directory -Path $DataDirectory -Force | Out-Null
$Sentinel = Join-Path $DataDirectory "must-survive-uninstall.txt"
"user-data" | Set-Content -Path $Sentinel -Encoding ascii
$env:RISK_AGENT_DATA_DIR = $DataDirectory
$env:RISK_AGENT_OPEN_BROWSER = "0"
$Process = Start-Process -FilePath $Executable -PassThru
try {
    $Ready = $false
    for ($Attempt = 1; $Attempt -le 90; $Attempt++) {
        try {
            $Health = Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8765/api/v1/health
            $RootPage = Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8765/
            if ($Health.StatusCode -eq 200 -and $Health.Content -match '"runtime":"local"' -and $RootPage.Content -match '<div id="root"></div>') {
                $Ready = $true
                break
            }
        } catch { }
        Start-Sleep -Seconds 2
    }
    if (-not $Ready) {
        throw "Installed application did not pass the localhost startup smoke test."
    }
    & python (Join-Path $Root "scripts\smoke_packaged_service.py")
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
} finally {
    try {
        & taskkill.exe /PID $Process.Id /T /F 2>$null | Out-Null
    } catch {
        Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
    }
    $global:LASTEXITCODE = 0
}

$UninstallArguments = @(
    "/VERYSILENT",
    "/SUPPRESSMSGBOXES",
    "/NORESTART",
    "/LOG=$UninstallLog"
)
$Uninstall = Start-Process -FilePath $Uninstaller -ArgumentList $UninstallArguments -Wait -PassThru
if ($Uninstall.ExitCode -ne 0) {
    Get-Content $UninstallLog -ErrorAction SilentlyContinue
    throw "Uninstaller exited with code $($Uninstall.ExitCode)."
}
for ($Attempt = 1; $Attempt -le 30 -and (Test-Path $Executable); $Attempt++) {
    Start-Sleep -Seconds 1
}
if (Test-Path $Executable) {
    throw "Application executable still exists after uninstall."
}
if (-not (Test-Path $Sentinel)) {
    throw "Application uninstall removed user data."
}

Write-Host "Installer, installed application, full modeling smoke and uninstall checks passed."
