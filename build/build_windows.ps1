# Build dist\RootRecordBusinessManager\ and compile installer.
# Requires: pip install pyinstaller
#
# Optional: -Sign runs sign_release_azure.ps1 twice so the signed app is *inside* the installer:
#   1) After PyInstaller — sign dist\...\RootRecordBusinessManager.exe only (-SkipInstaller)
#   2) After Inno — sign build\output\RootRecordSetup-*.exe only (-SkipExe)
# Inno snapshots [Files] at compile time; signing only after ISCC leaves an unsigned exe in the package.
[CmdletBinding()]
param(
    [switch] $Sign,
    [switch] $StopRunningApp
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$pkg = Resolve-Path (Join-Path $here "..")
Set-Location $pkg

function Remove-RrDistBackupIfAny {
    if ($script:RR_DistBackupToRemove -and (Test-Path -LiteralPath $script:RR_DistBackupToRemove)) {
        try {
            Remove-Item -LiteralPath $script:RR_DistBackupToRemove -Recurse -Force -ErrorAction SilentlyContinue
        } catch { }
        $script:RR_DistBackupToRemove = $null
    }
}

# PyInstaller must wipe dist\...\ ; locks cause WinError 32 (Cursor, Explorer, AV — not always our .exe name).
if ($Sign -or $StopRunningApp) {
    foreach ($im in @("RootRecordBusinessManager.exe", "RootRecord.exe")) {
        Write-Host "Stopping any running: $im (if present)..." -ForegroundColor Yellow
        cmd /c "taskkill /F /IM $im /T 2>nul" | Out-Null
    }
    Start-Sleep -Milliseconds 1200
}

$distAppDir = Join-Path $pkg "dist\RootRecordBusinessManager"
$script:RR_DistBackupToRemove = $null
if (Test-Path -LiteralPath $distAppDir) {
    $bakName = "RootRecordBusinessManager.rr-bak-" + [Guid]::NewGuid().ToString("N")
    for ($ri = 0; $ri -lt 8; $ri++) {
        try {
            Rename-Item -LiteralPath $distAppDir -NewName $bakName -ErrorAction Stop
            $script:RR_DistBackupToRemove = Join-Path (Split-Path -LiteralPath $distAppDir -Parent) $bakName
            break
        } catch {
            if ($ri -ge 7) {
                throw "Cannot move aside locked folder (close Cursor/Explorer on dist\, then retry): $distAppDir`n$($_.Exception.Message)"
            }
            Start-Sleep -Seconds 2
        }
    }
}

python -m PyInstaller --noconfirm (Join-Path $pkg "build_rootrecord.spec")
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed (exit $LASTEXITCODE). Fix errors above; dist folder may still be locked."
}
Write-Host "Built: $(Join-Path $pkg 'dist\RootRecordBusinessManager\RootRecordBusinessManager.exe')"

$signScript = Join-Path $here "sign_release_azure.ps1"
if ($Sign) {
    if (-not (Test-Path -LiteralPath $signScript)) {
        throw "Signing requested but missing: $signScript"
    }
    Write-Host "Signing app (before Inno packages it)..." -ForegroundColor Cyan
    # Do not splat string array into .ps1 — that passes positional args, not switches.
    if ($StopRunningApp) {
        & $signScript -SkipInstaller -StopRunningApp
    } else {
        & $signScript -SkipInstaller
    }
}

$iss = Join-Path $pkg "build\rootrecord.iss"
$isccCandidates = @(
    (Join-Path ${env:LOCALAPPDATA} "Programs\Inno Setup 6\ISCC.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
)
$iscc = $isccCandidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $iscc) {
    Write-Warning "ISCC.exe not found. Skipping installer compile."
    Remove-RrDistBackupIfAny
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

if ($Sign) {
    Write-Host "Signing installer..." -ForegroundColor Cyan
    if ($StopRunningApp) {
        & $signScript -SkipExe -StopRunningApp
    } else {
        & $signScript -SkipExe
    }
}

Remove-RrDistBackupIfAny
