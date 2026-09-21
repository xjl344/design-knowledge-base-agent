# 一键运行 generation replay 评测（避免手写多行续行符出错）
#
# 用法：
#   .\run_generation_replay.ps1                 # dry-run，只做证据打包校验
#   .\run_generation_replay.ps1 -Full           # 真实调用模型生成
#   .\run_generation_replay.ps1 -QuestionId q01_hit   # 只跑单题
#
param(
    [switch]$Full,
    [string[]]$QuestionId = @(),
    [int]$MaxEvidence = 5
)

$ErrorActionPreference = 'Stop'

# 切到脚本所在目录，保证相对路径 data\... 可用
Set-Location -LiteralPath $PSScriptRoot

# 统一转发到受保护的新入口：使用 golden 输入，结果自动写入
# data\runs\generation_replay_<run_id>.json，永不覆盖已有结果。
$runner = Join-Path $PSScriptRoot 'scripts\Run-GenerationReplay.ps1'
$runnerArgs = @{
    SnapshotPath = 'data\frozen_retrieval_cases.jsonl'
    EvaluationPath = 'data\generation_eval.golden.json'
    MaxEvidence = $MaxEvidence
    QuestionId = $QuestionId
}
if ($Full) {
    Write-Host "[run] 真实生成模式（会调用模型，耗时较长）" -ForegroundColor Cyan
} else {
    $runnerArgs['DryRun'] = $true
    Write-Host "[run] dry-run 模式（只做证据打包，秒级完成）" -ForegroundColor Cyan
}
& $runner @runnerArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
