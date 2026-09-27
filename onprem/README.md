# オンプレミス版・導入手順

## 統合デプロイ

Webサーバー起動時に既定の車両検出モデルとLipla-jpを読み込んで初回推論を行います。
初回起動はダウンロードと初期化が終わるまでWeb画面を開けません。モデルはジョブ終了後もメモリに保持されます。

通常の更新は `./onprem/deploy-local.sh`、業務データを初期化して再展開する場合は `./onprem/deploy-local.sh --reset-data` を使用します。初期化時は確認文字列 `DELETE DATA` が必要です。`gate-data` 内の履歴・登録・通知・保存画像/映像・OCR学習データを削除しますが、`gate-models` のモデルキャッシュは保持します。モデル用volumeも削除する `docker compose down -v` は使用しません。

AWSアカウントなしで、認識、車両登録・CSV、画面通知、映像のローカル保存、社内SMTPでのメール通知を利用できます。
この配布物はソース導入キットです。Python・依存ライブラリ・モデルは同梱していません。
本体ライセンスは権利者の採用決定待ちです。`LICENSE-PROPOSAL.md` を参照してください。

## Windows（Python 3.11）

Python 3.11をインストールし、ZIPを展開した `AI-Gate-System` ディレクトリで実行します。
初回セットアップとモデル取得にはインターネット接続が必要です。

```powershell
.\onprem\setup.ps1
.\onprem\start.ps1
```

PowerShellの実行制限がある環境では組織の設定に従ってスクリプトを承認してください。
ブラウザで `http://localhost:8080` を開きます。USBカメラはこのネイティブ版で利用できます。
MP4で映像を保存する場合はFFmpegを別途導入しPATHへ追加します。未導入時はAVIで保存します。

## Linux（Python 3.11以上）

ディストリビューションのPython venv機能、OpenCVが必要とするlibGL/libglib、任意でFFmpegを導入します。

```sh
sh onprem/setup.sh
sh onprem/start.sh
```

LAN公開する場合は `GATE_HOST=0.0.0.0` と `GATE_ADMIN_PASSWORD` を環境変数で設定します。
管理ユーザー名は `admin` です。ブラウザ端末のWebカメラはHTTPSまたはlocalhostが必要です。
LANでは社内HTTPSリバースプロキシを使用し、サーバー証明書を端末で信頼してください。

## Linux Docker Compose

```sh
cd onprem
cp env.example .env
# .envの管理パスワードを必ず変更する
docker compose up -d --build
docker compose logs --tail=100 gate trainer
```

`gate` と `trainer` が同時に起動します。運用の `gate` はLipla-jpだけを使います。`trainer` は学習コード・事前学習重みが揃うまで待機状態を表示し、運用側の起動には影響しません。既定はlocalhost:8080に公開します。名前付きvolume `gate-data` / `gate-models` にデータ・モデルを保持します。
同じComposeプロジェクト名で再起動してください。`down -v` は保存領域を削除するため使用しないでください。
ComposeのUSBデバイス割当は含めていません。Docker版では端末WebカメラまたはRTSPを利用します。
この環境ではDockerビルドとWindows実機検証は未実施です。

## 日本プレート専用検出器とPaddleOCRの追加学習（任意）

通常運用はLipla-jpです。認識履歴から登録・確認に取り込み、ナンバーと4項目の画像範囲を確認・修正して「登録・更新」を押すと、元画像がある場合は車両情報と学習データを同時保存します。修正した値が正解になります。元画像・プレート候補のない手入力登録からは学習データを作れません。Lipla-jpの未修正結果も、信頼度98%以上であれば**学習側だけ**に追加します。評価側には登録時に確認した画像のみを入れます。最低でも別番号の学習用5件・評価用2件が必要です。少量では実運用精度は判定できないため、実際には多様な撮影条件と十分な独立評価データを集めてください。

学習は運用コンテナから隔離した `trainer` コンテナで行います。標準の `docker compose up -d --build` で両方起動します。モデルと教師データは名前付きvolumeで共有します。`docker compose down -v` は学習データも削除するため、保存したい場合は使用しないでください。

