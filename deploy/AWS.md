# AWS実行仕様（EC2単一ホスト）

## 対応構成

- Linux x86_64 EC2 + Docker Compose。初期評価の目安は4vCPU/16GB RAM。認識速度を測定して調整する。
- Caddy HTTPS → Waitress/Flask → 認識子プロセス。
- SQLite WALと映像・モデルは同じEC2に接続した暗号化EBSの `/srv/ai-gate` に永続化。
- Amazon SESでメール通知、S3で検知映像の追加保管。
- IAMインスタンスロールで認証。AWSアクセスキーをコード・イメージに埋め込まない。
- カメラはVPN/Direct Connect等で到達可能なRTSP。AWSのEC2から現地USBカメラ番号は使用できない。
  現地USBカメラは現地でRTSPへ中継するか、現地サーバー版を利用する。

**アプリ・通知ディスパッチャは1インスタンスのみ。** SQLite WALをEFS/NFSへ配置しない。
この版はECS/Fargateへの水平分散、複数サーバー、無停止切替には非対応。
必要になった段階でRDS/PostgreSQL、メッセージキュー、分散ジョブ制御へ移行する。

## デプロイ手順

### 自動準備（CloudFormation）

`cloudformation-ec2.yaml` は、既存VPC・サブネット上に、暗号化EBS、EC2、IAMインスタンスロール、
管理用セキュリティグループを作成します。実行前に `aws-preflight.sh` で認証状態を確認してください。
EC2作成後の初期設定（Docker、Git、リポジトリ取得）はUserDataで行います。

```bash
export AWS_REGION=ap-northeast-1
export GATE_VPC_ID=vpc-xxxxxxxx
export GATE_SUBNET_ID=subnet-xxxxxxxx
export GATE_AVAILABILITY_ZONE=ap-northeast-1a
export GATE_KEY_NAME=your-key-pair
export GATE_ADMIN_CIDR=203.0.113.10/32
# 任意：既存の非公開S3バケット
export GATE_S3_BUCKET=your-private-bucket
sh deploy/aws-preflight.sh
sh deploy/aws-prepare.sh
```

`GATE_ADMIN_CIDR` は管理端末の固定グローバルIPに限定してください。CloudFormationは
S3バケット自体を作成しません。既存バケットを使う場合は `GATE_S3_BUCKET` を指定し、SES送信元を
検証してから `deploy/.env` を設定してください。既存VPC・サブネット・キーペアは利用者が用意します。

1. EC2と暗号化EBSを用意し、Docker EngineとCompose v2を導入する。
   EBSのスナップショット・保持設定を行う。コンテナやホストを入れ替えてもデータを保持する運用にする。
2. Elastic IP等の安定したアドレスとDNS名を割り当てる。セキュリティグループの443は管理端末の送信元範囲に限定する。
   Caddyの証明書発行・更新に必要な80の到達性を確保する。8080は外部へ公開しない。
3. SESの送信元（メールまたはドメイン）を検証し、同じAWSリージョンを指定する。
   SESサンドボックスでは宛先も検証が必要。一般宛先へ送るには本番アクセス承認が必要。
4. S3バケットを作成する。Block Public Accessを有効にし、暗号化・保持期間・ライフサイクルを設定する。
   アプリは `events/` 以下へAES256暗号化で送信する。KMS必須ポリシーの場合はコードとIAMの変更が必要。
5. `iam-policy.example.json` のアカウントID・送信元・バケット・リージョンを置換し、EC2インスタンスロールへ付与する。
   SESをドメイン検証した場合は、そのドメインidentity ARNを使用する。
   IMDSv2を必須にし、Dockerブリッジ内のSDKがロールを取得できるようメタデータ応答ホップ上限を2に設定する。
6. リポジトリをEC2へ配置し、以下を実行する。

```bash
sudo mkdir -p /srv/ai-gate/data /srv/ai-gate/models
sudo chown -R 10001:10001 /srv/ai-gate/data /srv/ai-gate/models
cd deploy
cp env.example .env
chmod 600 .env
# .envを編集し、実際のDNS名、管理パスワード、検証済み送信元、通知宛先、バケットを設定
sudo docker compose --env-file .env up -d --build
sudo docker compose ps
sudo docker compose logs --tail=100 gate
```

