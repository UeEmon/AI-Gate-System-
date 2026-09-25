#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "$(uname -s)" != Darwin ]]; then
  echo 'この起動スクリプトはmacOS用です。' >&2
  exit 1
fi
python3 -m venv .venv-plate-mac
.venv-plate-mac/bin/python -m pip install -r requirements-mac-benchmark.txt
mkdir -p benchmark-models benchmark-data benchmark-cache
if [[ -z "${GATE_ADMIN_PASSWORD:-}" ]]; then
  read -r -s -p 'Webログイン用パスワードを入力: ' GATE_ADMIN_PASSWORD
  echo
fi
export GATE_BENCHMARK_OCR_CHOICES=easyocr
export GATE_ADMIN_PASSWORD
export GATE_BENCHMARK_DATA="$PWD/benchmark-data"
export GATE_BENCHMARK_MODELS="$PWD/benchmark-models"
export EASYOCR_MODULE_PATH="$PWD/benchmark-cache/easyocr"
export YOLO_CONFIG_DIR="$PWD/benchmark-cache/ultralytics"
export GATE_BENCHMARK_HOST=127.0.0.1
export GATE_BENCHMARK_PORT="${GATE_BENCHMARK_PORT:-8888}"
exec .venv-plate-mac/bin/python plate_benchmark_web.py
