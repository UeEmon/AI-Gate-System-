$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
if (-not $env:GATE_EMAIL_BACKEND) { $env:GATE_EMAIL_BACKEND = 'smtp' }
$env:EASYOCR_MODULE_PATH = Join-Path $PWD 'models/easyocr'
$env:YOLO_CONFIG_DIR = Join-Path $PWD 'data/ultralytics'
$gateHost = if ($env:GATE_HOST) { $env:GATE_HOST } else { '127.0.0.1' }
New-Item -ItemType Directory -Force models,data | Out-Null
& .\.venv\Scripts\python.exe web.py --host $gateHost --model (Join-Path $PWD 'models/yolo11n.pt') --data (Join-Path $PWD 'data')
exit $LASTEXITCODE
