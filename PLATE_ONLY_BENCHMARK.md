# Lipla-jp方式の検証

ブランチ: `experiment/lipla-jp-vehicle-roi`。車両検出（YOLO26n）の後、車両画像をLipla-jpの公開APIに直接渡し、同ライブラリ内の四隅検出、正規化、項目別OCRをまとめて測ります。追加のOpenCV輪郭抽出や専用YOLOプレート検出は使いません。全画面方式は車両検出を外した比較用です。

## Mac DockerのWeb検証

```bash
git fetch origin
git switch --track origin/experiment/lipla-jp-vehicle-roi
mkdir -p benchmark-models
test -f onprem/.env || cp onprem/env.example onprem/.env
# onprem/.env の GATE_ADMIN_PASSWORD を設定
docker compose --env-file onprem/.env -p ai-gate-lipla \
  -f onprem/compose.plate-only.yaml up -d --build
```

`http://localhost:8888`、ユーザー名 `admin` でログインします。画像・動画またはRTMPを選んで検証します。既定は車両検出後に各車両範囲をLiplaへ渡します。GoPro受信用のRTMPコンテナが必要な場合は先に `python3 onprem/setup_rtmp_receiver.py` を実行して、composeコマンドに `-f onprem/compose.gopro.yaml` を加えます。

初回はYOLO26nとLiplaモデルの取得が必要です。Liplaの内部推論はライブラリ既定のONNXランタイムで実行されます。MacのDockerではMPSを利用できません。車両検出のみMac上でMPSを試す場合は `bash onprem/start-plate-mac.sh` を使用します。

`frames.jsonl` は車両範囲、四隅、各項目と結果文字列を保存します。`summary.json` の `plate_recognition_ms` はLipla内部の検出・補正・OCRを合わせた時間です。ライブラリの公開APIが認識結果を返さなかった場合、その原因が検出失敗かOCR失敗かは区別できません。正解ラベルを登録していない検証の `accuracy` は null です。

Mac Dockerで両方式を同じ8888番ポートで動かす場合、ブランチの切り替え前に現在の検証サービスを停止してください。画像と重みのライセンス・取り扱いは利用前に確認してください。
