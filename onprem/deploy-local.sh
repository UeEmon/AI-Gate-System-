#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
COMPOSE_FILE="$SCRIPT_DIR/compose.yaml"

usage() {
  echo "Usage: $0 [--reset-data]"
  echo "  --reset-data  履歴・登録・通知・保存媒体・OCR学習データを削除し、モデルキャッシュは保持"
}

RESET_DATA=0
case "${1:-}" in
  "") ;;
  --reset-data) RESET_DATA=1 ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac

if [[ ! -f "$SCRIPT_DIR/.env" ]]; then
  echo "onprem/.env がありません。env.exampleをコピーして管理パスワードを設定してください。" >&2
  exit 1
fi

docker compose --env-file "$SCRIPT_DIR/.env" -f "$COMPOSE_FILE" build

if [[ "$RESET_DATA" -eq 1 ]]; then
  if [[ ! -t 0 ]]; then
    echo "データ初期化は対話端末から実行してください。" >&2
    exit 1
  fi
  read -r -p "業務データを初期化します。続行するには DELETE DATA と入力してください: " confirmation
  if [[ "$confirmation" != "DELETE DATA" ]]; then
    echo "中止しました。"
    exit 1
  fi
  docker compose --env-file "$SCRIPT_DIR/.env" -f "$COMPOSE_FILE" stop gate || true
  docker compose --env-file "$SCRIPT_DIR/.env" -f "$COMPOSE_FILE" run --rm --no-deps gate \
    python -m aigate.reset /data --confirm "DELETE DATA"
fi

# down -v はモデル用volumeも消すため使用しない。
docker compose --env-file "$SCRIPT_DIR/.env" -f "$COMPOSE_FILE" up -d --remove-orphans gate
docker compose --env-file "$SCRIPT_DIR/.env" -f "$COMPOSE_FILE" ps
