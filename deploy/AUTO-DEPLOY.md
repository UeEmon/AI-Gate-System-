# AWS自動デプロイ（既存EC2へのアプリ配布）

GitHub Actionsの `Deploy AI Gate to AWS` がmainへのpushまたは手動実行で、
テスト→OIDC認証→ソースを非公開S3へ保存→SSM→Dockerビルド→Compose起動確認を実施します。
EC2/VPC/DNS/証明書の初期構築を自動実行するものではありません。
従来のローカルcloudformation-ec2.yamlは未完成のため使用しないでください。

## AWSの初期設定

1. Linux x86_64 EC2を1台用意（既存アプリが動いている場合は事前に停止・バックアップ）。
   Docker Engine、Compose v2（`up --wait` 対応）、AWS CLI v2、SSM Agent、tar、flockを導入。
   EC2をSystems Managerの管理対象としてOnlineにする。
2. 暗号化EBSを **/srv/ai-gate** に永続マウント。dataだけのマウントではデプロイを停止します。
   data、models、Caddy設定・証明書はこの配下。稼働中のSQLiteはbackup APIまたは停止してバックアップ。
3. EC2ロールにAmazonSSMManagedInstanceCoreと、配布用バケットの
   `arn:aws:s3:::BUCKET/releases/*` に対する `s3:GetObject` を付与。
   アプリのS3/SES権限は別途deploy/iam-policy.example.jsonに従って必要な範囲だけ付与。
4. 同じリージョンに非公開の配布用S3バケットを作成。公開ブロック、暗号化、バージョニングを有効にする。
   この実装はSSE-S3を指定します。KMS必須のバケットはコードとIAM調整が必要です。
5. EC2の `/etc/ai-gate/production.env` にdeploy/env.exampleを基に設定。
   所有者root、モード600。管理パスワード、DNS名、任意のSES/S3を記入。
   GitHubやSSMコマンドの引数へパスワードを入れません。
6. DNSをEC2へ向ける。Caddyの公開証明書用HTTP-01を使う場合は80番への到達性が必要。
   443は管理端末の送信元へ制限し、8080/22の公開は不要。
   EC2からS3、SSM、パッケージ配布先へのHTTPS接続も必要です。

## GitHub OIDCと権限

IAMに `https://token.actions.githubusercontent.com`（audience: `sts.amazonaws.com`）を登録。
`github-deploy-trust.example.json` のACCOUNT_IDとEXACT_GITHUB_OIDC_SUBを実環境に置換。
subはaws-production環境に完全一致させ、ワイルドカードにしないでください。
従来形式は `repo:UeEmon/AI-Gate-System-:environment:aws-production`。
GitHubのimmutable subject形式を使用するリポジトリではowner/repository IDも含むため、
実際の設定を確認してください。例の文字列をそのまま適用しないでください。

デプロイロールに `github-deploy-policy.example.json` を適用します。
ACCOUNT_ID、REGION、BUCKET、INSTANCE_IDを置換し、対象インスタンス1台に限定します。
SSM Run Commandは当該EC2上でrootのコマンドを実行する権限です。
mainの変更権限とGitHub Environmentの対象ブランチ制限を管理してください。

GitHub Settings → Environmentsで `aws-production` を作り、deployment branchesをmainだけに制限。
Settings → Secrets and variables → Actions → Variables に以下を設定します。

| Repository variable | 値 |
|---|---|
| AWS_DEPLOY_ENABLED | 全準備完了後に `true`（未設定はスキップ） |
| AWS_REGION | 例: ap-northeast-1 |
| AWS_DEPLOY_ROLE_ARN | 上記OIDCデプロイロールARN |
| AWS_DEPLOY_BUCKET | 配布用バケット名 |
| AWS_INSTANCE_ID | 対象EC2 ID |

最初はActions → Deploy AI Gate to AWS → Run workflow（main）で実行。
以後main更新で自動実行。AWSアクセスキーをGitHubに登録する必要はありません。

## 確認と復旧

Composeのプロジェクト名はai-gate固定。初回移行時、別のComposeプロジェクトのコンテナが
同じDBやポートを使用していないことを確認してください。
コミットごとのイメージ・リリースを保持し、起動確認に失敗した場合は以前のリリースへ戻します。
初回の失敗時は新コンテナを停止し、Actionsを失敗にします。
DBの巻き戻しやスキーマ移行の復旧はしません。破壊的DB変更は別途移行計画が必要です。
再起動中の認識ジョブは自動再開されません。短いサービス停止が発生します。
同時実行はGitHub concurrencyとEC2上のflockで防止します。

ヘルスチェックが確認するのはWebサーバーです。TLSの外部到達性、実カメラ認識、
YOLO/EasyOCRの初回モデル取得、実メール送信は導入後の運用試験で確認してください。
旧イメージ・リリース・一時配布ファイルは自動削除しないため、容量監視と保持運用が必要です。
SSMタイムアウト・通信断・強制終了時はAWSコンソールでコマンド結果と実コンテナ状態を確認。

公式資料:
- https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws
- https://docs.aws.amazon.com/cli/latest/reference/ssm/send-command.html
