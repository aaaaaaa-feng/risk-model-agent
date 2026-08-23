$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Packaged = Join-Path $Root "dist\risk-model-agent\risk-model-agent.exe"

function Test-PackagedCurrent {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
    $packageTime = (Get-Item -LiteralPath $Path).LastWriteTimeUtc
    foreach ($directory in @((Join-Path $Root "app"), (Join-Path $Root "frontend\dist"))) {
        if (-not (Test-Path -LiteralPath $directory -PathType Container)) { continue }
        $newer = Get-ChildItem -LiteralPath $directory -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object {
                $_.FullName -notmatch "\\__pycache__\\" -and
                $_.Extension -ne ".pyc" -and
                $_.LastWriteTimeUtc -gt $packageTime
            } |
            Select-Object -First 1
        if ($null -ne $newer) { return $false }
    }
    return $true
}

if (Test-PackagedCurrent -Path $Packaged) {
    & $Packaged
    exit $LASTEXITCODE
}
if (Test-Path -LiteralPath $Packaged -PathType Leaf) {
    Write-Warning "打包后端早于当前源码，已改用源码环境启动；如需使用打包版请先重新构建。"
}
$PackagedFile = Join-Path $Root "dist\risk-model-agent.exe"
if (Test-Path -LiteralPath $PackagedFile -PathType Leaf) {
    & $PackagedFile
    exit $LASTEXITCODE
}
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Python = "py"
}
& $Python -m app.main
