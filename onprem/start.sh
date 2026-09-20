#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
export GATE_EMAIL_BACKEND="${GATE_EMAIL_BACKEND:-smtp}"
export EASYOCR_MODULE_PATH="$PWD/models/easyocr"
export YOLO_CONFIG_DIR="$PWD/data/ultralytics"
mkdir -p models data
exec .venv/bin/python web.py --host "${GATE_HOST:-127.0.0.1}" --model "$PWD/models/yolo26s.pt" --data "$PWD/data"
