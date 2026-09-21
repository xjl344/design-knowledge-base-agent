$ProjectRoot = Split-Path -Parent $PSScriptRoot
$AiInfraRoot = if ($env:AI_INFRA_DIR) { $env:AI_INFRA_DIR } else { "E:\AI-Infra" }

$env:AI_INFRA_DIR = $AiInfraRoot
$env:PIP_CACHE_DIR = Join-Path $AiInfraRoot "pip-cache"
$env:WHEELHOUSE_DIR = Join-Path $AiInfraRoot "wheels"
$env:HF_HOME = Join-Path $ProjectRoot ".cache\huggingface"
$env:TORCH_HOME = Join-Path $ProjectRoot ".cache\torch"
$env:TEMP = Join-Path $ProjectRoot ".tmp"
$env:TMP = $env:TEMP
$env:GRADIO_TEMP_DIR = Join-Path $ProjectRoot "data\gradio_tmp"
$env:ANONYMIZED_TELEMETRY = "False"

$ProjectDirectories = @(
    $env:PIP_CACHE_DIR,
    $env:WHEELHOUSE_DIR,
    $env:HF_HOME,
    $env:TORCH_HOME,
    $env:TEMP,
    $env:GRADIO_TEMP_DIR,
    (Join-Path $ProjectRoot "data\documents"),
    (Join-Path $ProjectRoot "data\chroma_db"),
    (Join-Path $ProjectRoot "logs")
)

New-Item -ItemType Directory -Force -Path $ProjectDirectories | Out-Null

$ActivateScript = Join-Path $ProjectRoot ".venv\Scripts\Activate.ps1"
if (-not (Test-Path -LiteralPath $ActivateScript)) {
    throw "Virtual environment not found: $ActivateScript"
}

. $ActivateScript
Set-Location -LiteralPath $ProjectRoot
Write-Host "Project environment ready: $ProjectRoot"
Write-Host "Python: $((Get-Command python).Source)"
Write-Host "Shared pip cache: $env:PIP_CACHE_DIR"
Write-Host "Shared wheelhouse: $env:WHEELHOUSE_DIR"
