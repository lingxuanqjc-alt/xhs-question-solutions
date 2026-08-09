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
$source = (Resolve-Path -LiteralPath (Join-Path $repoRoot ".agents\skills\xhs-question-solutions")).Path
$destinationRootPath = [IO.Path]::GetFullPath($DestinationRoot)
$targets = @(
    (Join-Path $destinationRootPath ".agents\skills\xhs-question-solutions"),
    (Join-Path $destinationRootPath ".claude\skills\xhs-question-solutions")
)

$existing = @($targets | Where-Object { Test-Path -LiteralPath $_ })
if ($existing.Count -gt 0 -and -not $Force) {
    throw "Skill already exists at: $($existing -join ', '). Re-run with -Force to back it up and install."
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
foreach ($target in $targets) {
    $parent = Split-Path -Parent $target
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    if (Test-Path -LiteralPath $target) {
        $backup = "$target.backup-$timestamp"
        Move-Item -LiteralPath $target -Destination $backup
        Write-Host "Backed up existing skill to $backup"
    }
    Copy-Item -LiteralPath $source -Destination $target -Recurse
    Write-Host "Installed skill to $target"
}
