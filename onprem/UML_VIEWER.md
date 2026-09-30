# UML図の閲覧環境（Mac Docker Desktop）

AI Gate System の実装から作成した [UML図](../docs/UML.md) を、別の Compose プロジェクト `ai-gate-uml` で閲覧できます。オープンソースの [Kroki](https://github.com/yuzutech/kroki) が PlantUML を描画し、Kroki Mermaid companion が Markdown 内の Mermaid 図を描画します。UML閲覧用の小さな Python Webサーバーが図を一覧表示し、SVGと元テキストを参照できるようにします。

リポジトリのルートで実行します。運用用の `.env` やモデルは不要です。

```sh
docker compose -f onprem/compose.uml.yaml up -d --build
docker compose -f onprem/compose.uml.yaml ps
```

ブラウザで **http://127.0.0.1:8890/** を開きます。図を切り替えて閲覧し、「ソースを見る」で定義を確認、「SVGを保存」で図を取得できます。ポート8890を使用中の場合は `GATE_UML_PORT=8891 docker compose -f onprem/compose.uml.yaml up -d --build` として、8891にアクセスします。閲覧サーバーは `127.0.0.1` のみに公開し、Kroki のポートはホストに公開しません。

更新した [docs/UML.md](../docs/UML.md) と [docs/deployment.puml](../docs/deployment.puml) は読み取り専用でマウントされ、ページを再読込すると反映されます。閲覧コンテナから元ファイルは編集できません。Kroki のレンダリング先はローカルのコンテナだけです。

描画に失敗した場合は次を確認します。

```sh
docker compose -f onprem/compose.uml.yaml logs --tail=100 viewer kroki kroki-mermaid
```

停止・削除は閲覧環境だけに作用します。

```sh
docker compose -f onprem/compose.uml.yaml down
```

`onprem/deploy-local.sh` による運用用 Compose とは別プロジェクトです。閲覧環境の起動や停止によって運用の `gate-data`・`gate-models` を削除しません。イメージの初回取得と Mermaid 用Chromiumのため、Mac Docker Desktop のディスク・メモリ容量を確認してください。Compose 設定は `onprem/compose.uml.yaml` です。
