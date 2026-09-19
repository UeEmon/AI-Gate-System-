# オンプレミス版・導入手順

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
docker compose logs --tail=100 gate
```

既定はlocalhost:8080に公開します。名前付きvolume `gate-data` / `gate-models` にデータ・モデルを保持します。
同じComposeプロジェクト名で再起動してください。`down -v` は保存領域を削除するため使用しないでください。
ComposeのUSBデバイス割当は含めていません。Docker版では端末WebカメラまたはRTSPを利用します。
この環境ではDockerビルドとWindows実機検証は未実施です。

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

## 閉域・オフライン運用の準備

1. 接続可能な同一OS・CPU・Pythonの準備端末でセットアップと実際の認識を一度完了させます。
2. `models/yolo11n.pt` と `models/easyocr/` のモデル一式を保存し、取得元とSHA-256を記録します。
3. `data/dependencies/installed-versions.txt` を基に同一環境用wheelを準備します。
   `python -m pip download -r data/dependencies/installed-versions.txt -d wheelhouse` を準備端末で実行します。
4. 閉域側でPython・OS共有ライブラリを導入し、仮想環境に `pip install --no-index --find-links wheelhouse -r installed-versions.txt` で導入します。
   バイナリwheelが得られない依存先は準備端末でビルドし、そのソースと配布条件も確認します。
5. モデルを上記パスへ配置し、`GATE_OFFLINE=1` で起動します。YOLOモデル未配置はエラー、EasyOCRの自動ダウンロードは無効です。
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

## OCR修正データで再学習

Web画面の登録候補で番号を修正し、地名・分類番号・ひらがな・一連指定番号の各画像範囲と正解を確認して学習データを保存します。
ひらがなには小書き・濁音・半濁音を使用できません。一連指定番号は半角数字1〜4桁で入力します。
「OCR再学習」で学習を開始し、評価に合格したモデルを適用してください。
認識処理を停止・再開するとモデルが切り替わります。「標準OCRに戻す」で復帰できます。
Dockerイメージの追加インストールは不要です。モデルと学習画像は `gate-data` に保存されます。
学習にはCPUとメモリを使用するため、必要に応じて認識を停止して実行してください。
画像保存を無効にした履歴からは学習データを作成できません。
詳しい実行条件・評価範囲・保存先はルートのREADMEを参照してください。
