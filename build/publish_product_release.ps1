# Publishes the Windows Inno installer to GitHub Releases for the customer-facing repo
# (README + docs live at RootRecord/rootrecord-business-manager-download).
# Uses ONLY build\output\RootRecordSetup-<APP_VERSION>.exe — no other versions.
#
# Usage (from build folder, after ISCC / build_windows.ps1):
#   .\publish_product_release.ps1
#
# Optional: -Draft, -Title "...", -Repo "owner/other"

[CmdletBinding()]
param(
    [string] $Version,
    [string] $InstallerPath,
    [string] $Repo = "RootRecord/rootrecord-business-manager-download",
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
    $InstallerPath = Join-Path $here "output\RootRecordSetup-$Version.exe"
}

if (-not (Test-Path -LiteralPath $InstallerPath)) {
    throw @"
Installer not found: $InstallerPath
Expected the current app version only: RootRecordSetup-$Version.exe under build\output\
Build with Inno (build_windows.ps1 / ISCC) so the filename matches app_version.py.
"@
}

$gh = Get-GhPath
& $gh auth status 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Not logged in to GitHub. Run: gh auth login"
}

$hash = (Get-FileHash -LiteralPath $InstallerPath -Algorithm SHA256).Hash
$exeName = [IO.Path]::GetFileName($InstallerPath)
$notes = @"
Windows installer (Inno Setup), version $Version.

**Download:** ``$exeName`` (attached to this release).

SHA256: ``$hash``

Verify in PowerShell:
  Get-FileHash -LiteralPath '$exeName' -Algorithm SHA256
"@

$releaseTitle = if ($Title) { $Title } else { "RootRecord Business Manager $Version" }

$argList = @(
    "release", "create", $tag
    $InstallerPath
    "--repo", $Repo
    "--title", $releaseTitle
    "--notes", $notes
)
if ($Draft) { $argList += "--draft" }
if (-not $NoLatest) { $argList += "--latest" }

Write-Host "Creating GitHub release $tag on $Repo" -ForegroundColor Cyan
Write-Host "  $(Resolve-Path -LiteralPath $InstallerPath)"
Write-Host ""

& $gh @argList
if ($LASTEXITCODE -ne 0) {
    throw "gh release create failed (exit $LASTEXITCODE). If the tag exists, delete the release or use a new version in app_version.py."
}
Write-Host ""
Write-Host "Done. Releases: https://github.com/$Repo/releases" -ForegroundColor Green
