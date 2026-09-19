#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/dependency_inventory.py --output data/dependencies
printf '%s\n' 'Setup complete. Run: sh onprem/start.sh'
