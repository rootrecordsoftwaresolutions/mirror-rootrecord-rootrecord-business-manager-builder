# Publishes both GitHub targets for the current APP_VERSION in app_version.py:
#   1) Developer — RootRecord/rootrecord-business-manager-builder (commit, push main, tag vX.Y.Z, GitHub Release)
#   2) Download — RootRecord/rootrecord-business-manager-download (sync README/assets, optional build, installer Release, push main)
#
# Usage (from repo root or build folder):
#   .\build\Publish-Dual-Releases.bat
#   .\build\publish_dual_releases.ps1 -NoSign          # unsigned build (local only; not for public GitHub)
#   .\build\publish_dual_releases.ps1 -CommitMessage "Release v1.3.46: …"
#
# Requires: git, gh (authenticated), PyInstaller + Inno (unless -SkipBuild with existing exe).
# By default runs build_windows.ps1 with -Sign (Azure Trusted Signing + Inno SignTool path).

[CmdletBinding()]
param(
    [string] $CommitMessage,
    [switch] $Sign,
    [switch] $NoSign,
    [switch] $SkipBuild,
    [switch] $SkipStopRunningApp,
    [switch] $SkipBuilderIfTagExists,
    [switch] $SkipDownloadIfReleaseExists,
    [switch] $NoLatest,
    [string] $BuilderRepo = "RootRecord/rootrecord-business-manager-builder",
    [string] $DownloadRepo = "RootRecord/rootrecord-business-manager-download"
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

function Get-DownloadRepoRoot([string] $packageRoot) {
    $resolvedPkg = (Resolve-Path -LiteralPath $packageRoot).Path
    $parent = Split-Path -Path $resolvedPkg -Parent
    $candidates = @(
        (Join-Path $resolvedPkg "rootrecord-business-manager-download"),
        (Join-Path $parent "rootrecord-business-manager-download")
    )
    foreach ($c in $candidates) {
        $gitDir = Join-Path $c ".git"
        if (Test-Path -LiteralPath $gitDir) {
            return (Resolve-Path -LiteralPath $c).Path
        }
    }
    throw "Could not find rootrecord-business-manager-download with a .git folder next to or under:`n  $resolvedPkg"
}

function Invoke-GhReleaseExists([string] $Gh, [string] $Repo, [string] $Tag) {
    & $Gh release view $Tag --repo $Repo 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}

function Copy-PublicSyncToDownload([string] $syncOutRoot, [string] $downloadRoot) {
    $syncResolved = (Resolve-Path -LiteralPath $syncOutRoot).Path
    $dlResolved = (Resolve-Path -LiteralPath $downloadRoot).Path
    if ($syncResolved -eq $dlResolved) {
        Write-Host "Public sync folder is the download clone; no copy needed." -ForegroundColor DarkGray
        return
    }
    Write-Host "Copying synced docs from:`n  $syncResolved`n  -> $dlResolved" -ForegroundColor Cyan
    Copy-Item -LiteralPath (Join-Path $syncOutRoot "README.md") -Destination (Join-Path $downloadRoot "README.md") -Force
    Copy-Item -LiteralPath (Join-Path $syncOutRoot ".gitignore") -Destination (Join-Path $downloadRoot ".gitignore") -Force
    $srcAssets = Join-Path $syncOutRoot "assets"
    $dstAssets = Join-Path $downloadRoot "assets"
    if (Test-Path -LiteralPath $srcAssets) {
        New-Item -ItemType Directory -Force -Path $dstAssets | Out-Null
        Copy-Item -LiteralPath (Join-Path $srcAssets "*") -Destination $dstAssets -Force
    }
}

$here = $PSScriptRoot
$pkgRoot = (Resolve-Path (Join-Path $here "..")).Path
$appVersionFile = Join-Path $pkgRoot "app_version.py"
$version = Read-AppVersion $appVersionFile
$tag = if ($version.StartsWith("v")) { $version } else { "v$version" }

$gh = Get-GhPath
& $gh auth status 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Not logged in to GitHub. Run: gh auth login"
}

# Default: signed release builds (Smart App Control / strict Windows). Opt out with -NoSign.
$doSign = if ($PSBoundParameters.ContainsKey('NoSign') -and $NoSign) {
    $false
} elseif ($PSBoundParameters.ContainsKey('Sign')) {
    [bool]$Sign
} else {
    $true
}

$syncScript = Join-Path $here "sync_public_github_docs.ps1"
$buildScript = Join-Path $here "build_windows.ps1"
$publishProductScript = Join-Path $here "publish_product_release.ps1"

Write-Host "=== RootRecord dual publish ===" -ForegroundColor Cyan
Write-Host "Package root: $pkgRoot"
Write-Host "Version: $version  (tag: $tag)"
Write-Host "Windows build signing: $(if ($doSign) { 'enabled (-Sign)' } else { 'disabled (-NoSign)' })"
Write-Host ""
if (-not $doSign -and -not $SkipBuild) {
    Write-Warning "Publishing without signing: GitHub installers are often blocked by Smart App Control. Prefer default signing or ship the Microsoft Store MSIX."
}

# --- 1) Builder: commit, push, tag, release ---
Set-Location -LiteralPath $pkgRoot
Write-Host "--- Developer repo ($BuilderRepo) ---" -ForegroundColor Cyan

$defaultMsg = "Release ${tag}: publish developer + download"
$msg = if ($CommitMessage) { $CommitMessage } else { $defaultMsg }

