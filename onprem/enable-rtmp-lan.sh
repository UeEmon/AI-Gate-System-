#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [[ ! -f "$SCRIPT_DIR/.env" ]]; then
  echo 'onprem/.env を env.example から作成し、管理パスワードを設定してください。' >&2
  exit 1
fi
python3 "$SCRIPT_DIR/setup_stream_receiver.py" --lan
# Published port bindings only change when the container is recreated.
docker compose --env-file "$SCRIPT_DIR/.env" -f "$SCRIPT_DIR/compose.yaml" \
  up -d --force-recreate stream-init rtmp-ingest
docker compose --env-file "$SCRIPT_DIR/.env" -f "$SCRIPT_DIR/compose.yaml" \
  up -d --no-deps gate
docker compose --env-file "$SCRIPT_DIR/.env" -f "$SCRIPT_DIR/compose.yaml" ps rtmp-ingest
echo 'MacのLAN IPに配信してください。Web画面「配信サーバー」で新規配信を許可し、「カメラ」の入力源を ingest に設定します。'
echo 'macOSファイアウォールではDockerの受信接続を許可してください。'
