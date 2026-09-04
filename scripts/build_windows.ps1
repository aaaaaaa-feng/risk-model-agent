$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python -PathType Leaf)) { $Python = "py" }
$Npm = (Get-Command "npm.cmd" -ErrorAction Stop).Source

function Assert-NativeSuccess {
    param([string]$Stage)

    if ($LASTEXITCODE -ne 0) {
        throw "$Stage 失败，退出码 $LASTEXITCODE。"
    }
}

# 打包缓存保留在项目 runtime 内，避免依赖用户级缓存权限或机器上的旧状态。
if (-not $env:PYINSTALLER_CONFIG_DIR) {
    $env:PYINSTALLER_CONFIG_DIR = Join-Path $Root "runtime\pyinstaller-cache"
}
New-Item -ItemType Directory -Force -Path $env:PYINSTALLER_CONFIG_DIR | Out-Null

Write-Host "[Windows 发布] 检查并构建现有 React 前端…"
Push-Location (Join-Path $Root "frontend")
try {
    & $Npm ci
    Assert-NativeSuccess "前端依赖安装"
    & $Npm run typecheck
    Assert-NativeSuccess "前端类型检查"
    & $Npm run lint
    Assert-NativeSuccess "前端 lint"
    & $Npm test
    Assert-NativeSuccess "前端测试"
    & $Npm run build
    Assert-NativeSuccess "前端构建"
} finally {
    Pop-Location
}

Write-Host "[Windows 发布] 核验后端与 Tauri 发布契约…"
& $Python (Join-Path $Root "scripts\verify_packaging.py")
Assert-NativeSuccess "打包契约核验"
& $Python (Join-Path $Root "scripts\verify_desktop_contract.py")
Assert-NativeSuccess "桌面契约核验"

& $Python -c "import PyInstaller"
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller 未安装，请先执行：$Python -m pip install `".[package]`""
}

Write-Host "[Windows 发布] 构建 PyInstaller onedir 后端…"
& $Python -m PyInstaller (Join-Path $Root "packaging\risk_model_agent.spec") --noconfirm --clean
Assert-NativeSuccess "PyInstaller 后端构建"

Write-Host "[Windows 发布] 生成后端完整性清单…"
& $Python (Join-Path $Root "scripts\create_backend_manifest.py")
Assert-NativeSuccess "后端完整性清单生成"

$FrozenBackend = Join-Path $Root "dist\risk-model-agent\risk-model-agent.exe"
Write-Host "[Windows 发布] 执行冻结后端内部自检…"
$FrozenSelfTest = Start-Process -FilePath $FrozenBackend -ArgumentList @(
    "--internal-package-self-test"
) -Wait -PassThru
if ($FrozenSelfTest.ExitCode -ne 0) {
    throw "冻结后端内部自检失败，退出码 $($FrozenSelfTest.ExitCode)。"
}

Write-Host "[Windows 发布] 构建并收集 Tauri NSIS…"
& (Join-Path $Root "scripts\build_windows_tauri.ps1") -RepositoryRoot $Root
& (Join-Path $Root "scripts\collect_tauri_installer.ps1") -RepositoryRoot $Root

& $Python (Join-Path $Root "scripts\audit_package_size.py") `
    --bundle (Join-Path $Root "dist\risk-model-agent") `
    --installer (Join-Path $Root "dist\installer") `
    --output (Join-Path $Root "dist\installer\package-size-report.json") `
    --baseline-kib 239176 `
    --maximum-mib 180 `
    --minimum-reduction-percent 25 `
    --enforce
Assert-NativeSuccess "Tauri 安装包体积与依赖审计"

Write-Host "[Windows 发布] 正式 Tauri 安装包已生成到 dist\installer。"
