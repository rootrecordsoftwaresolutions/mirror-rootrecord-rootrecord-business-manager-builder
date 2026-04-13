# Build an MSIX from PyInstaller output (dist\RootRecordBusinessManager) for Microsoft Store submission.
# Requires: Windows SDK (MakeAppx.exe), PyInstaller build already done.
#
# Before Partner Center upload:
# 1. Reserve package identity in Partner Center; set @@IDENTITY_NAME@@ and @@PUBLISHER@@ in the
#    generated AppxManifest.xml (this script substitutes placeholders from variables below).
# 2. Store certification may require privacy policy URL, screenshots, etc.
#
# Individual developer accounts: https://developer.microsoft.com/microsoft-store/register

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$pkg = Resolve-Path (Join-Path $here "..")
$dist = Join-Path $pkg "dist\RootRecordBusinessManager"
$template = Join-Path $here "msix\AppxManifest.template.xml"
$staging = Join-Path $here "msix\_stage"
$outDir = Join-Path $here "output"

# --- Match Partner Center reserved identity (edit before shipping) ---
# If you already published under RootRecordBusinessManagerBeta, keep that value until Partner Center is updated.
$identityName = "RootRecord.RootRecordBusinessManager"
# Placeholder publisher (must be replaced with your Store publisher subject, e.g. CN=...)
$publisher = "CN=RootRecord"

if (-not (Test-Path $dist)) {
    Write-Error "Missing $dist - run PyInstaller (build_windows.ps1) first."
}
if (-not (Test-Path $template)) {
    Write-Error "Missing template: $template"
}

$sem = $null
foreach ($line in Get-Content (Join-Path $pkg "app_version.py")) {
    $t = $line.Trim()
    if ($t.StartsWith("APP_VERSION = ")) {
        $sem = $t.Substring("APP_VERSION = ".Length).Trim().Trim([char]34)
        break
    }
}
if (-not $sem) { Write-Error "Could not parse APP_VERSION from app_version.py" }
$parts = $sem -split '\.'
while ($parts.Count -lt 4) { $parts += "0" }
$msixVersion = $parts[0..3] -join "."

New-Item -ItemType Directory -Force -Path $outDir | Out-Null
if (Test-Path $staging) { Remove-Item -Recurse -Force $staging }
New-Item -ItemType Directory -Force -Path $staging | Out-Null

Write-Host "Staging PyInstaller output..."
Copy-Item -Path (Join-Path $dist "*") -Destination $staging -Recurse -Force

$assets = Join-Path $staging "Assets"
New-Item -ItemType Directory -Force -Path $assets | Out-Null
Add-Type -AssemblyName System.Drawing
function Save-SquarePng([string]$path, [int]$size, [int]$argb) {
    $bmp = New-Object System.Drawing.Bitmap $size, $size
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $c = [System.Drawing.Color]::FromArgb($argb)
    $g.Clear($c)
    $g.Dispose()
    $bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
    $bmp.Dispose()
}
Save-SquarePng (Join-Path $assets "Square44x44Logo.png") 44 0xff1a1a2e
Save-SquarePng (Join-Path $assets "Square150x150Logo.png") 150 0xff1a1a2e
Save-SquarePng (Join-Path $assets "StoreLogo.png") 50 0xff1a1a2e

$manifest = Get-Content -Raw -Path $template
$manifest = $manifest -replace '@@IDENTITY_NAME@@', $identityName
$manifest = $manifest -replace '@@PUBLISHER@@', $publisher
$manifest = $manifest -replace '@@MSIX_VERSION@@', $msixVersion
$manifestPath = Join-Path $staging "AppxManifest.xml"
Set-Content -Path $manifestPath -Value $manifest -Encoding UTF8

$kitBin = Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"
$makeappxPath = $null
if (Test-Path $kitBin) {
    $makeappxPath = (Get-ChildItem -Path $kitBin -Filter "makeappx.exe" -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.DirectoryName -like "*\x64\*" } | Select-Object -First 1).FullName
}
if (-not $makeappxPath) {
    $cmd = Get-Command makeappx -ErrorAction SilentlyContinue
    if ($cmd) { $makeappxPath = $cmd.Source }
}
if (-not $makeappxPath) {
    Write-Error "makeappx.exe not found. Install Windows 10/11 SDK (Desktop development with C++ includes it)."
}

$outMsix = Join-Path $outDir "RootRecord-BusinessManager_$msixVersion.msix"
Write-Host "Packing: $outMsix"
& $makeappxPath pack /h sha256 /o /d $staging /p $outMsix
if ($LASTEXITCODE -ne 0) { throw "MakeAppx failed with exit code $LASTEXITCODE" }

Write-Host "Done. Edit Identity Publisher in AppxManifest if Partner Center requires it, then re-pack."
Write-Host "Upload $outMsix in Partner Center (MSIX)."
