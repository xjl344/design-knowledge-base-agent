param(
    [string]$PythonPath = '',
    [string]$ReportRoot = 'logs'
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$reportDir = if ([System.IO.Path]::IsPathRooted($ReportRoot)) {
    $ReportRoot
} else {
    Join-Path $projectRoot $ReportRoot
}
New-Item -ItemType Directory -Force -Path $reportDir | Out-Null

$checks = New-Object System.Collections.Generic.List[object]
function Add-Check {
    param(
        [string]$Name,
        [ValidateSet('PASS', 'WARN', 'FAIL')][string]$Status,
        [string]$Details,
        [string]$Action = ''
    )
    $checks.Add([pscustomobject]@{
        Name = $Name
        Status = $Status
        Details = $Details
        Action = $Action
    })
}

function Read-DotEnvValue {
    param([string]$Name)
    $envFile = Join-Path $projectRoot '.env'
    if (-not (Test-Path -LiteralPath $envFile)) { return $null }
    foreach ($line in Get-Content -LiteralPath $envFile -ErrorAction SilentlyContinue) {
        if ($line -match "^\s*$([regex]::Escape($Name))\s*=\s*(.*?)\s*$") {
            return $Matches[1].Trim().Trim('"').Trim("'")
        }
    }
    return $null
}

function Resolve-ConfiguredPath {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return $null }
    if ([System.IO.Path]::IsPathRooted($Value)) { return $Value }
    return Join-Path $projectRoot $Value
}

$requiredFiles = @(
    'README.md', 'app.py', 'config.py', 'ingest.py',
    'eval_chunking_retrieval.py', 'data\chunking_evaluation\chunking_qa_10.json',
    'src\retriever.py', 'src\reranker.py', 'src\graph_builder.py',
    'scripts\Run-RBaseline.ps1'
)
foreach ($relativePath in $requiredFiles) {
    $fullPath = Join-Path $projectRoot $relativePath
    if (Test-Path -LiteralPath $fullPath) {
        Add-Check "文件 $relativePath" 'PASS' '文件存在'
    } else {
        Add-Check "文件 $relativePath" 'FAIL' '文件不存在' "恢复文件或检查项目路径：$fullPath"
    }
}

$gitignore = Join-Path $projectRoot '.gitignore'
$gitignoreText = if (Test-Path -LiteralPath $gitignore) { Get-Content $gitignore -Raw } else { '' }
if ($gitignoreText -match '(?m)^\.env\s*$') {
    Add-Check '密钥文件保护' 'PASS' '.env 已列入 .gitignore，检查结果不显示密钥内容'
} else {
    Add-Check '密钥文件保护' 'FAIL' '.env 未列入 .gitignore' '把 .env 加入 .gitignore，提交前不要上传真实密钥'
}

$configuredStrategy = Read-DotEnvValue 'CHUNKING_STRATEGY'
if ($configuredStrategy -and $configuredStrategy.ToUpperInvariant() -eq 'R') {
    Add-Check 'R 分块策略' 'PASS' 'CHUNKING_STRATEGY=R'
} elseif ($configuredStrategy) {
    Add-Check 'R 分块策略' 'WARN' "当前 .env 为 CHUNKING_STRATEGY=$configuredStrategy" '运行 R 基线时脚本会临时覆盖为 R，不会修改 .env'
} else {
    Add-Check 'R 分块策略' 'WARN' '未在 .env 中显式设置 CHUNKING_STRATEGY' '建议写入 CHUNKING_STRATEGY=R'
}

$chromaDb = Join-Path $projectRoot 'data\chroma_db\chroma.sqlite3'
$rExperimentDb = Join-Path $projectRoot 'data\chunking_experiments\R\chroma.sqlite3'
if (Test-Path -LiteralPath $chromaDb) {
    Add-Check '正式 Chroma 索引' 'PASS' 'data\chroma_db\chroma.sqlite3 存在'
} elseif (Test-Path -LiteralPath $rExperimentDb) {
    Add-Check '正式 Chroma 索引' 'WARN' '正式索引不存在，但 R 实验索引存在' '运行 ingest.py 建立正式 data\chroma_db，或仅使用实验索引进行对照'
} else {
    Add-Check '正式 Chroma 索引' 'FAIL' '正式索引和 R 实验索引都不存在' '先完成文档入库'
}

