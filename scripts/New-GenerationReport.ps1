param(
    # 回放结果文件（可多个，代表同一配置的多次重复）。必须同模型、同提示词版本、
    # 同判定口径，否则聚合会直接拒绝——不同口径的数字平均在一起没有意义。
    [Parameter(Mandatory = $true)]
    [string[]]$RunPath,

    # 聚合结果（JSON）与报告（Markdown）的输出位置。
    [string]$OutputPath = ".tmp\generation-report.json",
    [string]$ReportPath = ".tmp\generation-report.md",

    # 作为对照的基线聚合结果。省略时报告只做切片统计，不做分组闸门。
    [string]$BaselinePath = "",

    # 本次改动声明会影响的指标组，逗号分隔（如 quality,citation）。
    # 未声明的组只要有任何变动即判定为回归——包括「improvement」。
    [string]$DeclaredChanges = "",

    [string]$SlicesPath = "data\generation_eval_slices.v1.json",
    [string]$EvaluationPath = "data\generation_eval.v2.json",
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
        if (-not $PythonPath) { $PythonPath = (Get-Command python -ErrorAction Stop).Source }
    }
}

# Resolve against the project root, leaving rooted paths alone. GetFullPath is
# used rather than Join-Path because Join-Path rejects an already-rooted child.
function Resolve-ProjectPath {
    param([string]$Value)
    if ([System.IO.Path]::IsPathRooted($Value)) {
        return [System.IO.Path]::GetFullPath($Value)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $root $Value))
}

$arguments = @(
    (Join-Path $root "aggregate_generation_replays.py")
)
foreach ($run in $RunPath) {
    $full = Resolve-ProjectPath $run
    if (-not (Test-Path -LiteralPath $full)) {
        throw "回放结果不存在：$full"
    }
    $arguments += $full
}
$arguments += @("--output", (Resolve-ProjectPath $OutputPath))
$arguments += @("--report", (Resolve-ProjectPath $ReportPath))
$arguments += @("--slices", (Resolve-ProjectPath $SlicesPath))
$arguments += @("--evaluation", (Resolve-ProjectPath $EvaluationPath))
if ($BaselinePath) { $arguments += @("--baseline", (Resolve-ProjectPath $BaselinePath)) }
if ($DeclaredChanges) { $arguments += @("--declared-changes", $DeclaredChanges) }

& $PythonPath @arguments
if ($LASTEXITCODE -ne 0) { throw "聚合生成报告失败，退出码：$LASTEXITCODE" }

Write-Host ""
Write-Host "报告已生成：$(Resolve-ProjectPath $ReportPath)"
