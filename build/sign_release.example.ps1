# Example: sign RootRecord.exe and the Inno installer with an Authenticode certificate.
# This is what removes "Unknown publisher" and reduces Microsoft SmartScreen warnings.
#
# Prerequisites:
#   - A standard (OV) or EV code signing certificate from a public CA (DigiCert, Sectigo, SSL.com, etc.)
#   - Windows SDK (signtool.exe), e.g. "SignTool" from Visual Studio Build Tools or standalone SDK
#
# Usage (after copying to sign_release.ps1 and filling secrets):
#   $env:CODESIGN_PFX = "C:\path\to\codesign.pfx"
#   $env:CODESIGN_PFX_PASSWORD = "your-pfx-password"
#   .\sign_release.ps1
#
# Or use a hardware token / Windows certificate store instead of PFX (see signtool /sha1).
#
# Sign order: sign RootRecord.exe before compiling the installer, OR sign both exe and Setup.exe after build.

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$pfx = $env:CODESIGN_PFX
$pwd = $env:CODESIGN_PFX_PASSWORD
if (-not $pfx -or -not (Test-Path $pfx)) {
    Write-Error "Set CODESIGN_PFX to your .pfx path (or adapt script for /sha1 from certificate store)."
}
if (-not $pwd) {
    Write-Error "Set CODESIGN_PFX_PASSWORD for the PFX (or use a secure prompt)."
}

$signtool = Get-Command signtool -ErrorAction SilentlyContinue
if (-not $signtool) {
    Write-Error "signtool.exe not on PATH. Install Windows SDK or add SignTools folder to PATH."
}

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$pkg = Resolve-Path (Join-Path $here "..")
$exe = Join-Path $pkg "dist\RootRecord\RootRecord.exe"
$setup = Get-ChildItem -Path (Join-Path $here "output") -Filter "RootRecordSetup-*.exe" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1

$stamp = "http://timestamp.digicert.com"
$argsCommon = @(
    "sign", "/fd", "sha256", "/tr", $stamp, "/td", "sha256",
    "/f", $pfx, "/p", $pwd, "/v"
)

if (Test-Path $exe) {
    Write-Host "Signing: $exe"
    & signtool @argsCommon $exe
} else {
    Write-Warning "Skip exe: not found: $exe (build PyInstaller first)."
}

if ($setup) {
    Write-Host "Signing: $($setup.FullName)"
    & signtool @argsCommon $setup.FullName
} else {
    Write-Warning "Skip installer: no RootRecordSetup-*.exe under build\output."
}

Write-Host "Done. Distribute the signed files; SmartScreen reputation still improves as users run them."
