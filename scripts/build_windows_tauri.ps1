param(
    [string]$RepositoryRoot = ""
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RepositoryRoot)) {
    $RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
} else {
    $RepositoryRoot = (Resolve-Path $RepositoryRoot).Path
}

if (-not $IsWindows) {
    throw "Tauri Windows 安装包只能在 Windows Runner 上构建。"
}
if ([System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture -ne [System.Runtime.InteropServices.Architecture]::X64) {
    throw "当前不是 Windows x64 环境，已阻止生成错误架构的安装包。"
}

$DesktopRoot = Join-Path $RepositoryRoot "desktop"
$CargoManifest = Join-Path $DesktopRoot "src-tauri\Cargo.toml"
$BackendExecutable = Join-Path $RepositoryRoot "dist\risk-model-agent\risk-model-agent.exe"
$BackendManifest = Join-Path $RepositoryRoot "dist\risk-model-agent\backend-manifest.json"
$NsisDirectory = Join-Path $DesktopRoot "src-tauri\target\release\bundle\nsis"
$Npm = (Get-Command "npm.cmd" -ErrorAction Stop).Source
$Cargo = (Get-Command "cargo.exe" -ErrorAction Stop).Source

if (-not (Test-Path $BackendExecutable -PathType Leaf)) {
    throw "缺少经过自检的 PyInstaller 后端：$BackendExecutable"
}
if (-not (Test-Path $BackendManifest -PathType Leaf)) {
    throw "缺少后端完整性清单，请先运行 scripts\create_backend_manifest.py。"
}
if (-not (Test-Path $CargoManifest -PathType Leaf)) {
    throw "缺少 Tauri Cargo 清单：$CargoManifest"
}

function Assert-NativeSuccess {
    param([string]$Stage)

    if ($LASTEXITCODE -ne 0) {
        throw "$Stage 失败，退出码 $LASTEXITCODE。"
    }
}

Write-Host "[桌面构建] 安装锁定的 Node 依赖…"
Push-Location $DesktopRoot
try {
    & $Npm ci
    Assert-NativeSuccess "桌面依赖安装"

    Write-Host "[桌面构建] 执行 TypeScript 类型检查…"
    & $Npm run typecheck
    Assert-NativeSuccess "桌面 TypeScript 类型检查"

    Write-Host "[桌面构建] 构建启动页…"
    & $Npm run build
    Assert-NativeSuccess "桌面启动页构建"
} finally {
    Pop-Location
}

Write-Host "[桌面构建] 执行 Rust 格式、测试与 Clippy 门禁…"
& $Cargo fmt --manifest-path $CargoManifest -- --check
Assert-NativeSuccess "Rust 格式检查"
& $Cargo test --manifest-path $CargoManifest --locked
Assert-NativeSuccess "Rust 测试"
& $Cargo clippy --manifest-path $CargoManifest --locked --all-targets -- -D warnings
Assert-NativeSuccess "Rust Clippy"

# Tauri 的目标目录可能残留旧安装器。只清理 Cargo 生成的固定 NSIS 子目录，
# 确保后续的“恰好一个安装器”断言证明本次构建，而不是误用旧文件。
if (Test-Path $NsisDirectory) {
    [System.IO.Directory]::Delete($NsisDirectory, $true)
}

Write-Host "[桌面构建] 构建 Tauri NSIS 安装包…"
$OriginalBackendManifest = [Environment]::GetEnvironmentVariable("RISK_AGENT_BACKEND_MANIFEST", "Process")
$ManifestForBuild = (Resolve-Path $BackendManifest).Path
if (-not [System.IO.Path]::IsPathFullyQualified($ManifestForBuild)) {
    throw "后端完整性清单不是绝对路径，已阻止 Tauri 正式构建：$ManifestForBuild"
}
$LocationPushed = $false
try {
    $env:RISK_AGENT_BACKEND_MANIFEST = $ManifestForBuild
    Push-Location $DesktopRoot
    $LocationPushed = $true
    & $Npm run tauri -- build --bundles nsis --ci
    Assert-NativeSuccess "Tauri NSIS 构建"
} finally {
    if ($LocationPushed) { Pop-Location }
    [Environment]::SetEnvironmentVariable("RISK_AGENT_BACKEND_MANIFEST", $OriginalBackendManifest, "Process")
}

$Installers = @(Get-ChildItem -Path $NsisDirectory -Filter "*-setup.exe" -File -ErrorAction SilentlyContinue)
if ($Installers.Count -ne 1) {
    throw "Tauri 构建后应恰好生成一个 NSIS 安装包，当前找到 $($Installers.Count) 个。"
}

Write-Host "[桌面构建] Tauri NSIS 已生成：$($Installers[0].FullName)"
