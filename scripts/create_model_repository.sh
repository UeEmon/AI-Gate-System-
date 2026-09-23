#!/usr/bin/env bash
# Run on an authenticated Mac/Linux host with GitHub CLI installed.
set -euo pipefail
command -v gh >/dev/null || { echo 'GitHub CLI (gh) is required.' >&2; exit 1; }
gh auth status
source_root="$(cd "$(dirname "$0")/.." && pwd)"
destination="${1:-../AI-Gate-JP-Models-new}"
if [[ -e "$destination" ]]; then
  echo 'Choose a new, empty destination path.' >&2
  exit 1
fi
cp -R "$source_root/training/model-repository-template" "$destination"
git -C "$destination" init -b main
git -C "$destination" add .
git -C "$destination" commit -m 'Initialize reusable Japanese OCR model repository'
gh repo create UeEmon/AI-Gate-JP-Models --private --source "$destination" --remote origin --push
