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

# Capture the two images this deployment is about to replace. Docker refuses
# removal if another container still uses one of them.
OLD_IMAGES=()
for image in ai-gate-system:onprem ai-gate-system:paddle-trainer; do
  old_id=$(docker image inspect --format '{{.Id}}' "$image" 2>/dev/null || true)
  if [[ -n "$old_id" ]]; then OLD_IMAGES+=("$old_id"); fi
done

if ! docker compose --env-file "$SCRIPT_DIR/.env" -f "$COMPOSE_FILE" build --progress=plain; then
  echo 'Dockerのビルドに失敗しました。上に表示されたpip/aptの最初のエラーを確認してください。' >&2
  echo '容量不足の場合は docker system df で確認し、ビルドキャッシュのみ docker builder prune -f で削除できます。' >&2
  echo 'gate-data/gate-modelsのボリュームを保持するため docker compose down -v や docker volume prune は実行しないでください。' >&2
  exit 1
fi

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
  docker compose --env-file "$SCRIPT_DIR/.env" -f "$COMPOSE_FILE" stop gate trainer || true
  docker compose --env-file "$SCRIPT_DIR/.env" -f "$COMPOSE_FILE" run --rm --no-deps gate \
    python -m aigate.reset /data --confirm "DELETE DATA"
fi

# down -v はモデル用volumeも消すため使用しない。
docker compose --env-file "$SCRIPT_DIR/.env" -f "$COMPOSE_FILE" up -d --remove-orphans gate trainer
docker compose --env-file "$SCRIPT_DIR/.env" -f "$COMPOSE_FILE" ps

# Only clean up after both services started successfully. Do not touch volumes,
# build cache, base images or images used by another Compose project.
CURRENT_IMAGES=()
for image in ai-gate-system:onprem ai-gate-system:paddle-trainer; do
  CURRENT_IMAGES+=("$(docker image inspect --format '{{.Id}}' "$image")")
done
for old_id in "${OLD_IMAGES[@]}"; do
  if [[ "$old_id" == "${CURRENT_IMAGES[0]}" || "$old_id" == "${CURRENT_IMAGES[1]}" ]]; then continue; fi
  if docker image inspect "$old_id" >/dev/null 2>&1; then
    if ! docker image rm "$old_id"; then
      echo "使用中または他のタグが付いた旧イメージ $old_id は保持します。" >&2
    fi
  fi
done
# Also clear older untagged builds of these two labeled images, without
# removing dangling images from unrelated projects.
docker image prune --force --filter 'label=jp.ai-gate-system.cleanup-scope=onprem'
