param(
    [string[]]$Strategies = @('F', 'R', 'P'),
    [int]$QuestionCount = 10,
    [string]$OutputRoot = 'logs\chunking_cap_experiments'
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$root = if ([System.IO.Path]::IsPathRooted($OutputRoot)) {
    $OutputRoot
} else {
    Join-Path $projectRoot $OutputRoot
}
New-Item -ItemType Directory -Force -Path $root | Out-Null

foreach ($cap in @(2, 3, 0)) {
    $label = if ($cap -eq 0) { 'no_cap' } else { "cap_$cap" }
    $output = Join-Path $root $label
    New-Item -ItemType Directory -Force -Path $output | Out-Null
    & (Join-Path $PSScriptRoot 'Run-ChunkingEvaluation.ps1') `
        -Strategies $Strategies `
        -QuestionCount $QuestionCount `
        -SourceCap $cap `
        -OutputRoot $output
    if ($LASTEXITCODE -ne 0) {
        throw "Cap experiment failed for $label with exit code $LASTEXITCODE"
    }
}

Write-Host "Cap experiments written to: $root"
