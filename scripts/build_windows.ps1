$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { $Python = "py" }
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
exit $LASTEXITCODE
