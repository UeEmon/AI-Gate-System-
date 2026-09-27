#!/bin/sh
set -eu

# Run from any working directory; the trainer stays isolated from the gate.
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/.." && pwd)
cd "$repo_root"

if [ ! -f PaddleOCR/tools/train.py ]; then
  if [ -e PaddleOCR ]; then
    echo 'PaddleOCR ディレクトリがありますが tools/train.py がありません。配置を確認してください。' >&2
    exit 1
  fi
  git clone --depth 1 https://github.com/PaddlePaddle/PaddleOCR.git PaddleOCR
fi

mkdir -p models
weight="$repo_root/models/PP-OCRv5_mobile_rec_pretrained.pdparams"
if [ ! -s "$weight" ]; then
  tmp="$weight.download"
  trap 'rm -f "$tmp"' EXIT HUP INT TERM
  curl --fail --location --retry 3 --output "$tmp" \
    https://paddle-model-ecology.bj.bcebos.com/paddlex/official_pretrained_model/PP-OCRv5_mobile_rec_pretrained.pdparams
  if [ "$(wc -c < "$tmp")" -lt 1000000 ]; then
    echo '重みの取得サイズが小さすぎます。ダウンロード先を確認してください。' >&2
    exit 1
  fi
  mv "$tmp" "$weight"
  trap - EXIT HUP INT TERM
fi

cd "$script_dir"
# Named volume is shared with the already running trainer; never remove it.
docker compose run --rm --no-deps --user 0 \
  -v "$repo_root/models:/transfer:ro" trainer \
  sh -c 'cp /transfer/PP-OCRv5_mobile_rec_pretrained.pdparams /models/PP-OCRv5_mobile_rec_pretrained.pdparams && chmod 644 /models/PP-OCRv5_mobile_rec_pretrained.pdparams'
docker compose exec -T trainer sh -c \
  'test -s /training/PaddleOCR/tools/train.py && test -s /models/PP-OCRv5_mobile_rec_pretrained.pdparams'
echo '学習用コードと重みを trainer から確認できました。画面表示は最大30秒程度で更新されます。'