git add -A
git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
    git commit -m $msg
    if ($LASTEXITCODE -ne 0) {
        throw "git commit failed on builder repo."
    }
} else {
    Write-Host "No staged changes on builder; skipping commit." -ForegroundColor DarkGray
}

git push origin main
if ($LASTEXITCODE -ne 0) {
    throw "git push origin main failed on builder repo."
}

$builderTagRemote = (& git ls-remote origin "refs/tags/$tag" 2>$null)
if ($builderTagRemote) {
    if ($SkipBuilderIfTagExists) {
        Write-Host "Tag $tag already exists on builder; skipping builder tag and GitHub Release (-SkipBuilderIfTagExists)." -ForegroundColor Yellow
    } else {
        throw "Tag $tag already exists on $BuilderRepo. Bump APP_VERSION in app_version.py, delete the remote tag, or use -SkipBuilderIfTagExists."
    }
} else {
    if (git tag -l $tag) {
        Write-Host "Removing local tag $tag (recreate for this publish)." -ForegroundColor Yellow
        git tag -d $tag
    }
    git tag -a $tag -m "RootRecord Business Manager $tag"
    git push origin $tag
    if ($LASTEXITCODE -ne 0) {
        throw "git push origin $tag failed on builder."
    }
    $builderNotes = @"
Private source release for **RootRecord Business Manager $version**.

- Customer installer and product README: **https://github.com/$DownloadRepo/releases/tag/$tag**
- After changing ``app_version.py``, run ``build\\Publish-Dual-Releases.bat`` (or this script) to publish both repos.
"@
    Write-Host "Creating GitHub Release on $BuilderRepo for $tag ..." -ForegroundColor Cyan
    & $gh release create $tag --repo $BuilderRepo --title "RootRecord Business Manager $version (source)" --notes $builderNotes
    if ($LASTEXITCODE -ne 0) {
        throw "gh release create failed for builder. If the release already exists, delete it on GitHub or use -SkipBuilderIfTagExists after pushing the tag only."
    }
}

# --- 2) Sync public README into download clone ---
Write-Host ""
Write-Host "--- Public download repo ($DownloadRepo): sync docs ---" -ForegroundColor Cyan
& $syncScript
if ($LASTEXITCODE -ne 0) {
    throw "sync_public_github_docs.ps1 failed."
}

$devRoot = Split-Path -Path $pkgRoot -Parent
$syncOutRoot = Join-Path $devRoot "rootrecord-business-manager-download"
$downloadRoot = Get-DownloadRepoRoot $pkgRoot
if (-not (Test-Path -LiteralPath $syncOutRoot)) {
    throw "Expected sync output folder missing: $syncOutRoot"
}
Copy-PublicSyncToDownload $syncOutRoot $downloadRoot

Set-Location -LiteralPath $downloadRoot
git add -A
git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
    git commit -m "Docs: sync product README for $version (release notes + download)"
    if ($LASTEXITCODE -ne 0) {
        throw "git commit failed on download repo."
    }
} else {
    Write-Host "No doc changes in download clone; skipping commit." -ForegroundColor DarkGray
}
git pull --rebase origin main
if ($LASTEXITCODE -ne 0) {
    throw "git pull --rebase failed on download repo. Resolve conflicts and retry."
}
git push origin main
if ($LASTEXITCODE -ne 0) {
    throw "git push origin main failed on download repo."
}

# --- 3) Build Windows installer (optional skip) ---
Set-Location -LiteralPath $here
$installerPath = Join-Path $here "output\RootRecordSetup-$version.exe"
if ($SkipBuild) {
    Write-Host ""
    Write-Host "-SkipBuild: using existing installer if present." -ForegroundColor Yellow
    if (-not (Test-Path -LiteralPath $installerPath)) {
        throw "Installer not found: $installerPath`nBuild first or omit -SkipBuild."
    }
} else {
    Write-Host ""
    Write-Host "--- Build Windows installer ---" -ForegroundColor Cyan
    $buildArgs = @{}
    if ($doSign) { $buildArgs.Sign = $true }
    if (-not $SkipStopRunningApp) { $buildArgs.StopRunningApp = $true }
    & $buildScript @buildArgs
    if ($LASTEXITCODE -ne 0) {
        throw "build_windows.ps1 failed."
    }
    if (-not (Test-Path -LiteralPath $installerPath)) {
        throw "Build finished but installer missing: $installerPath"
    }
}

# --- 4) GitHub Release on download repo (installer) ---
Write-Host ""
Write-Host "--- GitHub Release (installer) on $DownloadRepo ---" -ForegroundColor Cyan
if ((Invoke-GhReleaseExists $gh $DownloadRepo $tag)) {
    if ($SkipDownloadIfReleaseExists) {
        Write-Host "Release $tag already on $DownloadRepo; skipping publish_product_release.ps1 (-SkipDownloadIfReleaseExists)." -ForegroundColor Yellow
    } else {
        throw "Release $tag already exists on $DownloadRepo. Delete the release on GitHub, bump the version, or use -SkipDownloadIfReleaseExists."
    }
} else {
    $pubArgs = @{
        Version = $version
        InstallerPath = $installerPath
        Repo = $DownloadRepo
    }
    if ($NoLatest) { $pubArgs.NoLatest = $true }
    & $publishProductScript @pubArgs
    if ($LASTEXITCODE -ne 0) {
        throw "publish_product_release.ps1 failed."
    }
}

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host "  Builder:   https://github.com/$BuilderRepo/releases/tag/$tag"
Write-Host "  Download: https://github.com/$DownloadRepo/releases/tag/$tag"
