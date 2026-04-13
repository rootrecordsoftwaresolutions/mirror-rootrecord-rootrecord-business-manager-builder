# Sign RootRecordBusinessManager.exe and RootRecordSetup-*.exe with Azure Artifact Signing (Trusted Signing).
# Prerequisites: https://learn.microsoft.com/en-us/azure/trusted-signing/how-to-signing-integrations
#   - winget install -e --id Microsoft.Azure.ArtifactSigningClientTools
#   - Windows SDK SignTool (10.0.22621.x or newer build under Windows Kits\10\bin\...\x64)
#   - .NET 8 Runtime (x64)
#   - az login (or another DefaultAzureCredential source) as a user with
#     "Artifact Signing Certificate Profile Signer" on the signing account/profile
#
# Metadata JSON (copy sample and edit if needed):
#   Copy artifact_signing_metadata.sample.json -> artifact_signing_metadata.json (gitignored)
# Or set ARTIFACT_SIGNING_METADATA to the full path of your JSON file.
#
# Optional overrides:
#   $env:AZURE_CODESIGN_DLIB = full path to Azure.CodeSigning.Dlib.dll (x64)
#   $env:SIGNTOOL_PATH = full path to x64 signtool.exe
#
# If SignTool says "file is being used by another process": close the app, or use -StopRunningApp
# (stops RootRecordBusinessManager.exe / RootRecord.exe before signing).
# If the real path stays locked, signed sidecar is used unless you pass -NoSignedSidecar (writes Name.signed.exe).
#
# Do not paste whole terminal transcripts (lines starting with PS>, "Signing:", errors) back into
# PowerShell — run only a single command line, or use sign_release_azure.cmd from cmd.exe.

[CmdletBinding()]
param(
    [switch] $SkipExe,
    [switch] $SkipInstaller,
    [switch] $StopRunningApp,
    [string[]] $ExtraFiles,
    [switch] $NoSignedSidecar
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Find-SignToolX64 {
    if ($env:SIGNTOOL_PATH -and (Test-Path -LiteralPath $env:SIGNTOOL_PATH)) {
        return (Resolve-Path -LiteralPath $env:SIGNTOOL_PATH).Path
    }
    $cmd = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -match '\\x64\\') {
        return $cmd.Source
    }
    $binRoot = Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"
    if (-not (Test-Path -LiteralPath $binRoot)) { return $null }
    $signtool = Get-ChildItem -Path $binRoot -Directory -ErrorAction SilentlyContinue |
        Sort-Object { $_.Name } -Descending |
        ForEach-Object {
            $p = Join-Path $_.FullName "x64\signtool.exe"
            if (Test-Path -LiteralPath $p) { [PSCustomObject]@{ Name = $_.Name; Path = $p } }
        } |
        Select-Object -First 1
    if ($signtool) { return $signtool.Path }
    return $null
}

function Find-AzureCodeSigningDlib {
    if ($env:AZURE_CODESIGN_DLIB -and (Test-Path -LiteralPath $env:AZURE_CODESIGN_DLIB)) {
        return (Resolve-Path -LiteralPath $env:AZURE_CODESIGN_DLIB).Path
    }
    $localCandidates = @(
        (Join-Path $env:LOCALAPPDATA "Microsoft\MicrosoftArtifactSigningClientTools\Azure.CodeSigning.Dlib.dll"),
        (Join-Path $env:LOCALAPPDATA "Microsoft\MicrosoftTrustedSigningClientTools\x64\Azure.CodeSigning.Dlib.dll")
    )
    foreach ($lc in $localCandidates) {
        if (Test-Path -LiteralPath $lc) {
            return (Resolve-Path -LiteralPath $lc).Path
        }
    }

    $searchRoots = @(
        (Join-Path $env:ProgramFiles "Microsoft Azure Artifact Signing Client Tools"),
        (Join-Path ${env:ProgramFiles(x86)} "Microsoft Azure Artifact Signing Client Tools"),
        (Join-Path $env:ProgramFiles "Microsoft Azure Artifact Signing")
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }

    foreach ($root in $searchRoots) {
        $hit = Get-ChildItem -Path $root -Recurse -Filter "Azure.CodeSigning.Dlib.dll" -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match '[\\/]x64[\\/]Azure\.CodeSigning\.Dlib\.dll$' } |
            Select-Object -First 1
        if ($hit) { return $hit.FullName }
        $hit = Get-ChildItem -Path $root -Filter "Azure.CodeSigning.Dlib.dll" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($hit) { return $hit.FullName }
    }
    return $null
}

