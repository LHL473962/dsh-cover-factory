#Requires -Version 5.1
<#
.SYNOPSIS
  One-shot installer for the DSH cover-factory plugin (safe for AI execution).
.DESCRIPTION
  Detects the DSH profile, places this repo at <profile>/node_modules/dsh-cover-factory,
  adds "dsh-cover-factory" to dsh.profile.bundles (idempotent), then runs
  engine/setup/bootstrap.py --install --with-models --yes unless -SkipDeps is given.
  Re-running is safe: existing repo is git-pulled, existing bundle entry is skipped.
.PARAMETER ProfilePath
  DSH profile directory containing package.json. Auto-detected when omitted.
.PARAMETER RepoPath
  Source repo. Defaults to the parent of this scripts/ directory.
.PARAMETER Yes
  Confirm downloads/installs (passed to bootstrap as --yes).
.PARAMETER SkipDeps
  Skip the bootstrap dependency/model step (useful for idempotence tests).
.PARAMETER PythonPath
  Python 3.10+ interpreter used for bootstrap. Defaults to "python" on PATH.
#>
param(
    [string]$ProfilePath = "",
    [string]$RepoPath = "",
    [switch]$Yes,
    [switch]$SkipDeps,
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Continue"
$PluginName = "dsh-cover-factory"
$py = "python"

function Write-Step([string]$Name, [string]$Status, [string]$Detail) {
    $color = "Green"
    if ($Status -eq "skipped") { $color = "DarkGray" }
    if ($Status -eq "failed") { $color = "Red" }
    Write-Host ("[{0,-7}] {1}: {2}" -f $Status, $Name, $Detail) -ForegroundColor $color
}

function Fail-Install([int]$Code, [string]$ErrorCode, [string]$Message, [string]$Hint) {
    Write-Host ""
    Write-Host ("ERROR [{0}] {1}" -f $ErrorCode, $Message) -ForegroundColor Red
    if ($Hint) { Write-Host ("HINT: {0}" -f $Hint) -ForegroundColor Yellow }
    exit $Code
}

function Resolve-Profile {
    $candidates = New-Object System.Collections.Generic.List[string]
    if ($ProfilePath) {
        if (-not (Test-Path -LiteralPath $ProfilePath -PathType Container)) {
            Fail-Install 10 "E_PROFILE_NOT_FOUND" ("Profile path does not exist: " + $ProfilePath) "Pass -ProfilePath <profile dir> or start DSH once to create ~/.dsh/profiles/desktop"
        }
        return (Resolve-Path -LiteralPath $ProfilePath).Path
    }
    if ($env:DSH_PROFILE_PATH) { $candidates.Add($env:DSH_PROFILE_PATH) }
    if ($env:DSH_PROFILE) {
        $p = $env:DSH_PROFILE
        if (Test-Path -LiteralPath $p -PathType Container) { $candidates.Add($p) }
        else { $candidates.Add((Join-Path $HOME (".dsh\profiles\" + $p))) }
    }
    $base = Join-Path $HOME ".dsh\profiles"
    foreach ($name in @("desktop", "web", "headless")) {
        $candidates.Add((Join-Path $base $name))
    }
    if (Test-Path -LiteralPath $base -PathType Container) {
        Get-ChildItem -LiteralPath $base -Directory -ErrorAction SilentlyContinue | ForEach-Object {
            $candidates.Add($_.FullName)
        }
    }
    foreach ($c in $candidates) {
        if ((Test-Path -LiteralPath $c -PathType Container) -and (Test-Path -LiteralPath (Join-Path $c "package.json"))) {
            return (Resolve-Path -LiteralPath $c).Path
        }
    }
    Fail-Install 10 "E_PROFILE_NOT_FOUND" "Could not find a DSH profile with package.json" "Pass -ProfilePath <dir>; usually ~/.dsh/profiles/desktop"
}

function Ensure-Repo([string]$Source, [string]$Dest) {
    $nm = Split-Path -Parent $Dest
    if (-not (Test-Path -LiteralPath $nm)) { New-Item -ItemType Directory -Path $nm -Force | Out-Null }
    if (Test-Path -LiteralPath $Dest) {
        if (Test-Path -LiteralPath (Join-Path $Dest ".git")) {
            $pull = & git -C $Dest pull --ff-only 2>&1
            $pullRc = $LASTEXITCODE
            $pull | ForEach-Object { Write-Host ("    " + $_) }
            if ($pullRc -ne 0) {
                Fail-Install 13 "E_GIT_PULL_FAILED" ("git pull failed in " + $Dest) "Resolve the git error above, or delete the folder and re-run"
            }
            Write-Step "repo" "skipped" ("already present; git pull ok: " + $Dest)
        } else {
            Write-Step "repo" "skipped" ("already present (not a git repo): " + $Dest)
        }
        return
    }
    if (Test-Path -LiteralPath (Join-Path $Source ".git")) {
        $cloneOut = & git clone --depth 1 $Source $Dest 2>&1
        $cloneRc = $LASTEXITCODE
        $cloneOut | ForEach-Object { Write-Host ("    " + $_) }
        if ($cloneRc -ne 0) {
            Fail-Install 12 "E_GIT_CLONE_FAILED" ("git clone failed: " + $Source + " -> " + $Dest) "Check git/network and retry, or copy the repo manually"
        }
        Write-Step "repo" "done" ("cloned to " + $Dest)
    } else {
        New-Item -ItemType Directory -Path $Dest -Force | Out-Null
        Get-ChildItem -LiteralPath $Source -Force | Where-Object {
            $_.Name -notin @(".git", "node_modules")
        } | Copy-Item -Destination $Dest -Recurse -Force
        Write-Step "repo" "done" ("copied to " + $Dest)
    }
}

function Ensure-Bundle([string]$Profile) {
    $pkgPath = Join-Path $Profile "package.json"
    if (-not (Test-Path -LiteralPath $pkgPath)) {
        Fail-Install 11 "E_PROFILE_INVALID" ("Missing package.json in " + $Profile) "Create/repair the DSH profile first, then re-run"
    }
    try {
        $pkg = Get-Content -LiteralPath $pkgPath -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        Fail-Install 11 "E_PROFILE_INVALID" ("package.json is not valid JSON: " + $pkgPath) "Fix package.json (git restore) and re-run"
    }
    if (-not $pkg.PSObject.Properties["dsh"]) {
        $pkg | Add-Member -MemberType NoteProperty -Name dsh -Value ([pscustomobject]@{})
    }
    if (-not $pkg.dsh.PSObject.Properties["profile"]) {
        $pkg.dsh | Add-Member -MemberType NoteProperty -Name profile -Value ([pscustomobject]@{})
    }
    if (-not $pkg.dsh.profile.PSObject.Properties["bundles"]) {
        $pkg.dsh.profile | Add-Member -MemberType NoteProperty -Name bundles -Value @()
    }
    $bundles = @($pkg.dsh.profile.bundles)
    if ($bundles -contains $PluginName) {
        Write-Step "bundle" "skipped" ("already in dsh.profile.bundles")
        return
    }
    $pkg.dsh.profile.bundles = @($bundles + $PluginName)
    $json = $pkg | ConvertTo-Json -Depth 50
    [System.IO.File]::WriteAllText($pkgPath, $json, (New-Object System.Text.UTF8Encoding($false)))
    Write-Step "bundle" "done" ("added " + $PluginName + " to dsh.profile.bundles")
}

$profile = Resolve-Profile
Write-Host ("Profile: " + $profile)

if (-not $RepoPath) { $RepoPath = Split-Path -Parent $PSScriptRoot }
if (-not (Test-Path -LiteralPath (Join-Path $RepoPath "engine\setup\bootstrap.py"))) {
    Fail-Install 14 "E_REPO_NOT_FOUND" ("Not a cover-factory repo (engine/setup/bootstrap.py missing): " + $RepoPath) "Pass -RepoPath <repo> or run this script from the repo's scripts/ directory"
}
$RepoPath = (Resolve-Path -LiteralPath $RepoPath).Path
$dest = Join-Path $profile ("node_modules\" + $PluginName)

Ensure-Repo $RepoPath $dest
$srcLocalCfg = Join-Path $RepoPath "engine\config.local.json"
$dstLocalCfg = Join-Path $dest "engine\config.local.json"
if ((Test-Path -LiteralPath $srcLocalCfg) -and -not (Test-Path -LiteralPath $dstLocalCfg)) {
    Copy-Item -LiteralPath $srcLocalCfg -Destination $dstLocalCfg -Force
    Write-Step "config.local" "done" ("copied machine paths to " + $dstLocalCfg)
}
Ensure-Bundle $profile

$bootstrapPath = Join-Path $dest "engine\setup\bootstrap.py"
if (-not (Test-Path -LiteralPath $bootstrapPath)) {
    Fail-Install 14 "E_REPO_INCOMPLETE" ("Plugin files missing after install: " + $bootstrapPath) "Delete the folder and re-run; check disk permissions"
}

if ($SkipDeps) {
    Write-Step "deps" "skipped" "SkipDeps requested (bootstrap not run)"
} else {
    if ($PythonPath) { $py = $PythonPath }
    else {
        $cmd = Get-Command python -ErrorAction SilentlyContinue
        if (-not $cmd) {
            Fail-Install 16 "E_PYTHON_NOT_FOUND" "python not found on PATH" "Install Python 3.10+, or pass -PythonPath <python.exe>"
        }
        $py = $cmd.Source
    }
    if (-not (Test-Path -LiteralPath $py)) {
        Fail-Install 16 "E_PYTHON_NOT_FOUND" ("Python not found: " + $py) "Pass -PythonPath <python.exe>"
    }
    $pyArgs = @("engine/setup/bootstrap.py", "--install", "--with-models")
    if ($Yes) { $pyArgs += "--yes" }
    Write-Step "deps" "running" ("bootstrap --install --with-models" + $(if ($Yes) { " --yes" } else { " (dry-run; pass -Yes to download)" }))
    $bootstrapJson = ""
    $tmp = [System.IO.Path]::GetTempFileName()
    $origEnc = [Console]::OutputEncoding
    Push-Location $dest
    try {
        [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
        & $py @pyArgs > $tmp
        $rc = $LASTEXITCODE
        if (Test-Path -LiteralPath $tmp) { $bootstrapJson = Get-Content -LiteralPath $tmp -Raw -Encoding UTF8 }
    } finally {
        [Console]::OutputEncoding = $origEnc
        Pop-Location
        Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
    }
    if ($rc -ne 0) {
        if ($bootstrapJson) { Write-Host $bootstrapJson }
        Fail-Install 15 "E_BOOTSTRAP_FAILED" ("bootstrap exited with " + $rc) ("Inspect " + (Join-Path $dest "engine\setup\setup.log") + " and fix the failed step, then re-run")
    }
    if ($bootstrapJson) { Write-Host $bootstrapJson }
    $allSkipped = $false
    try {
        $bj = $bootstrapJson | ConvertFrom-Json
        $notSkipped = @($bj.steps | Where-Object { $_.status -ne "skipped" })
        $allSkipped = ($notSkipped.Count -eq 0)
    } catch { $allSkipped = $false }
    if ($allSkipped) {
        Write-Step "deps" "skipped" "bootstrap: all steps already satisfied"
    } else {
        Write-Step "deps" "done" "bootstrap completed"
    }
}

Write-Host ""
Write-Host "Install complete." -ForegroundColor Green
Write-Host "Plugin path: $dest"
Write-Host "RESTART DSH (close and reopen the app / restart dsh web) to load the plugin."
Write-Host "Verify:"
Write-Host ("  " + $py + " " + (Join-Path $dest "engine\run.py") + " doctor")
Write-Host "Success gate: doctor outputs ok=true (all checks green)."
