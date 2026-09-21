param(
    [string]$PythonPath = '',
    [string]$Dataset = 'data\chunking_evaluation\chunking_qa_10.json',
    [string]$OutputRoot = 'logs\portfolio_baseline',
    [int]$TopK = 10,
    [int]$SourceCap = 2,
    [switch]$NoLangSmithUpload
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = if ($PythonPath) {
    $PythonPath
} elseif (Test-Path -LiteralPath (Join-Path $projectRoot '.venv\Scripts\python.exe')) {
    Join-Path $projectRoot '.venv\Scripts\python.exe'
} else {
    'python'
}
$datasetPath = if ([System.IO.Path]::IsPathRooted($Dataset)) { $Dataset } else { Join-Path $projectRoot $Dataset }
$outputDir = if ([System.IO.Path]::IsPathRooted($OutputRoot)) { $OutputRoot } else { Join-Path $projectRoot $OutputRoot }
if (-not (Test-Path -LiteralPath $datasetPath)) { throw "评测集不存在：$datasetPath" }
$formalChroma = Join-Path $projectRoot 'data\chroma_db\chroma.sqlite3'
if (-not (Test-Path -LiteralPath $formalChroma)) { throw "正式 Chroma 索引不存在：$formalChroma，请先完成入库。" }
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null

$oldStrategy = $env:CHUNKING_STRATEGY
$oldChroma = $env:CHROMA_DIR
$oldCollection = $env:COLLECTION_NAME
$oldSourceCap = $env:RETRIEVER_SOURCE_CAP
$oldPythonUtf8 = $env:PYTHONUTF8
$stamp = (Get-Date).ToString('yyyyMMdd_HHmmss')
$jsonPath = Join-Path $outputDir "R_retrieval_$stamp.json"
$markdownPath = Join-Path $outputDir "R_baseline_$stamp.md"

try {
    $env:PYTHONUTF8 = '1'
    $env:CHUNKING_STRATEGY = 'R'
    $env:CHROMA_DIR = Join-Path $projectRoot 'data\chroma_db'
    $env:COLLECTION_NAME = 'design_knowledge'
    $env:RETRIEVER_SOURCE_CAP = [string]$SourceCap
    $arguments = @(
        (Join-Path $projectRoot 'eval_chunking_retrieval.py'),
        '--dataset', $datasetPath,
        '--top-k', [string]$TopK,
        '--output', $jsonPath
    )
    Write-Host "运行 R 策略10题基线：$python"
    & $python @arguments
    if ($LASTEXITCODE -ne 0) { throw "R 策略评测失败，退出码：$LASTEXITCODE" }
    $result = Get-Content -LiteralPath $jsonPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $aggregate = $result.aggregate
    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add('# R 策略 10 题作品集基线')
    $lines.Add('')
    $lines.Add("运行时间：$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')")
    $lines.Add('评测口径：目标来源召回，不等同于最终答案正确率。')
    $lines.Add("配置：R 分块、top-k=$TopK、source cap=$SourceCap、正式 design_knowledge collection。")
    $lines.Add('')
    $lines.Add('| 指标 | 结果 |')
    $lines.Add('|---|---:|')
    $lines.Add("| 题目数量 | $($aggregate.question_count) |")
    $lines.Add("| Source hit mean | $($aggregate.source_hit_mean) |")
    $lines.Add("| Recall@5 | $($aggregate.recall_at_5_mean) |")
    $lines.Add("| Recall@10 | $($aggregate.recall_at_10_mean) |")
    $lines.Add("| MRR | $($aggregate.mrr_mean) |")
    $lines.Add("| NDCG@10 | $($aggregate.ndcg_at_10_mean) |")
    $lines.Add("| 重排成功率 | $($aggregate.reranker_success_rate) |")
    $lines.Add("| 平均延迟（秒） | $($aggregate.latency_mean_seconds) |")
    $lines.Add('')
    $lines.Add('| 题号 | 类别 | Source hit | Recall@10 | MRR | 首个命中排名 | 缺失来源 |')
    $lines.Add('|---|---|---:|---:|---:|---:|---|')
    $index = 1
    foreach ($row in $result.results) {
        $missing = if ($row.missing_sources) { ($row.missing_sources -join ', ') } else { '-' }
        $missing = $missing -replace '\|', '\\|'
        $questionId = 'k' + $index.ToString('00')
        $lines.Add("| $questionId | $($row.category) | $($row.source_hit) | $($row.retrieval_metrics.recall_at_10) | $($row.retrieval_metrics.mrr) | $($row.first_hit_rank) | $missing |")
        $index++
    }
    $lines.Add('')
    $lines.Add(('原始完整 JSON：`' + $jsonPath + '`'))
    $lines.Add('')
    $lines.Add('已知边界：k07 缺少一个 Tritan 工艺指南来源；k10 的首个目标来源排名偏后。')
    $lines | Set-Content -LiteralPath $markdownPath -Encoding UTF8
    Write-Host "原始结果已写入：$jsonPath"
    Write-Host "Markdown 摘要已写入：$markdownPath"
    if ($NoLangSmithUpload) { Write-Host '已按参数跳过 LangSmith 上传（本脚本默认不上传）。' }
}
finally {
    if ($null -eq $oldStrategy) { Remove-Item Env:CHUNKING_STRATEGY -ErrorAction SilentlyContinue } else { $env:CHUNKING_STRATEGY = $oldStrategy }
    if ($null -eq $oldChroma) { Remove-Item Env:CHROMA_DIR -ErrorAction SilentlyContinue } else { $env:CHROMA_DIR = $oldChroma }
    if ($null -eq $oldCollection) { Remove-Item Env:COLLECTION_NAME -ErrorAction SilentlyContinue } else { $env:COLLECTION_NAME = $oldCollection }
    if ($null -eq $oldSourceCap) { Remove-Item Env:RETRIEVER_SOURCE_CAP -ErrorAction SilentlyContinue } else { $env:RETRIEVER_SOURCE_CAP = $oldSourceCap }
    if ($null -eq $oldPythonUtf8) { Remove-Item Env:PYTHONUTF8 -ErrorAction SilentlyContinue } else { $env:PYTHONUTF8 = $oldPythonUtf8 }
}
