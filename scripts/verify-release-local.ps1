[CmdletBinding()]
param(
    [ValidateSet("affected", "all", "unit", "minimum", "current", "release")]
    [string]$Mode = "affected",
    [string]$Base,
    [string]$Head,
    [string[]]$ChangedPath,
    [switch]$PlanOnly
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$selection = @()
if ($Mode -eq 'affected') {
    if ($Base) { $selection += @('--base', $Base) }
    if ($Head) { $selection += @('--head', $Head) }
    foreach ($path in $ChangedPath) { $selection += @('--path', $path) }
    if ($PlanOnly) {
        & python (Join-Path $repoRoot 'scripts/plan_validation.py')  @selection --plan-only
        if ($LASTEXITCODE -ne 0) { throw 'Validation applicability could not be resolved.' }
        return
    }
} elseif ($PlanOnly) { throw 'PlanOnly requires affected mode.' }
$wslInput = $repoRoot -replace "\\", "/"
$linuxRoot = (& wsl.exe -d Ubuntu-24.04 -- wslpath -a -u $wslInput).Trim()
if ($LASTEXITCODE -ne 0 -or -not $linuxRoot) {
    throw "Could not map the repository into Ubuntu-24.04."
}

$gitDir = (& git -C $repoRoot rev-parse --path-format=absolute --git-dir).Trim()
if ($LASTEXITCODE -ne 0 -or -not $gitDir) {
    throw "Could not resolve the repository Git directory."
}
$wslGitInput = $gitDir -replace "\\", "/"
$linuxGitDir = (& wsl.exe -d Ubuntu-24.04 -- wslpath -a -u $wslGitInput).Trim()
if ($LASTEXITCODE -ne 0 -or -not $linuxGitDir) {
    throw "Could not map the repository Git directory into Ubuntu-24.04."
}

& wsl.exe -d Ubuntu-24.04 -- bash "$linuxRoot/scripts/verify-release-local.sh" $Mode container $linuxGitDir @selection
if ($LASTEXITCODE -ne 0) {
    throw "Local release validation failed with exit code $LASTEXITCODE."
}
