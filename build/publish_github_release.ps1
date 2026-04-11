# Publishes the Inno installer as a GitHub Release (uploads the .exe only; source stays in git).
#
# First-time setup (once per machine):
#   1. Add the project folder as a git repo and push to GitHub, OR clone your repo locally.
#   2. Run:  & "$env:ProgramFiles\GitHub CLI\gh.exe" auth login
#      (HTTPS + browser login is easiest; grant "repo" scope for private repos.)
#   3. From the repo root, optional:  gh repo set-default owner/repo
#
# Usage (from PowerShell, after building the installer with ISCC):
#   cd "...\Root Record Business Manager\build"
#   .\publish_github_release.ps1
#
# Optional:  .\publish_github_release.ps1 -InstallerPath "C:\path\RootRecordSetup-Beta-1.3.22.exe" -Draft
#
# Requires: GitHub CLI (winget install GitHub.cli). Uses APP_VERSION in ..\app_version.py unless -Version is set.

[CmdletBinding()]
param(
    [string] $Version,
    [string] $InstallerPath,
    [string] $Repo,
    [switch] $Draft,
    [switch] $NoLatest,
    [string] $Title
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-GhPath {
    $cmd = Get-Command gh -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $p = Join-Path ${env:ProgramFiles} "GitHub CLI\gh.exe"
    if (Test-Path -LiteralPath $p) { return $p }
    throw "GitHub CLI (gh) not found. Install with: winget install GitHub.cli"
}

function Read-AppVersion([string] $appVersionPyPath) {
    foreach ($line in Get-Content -LiteralPath $appVersionPyPath) {
        $t = $line.Trim()
        if ($t.StartsWith("APP_VERSION = ")) {
            return $t.Substring("APP_VERSION = ".Length).Trim().Trim([char]34)
        }
    }
    throw "Could not parse APP_VERSION from $appVersionPyPath"
}

$here = $PSScriptRoot
$pkgRoot = Resolve-Path (Join-Path $here "..")
$appVersionFile = Join-Path $pkgRoot "app_version.py"

if (-not $Version) {
    $Version = Read-AppVersion $appVersionFile
}

$tag = if ($Version.StartsWith("v")) { $Version } else { "v$Version" }

if (-not $InstallerPath) {
    $defaultName = "RootRecordSetup-Beta-$Version.exe"
    $InstallerPath = Join-Path $here "output\$defaultName"
}

if (-not (Test-Path -LiteralPath $InstallerPath)) {
    throw "Installer not found: $InstallerPath`nBuild it first (Inno Setup / ISCC) so output matches app_version.py ($Version)."
}

$gh = Get-GhPath
& $gh auth status 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw @"
Not logged in to GitHub. Run once:
  & `"$gh`" auth login
Then re-run this script.
"@
}

$hash = (Get-FileHash -LiteralPath $InstallerPath -Algorithm SHA256).Hash
$exeName = [IO.Path]::GetFileName($InstallerPath)
$notes = @"
Windows installer (Inno Setup), version $Version.

SHA256: $hash

Verify in PowerShell:
  Get-FileHash -LiteralPath '$exeName' -Algorithm SHA256
"@

$releaseTitle = if ($Title) { $Title } else { "RootRecord Business Manager $Version (Beta)" }

$argList = @(
    "release", "create", $tag
    $InstallerPath
    "--title", $releaseTitle
    "--notes", $notes
)
if ($Draft) { $argList += "--draft" }
if (-not $NoLatest) { $argList += "--latest" }
if ($Repo) {
    $argList += "--repo"
    $argList += $Repo
}

Write-Host "Creating GitHub release $tag with:" -ForegroundColor Cyan
Write-Host "  $($InstallerPath | Resolve-Path)"
Write-Host ""

# Prefer running gh from a directory that contains .git so default repo is detected.
$gitRoot = $pkgRoot.Path
while ($gitRoot -and -not (Test-Path (Join-Path $gitRoot ".git"))) {
    $parent = Split-Path $gitRoot -Parent
    if (-not $parent -or $parent -eq $gitRoot) { $gitRoot = $null; break }
    $gitRoot = $parent
}
if ($gitRoot) {
    Push-Location -LiteralPath $gitRoot
    try {
        & $gh @argList
    } finally {
        Pop-Location
    }
} else {
    if (-not $Repo) {
        throw "No .git folder found above this project. Either run from a git clone, or pass -Repo 'owner/repository'."
    }
    & $gh @argList
}
if ($LASTEXITCODE -ne 0) {
    throw "gh release create failed (exit $LASTEXITCODE)."
}
Write-Host ""
Write-Host "Done. Open:  gh browse releases" -ForegroundColor Green
