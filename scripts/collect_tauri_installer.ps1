param(
    [string]$RepositoryRoot = "",
    [string]$SourceDirectory = "",
    [string]$OutputDirectory = "",
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RepositoryRoot)) {
    $RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
} else {
    $RepositoryRoot = (Resolve-Path $RepositoryRoot).Path
}
if ([string]::IsNullOrWhiteSpace($SourceDirectory)) {
    $SourceDirectory = Join-Path $RepositoryRoot "desktop\src-tauri\target\release\bundle\nsis"
}
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $RepositoryRoot "dist\installer"
}
if ([string]::IsNullOrWhiteSpace($Version)) {
    $TauriConfig = Get-Content (Join-Path $RepositoryRoot "desktop\src-tauri\tauri.conf.json") -Raw | ConvertFrom-Json
    $Version = [string]$TauriConfig.version
}
if ($Version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$') {
    throw "桌面客户端版本必须使用 MAJOR.MINOR.PATCH 格式。"
}

$SourceDirectory = (Resolve-Path $SourceDirectory).Path
$Installers = @(Get-ChildItem -Path $SourceDirectory -Filter "*-setup.exe" -File)
if ($Installers.Count -ne 1) {
    throw "Tauri NSIS 来源目录必须恰好包含一个安装包，当前找到 $($Installers.Count) 个。"
}

New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$OutputDirectory = (Resolve-Path $OutputDirectory).Path
$CanonicalName = "RiskModelAgent-$Version-windows-x64-setup.exe"
$Destination = Join-Path $OutputDirectory $CanonicalName
$ChecksumFile = "$Destination.sha256"
$ManifestFile = Join-Path $OutputDirectory "tauri-installer-manifest.json"
$SizeReportFile = Join-Path $OutputDirectory "package-size-report.json"

# 只移除当前版本脚本生成的正式发布文件。其他版本或人工文件一律阻断，
# 避免旧安装包、校验文件或体积报告混进本次 Tauri artifact。
foreach ($GeneratedFile in @($Destination, $ChecksumFile, $ManifestFile, $SizeReportFile)) {
    if (Test-Path $GeneratedFile -PathType Leaf) {
        Remove-Item -Path $GeneratedFile -Force
    }
}
$UnexpectedFiles = @(
    Get-ChildItem -Path $OutputDirectory -File -Recurse -ErrorAction SilentlyContinue
)
if ($UnexpectedFiles.Count -gt 0) {
    throw "正式产物目录含有未识别文件，已阻止混合上传：$($UnexpectedFiles.Name -join ', ')"
}

Copy-Item -Path $Installers[0].FullName -Destination $Destination
$Hash = (Get-FileHash -Path $Destination -Algorithm SHA256).Hash.ToLowerInvariant()
"$Hash  $CanonicalName" | Set-Content -Path $ChecksumFile -Encoding ascii

$Manifest = [ordered]@{
    schema_version = "risk-tauri-installer/v1"
    package_kind = "tauri-nsis"
    version = $Version
    architecture = "windows-x64"
    file_name = $CanonicalName
    bytes = (Get-Item $Destination).Length
    sha256 = $Hash
    source_file_name = $Installers[0].Name
}
$Manifest | ConvertTo-Json -Depth 4 | Set-Content -Path $ManifestFile -Encoding utf8

$FormalInstallers = @(Get-ChildItem -Path $OutputDirectory -Filter "*.exe" -File)
if ($FormalInstallers.Count -ne 1 -or $FormalInstallers[0].Name -ne $CanonicalName) {
    throw "正式 Windows artifact 必须且只能包含本次 Tauri NSIS 安装器。"
}

Write-Host "[产物收集] 正式 Tauri 安装包：$Destination"
Write-Host "[产物收集] SHA-256：$Hash"
