$ProjectRoot = Split-Path -Parent $PSScriptRoot
$AiInfraRoot = if ($env:AI_INFRA_DIR) { $env:AI_INFRA_DIR } else { "E:\AI-Infra" }
$PipCache = Join-Path $AiInfraRoot "pip-cache"
$Wheelhouse = Join-Path $AiInfraRoot "wheels"
$ProjectTemp = Join-Path $ProjectRoot ".tmp"
$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

$TorchVersion = "2.13.0+cu130"
$WheelName = "torch-2.13.0+cu130-cp314-cp314-win_amd64.whl"
$WheelPath = Join-Path $Wheelhouse $WheelName
$WheelUrl = "https://download.pytorch.org/whl/cu130/torch-2.13.0%2Bcu130-cp314-cp314-win_amd64.whl"

if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw "Virtual environment not found: $PythonExe"
}

$env:PIP_CACHE_DIR = $PipCache
$env:TEMP = $ProjectTemp
$env:TMP = $ProjectTemp
New-Item -ItemType Directory -Force -Path $PipCache, $Wheelhouse, $ProjectTemp | Out-Null

if (-not (Test-Path -LiteralPath $WheelPath)) {
    Write-Host "Downloading CUDA Torch to shared wheelhouse: $Wheelhouse"
    & $PythonExe -m pip download --no-deps --dest $Wheelhouse $WheelUrl
    if ($LASTEXITCODE -ne 0) {
        throw "CUDA Torch download failed with exit code $LASTEXITCODE"
    }
} else {
    Write-Host "Using existing shared wheel: $WheelPath"
}

Write-Host "Installing $TorchVersion into the project virtual environment..."
& $PythonExe -m pip install --no-deps --force-reinstall $WheelPath
if ($LASTEXITCODE -ne 0) {
    throw "CUDA Torch installation failed with exit code $LASTEXITCODE"
}

& $PythonExe -c "import torch; print('torch:', torch.__version__); print('cuda:', torch.version.cuda); print('available:', torch.cuda.is_available()); print('gpu:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NONE')"
if ($LASTEXITCODE -ne 0) {
    throw "CUDA Torch verification failed with exit code $LASTEXITCODE"
}

