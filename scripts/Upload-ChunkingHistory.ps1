param(
    [string]$InputDir = 'logs',
    [string[]]$Files,
    [string]$Dataset = 'design-knowledge-chunking-10',
    [string]$ExperimentPrefix = 'design-kb-history',
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw "未找到项目虚拟环境：$python" }

$resolvedInputDir = if ([System.IO.Path]::IsPathRooted($InputDir)) { $InputDir } else { Join-Path $projectRoot $InputDir }
$arguments = @((Join-Path $projectRoot 'upload_chunking_history.py'), '--input-dir', $resolvedInputDir, '--dataset', $Dataset, '--experiment-prefix', $ExperimentPrefix)
if ($Files) {
    $resolvedFiles = @($Files | ForEach-Object {
        if ([System.IO.Path]::IsPathRooted($_)) { $_ } else { Join-Path $projectRoot $_ }
    })
    $arguments += '--files'; $arguments += $resolvedFiles
}
if ($DryRun) { $arguments += '--dry-run' }
& $python @arguments
exit $LASTEXITCODE
