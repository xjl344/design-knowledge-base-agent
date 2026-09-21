param(
    [string[]]$Strategies = @('R', 'P', 'F'),
    [int]$QuestionCount = 10,
    [string]$OutputRoot = 'logs\chunking_experiments',
    [int]$SourceCap = -1,
    [string]$LangSmithDataset = 'design-knowledge-chunking-10',
    [string]$LangSmithExperimentPrefix = 'design-kb-auto',
    [switch]$NoLangSmithUpload
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { $python = 'python' }
$output = if ([System.IO.Path]::IsPathRooted($OutputRoot)) { $OutputRoot } else { Join-Path $projectRoot $OutputRoot }
New-Item -ItemType Directory -Force -Path $output | Out-Null
$oldStrategy = $env:CHUNKING_STRATEGY
$oldChroma = $env:CHROMA_DIR
$oldCollection = $env:COLLECTION_NAME
$oldSourceCap = $env:RETRIEVER_SOURCE_CAP
$oldPythonUtf8 = $env:PYTHONUTF8
$runStamp = (Get-Date).ToString('yyyyMMdd-HHmmss')
$summary = @()

if ($QuestionCount -ne 10) {
    throw "The dedicated chunking benchmark is fixed at 10 questions; QuestionCount must remain 10."
}

try {
    $env:PYTHONUTF8 = '1'
    foreach ($strategy in $Strategies) {
        $normalized = $strategy.ToUpperInvariant()
        if ($normalized -notin @('F', 'R', 'P')) { throw "Strategy must be F, R, or P: $strategy" }
        $experimentDir = Join-Path $projectRoot "data\chunking_experiments\$normalized"
        $collection = "design_knowledge_$($normalized.ToLowerInvariant())"
        if (-not (Test-Path -LiteralPath $experimentDir)) {
            throw "Experiment index does not exist: $experimentDir. Run Prepare-ChunkingExperiment.ps1 first."
        }
        $env:CHUNKING_STRATEGY = $normalized
        $env:CHROMA_DIR = $experimentDir
        $env:COLLECTION_NAME = $collection
        if ($SourceCap -ge 0) { $env:RETRIEVER_SOURCE_CAP = [string]$SourceCap }
        $resultPath = Join-Path $output "${normalized}_retrieval_$((Get-Date).ToString('yyyyMMdd_HHmmss')).json"
        & $python (Join-Path $projectRoot 'eval_chunking_retrieval.py') --dataset (Join-Path $projectRoot "data\chunking_evaluation\chunking_qa_10.json") --top-k 10 --output $resultPath
        if ($LASTEXITCODE -ne 0) { throw "Evaluation failed for $normalized with exit code $LASTEXITCODE" }
        $result = Get-Content -LiteralPath $resultPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $summary += [pscustomobject]@{
            strategy = $result.strategy
            question_count = $result.aggregate.question_count
            source_hit_mean = $result.aggregate.source_hit_mean
            recall_at_5_mean = $result.aggregate.recall_at_5_mean
            recall_at_10_mean = $result.aggregate.recall_at_10_mean
            mrr_mean = $result.aggregate.mrr_mean
            ndcg_at_10_mean = $result.aggregate.ndcg_at_10_mean
            latency_mean_seconds = $result.aggregate.latency_mean_seconds
            reranker_success_rate = $result.aggregate.reranker_success_rate
            reranker_latency_mean_seconds = $result.aggregate.reranker_latency_mean_seconds
            bm25_only_union_count = $result.aggregate.bm25_only_union_count
            bm25_only_rerank_top20_count = $result.aggregate.bm25_only_rerank_top20_count
            bm25_only_final10_count = $result.aggregate.bm25_only_final10_count
            source_cap = $result.results[0].source_cap
            result_file = $resultPath
        }
        Write-Host "Retrieval comparison written: $resultPath"
        if (-not $NoLangSmithUpload) {
            $experimentPrefix = "$LangSmithExperimentPrefix-$runStamp"
            $uploadArgs = @(
                (Join-Path $projectRoot 'upload_chunking_history.py'),
                '--files', $resultPath,
                '--dataset', $LangSmithDataset,
                '--experiment-prefix', $experimentPrefix
            )
            & $python @uploadArgs
            if ($LASTEXITCODE -ne 0) {
                throw "LangSmith upload failed for $normalized with exit code $LASTEXITCODE"
            }
            Write-Host "LangSmith upload completed: $experimentPrefix-$normalized"
        }
    }
    $summaryPath = Join-Path $output "summary_$((Get-Date).ToString('yyyyMMdd_HHmmss')).json"
    $summary | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $summaryPath -Encoding UTF8
    Write-Host "Comparison summary written: $summaryPath"
}
finally {
    if ($null -eq $oldStrategy) { Remove-Item Env:CHUNKING_STRATEGY -ErrorAction SilentlyContinue } else { $env:CHUNKING_STRATEGY = $oldStrategy }
    if ($null -eq $oldChroma) { Remove-Item Env:CHROMA_DIR -ErrorAction SilentlyContinue } else { $env:CHROMA_DIR = $oldChroma }
    if ($null -eq $oldCollection) { Remove-Item Env:COLLECTION_NAME -ErrorAction SilentlyContinue } else { $env:COLLECTION_NAME = $oldCollection }
    if ($null -eq $oldSourceCap) { Remove-Item Env:RETRIEVER_SOURCE_CAP -ErrorAction SilentlyContinue } else { $env:RETRIEVER_SOURCE_CAP = $oldSourceCap }
    if ($null -eq $oldPythonUtf8) { Remove-Item Env:PYTHONUTF8 -ErrorAction SilentlyContinue } else { $env:PYTHONUTF8 = $oldPythonUtf8 }
}
