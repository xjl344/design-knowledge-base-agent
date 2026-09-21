[CmdletBinding()]
param(
    # 离线回放结果文件（必须是真实运行，dry_run=true 会被脚本拒绝）。
    [Parameter(Mandatory = $true)]
    [string]$ResultPath,

    # 与结果对应的契约文件。题号集合必须与结果完全一致，否则脚本直接报错。
    [string]$EvaluationPath = "data\generation_eval.multihop.v1.json",

    # 数据集名。省略时由契约的 family 与题数推导，因此同一题集的多次上传会
    # 复用同一个 dataset —— 这是 LangSmith 里两个 experiment 能并排对比的前提。
    [string]$DatasetName = "",

    # 实验名。省略时由模型名 + arm + 时间戳推导。
    [string]$ExperimentName = "",

    # 本次运行的对照臂标签（如 evidence5 / evidence-all）。写入实验名、元数据与 tag，
    # 用于在同一 dataset 下区分两次运行。
    [string]$Arm = "",

    [switch]$DryRun,

    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\")).Path
if (-not $PythonPath) {
    $candidate = Join-Path $root ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $candidate) {
        $PythonPath = $candidate
    } else {
        $PythonPath = (Get-Command py -ErrorAction SilentlyContinue).Source
    }
}

$script = Join-Path $root "upload_generation_replay_langsmith.py"
$result = (Resolve-Path (Join-Path $root $ResultPath)).Path
$evaluation = (Resolve-Path (Join-Path $root $EvaluationPath)).Path

$arguments = @($script, "--result", $result, "--evaluation", $evaluation)
if ($DatasetName) { $arguments += @("--dataset-name", $DatasetName) }
if ($ExperimentName) { $arguments += @("--experiment-name", $ExperimentName) }
if ($Arm) { $arguments += @("--arm", $Arm) }
if ($DryRun) { $arguments += "--dry-run" }

if (-not $DryRun) {
    Write-Host "将复用或创建 dataset，并新建一个绑定到它的 experiment；逐题上传答案与指标。"
}
& $PythonPath @arguments
if ($LASTEXITCODE -ne 0) { throw "LangSmith generation replay upload failed with exit code $LASTEXITCODE" }
