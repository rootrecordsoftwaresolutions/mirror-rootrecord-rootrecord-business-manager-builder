# Invoked by Inno Setup during ISCC (SignTool=AzureInno) to sign setup/uninstall binaries with Azure Trusted Signing.
# Signing only the final .exe after compile leaves unsigned code extracted under %TEMP% — Application Control (4551) blocks it.
# Requires same setup as sign_release_azure.ps1 (metadata JSON, dlib, az login, x64 signtool).

param(
    [Parameter(Position = 0)]
    [string] $FilePath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $FilePath -and $args.Count -gt 0) {
    $FilePath = $args[0]
}
if (-not $FilePath) {
    Write-Error "No file path (Inno SignTool should pass `$f)."
    exit 2
}
$FilePath = $FilePath.Trim().Trim([char]0x22)
if (-not (Test-Path -LiteralPath $FilePath)) {
    Write-Error "File not found: $FilePath"
    exit 2
}

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

$here = $PSScriptRoot
$tenantFile = Join-Path $here "azure_tenant_id.txt"
if (-not $env:AZURE_TENANT_ID -and (Test-Path -LiteralPath $tenantFile)) {
    $env:AZURE_TENANT_ID = (Get-Content -LiteralPath $tenantFile -Raw).Trim()
}

$metaPath = $env:ARTIFACT_SIGNING_METADATA
if (-not $metaPath) {
    $metaPath = Join-Path $here "artifact_signing_metadata.json"
}
if (-not (Test-Path -LiteralPath $metaPath)) {
    Write-Error "Inno SignTool: metadata not found: $metaPath"
    exit 3
}

$signtool = Find-SignToolX64
if (-not $signtool) {
    Write-Error "signtool.exe (x64) not found."
    exit 3
}
$dlib = Find-AzureCodeSigningDlib
if (-not $dlib) {
    Write-Error "Azure.CodeSigning.Dlib.dll (x64) not found."
    exit 3
}

$metaResolved = (Resolve-Path -LiteralPath $metaPath).Path
$timestamp = "http://timestamp.acs.microsoft.com/"
Write-Host "Inno SignTool: signing $FilePath" -ForegroundColor Cyan
$signArgs = @(
    "sign", "/v",
    "/fd", "SHA256",
    "/tr", $timestamp,
    "/td", "SHA256",
    "/dlib", $dlib,
    "/dmdf", $metaResolved,
    $FilePath
)
$oldEa = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $signtool @signArgs
$code = $LASTEXITCODE
$ErrorActionPreference = $oldEa
exit $code
