#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ENGINE_ARCH=$(docker version --format '{{.Server.Arch}}')
case "$ENGINE_ARCH" in
  arm64|aarch64) EXPECTED_ARCH=aarch64 ;;
  amd64|x86_64) EXPECTED_ARCH=x86_64 ;;
  *) echo "未対応のDocker Engineアーキテクチャ: $ENGINE_ARCH" >&2; exit 1 ;;
esac
for service in gate trainer; do
  docker compose --env-file "$SCRIPT_DIR/.env" -f "$SCRIPT_DIR/compose.yaml" \
    exec -T -e EXPECTED_ARCH="$EXPECTED_ARCH" "$service" python -c \
    'import os,platform,torch; actual=platform.machine(); print("architecture:",actual,"expected:",os.environ["EXPECTED_ARCH"]); assert actual == os.environ["EXPECTED_ARCH"], "コンテナがホストと異なるCPU形式です。DOCKER_DEFAULT_PLATFORMを解除して再ビルドしてください。"; assert torch.ones(2,2).matmul(torch.ones(2,2))[0,0].item() == 2; print("PyTorch CPU OK:",torch.__version__)'
done
docker compose --env-file "$SCRIPT_DIR/.env" -f "$SCRIPT_DIR/compose.yaml" \
  exec -T -e OMP_NUM_THREADS=1 trainer python -c \
  'import paddle; paddle.set_device("cpu"); assert float(paddle.matmul(paddle.ones([2,2]),paddle.ones([2,2]))[0,0]) == 2; print("Paddle CPU OK:",paddle.__version__)'
echo '運用・学習コンテナのCPU形式と基本演算を確認しました。実画像の認識・追加学習はWeb画面で確認してください。'
