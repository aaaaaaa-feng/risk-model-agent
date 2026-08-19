$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Packaged = Join-Path $Root "dist\risk-model-agent\risk-model-agent.exe"
if (Test-Path $Packaged) {
    & $Packaged
    exit $LASTEXITCODE
}
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Python = "py"
}
& $Python -m app.main
