param(
    [string[]]$Strategies = @('F', 'R', 'P'),
    [string]$Output = 'logs\chunking_experiments\metadata_inspection.json'
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { $python = 'python' }
$outputPath = if ([System.IO.Path]::IsPathRooted($Output)) { $Output } else { Join-Path $projectRoot $Output }
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $outputPath) | Out-Null
$env:PYTHONUTF8 = '1'
& $python (Join-Path $projectRoot 'inspect_chunking_metadata.py') --strategies @($Strategies) --output $outputPath
if ($LASTEXITCODE -ne 0) { throw "Metadata inspection failed with exit code $LASTEXITCODE" }
Write-Host "Metadata inspection written: $outputPath"
