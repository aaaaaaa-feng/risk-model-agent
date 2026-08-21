param(
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

if ([string]::IsNullOrWhiteSpace($Version)) {
    $VersionLine = Select-String -Path (Join-Path $Root "pyproject.toml") -Pattern '^version = "([0-9]+\.[0-9]+\.[0-9]+)"$'
    if (-not $VersionLine) {
        throw "Unable to read the application version from pyproject.toml."
    }
    $Version = $VersionLine.Matches[0].Groups[1].Value
}
if ($Version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$') {
    throw "Installer version must use the numeric MAJOR.MINOR.PATCH format."
}

$IsccCommand = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
$Iscc = if ($IsccCommand) { $IsccCommand.Source } else { $null }
if (-not $Iscc) {
    $Candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
    )
    $Iscc = $Candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
}
if (-not $Iscc) {
    throw "Inno Setup 6 was not found. Install it from https://jrsoftware.org/isdl.php."
}

$Bundle = Join-Path $Root "dist\risk-model-agent\risk-model-agent.exe"
if (-not (Test-Path $Bundle)) {
    throw "The PyInstaller bundle is missing. Build dist\risk-model-agent first."
}

$InstallerDirectory = Join-Path $Root "dist\installer"
New-Item -ItemType Directory -Path $InstallerDirectory -Force | Out-Null
Get-ChildItem -Path $InstallerDirectory -Filter "RiskModelAgent-*-windows-x64-setup.exe" -ErrorAction SilentlyContinue |
    Remove-Item -Force
Get-ChildItem -Path $InstallerDirectory -Filter "*.sha256" -ErrorAction SilentlyContinue |
    Remove-Item -Force

$Script = Join-Path $Root "packaging\windows_installer.iss"
& $Iscc "/DMyAppVersion=$Version" $Script
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$Installers = @(Get-ChildItem -Path $InstallerDirectory -Filter "RiskModelAgent-$Version-windows-x64-setup.exe")
if ($Installers.Count -ne 1) {
    throw "Expected exactly one Windows installer, found $($Installers.Count)."
}
$Installer = $Installers[0]
$Hash = (Get-FileHash -Path $Installer.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
$ChecksumFile = "$($Installer.FullName).sha256"
"$Hash  $($Installer.Name)" | Set-Content -Path $ChecksumFile -Encoding ascii

Write-Host "Windows installer: $($Installer.FullName)"
Write-Host "SHA-256: $Hash"
