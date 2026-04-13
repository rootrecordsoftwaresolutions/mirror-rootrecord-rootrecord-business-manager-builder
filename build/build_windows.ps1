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

$metaForInno = Join-Path $here "artifact_signing_metadata.json"
$signInnoHook = Join-Path $here "sign_inno_azure.ps1"
$innoAzureSign = $false
if ($Sign -and (Test-Path -LiteralPath $metaForInno) -and (Test-Path -LiteralPath $signInnoHook)) {
    $innoAzureSign = $true
}

# ISCC treats space-separated tokens on its command line as separate arguments. A /SAzureInno= value like
# "powershell.exe -NoProfile ... -File \"long path\" $f" is easy to misparse as multiple args and triggers
# "You may not specify more than one script filename." Fix: a tiny .cmd in %TEMP% (path has no spaces) that
# forwards to sign_inno_azure.ps1; /SAzureInno=<wrapper.cmd> $f is one logical SignTool command.
$isccBaseArgs = [System.Collections.Generic.List[string]]::new()
if ($innoAzureSign) {
    $wrapperCmd = Join-Path $env:TEMP "rr-inno-sign.cmd"
    $ps1Full = (Resolve-Path -LiteralPath $signInnoHook).Path
    $ps1ForCmd = $ps1Full.Replace('"', '""')
    $cmdLines = @(
        '@echo off',
        "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$ps1ForCmd`" %*"
    )
    Set-Content -LiteralPath $wrapperCmd -Value $cmdLines -Encoding ascii
    $isccBaseArgs.Add('/DInnoSignAzure')
    $isccBaseArgs.Add('/SAzureInno=' + $wrapperCmd + ' $f')
    Write-Host "Inno compile will Azure-sign setup internals (SignTool=AzureInno); wrapper: $wrapperCmd" -ForegroundColor Cyan
} elseif ($Sign -and -not (Test-Path -LiteralPath $metaForInno)) {
    Write-Warning "Signing requested but build\artifact_signing_metadata.json missing - Inno will not SignTool-sign internals; post-build sign only (may hit Application Control 4551)."
}

$issFull = (Resolve-Path -LiteralPath $iss).Path
Write-Host "Compiling installer: $issFull"
$isccAll = [System.Collections.Generic.List[string]]::new()
foreach ($a in $isccBaseArgs) { $isccAll.Add($a) }
$isccAll.Add($issFull)
$compileOutput = @(& $iscc @($isccAll.ToArray()) 2>&1)
$compileOutput | ForEach-Object { $_ }
$failed = ($LASTEXITCODE -ne 0)
$isLocked = ($compileOutput -join "`n") -match "Error 32"
if ($failed -and $isLocked) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $fallbackName = "RootRecordSetup-$stamp"
    Write-Warning "Installer output file is locked. Retrying as $fallbackName.exe"
    $retryAll = [System.Collections.Generic.List[string]]::new()
    foreach ($a in $isccBaseArgs) { $retryAll.Add($a) }
    $retryAll.Add("/F$fallbackName")
    $retryAll.Add($issFull)
    $retryOut = @(& $iscc @($retryAll.ToArray()) 2>&1)
    $retryOut | ForEach-Object { $_ }
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup compile failed even after fallback filename retry."
    }
}
elseif ($failed) {
    throw "Inno Setup compile failed."
}

if ($Sign -and -not $innoAzureSign) {
    Write-Host "Signing installer (post-Inno)..." -ForegroundColor Cyan
    if ($StopRunningApp) {
        & $signScript -SkipExe -StopRunningApp
    } else {
        & $signScript -SkipExe
    }
} elseif ($Sign -and $innoAzureSign) {
    Write-Host "Installer already signed during Inno compile (internals + output); skipping duplicate post-Inno sign." -ForegroundColor Green
}

Remove-RrDistBackupIfAny
