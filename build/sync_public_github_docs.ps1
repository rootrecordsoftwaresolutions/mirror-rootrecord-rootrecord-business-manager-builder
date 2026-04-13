# Syncs customer-facing docs into the GitHub repo folder (README + images only).
# Windows installer `.exe` is NOT committed here - publish it with publish_product_release.ps1 (GitHub Releases).
#
# Usage (PowerShell):
#   cd "...\Root Record Business Manager\build"
#   .\sync_public_github_docs.ps1
#
# Optional: copy marketing images from workspace root (RR Business Operations):
#   .\sync_public_github_docs.ps1 -SyncImagesFromWorkspaceRoot
#
# Then from the output folder: git add -A; git commit -m "Sync product readme"; git push

[CmdletBinding()]
param(
    [switch] $SyncImagesFromWorkspaceRoot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$pkgRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$devRoot = Split-Path $pkgRoot -Parent
$opsRoot = Resolve-Path (Join-Path $devRoot "..")
$productRepoRoot = Join-Path $devRoot "rootrecord-business-manager-download"

New-Item -ItemType Directory -Force -Path (Join-Path $productRepoRoot "assets") | Out-Null

if ($SyncImagesFromWorkspaceRoot) {
    $pairs = @(
        @( (Join-Path $opsRoot "banner.jpg"), (Join-Path $pkgRoot "docs\assets\banner.jpg") ),
        @( (Join-Path $opsRoot "dashboard_image.jpg"), (Join-Path $pkgRoot "docs\assets\dashboard_image.jpg") ),
        @( (Join-Path $opsRoot "main gitgub icon.jpg"), (Join-Path $pkgRoot "docs\assets\github-icon.jpg") )
    )
    foreach ($p in $pairs) {
        $src = $p[0]; $dst = $p[1]
        if (Test-Path -LiteralPath $src) {
            Copy-Item -LiteralPath $src -Destination $dst -Force
            Write-Host "Copied: $src -> $dst"
        } else {
            Write-Warning "Missing source image (skipped): $src"
        }
    }
}

$srcReadme = Join-Path $pkgRoot "docs\PRODUCT_README.md"
$dstReadme = Join-Path $productRepoRoot "README.md"
$body = Get-Content -LiteralPath $srcReadme -Raw -Encoding UTF8
$banner = @"
> **RootRecord Business Manager** - product overview, download links, and release notes. The Windows installer is attached to **[GitHub Releases](https://github.com/RootRecord/rootrecord-business-manager-download/releases)** only (not stored in git).

---
"@
# UTF-8 with BOM so viewers that assume Windows-1252 still decode the rest of the file correctly.
$utf8Bom = New-Object System.Text.UTF8Encoding $true
[System.IO.File]::WriteAllText($dstReadme, $banner + $body, $utf8Bom)

$destAssets = Join-Path $productRepoRoot "assets"
$keepNames = @("banner.jpg", "dashboard_image.jpg", "github-icon.jpg", "README.md") | ForEach-Object { $_.ToLowerInvariant() }
if (Test-Path -LiteralPath $destAssets) {
    Get-ChildItem -LiteralPath $destAssets -File | ForEach-Object {
        if ($keepNames -notcontains $_.Name.ToLowerInvariant()) {
            Remove-Item -LiteralPath $_.FullName -Force
        }
    }
}
$imageNames = @("banner.jpg", "dashboard_image.jpg", "github-icon.jpg", "README.md")
foreach ($name in $imageNames) {
    $src = Join-Path $pkgRoot "docs\assets\$name"
    if (Test-Path -LiteralPath $src) {
        Copy-Item -LiteralPath $src -Destination (Join-Path $destAssets $name) -Force
    } elseif ($name -eq "README.md") {
        Write-Warning "Missing docs\assets\README.md"
    } else {
        throw "Required image missing: $src`nAdd the file or run with -SyncImagesFromWorkspaceRoot."
    }
}

$gitignore = Join-Path $productRepoRoot ".gitignore"
@(
    "Thumbs.db",
    ".DS_Store",
    "*.exe",
    ""
) | Set-Content -LiteralPath $gitignore -Encoding UTF8

Write-Host "Synced to: $productRepoRoot"
Write-Host "Publish installer: .\publish_product_release.ps1"
