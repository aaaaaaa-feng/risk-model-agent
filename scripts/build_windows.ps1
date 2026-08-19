$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { $Python = "py" }
& $Python -m PyInstaller (Join-Path $Root "packaging\risk_model_agent.spec") --noconfirm --clean
exit $LASTEXITCODE
