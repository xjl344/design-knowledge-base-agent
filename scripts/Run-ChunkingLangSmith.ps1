param(
    [string[]]$Strategies = @("R", "P", "F"),
    [string]$ExperimentPrefix = "design-kb-chunking",
    [int]$SourceCap = -1
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { $python = "python" }

$oldStrategy = $env:CHUNKING_STRATEGY
$oldChroma = $env:CHROMA_DIR
$oldCollection = $env:COLLECTION_NAME
$oldPythonUtf8 = $env:PYTHONUTF8
$oldSourceCap = $env:RETRIEVER_SOURCE_CAP
try {
    $env:PYTHONUTF8 = "1"
    foreach ($strategy in $Strategies) {
        $normalized = $strategy.ToUpperInvariant()
        if ($normalized -notin @("F", "R", "P")) { throw "Strategy must be F, R, or P: $strategy" }
        $index = Join-Path $projectRoot "data\chunking_experiments\$normalized"
        if (-not (Test-Path -LiteralPath $index)) {
            throw "Missing experiment index: $index. Run Prepare-ChunkingExperiment.ps1 first."
        }
        $env:CHUNKING_STRATEGY = $normalized
        $env:CHROMA_DIR = $index
        $env:COLLECTION_NAME = "design_knowledge_$($normalized.ToLowerInvariant())"
        if ($SourceCap -ge 0) { $env:RETRIEVER_SOURCE_CAP = [string]$SourceCap }
        & $python (Join-Path $projectRoot "eval_chunking_langsmith.py") --strategy $normalized --experiment-prefix "$ExperimentPrefix-$normalized"
        if ($LASTEXITCODE -ne 0) { throw "LangSmith evaluation failed for $normalized with exit code $LASTEXITCODE" }
    }
}
finally {
    if ($null -eq $oldStrategy) { Remove-Item Env:CHUNKING_STRATEGY -ErrorAction SilentlyContinue } else { $env:CHUNKING_STRATEGY = $oldStrategy }
    if ($null -eq $oldChroma) { Remove-Item Env:CHROMA_DIR -ErrorAction SilentlyContinue } else { $env:CHROMA_DIR = $oldChroma }
    if ($null -eq $oldCollection) { Remove-Item Env:COLLECTION_NAME -ErrorAction SilentlyContinue } else { $env:COLLECTION_NAME = $oldCollection }
    if ($null -eq $oldPythonUtf8) { Remove-Item Env:PYTHONUTF8 -ErrorAction SilentlyContinue } else { $env:PYTHONUTF8 = $oldPythonUtf8 }
    if ($null -eq $oldSourceCap) { Remove-Item Env:RETRIEVER_SOURCE_CAP -ErrorAction SilentlyContinue } else { $env:RETRIEVER_SOURCE_CAP = $oldSourceCap }
}
