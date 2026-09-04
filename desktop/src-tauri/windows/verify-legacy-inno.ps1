param(
    [Parameter(Mandatory = $true)]
    [string]$InstallDirectory,
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[0-9a-fA-F]{64}$")]
    [string]$ExpectedApplicationHash,
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[0-9a-fA-F]{64}$")]
    [string]$ExpectedUninstallerHash
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Get-Sha256 {
    param([Parameter(Mandatory = $true)][string]$Path)

    $Algorithm = [System.Security.Cryptography.SHA256]::Create()
    $Stream = $null
    try {
        $Stream = [System.IO.File]::Open(
            $Path,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read,
            [System.IO.FileShare]::Read
        )
        $Digest = $Algorithm.ComputeHash($Stream)
        return ([System.BitConverter]::ToString($Digest)).Replace("-", "").ToLowerInvariant()
    } finally {
        if ($null -ne $Stream) {
            $Stream.Dispose()
        }
        $Algorithm.Dispose()
    }
}

try {
    $LocalApplicationData = [System.Environment]::GetFolderPath(
        [System.Environment+SpecialFolder]::LocalApplicationData
    )
    if ([string]::IsNullOrWhiteSpace($LocalApplicationData)) {
        exit 10
    }

    $ExpectedDirectory = [System.IO.Path]::GetFullPath(
        [System.IO.Path]::Combine($LocalApplicationData, "Programs", "RiskModelAgent")
    ).TrimEnd("\")
    $ResolvedDirectory = [System.IO.Path]::GetFullPath($InstallDirectory).TrimEnd("\")
    if (-not [string]::Equals(
        $ResolvedDirectory,
        $ExpectedDirectory,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        exit 11
    }

    $DirectoryInfo = Get-Item -LiteralPath $ResolvedDirectory -Force
    if (-not $DirectoryInfo.PSIsContainer) {
        exit 12
    }
    if (($DirectoryInfo.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        exit 13
    }

    $ApplicationPath = [System.IO.Path]::Combine($ResolvedDirectory, "risk-model-agent.exe")
    $UninstallerPath = [System.IO.Path]::Combine($ResolvedDirectory, "unins000.exe")
    $ApplicationInfo = Get-Item -LiteralPath $ApplicationPath -Force
    $UninstallerInfo = Get-Item -LiteralPath $UninstallerPath -Force
    foreach ($FileInfo in @($ApplicationInfo, $UninstallerInfo)) {
        if ($FileInfo.PSIsContainer) {
            exit 14
        }
        if (($FileInfo.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            exit 15
        }
    }

    $ApplicationHash = Get-Sha256 -Path $ApplicationPath
    if (-not [string]::Equals(
        $ApplicationHash,
        $ExpectedApplicationHash,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        exit 16
    }

    $UninstallerHash = Get-Sha256 -Path $UninstallerPath
    if (-not [string]::Equals(
        $UninstallerHash,
        $ExpectedUninstallerHash,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        exit 17
    }

    exit 0
} catch {
    exit 90
}
