param(
    [switch]$WithWeb,
    [switch]$WithJudge,
    [string]$OutputDir,
    [int]$RetrieverTopK = 0,
    [int]$DenseTopK = 0,
    [int]$Bm25TopK = 0,
    [Nullable[bool]]$RerankerEnabled,
    [Nullable[bool]]$PlanningEnabled,
    [Nullable[bool]]$QueryDecomposeEnabled,
    [string]$LlmModel,
    [int]$Repeat = 1,
    [string]$RunLabel,
    [string]$PythonPath = "E:\venvs\design-kb-round2\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = $PythonPath
if (-not (Test-Path -LiteralPath $python)) {
    throw "未找到项目虚拟环境：$python"
}

$arguments = @(
    (Join-Path $projectRoot "eval_round2.py"),
    "--dataset", (Join-Path $projectRoot "data\test_qa_round2.json")
)
if ($WithWeb) { $arguments += "--with-web" }
if ($WithJudge) { $arguments += "--with-judge" }
if ($OutputDir) { $arguments += @("--output-dir", $OutputDir) }
if ($RetrieverTopK -gt 0) { $arguments += @("--retriever-top-k", $RetrieverTopK) }
if ($DenseTopK -gt 0) { $arguments += @("--dense-top-k", $DenseTopK) }
if ($Bm25TopK -gt 0) { $arguments += @("--bm25-top-k", $Bm25TopK) }
if ($null -ne $RerankerEnabled) { $arguments += @("--reranker-enabled", $RerankerEnabled.ToString().ToLowerInvariant()) }
if ($null -ne $PlanningEnabled) { $arguments += @("--planning-enabled", $PlanningEnabled.ToString().ToLowerInvariant()) }
if ($null -ne $QueryDecomposeEnabled) { $arguments += @("--query-decompose-enabled", $QueryDecomposeEnabled.ToString().ToLowerInvariant()) }
if ($LlmModel) { $arguments += @("--llm-model", $LlmModel) }
if ($Repeat -gt 1) { $arguments += @("--repeat", $Repeat) }
if ($RunLabel) { $arguments += @("--run-label", $RunLabel) }
& $python @arguments
exit $LASTEXITCODE
