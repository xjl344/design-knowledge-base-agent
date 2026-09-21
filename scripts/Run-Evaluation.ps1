param(
    [switch]$WithWeb,
    [switch]$CheckOnly,
    [switch]$DecisionQuality,
    [switch]$ComplexQuality,
    [switch]$DownloadComplex,
    [string]$Experiment,
    [switch]$NoJudge,
    [string]$DatasetName,
    [string]$ExperimentPrefix,
    [int]$Limit,
    [int]$Start = 1
)

$ErrorActionPreference = "Stop"
$python = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "未找到项目虚拟环境：$python"
}

if ($DownloadComplex) {
    $arguments = @((Join-Path $PSScriptRoot "..\download_langsmith_experiment.py"))
    if ($Experiment) { $arguments += @("--experiment", $Experiment) }
    & $python @arguments
    exit $LASTEXITCODE
}

& $python -m pytest
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if ($CheckOnly) {
    & $python (Join-Path $PSScriptRoot "..\eval_langsmith.py") --check
    exit $LASTEXITCODE
}

if ($DecisionQuality) {
    & $python (Join-Path $PSScriptRoot "..\eval_decision_quality.py")
    exit $LASTEXITCODE
}

if ($ComplexQuality) {
    $arguments = @((Join-Path $PSScriptRoot "..\eval_complex_qa.py"))
    if ($WithWeb) { $arguments += "--with-web" }
    if ($NoJudge) { $arguments += "--no-judge" }
    if ($DatasetName) { $arguments += @("--dataset-name", $DatasetName) }
    if ($ExperimentPrefix) { $arguments += @("--experiment-prefix", $ExperimentPrefix) }
    if ($Limit -gt 0) { $arguments += @("--limit", $Limit) }
    if ($Start -gt 1) { $arguments += @("--start", $Start) }
    & $python @arguments
    exit $LASTEXITCODE
}

$arguments = @((Join-Path $PSScriptRoot "..\eval_langsmith.py"))
if ($WithWeb) { $arguments += "--with-web" }
& $python @arguments
exit $LASTEXITCODE
