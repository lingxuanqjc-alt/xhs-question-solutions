[CmdletBinding()]
param(
    [string]$DestinationRoot = [Environment]::GetFolderPath("UserProfile"),
    [switch]$Force
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($DestinationRoot)) {
    throw "DestinationRoot cannot be empty."
}

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$destinationRootPath = [IO.Path]::GetFullPath($DestinationRoot)
$entries = @(
    @{
        Source = (Join-Path $repoRoot ".agents\skills\xhs-question-solutions")
        Target = (Join-Path $destinationRootPath ".agents\skills\xhs-question-solutions")
        Stage = $null
        Backup = $null
        HadExisting = $false
        BackupCreated = $false
        Installed = $false
    },
    @{
        Source = (Join-Path $repoRoot ".claude\skills\xhs-question-solutions")
        Target = (Join-Path $destinationRootPath ".claude\skills\xhs-question-solutions")
        Stage = $null
        Backup = $null
        HadExisting = $false
        BackupCreated = $false
        Installed = $false
    }
)

foreach ($entry in $entries) {
    if (-not (Test-Path -LiteralPath $entry.Source -PathType Container)) {
        throw "Source skill not found: $($entry.Source)"
    }
    $entry.HadExisting = Test-Path -LiteralPath $entry.Target
}

$existing = @($entries | Where-Object { $_.HadExisting } | ForEach-Object { $_.Target })
if ($existing.Count -gt 0 -and -not $Force) {
    throw "Skill already exists at: $($existing -join ', '). Re-run with -Force to back it up and install."
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$transactionId = [Guid]::NewGuid().ToString("N")
foreach ($entry in $entries) {
    $entry.Stage = "$($entry.Target).installing-$transactionId"
    if ($entry.HadExisting) {
        $entry.Backup = "$($entry.Target).backup-$timestamp"
        if (Test-Path -LiteralPath $entry.Backup) {
            throw "Backup path already exists: $($entry.Backup)"
        }
    }
}

try {
    # Prepare both payloads before changing either installed copy.
    foreach ($entry in $entries) {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $entry.Target) | Out-Null
        Copy-Item -LiteralPath $entry.Source -Destination $entry.Stage -Recurse
    }

    foreach ($entry in $entries) {
        if ($entry.HadExisting) {
            Move-Item -LiteralPath $entry.Target -Destination $entry.Backup
            $entry.BackupCreated = $true
        }
        Move-Item -LiteralPath $entry.Stage -Destination $entry.Target
        $entry.Installed = $true
    }
}
catch {
    $installError = $_
    $rollbackErrors = @()
    for ($index = $entries.Count - 1; $index -ge 0; $index--) {
        $entry = $entries[$index]
        try {
            if ($entry.Installed -and (Test-Path -LiteralPath $entry.Target)) {
                Remove-Item -LiteralPath $entry.Target -Recurse -Force
            }
            if ($entry.BackupCreated -and (Test-Path -LiteralPath $entry.Backup)) {
                Move-Item -LiteralPath $entry.Backup -Destination $entry.Target
            }
        }
        catch {
            $rollbackErrors += $_.Exception.Message
        }
    }
    if ($rollbackErrors.Count -gt 0) {
        throw "Install failed: $($installError.Exception.Message) Rollback also failed: $($rollbackErrors -join '; ')"
    }
    throw $installError
}
finally {
    foreach ($entry in $entries) {
        if ($entry.Stage -and (Test-Path -LiteralPath $entry.Stage)) {
            Remove-Item -LiteralPath $entry.Stage -Recurse -Force
        }
    }
}

foreach ($entry in $entries) {
    if ($entry.BackupCreated) {
        Write-Host "Backed up existing skill to $($entry.Backup)"
    }
    Write-Host "Installed skill to $($entry.Target)"
}
