param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("F", "R", "P")]
    [string]$Strategy,
    [string]$OutputRoot = "data\chunking_experiments"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { $python = "python" }
$output = if ([System.IO.Path]::IsPathRooted($OutputRoot)) { $OutputRoot } else { Join-Path $projectRoot $OutputRoot }
$persist = Join-Path $output $Strategy
$collection = "design_knowledge_$($Strategy.ToLowerInvariant())"
$metadataPath = Join-Path $persist "chunking_experiment.json"
New-Item -ItemType Directory -Force -Path $persist | Out-Null

$oldStrategy = $env:CHUNKING_STRATEGY
try {
    $env:CHUNKING_STRATEGY = $Strategy
    & $python (Join-Path $projectRoot "ingest.py") --full --persist-dir $persist --collection-name $collection --metadata-output $metadataPath
    if ($LASTEXITCODE -ne 0) { throw "ingest.py failed with exit code $LASTEXITCODE" }
    $manifest = Get-Content -LiteralPath $metadataPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $createdAt = (Get-Date).ToUniversalTime().ToString("o")
    $manifest | Add-Member -NotePropertyName created_at -NotePropertyValue $createdAt -Force
    $embeddingPath = $env:EMBEDDING_MODEL_PATH
    $manifest | Add-Member -NotePropertyName embedding_model_path -NotePropertyValue $embeddingPath -Force
    $manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $metadataPath -Encoding UTF8
    Write-Host "Chunking experiment ready: $persist"
}
finally {
    if ($null -eq $oldStrategy) { Remove-Item Env:CHUNKING_STRATEGY -ErrorAction SilentlyContinue }
    else { $env:CHUNKING_STRATEGY = $oldStrategy }
}
