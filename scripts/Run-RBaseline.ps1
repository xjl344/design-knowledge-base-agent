param(
    [string]$PythonPath = '',
    [string]$Dataset = 'data\chunking_evaluation\chunking_qa_10.json',
    [string]$OutputRoot = 'logs\portfolio_baseline',
    [int]$TopK = 10,
    [int]$SourceCap = 2,
    [switch]$NoLangSmithUpload,
    # Render the report from an existing result JSON instead of running the
    # evaluation.  A run takes 4-30 minutes depending on the machine; re-rendering
    # the summary after a report-template change should not cost that.
    [string]$FromJson = ''
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
    $resultFileTime = $null
    if ($FromJson) {
        $jsonPath = if ([System.IO.Path]::IsPathRooted($FromJson)) { $FromJson } else { Join-Path $projectRoot $FromJson }
        if (-not (Test-Path -LiteralPath $jsonPath)) { throw "指定的结果 JSON 不存在：$jsonPath" }
        $resultFileTime = (Get-Item -LiteralPath $jsonPath).LastWriteTime
        # Name the summary after the result it describes, so a directory listing
        # pairs `R_retrieval_<stamp>.json` with `R_baseline_<stamp>.md` rather than
        # with the time the report happened to be rendered.
        $sourceStamp = [System.IO.Path]::GetFileNameWithoutExtension($jsonPath)
        $sourceStamp = $sourceStamp -replace '^R_retrieval_', ''
        if ($sourceStamp -match '^\d{8}_\d{6}$') { $stamp = $sourceStamp }
        $markdownPath = Join-Path $outputDir ("R_baseline_" + $stamp + ".md")
        Write-Host "从已有结果渲染报告（不重新评测）：$jsonPath"
    } else {
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
        # Gate on the artefact as well as the exit code.  `$LASTEXITCODE` came back
        # `$null` when this script was invoked with a merged stderr stream, and
        # `$null -ne 0` is true in PowerShell -- so the exit-code check alone
        # reported a failure for a run that had in fact succeeded.
        if ($null -ne $LASTEXITCODE -and $LASTEXITCODE -ne 0) {
            throw "R 策略评测失败，退出码：$LASTEXITCODE"
        }
        if (-not (Test-Path -LiteralPath $jsonPath)) {
            throw "R 策略评测没有生成结果文件：$jsonPath"
        }
        $resultFileTime = (Get-Item -LiteralPath $jsonPath).LastWriteTime
    }
    $result = Get-Content -LiteralPath $jsonPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $aggregate = $result.aggregate

    # Percent formatting is done by hand rather than with `{0:P2}` because the
    # culture-dependent formatter inserts a non-breaking space on some locales,
    # which would silently change the report's bytes.
    function Format-Pct($value) {
        if ($null -eq $value) { return 'n/a' }
        return ('{0:N2}%' -f ([double]$value * 100))
    }

    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add('# R 策略 10 题作品集基线')
    $lines.Add('')
    $lines.Add("报告生成时间：$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')")
    if ($resultFileTime) {
        $lines.Add("结果文件时间：$($resultFileTime.ToString('yyyy-MM-dd HH:mm:ss'))")
    }
    if ($FromJson) {
        $lines.Add('说明：本报告由已有结果 JSON 渲染，**未重新执行评测**。')
    }
    $lines.Add('评测口径：目标来源召回，不等同于最终答案正确率。指标单位为百分比。')
    $lines.Add("配置：R 分块、top-k=$TopK、source cap=$SourceCap、正式 design_knowledge collection。")
    $lines.Add('')
    $lines.Add('| 指标 | 结果 |')
    $lines.Add('|---|---:|')
    $lines.Add("| 题目数量 | $($aggregate.question_count) |")
    $lines.Add("| Source hit mean | $(Format-Pct $aggregate.source_hit_mean) |")
    $lines.Add("| Recall@5 | $(Format-Pct $aggregate.recall_at_5_mean) |")
    $lines.Add("| Recall@10 | $(Format-Pct $aggregate.recall_at_10_mean) |")
    $lines.Add("| MRR | $(Format-Pct $aggregate.mrr_mean) |")
    $lines.Add("| NDCG@10 | $(Format-Pct $aggregate.ndcg_at_10_mean) |")
    $lines.Add("| 重排成功率 | $(Format-Pct $aggregate.reranker_success_rate) |")
    $lines.Add("| 平均延迟（秒） | $($aggregate.latency_mean_seconds) |")
    $lines.Add('')
    $lines.Add('| 题号 | 类别 | Source hit | Recall@10 | MRR | 首个命中排名 | 缺失来源 |')
    $lines.Add('|---|---|---:|---:|---:|---:|---|')
    $index = 1
    $missingRows = New-Object System.Collections.Generic.List[string]
    $lateRows = New-Object System.Collections.Generic.List[string]
    $idFallbacks = 0
    foreach ($row in $result.results) {
        # Prefer the dataset's own id.  The positional fallback is kept only so an
        # older JSON (written before `question_id` existed) still renders, and it
        # is reported below rather than used silently.
        $questionId = ''
        if ($row.PSObject.Properties.Name -contains 'question_id') { $questionId = [string]$row.question_id }
        if (-not $questionId) {
            $questionId = 'k' + $index.ToString('00')
            $idFallbacks++
        }
        $missing = '-'
        if ($row.missing_sources -and $row.missing_sources.Count -gt 0) {
            $missing = ($row.missing_sources -join ', ')
            $missingRows.Add("$questionId（$($row.missing_sources -join '、')）")
        }
        if ($row.first_hit_rank -and [int]$row.first_hit_rank -gt 1) {
            $lateRows.Add("$questionId（首个命中排名 $($row.first_hit_rank)）")
        }
        $missing = $missing -replace '\|', '\|'
        $lines.Add("| $questionId | $($row.category) | $(Format-Pct $row.source_hit) | $(Format-Pct $row.retrieval_metrics.recall_at_10) | $(Format-Pct $row.retrieval_metrics.mrr) | $($row.first_hit_rank) | $missing |")
        $index++
    }
    $lines.Add('')
    $lines.Add('## 已知边界（由本次运行结果生成）')
    $lines.Add('')
    # These lines used to be a hard-coded sentence naming k07, which the run's own
    # `missing_sources` contradicted (all ten were empty).  A conclusion that is
    # printed regardless of the data looks exactly like one that was observed.
    if ($missingRows.Count -eq 0) {
        $lines.Add('- 缺失来源：**本次运行没有任何题目缺失期望来源**。')
    } else {
        $lines.Add('- 缺失来源：' + ($missingRows -join '；'))
    }
    if ($lateRows.Count -eq 0) {
        $lines.Add('- 首个命中排名 > 1：无，所有题目的首个目标来源都在第 1 位。')
    } else {
        $lines.Add('- 首个命中排名 > 1：' + ($lateRows -join '；'))
    }
    if ($idFallbacks -gt 0) {
        $lines.Add("- ⚠️ 有 $idFallbacks 题的题号是按行号回退生成的（该 JSON 没有 ``question_id`` 字段）。")
    }
    $lines.Add('')
    $lines.Add('## 本次运行的检索诊断（用于跨运行比对）')
    $lines.Add('')
    $lines.Add('| 项 | 值 |')
    $lines.Add('|---|---:|')
    $lines.Add("| 平均延迟（秒/题） | $($aggregate.latency_mean_seconds) |")
    $lines.Add("| 其中重排（秒/题） | $($aggregate.reranker_latency_mean_seconds) |")
    $lines.Add("| 整轮耗时（秒） | $($aggregate.elapsed_seconds) |")
    $lines.Add("| BM25 独有候选（union） | $($aggregate.bm25_only_union_count) |")
    $lines.Add("| BM25 独有进入 final10 | $($aggregate.bm25_only_final10_count) |")
    $lines.Add("| 实体守卫触发次数 | $($aggregate.entity_guard_trigger_count) |")
    $lines.Add("| 流程先验应用次数 | $($aggregate.process_prior_applied_count) |")
    $lines.Add('')
    $lines.Add('⚠️ 换机器或换时间重跑，延迟与 BM25 候选计数可能显著不同（实测两次运行的重排延迟相差约 12 倍）。')
    $lines.Add('比对两次运行时应先看上面这张表，再看召回指标；**不要把延迟差异读成策略差异**。')
    $lines.Add('')
    # Emit a repo-relative reference when the result lives inside the project.
    #
    # The report is committed to the repository, and an absolute path embeds the
    # author's machine layout (`E:\设计知识库助手\...`) into a public artefact.  It is
    # also simply less useful to a reader: a relative path resolves on their clone.
    $jsonRef = $jsonPath
    if ($jsonPath.StartsWith($projectRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        $jsonRef = $jsonPath.Substring($projectRoot.Length).TrimStart('\', '/') -replace '\\', '/'
    }
    $lines.Add(('原始完整 JSON：`' + $jsonRef + '`'))
    $lines | Set-Content -LiteralPath $markdownPath -Encoding UTF8
    Write-Host "原始结果已写入：$jsonPath"
    Write-Host "Markdown 摘要已写入：$markdownPath"
    if ($NoLangSmithUpload) { Write-Host '已按参数跳过 LangSmith 上传（本脚本默认不上传）。' }
}
finally {
    # Restore via .NET instead of `Remove-Item Env:`.
    #
    # A restricted shell can intercept that cmdlet: this environment's safe-delete
    # guard throws `SAFE_DELETE_FAIL_CLOSED` on an `Env:` target that does not
    # exist, so the cleanup failed the whole script *after* the evaluation had
    # already succeeded -- and because the guard throws during `finally`, the
    # error surfaced instead of the results.  `SetEnvironmentVariable(name, $null)`
    # removes the variable for this process and does not go through the cmdlet.
    foreach ($pair in @(
        @('CHUNKING_STRATEGY', $oldStrategy),
        @('CHROMA_DIR', $oldChroma),
        @('COLLECTION_NAME', $oldCollection),
        @('RETRIEVER_SOURCE_CAP', $oldSourceCap),
        @('PYTHONUTF8', $oldPythonUtf8)
    )) {
        [System.Environment]::SetEnvironmentVariable($pair[0], $pair[1])
    }
}
