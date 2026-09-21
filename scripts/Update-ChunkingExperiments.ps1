param(
    [string[]]$Strategies = @('F', 'R', 'P')
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { $python = 'python' }

$oldStrategy = $env:CHUNKING_STRATEGY
$oldChroma = $env:CHROMA_DIR
$oldCollection = $env:COLLECTION_NAME

try {
    foreach ($strategy in $Strategies) {
        $normalized = $strategy.ToUpperInvariant()
        if ($normalized -notin @('F', 'R', 'P')) {
            throw "Strategy must be F, R, or P: $strategy"
        }

        $persistDir = Join-Path $projectRoot "data\chunking_experiments\$normalized"
        $collection = "design_knowledge_$($normalized.ToLowerInvariant())"
        if (-not (Test-Path -LiteralPath $persistDir)) {
            throw "Experiment index does not exist: $persistDir"
        }

        $env:CHUNKING_STRATEGY = $normalized
        $env:CHROMA_DIR = $persistDir
        $env:COLLECTION_NAME = $collection

        Write-Host "=== Incremental ingest: $normalized ==="
        & $python (Join-Path $projectRoot 'ingest.py') --persist-dir $persistDir --collection-name $collection
        if ($LASTEXITCODE -ne 0) {
            throw "Incremental ingest failed for $normalized with exit code $LASTEXITCODE"
        }
    }
}
finally {
    if ($null -eq $oldStrategy) { Remove-Item Env:CHUNKING_STRATEGY -ErrorAction SilentlyContinue } else { $env:CHUNKING_STRATEGY = $oldStrategy }
    if ($null -eq $oldChroma) { Remove-Item Env:CHROMA_DIR -ErrorAction SilentlyContinue } else { $env:CHROMA_DIR = $oldChroma }
    if ($null -eq $oldCollection) { Remove-Item Env:COLLECTION_NAME -ErrorAction SilentlyContinue } else { $env:COLLECTION_NAME = $oldCollection }
}
