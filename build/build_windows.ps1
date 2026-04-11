# Build dist\RootRecord\ and compile installer.
# Requires: pip install pyinstaller
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$pkg = Resolve-Path (Join-Path $here "..")
Set-Location $pkg
python -m PyInstaller --noconfirm (Join-Path $pkg "build_rootrecord.spec")
Write-Host "Built: $(Join-Path $pkg 'dist\RootRecord\RootRecord.exe')"

$iss = Join-Path $pkg "build\rootrecord.iss"
$isccCandidates = @(
    (Join-Path ${env:LOCALAPPDATA} "Programs\Inno Setup 6\ISCC.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
)
$iscc = $isccCandidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $iscc) {
    Write-Warning "ISCC.exe not found. Skipping installer compile."
    return
}

Write-Host "Compiling installer: $iss"
$compileOutput = & $iscc $iss 2>&1
$compileOutput | ForEach-Object { $_ }
$failed = ($LASTEXITCODE -ne 0)
$isLocked = ($compileOutput -join "`n") -match "Error 32"
if ($failed -and $isLocked) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $fallbackName = "RootRecordSetup-$stamp"
    Write-Warning "Installer output file is locked. Retrying as $fallbackName.exe"
    & $iscc "/F$fallbackName" $iss
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup compile failed even after fallback filename retry."
    }
}
elseif ($failed) {
    throw "Inno Setup compile failed."
}
