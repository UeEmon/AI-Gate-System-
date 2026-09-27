# 専用プレート検出器＋PaddleOCR方式の検証

ブランチ: `experiment/plate-detector-paddle-ocr`。車両検出（YOLO26n）の後、車両画像に専用プレート検出重み（.pt）を適用し、四隅補正とPP-OCRv6 mediumによるOCRを測ります。専用重みは配布されていません。未指定時は実行せず、OpenCV輪郭抽出にも切り替えません。

## Mac DockerのWeb検証

```bash
git fetch origin
git switch --track origin/experiment/plate-detector-paddle-ocr
mkdir -p benchmark-models benchmark-paddle-models
test -f onprem/.env || cp onprem/env.example onprem/.env
# onprem/.env の GATE_ADMIN_PASSWORD を設定
# ライセンスを確認した専用プレート検出重みを benchmark-models/ に配置
docker compose --env-file onprem/.env -p ai-gate-paddle \
  -f onprem/compose.plate-only.yaml up -d --build
```

`http://localhost:8889`、ユーザー名 `admin` でログインします。画像・動画またはRTMP、専用プレート重み、PaddleOCRモデルを選んで検証します。モデル未配置時には画面から検証を開始できません。GoProのRTMP受信は先に `python3 onprem/setup_rtmp_receiver.py` を実行し、composeコマンドに `-f onprem/compose.gopro.yaml` を加えてください。

ホスト側のポートは `onprem/env.example` で8889に設定済みです。既存の `onprem/.env` にポート設定がなくても8889を使います。8889が使用中なら `onprem/.env` の `GATE_BENCHMARK_PORT` を別の空きポートに変更します。コンテナ内のポートは8080のままです。

PaddleOCR認識モデルは「PP-OCRv6 medium（追加学習前）」が既定です。追加学習済みモデルを試すには、PaddleOCRが読み込める**エクスポート済み推論モデルのディレクトリ**を `benchmark-paddle-models/<モデル名>/` に配置し、Web画面で選びます。`TextRecognition(model_name='PP-OCRv6_medium_rec', model_dir=...)` に渡します。既存システムにはPaddleOCR追加学習ジョブがないため、このブランチで新しい重みが自動生成されるわけではありません。専用プレート検出重みの学習入口は `vision_train.py --task plate` です。学習・評価に使うデータは別々に管理してください。

`frames.jsonl` には各車両の検出枠、プレートの検出枠、OCR候補、工程別時間を記録します。`summary.json` は推論FPS・遅延と実効FPSを記録します。専用検出器が失敗した場合、プレート検出件数は0です。現時点の補正はOpenCVによる四隅探索であり、専用モデルによる四隅推定ではありません。正解ラベル未登録の検証の `accuracy` は null です。

Mac DockerではCPUで実行します。Mac直接起動スクリプトの依存はPaddleOCRを含まないため、このブランチの手順はDockerを使ってください。両方式を同じ8888番ポートで検証する場合は先に現在のサービスを停止してください。
