[CmdletBinding()]
param(
    [int]$Repetitions = 3,
    [string]$SnapshotPath = "data\frozen_retrieval_cases.jsonl",
    [string]$EvaluationPath = "data\generation_eval.v2.json"
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = 'E:\venvs\design-kb-round2\Scripts\python.exe'
$replay = Join-Path $root 'eval_generation_replay.py'
$aggregate = Join-Path $root 'aggregate_generation_replays.py'
if (-not (Test-Path -LiteralPath $python)) { throw "找不到指定评测环境：$python" }

$stamp = Get-Date -Format 'yyyyMMddTHHmmss'
$matrixId = "p0_$stamp"
$results = @()
for ($index = 1; $index -le $Repetitions; $index++) {
    $output = Join-Path $root ("data\runs\{0}_r{1}.json" -f $matrixId, $index)
    Write-Host "开始 P0 repetition $index/$Repetitions（固定快照、生成模型由 LLM_MODEL 指定、max_retries=0）"
    & $python $replay --snapshot (Join-Path $root $SnapshotPath) --evaluation (Join-Path $root $EvaluationPath) --output $output --repetition-index $index
    if ($LASTEXITCODE -ne 0) { throw "P0 repetition $index 失败，exit code=$LASTEXITCODE" }
    $results += $output
}
$summary = Join-Path $root ("data\runs\{0}_summary.json" -f $matrixId)
& $python $aggregate @results --output $summary
if ($LASTEXITCODE -ne 0) { throw "P0 聚合失败，exit code=$LASTEXITCODE" }
Write-Host "P0 $Repetitions 次独立回放完成。聚合报告：$summary"
