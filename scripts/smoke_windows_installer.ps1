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

# 模拟从曾携带 httptools 的旧版本原地升级。新安装必须先清除这个受控残留目录。
$StaleHttpToolsDirectory = Join-Path $InstallDirectory "_internal\httptools"
$StaleHttpToolsMarker = Join-Path $StaleHttpToolsDirectory "stale-upgrade-marker.txt"
New-Item -ItemType Directory -Path $StaleHttpToolsDirectory -Force | Out-Null
"stale-optional-http-backend" | Set-Content -Path $StaleHttpToolsMarker -Encoding ascii

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
if (Test-Path $StaleHttpToolsMarker) {
    throw "安装程序未清除旧版 httptools 残留。"
}

$Executable = Join-Path $InstallDirectory "risk-model-agent.exe"
$Uninstaller = Join-Path $InstallDirectory "unins000.exe"
if (-not (Test-Path $Executable) -or -not (Test-Path $Uninstaller)) {
    throw "Installed executable or uninstaller is missing."
}

New-Item -ItemType Directory -Path $DataDirectory -Force | Out-Null
$Sentinel = Join-Path $DataDirectory "must-survive-uninstall.txt"
"user-data" | Set-Content -Path $Sentinel -Encoding ascii
& (Join-Path $Root "scripts\smoke_windows_service.ps1") -ExecutablePath $Executable -DataDirectory $DataDirectory -RepositoryRoot $Root

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
