# AI Gate System

OpenCVを使った車両分類・日本のナンバープレートOCRの**初期試作版**です。
写真、録画動画、USBカメラ、RTSP映像を入力し、結果をSQLiteに追記します。

## 実装範囲

- OpenCV：映像入力、車両切り出し、矩形のナンバー候補領域抽出。
- Ultralytics YOLO：COCOのcar / motorcycle / bus / truckを分類。
- EasyOCR：日本語・英数字の読取候補、信頼度、通常の日本式ナンバーの項目分解。
- SQLite：処理日時（UTC）、動画内位置、フレーム番号、車両分類、OCR候補を保存。
- オプションで検出車両の切り抜きJPEGを保存。Ctrl+Cで終了。

**メーカー名・モデル名の識別、通過台数集計、入退場判定、ゲート機器の制御は未実装です。**
車種は上記4区分です。同じ車両が複数フレームに現れると複数レコードになります。

## セットアップ（Windows PowerShell / Python 3.11）

ZIPを展開し、このREADMEとapp.pyがあるフォルダーで実行します。

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py --source "C:\images\car.jpg" --save-images
```

macOS / Linuxでは `python3 -m venv .venv` を実行し、上記のPythonパスを
`.venv/bin/python` に置き換えます。Linuxで共有ライブラリ不足が出る場合は
OpenCVに必要なOSパッケージを導入してください。

初回はYOLOとEasyOCRの学習済み重みをインターネットからダウンロードします。
CPUを使用します。モデル取得後はローカルで処理します。
パッケージの組合せはバージョン範囲指定であり、全環境で検証済みの固定構成ではありません。

## 入力例

以下の `python` は作成した仮想環境のPythonを指します。

```bash
# 写真：車両画像も保存
python app.py --source car.jpg --save-images

# 動画：10フレームごとに認識
python app.py --source gate.mp4 --every 10

# USBカメラ：0番
python app.py --source 0 --every 10

# RTSPカメラ
python app.py --source "rtsp://CAMERA_ADDRESS/stream" --every 15

# 保存先と車両検出しきい値
python app.py --source car.jpg --output output --confidence 0.5
```

この版はコマンドライン用です。映像プレビュー画面やWeb画面はありません。
認識速度はCPU、解像度、車両数に依存し、リアルタイム性能を保証しません。
カメラ読取が停止した場合は終了します。自動再接続は未実装です。

## 結果の確認

既定の保存先は `data/gate.db`、テーブルは `observations` です。
各観測結果を標準出力にもJSONで出します。

```sql
SELECT processed_at, frame_index, media_ms, vehicle_type, confidence, details_json
FROM observations ORDER BY processed_at DESC;
```

`details_json` 内の `plate_candidates` に候補文字列・信頼度・候補枠・解析項目があります。
`fields` が存在する場合は、`region`（地名）、`category`（分類番号）、
`kana`（ひらがな）、`serial`（一連指定番号、表示上の点・ハイフンを除去）を格納します。
原文は `text` に保持します。取得日時は撮影日時ではなく処理日時です。
写真の撮影日時は読み取りません。動画内の位置は `media_ms` で記録します。

`candidate` は形式に合う候補、`needs_review` は要確認、`unreadable` は読取候補なしです。
形式一致や高いOCR信頼度は、番号の正しさを保証しません。
観測全体は常に要確認扱いにし、未読文字を推測して埋めません。

## 認識上の制限

ナンバー領域抽出は輪郭と縦横比による簡易方式です。専用学習済みナンバー検出モデルは
含みません。斜め、遠距離、反射、夜間、小さい文字、二輪車などで未検出・誤検出が起こります。
OCRが車体上の文字を読むこともあります。通常のナンバー形式だけを簡易解析し、
外交官・臨時・特殊形式、ローマ字の用途記号等は未対応です。
ナンバー文字列が解析できなくても、OCR原文は候補として残します。
車両そのものを検出できなかった場合は観測レコードを作りません。

実運用に向けては、使用するカメラの昼夜・雨天・斜め画像で精度を測定し、
専用ナンバー検出モデルと認識辞書、追跡・重複抑制を追加してください。

## テストと確認状態

```bash
python -m unittest discover -s tests -v
python -m compileall -q app.py tests
```

文字列解析（全角、2行、英字入り分類番号、短い番号、不正入力）と、SQLiteへの
追記・日本語保存をテストしています。実画像での推論、カメラ接続、モデル取得、
依存パッケージの統合動作はこの提供環境で未検証です。

## GitHubへの追加

対象： https://github.com/UeEmon/AI-Gate-System-

ソースコード、依存パッケージ一覧、テスト、実行手順をこのリポジトリで管理します。

画像・DB・学習済み重み・認証情報は `.gitignore` の対象です。

## 参照・依存ライセンス

- OpenCV: https://docs.opencv.org/4.x/
- Ultralytics推論API: https://docs.ultralytics.com/modes/predict/
- EasyOCR API: https://www.jaided.ai/easyocr/documentation/
- Ultralyticsライセンス: https://www.ultralytics.com/license

UltralyticsにはAGPL-3.0/Enterpriseのライセンス選択があります。
配布・運用時は利用する依存パッケージと重みのライセンス条件を確認してください。
