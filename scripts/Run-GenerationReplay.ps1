param(
    [string]$PythonPath = "",
    [string]$SnapshotPath = "data\frozen_retrieval_cases.jsonl",
    # 评测输入（人工标注的 ground truth）。此路径只读，绝不能被输出覆盖。
    # 必须是携带完整行为契约的 v2 集：required_terms / refusal_requirements /
    # ambiguity_requirements 三者缺失时，相关指标会静默变成 None。
    [string]$EvaluationPath = "data\generation_eval.v2.json",
    # 结果输出。默认写入独立文件，禁止指向 EvaluationPath。
    [string]$OutputPath = "",
    [int]$MaxEvidence = 5,
    [string[]]$QuestionId = @(),
    # 重复序号。同一配置的多次独立回放必须用不同序号，聚合会拒绝重复——
    # 两轮同号在统计上不是两次独立观测。
    [int]$RepetitionIndex = 1,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\")).Path
if (-not $PythonPath) {
    $PythonPath = (Get-Command py -ErrorAction SilentlyContinue).Source
    if (-not $PythonPath) { $PythonPath = (Get-Command python -ErrorAction Stop).Source }
}

$evaluationFull = [System.IO.Path]::GetFullPath((Join-Path $root $EvaluationPath))
$outputFull = if ($OutputPath) { [System.IO.Path]::GetFullPath((Join-Path $root $OutputPath)) } else { $null }

# 防覆盖闸门：评测输入是人工标注的语义基准，一旦被结果覆盖且无备份，
# 整轮生成评测将失去 ground truth。这里硬性拒绝同路径。
if ($outputFull -and $evaluationFull -eq $outputFull) {
    throw "输出路径与评测输入路径相同（$outputFull）。这会把人工标注的评测基准覆盖为结果文件。请为 -OutputPath 指定不同路径。"
}
if (-not (Test-Path -LiteralPath $evaluationFull)) {
    throw "评测输入不存在：$evaluationFull"
}

# 结果文件绝不写入密钥等敏感字段。
$arguments = @(
    (Join-Path $root "eval_generation_replay.py"),
    "--snapshot", (Join-Path $root $SnapshotPath),
    "--evaluation", $evaluationFull,
    "--max-evidence", $MaxEvidence
)
if ($outputFull) { $arguments += @("--output", $outputFull) }
if ($DryRun) { $arguments += "--dry-run" }
$arguments += @("--repetition-index", $RepetitionIndex)
foreach ($qid in $QuestionId) { $arguments += @("--question-id", $qid) }
& $PythonPath @arguments
if ($LASTEXITCODE -ne 0) { throw "生成回放失败，退出码：$LASTEXITCODE" }
