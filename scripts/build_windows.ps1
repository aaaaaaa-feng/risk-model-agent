$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { $Python = "py" }

# Keep build caches inside the project so packaging does not depend on a
# user's global cache permissions or stale machine-level state.
if (-not $env:PYINSTALLER_CONFIG_DIR) {
    $env:PYINSTALLER_CONFIG_DIR = Join-Path $Root "runtime\pyinstaller-cache"
}
if (-not $env:MPLCONFIGDIR) {
    $env:MPLCONFIGDIR = Join-Path $Root "runtime\matplotlib-cache"
}
New-Item -ItemType Directory -Force -Path $env:PYINSTALLER_CONFIG_DIR, $env:MPLCONFIGDIR | Out-Null

Push-Location (Join-Path $Root "frontend")
try {
    & npm ci
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & npm run build
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
    Pop-Location
}
& $Python (Join-Path $Root "scripts\verify_packaging.py")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $Python -c "import PyInstaller"
if ($LASTEXITCODE -ne 0) {
    Write-Error "PyInstaller 未安装，请先执行: $Python -m pip install `".[package]`""
    exit 2
}
& $Python -m PyInstaller (Join-Path $Root "packaging\risk_model_agent.spec") --noconfirm --clean
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& (Join-Path $Root "scripts\compile_windows_installer.ps1")
exit $LASTEXITCODE