$embeddingPath = Resolve-ConfiguredPath (Read-DotEnvValue 'EMBEDDING_MODEL_PATH')
$rerankerPath = Resolve-ConfiguredPath (Read-DotEnvValue 'RERANKER_MODEL_PATH')
foreach ($model in @(
    @{ Name = 'BGE-M3 embedding'; Path = $embeddingPath },
    @{ Name = 'BGE reranker'; Path = $rerankerPath }
)) {
    if ($model.Path -and (Test-Path -LiteralPath $model.Path)) {
        Add-Check $model.Name 'PASS' "模型目录存在：$($model.Path)"
    } elseif ($model.Path) {
        Add-Check $model.Name 'FAIL' "模型目录不存在：$($model.Path)" '修正 .env 中的模型路径或准备模型文件'
    } else {
        Add-Check $model.Name 'WARN' '未配置模型路径' '在 .env 中配置 EMBEDDING_MODEL_PATH 或 RERANKER_MODEL_PATH'
    }
}

$pythonCandidates = New-Object System.Collections.Generic.List[string]
if ($PythonPath) {
    $pythonCandidates.Add($PythonPath)
} else {
    $pythonCandidates.Add((Join-Path $projectRoot '.venv\Scripts\python.exe'))
    $pythonCandidates.Add('python')
    $pythonCandidates.Add('py')
}
$workingPython = $null
foreach ($candidate in $pythonCandidates) {
    try {
        if ([System.IO.Path]::IsPathRooted($candidate) -and -not (Test-Path -LiteralPath $candidate)) { continue }
        $versionOutput = & $candidate --version 2>&1
        if ($LASTEXITCODE -eq 0) {
            $workingPython = $candidate
            Add-Check 'Python 可执行文件' 'PASS' "$candidate ($($versionOutput -join ' '))"
            break
        }
    } catch {
        continue
    }
}
if (-not $workingPython) {
    Add-Check 'Python 可执行文件' 'FAIL' '没有找到可启动的 Python；中文工作区中的 .venv 可能无法启动' '在 ASCII 路径重新创建虚拟环境，并通过 -PythonPath 显式传入 python.exe'
} else {
    try {
        & $workingPython -c "import langchain_core, langgraph, chromadb, gradio, pytest" 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Add-Check '核心依赖' 'PASS' 'langchain_core、langgraph、chromadb、gradio、pytest 均可导入'
        } else {
            Add-Check '核心依赖' 'FAIL' '核心依赖导入失败' '执行 python -m pip install -r requirements.txt'
        }
    } catch {
        Add-Check '核心依赖' 'FAIL' "依赖检查启动失败：$($_.Exception.Message)" '确认 Python 环境可执行'
    }
}

$failed = @($checks | Where-Object { $_.Status -eq 'FAIL' }).Count
$warnings = @($checks | Where-Object { $_.Status -eq 'WARN' }).Count
$stamp = (Get-Date).ToString('yyyyMMdd_HHmmss')
$reportPath = Join-Path $reportDir "portfolio_readiness_$stamp.md"
$lines = New-Object System.Collections.Generic.List[string]
$lines.Add('# 作品集 v1.0 可复现性检查结果')
$lines.Add('')
$lines.Add("检查时间：$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')")
$lines.Add("项目目录：$projectRoot")
$lines.Add('')
$lines.Add('| 检查项 | 状态 | 结果 | 下一步 |')
$lines.Add('|---|---|---|---|')
foreach ($check in $checks) {
    $details = ($check.Details -replace '\|', '\\|')
    $action = if ($check.Action) { ($check.Action -replace '\|', '\\|') } else { '' }
    $lines.Add("| $($check.Name) | $($check.Status) | $details | $action |")
}
$lines.Add('')
$lines.Add("汇总：$($checks.Count - $failed - $warnings) 项 PASS，$warnings 项 WARN，$failed 项 FAIL。")
$lines.Add('')
$lines.Add('说明：本报告不会记录 API key、模型权重内容或完整 .env。FAIL 必须处理；WARN 表示可运行但不满足完整作品集复现条件。')
$lines | Set-Content -LiteralPath $reportPath -Encoding UTF8

$checks | Format-Table Status, Name, Details -AutoSize
Write-Host "检查报告已写入：$reportPath"
if ($failed -gt 0) { exit 1 }
exit 0
