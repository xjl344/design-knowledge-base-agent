param(
    [string[]]$Strategies = @('F', 'R', 'P'),
    [int]$QuestionCount = 10,
    [string]$OutputRoot = 'logs\\retrieval_ablations',
    [switch]$NoLangSmithUpload
)

$ErrorActionPreference = 'Stop'
if ($QuestionCount -ne 10) { throw 'The dedicated benchmark is fixed at 10 questions.' }
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot '.venv\\Scripts\\python.exe'
if (-not (Test-Path -LiteralPath $python)) { $python = 'python' }
$root = if ([IO.Path]::IsPathRooted($OutputRoot)) { $OutputRoot } else { Join-Path $projectRoot $OutputRoot }
New-Item -ItemType Directory -Force -Path $root | Out-Null
$stamp = (Get-Date).ToString('yyyyMMdd_HHmmss')
$modes = @(
    @{ Name='baseline'; Bm25Mode='original_only'; SourceRole='false'; Separate='false'; Independent='false' },
    @{ Name='source_role'; Bm25Mode='original_only'; SourceRole='true'; Separate='false'; Independent='false' },
    @{ Name='source_role_entities'; Bm25Mode='conditional'; SourceRole='true'; Separate='true'; Independent='false' },
    @{ Name='full_independent_bm25'; Bm25Mode='independent_all'; SourceRole='true'; Separate='true'; Independent='true' }
)
$old = @{}
foreach ($name in @('RETRIEVER_BM25_MODE','RETRIEVER_SOURCE_ROLE_PRIORITY','RETRIEVER_SEPARATE_ENTITIES','RETRIEVER_INDEPENDENT_BM25_QUERIES','PYTHONUTF8','CHUNKING_STRATEGY','CHROMA_DIR','COLLECTION_NAME')) { $old[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
try {
    $env:PYTHONUTF8 = '1'
    foreach ($mode in $modes) {
        $modeDir = Join-Path $root ($mode.Name + '_' + $stamp)
        New-Item -ItemType Directory -Force -Path $modeDir | Out-Null
        $env:RETRIEVER_BM25_MODE = $mode.Bm25Mode
        $env:RETRIEVER_SOURCE_ROLE_PRIORITY = $mode.SourceRole
        $env:RETRIEVER_SEPARATE_ENTITIES = $mode.Separate
        $env:RETRIEVER_INDEPENDENT_BM25_QUERIES = $mode.Independent
        foreach ($strategy in $Strategies) {
            $normalized = $strategy.ToUpperInvariant()
            if ($normalized -notin @('F','R','P')) { throw ('Strategy must be F, R, or P: ' + $strategy) }
            $env:CHUNKING_STRATEGY = $normalized
            $env:CHROMA_DIR = Join-Path $projectRoot ('data\\chunking_experiments\\' + $normalized)
            $env:COLLECTION_NAME = 'design_knowledge_' + $normalized.ToLowerInvariant()
            $resultPath = Join-Path $modeDir ($normalized + '_retrieval.json')
            & $python (Join-Path $projectRoot 'eval_chunking_retrieval.py') --dataset (Join-Path $projectRoot 'data\\chunking_evaluation\\chunking_qa_10.json') --top-k 10 --output $resultPath
            if ($LASTEXITCODE -ne 0) { throw ('Ablation failed: ' + $mode.Name + '/' + $normalized) }
            if (-not $NoLangSmithUpload) {
                & $python (Join-Path $projectRoot 'upload_chunking_history.py') --files $resultPath --dataset 'design-knowledge-chunking-10' --experiment-prefix ('retrieval-ablation-' + $mode.Name + '-' + $stamp)
                if ($LASTEXITCODE -ne 0) { throw ('LangSmith upload failed: ' + $mode.Name + '/' + $normalized) }
            }
        }
    }
} finally {
    foreach ($name in $old.Keys) {
        $value = $old[$name]
        if ($null -eq $value) { Remove-Item ('Env:' + $name) -ErrorAction SilentlyContinue } else { [Environment]::SetEnvironmentVariable($name, $value, 'Process') }
    }
}
Write-Host ('Ablation results written to: ' + $root)