function Test-SignToolVersion([string] $signToolPath) {
    $vi = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($signToolPath)
    $fv = $vi.FileVersion
    if (-not $fv) { return }
    $parts = $fv.Split('.')
    if ($parts.Count -lt 3) { return }
    $build = 0
    [void][int]::TryParse($parts[2], [ref]$build)
    if ($build -lt 22621) {
        Write-Warning "SignTool file version $fv may be below the minimum (10.0.22621.x) for Artifact Signing. If signing fails, install a newer Windows SDK."
    }
}

function Stop-ProcessesUsingImagePath([string] $imagePath) {
    if (-not (Test-Path -LiteralPath $imagePath)) { return }
    $full = (Resolve-Path -LiteralPath $imagePath).Path
    $norm = $full.TrimEnd([char]0x00).Trim()
    $hits = @(Get-CimInstance -ClassName Win32_Process -ErrorAction SilentlyContinue | Where-Object {
            $_.ExecutablePath -and ($_.ExecutablePath.TrimEnd([char]0x00).Trim() -ieq $norm)
        })
    foreach ($p in $hits) {
        Write-Host "Stopping process locking file: $($p.Name) (PID $($p.ProcessId))" -ForegroundColor Yellow
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    if ($hits.Count -gt 0) {
        Start-Sleep -Milliseconds 800
    }
}

function Invoke-SignSingleFile {
    param(
        [string] $FilePath,
        [string] $SigntoolPath,
        [string[]] $SignArgs,
        [switch] $StopLockers,
        [switch] $NoSignedSidecar
    )
    $oldEa = $ErrorActionPreference
    $ErrorActionPreference = "Continue"

    function Invoke-SigntoolOnce([string] $path) {
        $output = @(& $SigntoolPath @SignArgs $path 2>&1)
        $code = $LASTEXITCODE
        foreach ($line in $output) {
            if ($line -is [System.Management.Automation.ErrorRecord]) {
                Write-Host $line.Exception.Message
            } else {
                Write-Host $line
            }
        }
        $flat = ($output | Out-String)
        return @{ ExitCode = $code; Flat = $flat }
    }

    try {
        $ext = [IO.Path]::GetExtension($FilePath)
        if ([string]::IsNullOrEmpty($ext)) { $ext = ".exe" }
        $dirHint = [System.IO.Path]::GetDirectoryName($FilePath)
        $maxCopySignAttempts = 3
        $maxSwapAttempts = 10
        $swapDelaySec = 3
        $signedOk = $false
        $sidecarPath = $null

        function Save-SignedSidecar([string] $targetFile) {
            $d = [System.IO.Path]::GetDirectoryName($targetFile)
            $leaf = [System.IO.Path]::GetFileNameWithoutExtension($targetFile) + ".signed" + [System.IO.Path]::GetExtension($targetFile)
            return (Join-Path $d $leaf)
        }

        function Test-SignedWithSigntool([string] $path) {
            Write-Host "Verifying: $path" -ForegroundColor Cyan
            $vOut = @(& $SigntoolPath verify /pa /v $path 2>&1)
            foreach ($line in $vOut) {
                if ($line -is [System.Management.Automation.ErrorRecord]) {
                    Write-Host $line.Exception.Message
                } else {
                    Write-Host $line
                }
            }
            return ($LASTEXITCODE -eq 0)
        }

        # Prefer signing in place: File.Replace often fails when another handle has the path open
        # for read (Explorer, indexer); SignTool may still succeed with a shared read lock.
        if ($StopLockers) {
            Stop-ProcessesUsingImagePath $FilePath
        }
        $direct = Invoke-SigntoolOnce $FilePath
        if ($direct.ExitCode -eq 0) {
            $signedOk = $true
        } else {
            if ($direct.Flat -like '*403*' -or $direct.Flat -like '*Forbidden*') {
                throw "Azure returned 403 Forbidden. Confirm artifact_signing_metadata.json CodeSigningAccountName matches your Azure resource name (yours is rootrecordystems). In Portal: IAM -> Artifact Signing Certificate Profile Signer on the certificate profile. Endpoint must match region (East US = https://eus.codesigning.azure.net)."
            }
            Write-Warning "In-place sign failed (exit $($direct.ExitCode)); retrying with temp copy + replace..."
        }

        # Phase 1: one Azure sign per successful temp file (avoid re-submitting digest on swap retries).
        $tmp = $null
        :copySignLoop for ($cAttempt = 1; -not $signedOk -and $cAttempt -le $maxCopySignAttempts; $cAttempt++) {
            if ($cAttempt -gt 1) {
                Write-Warning "Retry copy/sign $cAttempt/$maxCopySignAttempts in ${swapDelaySec}s..."
                Start-Sleep -Seconds $swapDelaySec
            }
            if ($tmp -and (Test-Path -LiteralPath $tmp)) {
                Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
            }
            $tmp = Join-Path $env:TEMP ("rr-codesign-" + [Guid]::NewGuid().ToString("N") + $ext)
            try {
                Copy-Item -LiteralPath $FilePath -Destination $tmp -Force
            } catch {
                if ($cAttempt -ge $maxCopySignAttempts) {
                    throw "Cannot read file to copy (locked?). Close Explorer on: $dirHint`n$($_.Exception.Message)"
                }
                if (Test-Path -LiteralPath $tmp) {
                    Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
                }
                continue
            }

            $r = Invoke-SigntoolOnce $tmp
            if ($r.ExitCode -ne 0) {
                if (Test-Path -LiteralPath $tmp) {
                    Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
                }
                if ($r.Flat -like '*403*' -or $r.Flat -like '*Forbidden*') {
                    throw "Azure returned 403 Forbidden. Confirm artifact_signing_metadata.json CodeSigningAccountName matches your Azure resource name (yours is rootrecordystems). In Portal: IAM -> Artifact Signing Certificate Profile Signer on the certificate profile. Endpoint must match region (East US = https://eus.codesigning.azure.net)."
                }
                if ($cAttempt -ge $maxCopySignAttempts) {
                    throw "signtool failed (exit $($r.ExitCode)) on temp copy for: $FilePath`n$($r.Flat)"
                }
                continue
            }

            # Phase 2: retries only swap files onto disk (no second call to Azure for the same digest).
            $swapLastErr = ""
            for ($sAttempt = 1; $sAttempt -le $maxSwapAttempts; $sAttempt++) {
                if ($sAttempt -gt 1) {
                    Write-Warning "Retry unlock/swap $sAttempt/$maxSwapAttempts in ${swapDelaySec}s (signed temp reused; Azure not called again)..."
                    Start-Sleep -Seconds $swapDelaySec
                }
                if (-not (Test-Path -LiteralPath $tmp)) {
                    $swapLastErr = "Signed temp file disappeared."
                    break
                }
                $swapOk = $false
                try {
                    if ($StopLockers) {
                        Stop-ProcessesUsingImagePath $FilePath
                    }
                    $src = (Resolve-Path -LiteralPath $tmp).Path
                    $dst = (Resolve-Path -LiteralPath $FilePath).Path
                    $bak = Join-Path $env:TEMP ("rr-codesign-replaced-" + [Guid]::NewGuid().ToString("N") + $ext)
                    try {
                        [System.IO.File]::Replace($src, $dst, $bak)
                    } finally {
                        if (Test-Path -LiteralPath $bak) {
                            Remove-Item -LiteralPath $bak -Force -ErrorAction SilentlyContinue
                        }
                    }
                    $swapOk = $true
                } catch {
                    $swapLastErr = $_.Exception.Message
                    try {
                        if ($StopLockers) {
                            Stop-ProcessesUsingImagePath $FilePath
                        }
                        if (-not (Test-Path -LiteralPath $tmp)) {
                            throw "Signed temp missing after failed Replace."
                        }
                        $src2 = (Resolve-Path -LiteralPath $tmp).Path
                        $dst2 = (Resolve-Path -LiteralPath $FilePath).Path
                        $origLeaf = [System.IO.Path]::GetFileName($dst2)
                        $oldLeaf = $origLeaf + ".rr-codesign-old-" + [Guid]::NewGuid().ToString("N") + $ext
                        $dir = [System.IO.Path]::GetDirectoryName($dst2)
                        $oldPath = Join-Path $dir $oldLeaf
                        Rename-Item -LiteralPath $dst2 -NewName $oldLeaf -ErrorAction Stop
                        try {
                            Move-Item -LiteralPath $src2 -Destination $dst2 -Force -ErrorAction Stop
                            if (Test-Path -LiteralPath $oldPath) {
                                Remove-Item -LiteralPath $oldPath -Force -ErrorAction SilentlyContinue
                            }
                            $swapOk = $true
                        } catch {
                            if ((Test-Path -LiteralPath $oldPath) -and -not (Test-Path -LiteralPath $dst2)) {
                                Rename-Item -LiteralPath $oldPath -NewName $origLeaf -ErrorAction SilentlyContinue
                            }
                            throw
                        }
                    } catch {
                        $swapOk = $false
                        $swapLastErr = $_.Exception.Message
                    }
                }
                if ($swapOk) {
                    $signedOk = $true
                    $tmp = $null
                    break
                }
            }

            if ($signedOk) {
                break copySignLoop
            }
            if (-not $NoSignedSidecar -and (Test-Path -LiteralPath $tmp)) {
                $sc = Save-SignedSidecar $FilePath
                try {
                    Copy-Item -LiteralPath $tmp -Destination $sc -Force
                    if (Test-SignedWithSigntool $sc) {
                        Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
                        $tmp = $null
                        $signedOk = $true
                        $sidecarPath = $sc
                        Write-Warning "Original is still locked; signed copy written and verified: $sc`nWhen unlocked: Move-Item -LiteralPath '$sc' -Destination '$FilePath' -Force"
                        break copySignLoop
                    }
                    Remove-Item -LiteralPath $sc -Force -ErrorAction SilentlyContinue
                } catch {
                    # keep $tmp for preserve below
                }
            }
            if (Test-Path -LiteralPath $tmp) {
                $sc2 = Save-SignedSidecar $FilePath
                try {
                    Copy-Item -LiteralPath $tmp -Destination $sc2 -Force
                    Write-Host "Preserved Azure-signed copy (replace failed): $sc2" -ForegroundColor Yellow
                } catch { }
                Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
            }
            $tmp = $null
            if ($cAttempt -ge $maxCopySignAttempts) {
                throw "Signed temp file but could not replace original after $maxSwapAttempts tries (still locked?). Close Explorer on: $dirHint`nLast error: $swapLastErr"
            }
        }

        if (-not $signedOk) {
            throw "Signing did not complete for: $FilePath"
        }

        if (-not $sidecarPath) {
            if (-not (Test-SignedWithSigntool $FilePath)) {
                throw "signtool verify failed for: $FilePath"
            }
        }
        return $sidecarPath
    } finally {
        $ErrorActionPreference = $oldEa
    }
}

$here = $PSScriptRoot
$pkg = Resolve-Path (Join-Path $here "..")

$tenantFile = Join-Path $here "azure_tenant_id.txt"
if (-not $env:AZURE_TENANT_ID -and (Test-Path -LiteralPath $tenantFile)) {
    $env:AZURE_TENANT_ID = (Get-Content -LiteralPath $tenantFile -Raw).Trim()
}

$metaPath = $env:ARTIFACT_SIGNING_METADATA
if (-not $metaPath) {
    $metaPath = Join-Path $here "artifact_signing_metadata.json"
}
if (-not (Test-Path -LiteralPath $metaPath)) {
    throw @"
Artifact Signing metadata file not found: $metaPath

Copy build\artifact_signing_metadata.sample.json to build\artifact_signing_metadata.json
and set Endpoint (region), CodeSigningAccountName, and CertificateProfileName.
Or set env ARTIFACT_SIGNING_METADATA to your JSON path.
"@
}

$signtool = Find-SignToolX64
if (-not $signtool) {
    throw "signtool.exe (x64) not found. Install Windows 10/11 SDK (Build Tools) and ensure ...\Windows Kits\10\bin\<version>\x64\signtool.exe exists, or set SIGNTOOL_PATH."
}

$dlib = Find-AzureCodeSigningDlib
if (-not $dlib) {
    throw @"
Azure.CodeSigning.Dlib.dll (x64) not found.

Install: winget install -e --id Microsoft.Azure.ArtifactSigningClientTools
Typical locations after install:
  %LOCALAPPDATA%\Microsoft\MicrosoftArtifactSigningClientTools\Azure.CodeSigning.Dlib.dll
  %LOCALAPPDATA%\Microsoft\MicrosoftTrustedSigningClientTools\x64\Azure.CodeSigning.Dlib.dll
  or under Program Files\Microsoft Azure Artifact Signing Client Tools\

Set AZURE_CODESIGN_DLIB to the full path if discovery fails.
"@
}

Test-SignToolVersion $signtool

$timestamp = "http://timestamp.acs.microsoft.com/"
$signArgs = @(
    "sign", "/v",
    "/fd", "SHA256",
    "/tr", $timestamp,
    "/td", "SHA256",
    "/dlib", $dlib,
    "/dmdf", (Resolve-Path -LiteralPath $metaPath).Path
)

$targets = [System.Collections.Generic.List[string]]::new()
if (-not $SkipExe) {
    $exe = Join-Path $pkg "dist\RootRecordBusinessManager\RootRecordBusinessManager.exe"
    if (Test-Path -LiteralPath $exe) {
        $targets.Add((Resolve-Path -LiteralPath $exe).Path)
    } else {
        Write-Warning "Skip app exe (not found): $exe"
    }
}
if (-not $SkipInstaller) {
    $outDir = Join-Path $here "output"
    if (Test-Path -LiteralPath $outDir) {
        $setupItems = @(Get-ChildItem -LiteralPath $outDir -Filter "RootRecordSetup-*.exe" -ErrorAction SilentlyContinue)
        if ($setupItems.Count -gt 0) {
            $latest = $setupItems | Sort-Object -Property LastWriteTime -Descending | Select-Object -First 1
            $targets.Add($latest.FullName)
        } else {
            Write-Warning "Skip installer: no RootRecordSetup-*.exe under build\output."
        }
    } else {
        Write-Warning "Skip installer: build\output folder not found."
    }
}
foreach ($f in $ExtraFiles) {
    if ($f -and (Test-Path -LiteralPath $f)) {
        $targets.Add((Resolve-Path -LiteralPath $f).Path)
    }
}

if ($targets.Count -eq 0) {
    throw "Nothing to sign. Build the app and Inno installer first, or pass -ExtraFiles."
}

if ($StopRunningApp) {
    foreach ($im in @("RootRecordBusinessManager.exe", "RootRecord.exe")) {
        Write-Host "Stopping any running: $im (if present)..." -ForegroundColor Yellow
        cmd /c "taskkill /F /IM $im /T 2>nul" | Out-Null
    }
    Start-Sleep -Milliseconds 800
}

$sidecarFromThisRun = [System.Collections.Generic.List[string]]::new()
foreach ($file in $targets) {
    Write-Host "Signing: $file" -ForegroundColor Cyan
    $sc = Invoke-SignSingleFile -FilePath $file -SigntoolPath $signtool -SignArgs $signArgs -StopLockers:$StopRunningApp -NoSignedSidecar:$NoSignedSidecar
    if ($sc) { [void]$sidecarFromThisRun.Add($sc) }
}

if ($sidecarFromThisRun.Count -gt 0) {
    Write-Host "Done. Signed and verified; some outputs used .signed.exe beside the target (see warnings above)." -ForegroundColor Green
} else {
    Write-Host "Done. All listed files signed and verified in place." -ForegroundColor Green
}
