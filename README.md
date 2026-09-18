# AI Gate System

OpenCV・YOLO・EasyOCRによる車両区分・日本のナンバープレート読取候補を、Web画面で管理する初期試作版です。

## 入力方式

| 方式 | 入力 | 動作 |
|---|---|---|
| リアルタイム | 処理PCに接続したUSBカメラ、RTSPカメラ | 最新フレームを優先して連続認識。Web画面から停止 |
| ノンリアルタイム | 画像・録画動画のアップロード | ファイルを指定間隔で順番に処理し、終了時に完了を表示 |

カメラは**サーバー側のカメラ**です。スマートフォン等の閲覧端末のカメラを直接送信する機能はありません。
処理は同時に1件、ファイルは1回につき1ファイルです。自動キューと複数カメラ同時処理は未実装です。

## 起動（Windows PowerShell / Python 3.11推奨）

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe web.py
```

ブラウザで **http://127.0.0.1:8080** を開きます。
macOS/Linuxでは `python3 -m venv .venv` を実行し、Pythonのパスを `.venv/bin/python` に置き換えてください。
必要なOpenCVのOS共有ライブラリは環境に応じて導入してください。

1. 「カメラ」または「ファイル」を選択します。
2. カメラ番号（通常 `0`）、RTSP URL、または画像・動画ファイルを指定します。
3. 「認識を開始」を押します。
4. 処理モニターに検出枠付き画像、処理フレーム数、検出レコード数が表示されます。
5. 認識履歴で車両区分・ナンバー候補・信頼度・切り抜き画像を確認します。
6. カメラは「停止」で終了。ファイルは読込終了時に完了します。処理履歴の「表示」で過去の処理を選択できます。

WebサーバーはWaitress、認識処理は別のPythonプロセスで動作します。
モデル初回取得中も画面から停止できます。停止時は終了を要求し、3秒以内に終了しない処理を強制終了します。
保存済みの観測結果は残ります。停止時点の処理途中のフレームは保存されない場合があります。

## 他のPC・タブレットから管理する

環境変数 `GATE_ADMIN_PASSWORD` に管理パスワードを設定し、次のように起動します。

```powershell
# 下記の値を運用用のパスワードに置き換えてください
$env:GATE_ADMIN_PASSWORD = "replace-with-your-admin-password"
.\.venv\Scripts\python.exe web.py --host 0.0.0.0 --port 8080
```

同じLANのブラウザで `http://処理PCのIPアドレス:8080` を開きます。
ユーザー名は `admin`、パスワードは設定した値です。アクセス元に応じてOSのファイアウォールを設定してください。
既定はローカル接続のみ。LAN公開はパスワードが未設定だと起動しません。
HTTPのBasic認証自体は通信を暗号化しないため、LAN以外への公開や信頼できないネットワークでの利用にはHTTPSリバースプロキシ等が必要です。
多人数向けのアカウント管理・権限分離・監査証跡は未実装です。

**同じ保存先に対して起動するWebサーバーは1プロセスに限定してください。**
サーバー再起動時には未完了ジョブを「中断」にします。自動再開はしません。

## オプション

```bash
python web.py --port 8080 --data data --model yolo11n.pt
# 従来のコマンドラインも利用可能
python app.py --source car.jpg --source-kind file --save-images
python app.py --source gate.mp4 --source-kind file --every 1
python app.py --source 0 --source-kind camera --every 1
```

- 画像：JPEG / PNG / BMP / WebP / TIFF。
- 動画：MP4 / AVI / MOV / MKV / M4V / WebM。実際の読込可否はOpenCVのコーデックに依存します。
- アップロード：512MB以下。サーバー側で生成したファイル名で保存します。
- 処理間隔：`1` は毎フレーム。ファイルはその順番を保ち、カメラでは処理待ちの古いフレームを捨てます。
- プレビュー：約1.5秒間隔で取得する処理済みJPEGです。入力カメラと同じFPSを保証するものではありません。
- カメラ切断時は処理失敗になります。自動再接続はありません。
- CPUで認識します。YOLO・EasyOCRの重みは初回にインターネットから取得します。

## 保存データ

`data/gate.db` の `observations` に認識結果、`jobs` に処理履歴を追記します。
既存の観測テーブルはそのまま利用し、WALモードで画面からの参照と認識結果の書込みを行います。

- `data/images/`：検出車両の切り抜きJPEG。
- `data/jobs/<処理ID>/input.*`：アップロードファイル。
- `data/jobs/<処理ID>/preview.jpg`：最新の処理済み映像。
- `data/jobs/<処理ID>/progress.json`：フレーム数・観測数・準備状態。
- `data/jobs/<処理ID>/worker.log`：モデル・入力エラー等の詳細。カメラ接続情報を含む場合があるため管理者のみで扱ってください。

ファイル・画像・ログの自動削除はありません。保存先の空き容量を管理してください。
映像の処理日時をUTCで記録し、Web画面では閲覧端末の現地時刻に表示します。撮影日時とは異なります。
同じ車両を連続フレームで検出すると複数レコードになります。表示件数は車両の通過台数ではありません。

`details_json` の `plate_candidates` にOCR原文・信頼度・候補枠・解析結果を保存します。
解析結果は `region`（地名）、`category`（分類番号）、`kana`（ひらがな）、`serial`（一連指定番号）です。
形式が合う文字列でも確定番号ではありません。日本語は履歴に表示し、映像の枠ラベルは英語の車両区分です。

## 認識の範囲

車種は乗用車・二輪車・バス・トラックの4区分です。メーカー名・モデル名の識別は未実装です。
ナンバー領域抽出は輪郭と縦横比の簡易方式で、専用学習済みナンバー検出モデルは含みません。
夜間・反射・斜め・小さい文字では未検出や誤読が起きます。特殊形式のナンバーは未対応です。
入退場判定、車両追跡、通過台数の集計、ゲート機器制御は行いません。

## テストと検証範囲

```bash
python -m unittest discover -s tests -v
python -m compileall -q app.py web.py tests
```

16件の自動テストで以下を確認しています。

- 通常の日本式ナンバー文字列解析、SQLite保存。
- ファイルアップロード、カメラ入力選択、開始・停止、同時開始の拒否、異常終了・再起動の状態。
- 履歴フィルター・ページング・画像取得、進捗、認証、CSRF、アップロード容量制限。
- 実際のOpenCVを使用した日本語パスの画像読込・JPEG出力・動画のフレーム間引き。
- 模擬カメラの最新フレーム選択と切断検知。

**推論部分はテスト用モデルに置き換えています。実カメラ、実YOLO/EasyOCRの認識精度・処理速度、
モデルの初回取得を含む全依存関係の統合動作、Windows実機での動作は未検証です。**
ブラウザによる画面の目視確認も実行環境の制約により未実施です。
使用カメラの実画像・実動画で評価してから運用してください。

## 参照

- [OpenCV](https://docs.opencv.org/4.x/)
- [Ultralytics推論API](https://docs.ultralytics.com/modes/predict/)
- [EasyOCR](https://www.jaided.ai/easyocr/documentation/)
- [Flaskのファイルアップロード](https://flask.palletsprojects.com/en/stable/patterns/fileuploads/)
- [Flaskの配備](https://flask.palletsprojects.com/en/stable/deploying/)

依存パッケージはバージョン範囲指定で、全環境で検証済みの固定構成ではありません。
UltralyticsにはAGPL-3.0/Enterpriseのライセンス選択があります。
配布・運用時は依存パッケージと学習済み重みのライセンス条件を確認してください。
