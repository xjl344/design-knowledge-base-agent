[CmdletBinding()]
param(
    [string]$ResultPath = "data\runs\generation_replay_20260919T211836+0800_814cd403.json",
    [string]$EvaluationPath = "data\generation_eval.golden.json",
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$script = Join-Path $root 'upload_generation_replay_langsmith.py'
$result = (Resolve-Path (Join-Path $root $ResultPath)).Path
$evaluation = (Resolve-Path (Join-Path $root $EvaluationPath)).Path

if ($DryRun) {
    & $python $script --result $result --evaluation $evaluation --dry-run
} else {
    Write-Host "将创建全新的 LangSmith Dataset 和 Experiment，并上传 12 条离线生成结果。"
    & $python $script --result $result --evaluation $evaluation
}
if ($LASTEXITCODE -ne 0) { throw "LangSmith generation replay upload failed with exit code $LASTEXITCODE" }
