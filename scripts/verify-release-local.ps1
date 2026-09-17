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
# Refuse inherited repository selection before any Git read or WSL handoff.
$localGitNames = @('GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_CONFIG', 'GIT_CONFIG_PARAMETERS', 'GIT_CONFIG_COUNT', 'GIT_OBJECT_DIRECTORY', 'GIT_DIR', 'GIT_WORK_TREE', 'GIT_IMPLICIT_WORK_TREE', 'GIT_GRAFT_FILE', 'GIT_INDEX_FILE', 'GIT_REPLACE_REF_BASE', 'GIT_PREFIX', 'GIT_SHALLOW_FILE', 'GIT_COMMON_DIR', 'GIT_CEILING_DIRECTORIES', 'GIT_DISCOVERY_ACROSS_FILESYSTEM')
$inheritedGitNames = @(Get-ChildItem Env: | Where-Object {
    $_.Name -in $localGitNames
} | Select-Object -ExpandProperty Name)
if ($inheritedGitNames.Count) { throw "Inherited local Git overrides are not supported: $($inheritedGitNames -join ', ')" }
$actualRoot = (& git --no-replace-objects -C $repoRoot rev-parse --show-toplevel)
if ($LASTEXITCODE -ne 0 -or -not $actualRoot -or (Resolve-Path -LiteralPath $actualRoot).Path -ne $repoRoot) {
    throw 'Git target root does not match the wrapper source root.'
}
$selection = @()
if ($Mode -eq 'affected') {
    foreach ($ref in @(@('--base', $Base), @('--head', $Head))) {
        if ($ref[1]) {
            $oid = (& git --no-replace-objects -C $repoRoot rev-parse --verify --end-of-options "$($ref[1])^{commit}")
            if ($LASTEXITCODE -ne 0 -or -not $oid) { throw 'Could not bind the validation comparison to a commit.' }
            $selection += @($ref[0], $oid.Trim())
        }
    }
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

$gitDir = (& git --no-replace-objects -C $repoRoot rev-parse --path-format=absolute --git-dir).Trim()
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
