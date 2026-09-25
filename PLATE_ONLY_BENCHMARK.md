# 車両検出を省く検証ブランチ

ブランチ: `experiment/plate-only-benchmark`。専用Web画面とCLI検証機能です。
既存Web画面は通常の車両検出経路のままです。

`plate_only_benchmark.py` は車両モデルを初期化せず、全画面を
既存のプレート検出→台形補正→OCRへ渡します。
専用重み未指定の場合はOpenCV輪郭抽出です。AIプレート検出を測る場合は
利用条件を確認した専用重みを`--plate-model /models/plate.pt`で指定してください。

## Web画面で検証（推奨）

リポジトリ直下で起動します。

```bash
git fetch origin
git switch experiment/plate-only-benchmark
git pull --ff-only
mkdir -p benchmark-models
test -f onprem/.env || cp onprem/env.example onprem/.env
```

`onprem/.env`の`GATE_ADMIN_PASSWORD`を設定してから実行してください。

```bash
docker compose --env-file onprem/.env -p ai-gate-plate-test \
  -f onprem/compose.plate-only.yaml up -d --build
```

http://localhost:8888 を開き、ユーザー名`admin`、設定したパスワードでログインします。
画像・動画の選択、プレート検出方式、OCR、処理間隔、最大枚数を指定して「検証を開始」を押します。
処理中は自動更新され、完了後に検出枠付き画像、OCR文字列、FPS・p95遅延が表示されます。
専用YOLOプレート重み（.pt）は`benchmark-models`へ置くと選択肢に表示されます。
ライセンスと取得元を確認した専用重みを使用してください。

- アップロード上限は1GB、処理上限は300フレーム、同時実行は1件です。
- 検証結果とモデルキャッシュは専用ボリュームに保存します。
- 起動中のジョブ一覧はメモリ管理です。再起動後も保存ファイルは残りますが一覧には復元しません。
- 通常のWebサーバーとは別サービスです。計測時は通常サービスを停止し、負荷条件を揃えてください。
- LAN公開時は`.env`の`GATE_BIND_ADDRESS=0.0.0.0`を設定して再作成し、`http://MacのIP:8888`を開きます。

停止：
```bash
docker compose --env-file onprem/.env -p ai-gate-plate-test \
  -f onprem/compose.plate-only.yaml stop
```

## CLIで実行（任意）


リポジトリ直下で以下を実行します。入力動画を`benchmark-input/test.mp4`に置いてください。

```bash
git fetch origin
git switch --track origin/experiment/plate-only-benchmark
mkdir -p benchmark-input benchmark-output
docker compose --env-file onprem/.env -f onprem/compose.yaml build gate
docker compose --env-file onprem/.env -f onprem/compose.yaml run --rm --no-deps \
  -v "$PWD/benchmark-input:/input:ro" \
  -v "$PWD/benchmark-output:/benchmark" \
  -e GATE_OCR_BACKEND=paddle gate \
  python plate_only_benchmark.py --source /input/test.mp4 \
  --output /benchmark --every 1 --warmup 5 --max-frames 300 --save-images
```

写真の場合は`--source /input/test.jpg --warmup 0 --max-frames 1`に変更します。
同時実行負荷を避けるには、実行前に通常のgateサービスを停止してください。

## 出力

各実行は別ディレクトリに保存します。
- `frames.jsonl`: 全画面座標の検出枠、OCR文字列、検出元、各段階の時間。
- 番号付きJPEG: OCR失敗時も検出候補枠を表示。文字はJSONと照合します。
- `summary.json`: 初期化時間、検出/OCR件数、プレート検出・補正・OCR平均時間、
  推論FPSとp95遅延、読込・画像保存・JSON書込を含む実効FPS。

推論FPSと段階別平均はwarmupを除外し、実効FPSは全処理フレームを対象にします。
終了後のsummaryファイル書込とモデル初期化は実効FPSに含みません。
保存画像あり/なしでは実効FPSが変わるため、比較時は条件を揃えてください。

正解ラベル未指定なので検出率・OCR正解率は算出せず`accuracy: null`とします。
`detected_frames`や`text_read_frames`は正解数ではありません。
全画面からの輪郭抽出は背景の誤検出が増える可能性があります。
実画像、Mac Dockerでの速度・精度は実行後に確認してください。
