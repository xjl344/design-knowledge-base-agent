param(
    [string]$PythonPath = "",
    [string]$OutputPath = "data\frozen_retrieval_cases.jsonl",
    [int]$TopK = 0
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\")).Path
if (-not $PythonPath) {
    $PythonPath = (Get-Command py -ErrorAction SilentlyContinue).Source
    if (-not $PythonPath) { $PythonPath = (Get-Command python -ErrorAction Stop).Source }
}
$output = Join-Path $root $OutputPath
& $PythonPath (Join-Path $root "scripts\create_frozen_snapshot.py") --output $output --top-k $TopK
if ($LASTEXITCODE -ne 0) { throw "冻结检索快照生成失败，退出码：$LASTEXITCODE" }