7. `https://設定したDNS名` へアクセスし、ユーザー `admin` と管理パスワードでログインする。
   メール未使用時は `GATE_EMAIL_FROM` / `GATE_EMAIL_TO` を空にする。S3未使用時は `GATE_S3_BUCKET` を空にする。
   設定変更後はコンテナを再作成する。実際の送信テストは運用担当者の承認済み宛先で行う。
8. Web画面で登録車両を作成し、通知対象にする車両にチェックを付ける。

AWSリソース作成・Dockerビルド・実送信は、このリポジトリの作成時点では実行していない。
実行時にはEC2・EBS・S3・SES等の利用料が発生する。
依存ライブラリの範囲指定は固定ロックではないため、採用バージョンを検証・固定してから本番運用する。

## 記録・通知の仕様

- 登録単位は「地名・分類番号・ひらがな・一連指定番号」と車種4区分。
- 登録番号に一致し車種も一致する通常車両：通知しない。
- 登録番号に一致し車種も一致し、通知指定あり：指定車両通知。
- 番号一致、車種不一致：不一致通知。登録内容のスナップショットをイベントに保存。
- 有効な登録がない番号：未登録通知。未使用/無効化した登録は未登録として扱う。
- 処理ごとのOCR信頼度未満、未読、形式不正：未登録とは断定せず「ナンバー要確認」通知。
- 同じ処理・理由・番号・車種は映像時間60秒につき1件に抑制。ジョブが変わると別イベント。
  読取不可は同じ車種をまとめるため、別車両の通知がまとまる場合がある。車両追跡の代替ではない。
- 判定はOCRの候補値。複数フレームの確定判定・車種モデル名識別は未実装。

### 映像

入力フレームから5FPS・幅最大960pxでサンプリングし、検知前3秒＋検知後5秒を記録する。
記録元は入力映像で、検出枠を焼き込まない。音声は保存しない。推論の遅延が23秒の保持範囲を超える場合や
入力開始直後には前方映像が不足する。EOF・停止時は保存可能な範囲を短縮保存し、画面に明示する。
静止画像入力はJPEGで保存する。

FFmpegがあればH.264 MP4へ変換する（Dockerイメージには同梱）。変換失敗・未導入時はAVIを残しダウンロードする。
同時録画は最大16イベント。上限超過や書込失敗は通知履歴に保存失敗として残す。
1入力の処理中に短時間で多数のイベントが出ると、映像保存・変換の負荷で処理が遅くなる。

ローカルの `events/` に先に保存する。S3設定時は確定済み映像を追加転送し、失敗時は最大5回まで再試行する。
S3保存成功後もローカルは削除しない。Web画面からはローカル映像を認証付きで配信する。
S3はアーカイブであり、ローカル消失時の自動復元・S3ストリーミングは未実装。

### 通知

画面：最新通知一覧、未確認件数、確認済み操作、映像リンク。約1.5秒間隔で更新。
メール：SES送信要求の成功を「SES受付済み」と表示する。これは受信者への到達確認ではない。
バウンス・苦情はSES側で監視する。未設定の場合も画面通知・映像保存は動作する。
メールは検知イベント作成後に送信するため、映像の確定を待たない。
障害時は指数バックオフで最大5回まで再試行。失敗・未設定分は画面から再送可能。
送信直後のクラッシュで重複メールが発生しうる（at-least-once）。イベントIDで判別する。

## バックアップ・障害復旧

SQLite稼働中のDBファイルだけを単純コピーしない。Python `sqlite3.Connection.backup()` による整合バックアップ、
または処理を停止してからDBと映像を保存する。S3転送は映像だけで、登録データ・通知DBのバックアップではない。
保存期間・ディスク容量は運用側で管理する。worker.logにカメラ認証情報が含まれる場合があるためアクセスを限定する。
強制終了で確定できなかった録画は再起動時に失敗へ更新する。ジョブの自動再開はしない。

## 公式資料

- https://docs.aws.amazon.com/ses/latest/dg/request-production-access.html
- https://docs.aws.amazon.com/boto3/latest/reference/services/ses/client/send_email.html
- https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/configuring-instance-metadata-service.html
- https://sqlite.org/wal.html
