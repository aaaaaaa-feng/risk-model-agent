param(
    [string]$RepositoryRoot = ""
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RepositoryRoot)) {
    $RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
} else {
    $RepositoryRoot = (Resolve-Path $RepositoryRoot).Path
}
$ReleaseUrl = "https://github.com/aaaaaaa-feng/risk-model-agent/releases/download/1.1.2/RiskModelAgent-1.1.2-windows-x64-setup.exe"
$ExpectedSha256 = "b0d3ce62632a95ffd72e76ac27c49727af11d856ee74d22586190b5efaf27636"

$FixtureDirectory = Join-Path $RepositoryRoot "dist\fixtures\legacy-inno"
$InstallerName = "RiskModelAgent-1.1.2-windows-x64-setup.exe"
$FixtureInstaller = Join-Path $FixtureDirectory $InstallerName
$FixtureChecksum = "$FixtureInstaller.sha256"
$FixtureManifest = Join-Path $FixtureDirectory "legacy-inno-fixture-manifest.json"
New-Item -ItemType Directory -Path $FixtureDirectory -Force | Out-Null
foreach ($Path in @($FixtureInstaller, $FixtureChecksum, $FixtureManifest)) {
    if (Test-Path $Path -PathType Leaf) {
        Remove-Item -Path $Path -Force
    }
}

Write-Host "[迁移夹具] 下载 GitHub Release 1.1.2 的真实 Inno 安装器…"
try {
    Invoke-WebRequest -UseBasicParsing -Uri $ReleaseUrl -OutFile $FixtureInstaller
} catch {
    Remove-Item -Path $FixtureInstaller -Force -ErrorAction SilentlyContinue
    throw "无法下载固定的 GitHub Release 1.1.2 迁移夹具：$($_.Exception.Message)"
}
$ActualHash = (Get-FileHash -Path $FixtureInstaller -Algorithm SHA256).Hash.ToLowerInvariant()
$ExpectedHash = $ExpectedSha256.ToLowerInvariant()
if ($ActualHash -ne $ExpectedHash) {
    Remove-Item -Path $FixtureInstaller -Force -ErrorAction SilentlyContinue
    throw "旧版 Release SHA-256 不匹配，已阻止迁移测试。期望 $ExpectedHash，实际 $ActualHash。"
}
"$ActualHash  $InstallerName" | Set-Content -Path $FixtureChecksum -Encoding ascii

$Manifest = [ordered]@{
    schema_version = "risk-legacy-inno-fixture/v1"
    purpose = "验证真实 1.1.2 Inno Release 到 Tauri NSIS 的迁移"
    installer_version = "1.1.2"
    source_url = $ReleaseUrl
    expected_sha256 = $ExpectedHash
    actual_sha256 = $ActualHash
    app_id = "{4CE3329A-CF6F-49E0-86C7-BE5C38DB1474}"
    file_name = $InstallerName
    bytes = (Get-Item $FixtureInstaller).Length
    formal_release_artifact = $false
}
$Manifest | ConvertTo-Json -Depth 4 | Set-Content -Path $FixtureManifest -Encoding utf8

$FormalDirectory = Join-Path $RepositoryRoot "dist\installer"
$UnexpectedFormalExe = @(
    Get-ChildItem -Path $FormalDirectory -Filter "*.exe" -File -ErrorAction SilentlyContinue
)
if ($UnexpectedFormalExe.Count -ne 0) {
    throw "真实迁移夹具未与正式安装包目录隔离，已阻止后续发布。"
}

Write-Host "[迁移夹具] 下载与固定哈希校验通过：$FixtureInstaller"