次に[PaddleOCR公式の学習用コード](https://github.com/PaddlePaddle/PaddleOCR)をリポジトリ直下の `PaddleOCR` ディレクトリに取得し、[PP-OCRv5_mobile_recの事前学習重み](https://paddle-model-ecology.bj.bcebos.com/paddlex/official_pretrained_model/PP-OCRv5_mobile_rec_pretrained.pdparams)を `gate-models` volume の `/models/PP-OCRv5_mobile_rec_pretrained.pdparams` に置きます。学習コンテナは `linux/amd64` で動かすため、Apple SiliconのDocker Desktopではエミュレーションにより学習・評価が遅くなる可能性があります。重みや依存ライブラリの取得・導入に失敗した場合はそこで停止してください。

MacのDocker Desktopで「学習環境未準備」と表示された場合は、リポジトリのルートで `sh onprem/setup-paddle-training.sh` を実行してください。学習用コードと重みを取得し、既存の `gate-models` volume に重みを配置して、起動中の `trainer` から見えることまで確認します。Docker Composeが先に作成した空の `PaddleOCR` ディレクトリも利用できます。中に既存ファイルがある場合は安全のため停止します。`trainer` が未起動なら先に `cd onprem && docker compose up -d --build` を実行してください。学習用コードと重みがあっても、十分な手動確認済み画像がなければ画面は「データ不足」となります。

```sh
# リポジトリのルートから実行
git clone --depth 1 https://github.com/PaddlePaddle/PaddleOCR.git PaddleOCR
mkdir -p models
curl -fL -o models/PP-OCRv5_mobile_rec_pretrained.pdparams \
  https://paddle-model-ecology.bj.bcebos.com/paddlex/official_pretrained_model/PP-OCRv5_mobile_rec_pretrained.pdparams
cd onprem
docker compose build trainer
docker compose run --rm \
  -v "$PWD/../models:/transfer:ro" trainer \
  cp /transfer/PP-OCRv5_mobile_rec_pretrained.pdparams /models/
```

```sh
docker compose run --rm trainer \
  python paddle_training.py --data /data --output /data/ocr-learning/paddle/run-001
docker compose run --rm trainer \
  python paddle_finetune.py --dataset /data/ocr-learning/paddle/run-001 \
  --paddle-repo /training/PaddleOCR \
  --pretrained /models/PP-OCRv5_mobile_rec_pretrained.pdparams
```

このコマンドは車両画像のLipla-jp検出枠から1クラスのYOLOプレート検出器を学習し、確認済みの上下2行画像からPP-OCRv5_mobile_recを追加学習・評価・推論形式に変換します。学習したモデルは `/data/ocr-learning/paddle/run-001/weights/` に保存します。学習スクリプトは本番モデルを自動で切り替えません。評価用の手動確認画像で検出率、ナンバー完全一致率、遅延を計測し、別の実映像でも確認してください。

```sh
docker compose run --rm trainer \
  python paddle_evaluate.py --data /data --dataset /data/ocr-learning/paddle/run-001 \
  --plate-weights /data/ocr-learning/paddle/run-001/weights/plate.pt \
  --recognition-dir /data/ocr-learning/paddle/run-001/weights/paddle-inference
```

新しい手動修正データが保存されると、`trainer` は約30秒ごとに確認し、学習コード・重みと教師データが揃った時点で自動学習を開始します。Webの「確認済みOCRデータ」で工程・学習ログ・件数を確認できます。完了時には手動確認済み評価画像でLipla-jpと学習済みPaddleOCRを自動比較し、検出率（IoU 0.50）、ナンバー完全一致率、平均・95%遅延、FPSを同じ画面に表示します。「比較を再実行」から再測定もできます。`GET /api/paddle-training` でも結果を取得できます。評価画像がない場合や重み・実行環境が不足する場合は比較が失敗し、`/data/ocr-learning/paddle/auto-*/comparison.log` を確認してください。少数の評価例では精度を一般化できません。Apple Siliconのx86エミュレーションで測ったFPSはMacネイティブ実行の速度ではありません。学習失敗時は `/data/ocr-learning/paddle/auto-*/train.log` を確認してください。**学習済み重みは運用コンテナには読み込まれません。運用の検出・OCRは常にLipla-jpです。**

終了コード `-9` は学習プロセスが強制終了されたことを示します。MacのDocker Desktopでメモリ不足の可能性があるため、YOLO学習の画像サイズ640・バッチ1・ワーカー0、PaddleOCRのバッチ1、CPUスレッド2に設定しています。これは原因の確定ではありません。Docker Desktopのメモリ割当と `docker stats` を確認してください。修正後はWeb画面の「同じデータで学習を再実行」を押せます。約30秒ごとの確認後に新しい学習ログが作成されます。`docker compose -f onprem/compose.yaml up -d --build trainer` で学習コンテナだけを更新できます。

`STAGE: recognizer` の直後に `ImportError: libgomp.so.1` と出る場合は古い学習用イメージです。`docker compose -f onprem/compose.yaml build trainer && docker compose -f onprem/compose.yaml up -d trainer` で再構築してください。学習用イメージは `libgomp1` を導入し、ビルド中に `import paddle` を検査します。再起動後にOCR学習タブの「同じデータで学習を再実行」を押してください。

## 社内SMTP通知

ネイティブ版はシェルの環境変数、Docker版は `onprem/.env` を設定します。
ネイティブ起動スクリプトは `.env` を読み込みません。

| 変数 | 設定 |
|---|---|
| GATE_EMAIL_BACKEND | smtp（オンプレミス起動スクリプトで既定設定） |
| GATE_EMAIL_FROM / GATE_EMAIL_TO | 送信元・宛先。複数宛先はカンマ区切り |
| GATE_SMTP_HOST / GATE_SMTP_PORT | SMTPサーバー / ポート（既定587） |
| GATE_SMTP_TLS | starttls（既定）、ssl（通常465）、none（明示的に指定した非TLSリレー用） |
| GATE_SMTP_USER / GATE_SMTP_PASSWORD | 認証が必要な場合に設定 |
| GATE_PUBLIC_URL | メール内に掲載する管理画面URL |

宛先または送信元を空にするとメールは無効です。送信失敗の再試行はSESと共通です。
TLS証明書は検証します。社内CAの場合はPythonが信頼するCAストアを整備してください。
複数宛先の一部だけが拒否された場合は再試行となり、先に受理された宛先に重複する場合があります。
受理ステータスはメールサーバーの受付までを示し、最終配送を保証しません。
SMTP設定時にSESを呼び出しません。S3未設定では映像はローカル保存だけです。

画面通知は直近10件だけを表示し、その範囲で未確認を先頭にして新しい日付順に並べます。
未確認件数は非表示分も含めて表示し、「未確認をすべて確認済みにする」で全件を一括確認できます。

## 閉域・オフライン運用の準備

1. 接続可能な同一OS・CPU・Pythonの準備端末でセットアップと実際の認識を一度完了させます。
2. `models/yolo26n.pt` とLipla-jpモデルを保存し、取得元とSHA-256を記録します。
3. `data/dependencies/installed-versions.txt` を基に同一環境用wheelを準備します。
   `python -m pip download -r data/dependencies/installed-versions.txt -d wheelhouse` を準備端末で実行します。
4. 閉域側でPython・OS共有ライブラリを導入し、仮想環境に `pip install --no-index --find-links wheelhouse -r installed-versions.txt` で導入します。
   バイナリwheelが得られない依存先は準備端末でビルドし、そのソースと配布条件も確認します。
5. モデルを上記パスへ配置し、`GATE_OFFLINE=1` で起動します。YOLO・Lipla-jpのモデルが未配置なら起動に失敗します。
6. Dockerの場合は準備端末でビルド済みイメージをsave/loadし、モデルvolumeも移送します。閉域側は `docker compose up -d --no-build --pull never` で起動します。

オフライン設定はモデル未配置時の取得を抑えるものです。依存ソフトの全ネットワーク動作を保証するものではありません。
閉域要件がある場合はネットワーク制御も適用し、カメラ・社内SMTPへの必要な到達性だけを許可してください。
完全オフライン用wheel・Dockerイメージ・モデル一式の作成は今回のZIPには含まれません。

## 保守と配布

DBと映像は同じ保存領域で管理し、単一サーバーのみ起動します。SQLiteをNAS/NFS上へ置かないでください。
バックアップは処理停止後の保存領域コピーかSQLiteのbackup APIを使います。CSVは登録リストだけで、通知・映像・履歴のバックアップではありません。
パスワード、CSV実データ、DB、映像を配布ZIPへ混入させないでください。
`python scripts/build_package.py --revision <commit-SHA>` でソースZIPとファイル別SHA-256マニフェストを再生成できます。
Python環境は `scripts/dependency_inventory.py --output data/dependencies` で版とライセンスを収集します。

## 確認済みナンバーの保存

Web画面で認識候補を訂正し、画像範囲と正解を確認して保存できます。
Lipla-jp以外のOCRモデルの学習・適用は提供していません。旧データは保持します。

## 履歴の一括削除

Web画面の処理履歴から、処理履歴と認識履歴を選んで一括削除できます。
実行中は削除できず、削除後の復元はできません。登録車両・通知・OCR学習データ・モデルは残ります。
認識履歴を削除すると、保持した学習データの元画像を履歴から再編集できなくなります。
