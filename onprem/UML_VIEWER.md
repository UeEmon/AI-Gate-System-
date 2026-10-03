# UMLコンテナ（Mac Docker Desktop）

AI Gate System の [UML一覧](../docs/UML.md)をローカルの独立したComposeプロジェクト `ai-gate-uml` で閲覧します。Docker Desktopには**`ai-gate-uml` というUMLコンテナ**が表示されます。コンテナ内のオープンソース [Pyreverse](https://pylint.readthedocs.io/en/stable/additional_tools/pyreverse/) がPythonコードからクラス図とパッケージ図をPlantUML形式で生成し、[PlantUML](https://plantuml.com/) のJava版とGraphvizでSVGを描画します。手作成のUMLもPlantUML形式です。Intel MacとApple Siliconに対応するJava実行環境を使用し、AVX2を要求するネイティブPlantUML実行ファイルは使用しません。

リポジトリのルートで実行します。運用用の `.env` やモデルは不要です。

```sh
git pull
docker compose -f onprem/compose.uml.yaml up -d --build --remove-orphans
docker compose -f onprem/compose.uml.yaml ps
```

ブラウザで **http://127.0.0.1:8890/** を開き、図を選びます。「ソースを見る」でPlantUMLソースを確認、「SVGを保存」で図を保存できます。「自動図を再生成」でコードから図を作り直せます。ポート8890が使用中なら、`GATE_UML_PORT=8891 docker compose -f onprem/compose.uml.yaml up -d --build --remove-orphans` を実行し、8891にアクセスしてください。`--remove-orphans` は旧版の同プロジェクトの `viewer` と `kroki` コンテナを整理します。

`docs/*.puml` と解析対象のPythonファイルは読み取り専用でマウントし、生成物はUMLコンテナの一時領域に保存します。「PlantUMLを保存」で `.puml` ファイルも取得できます。Pyreverseの自動図は、マウントされたコードの更新時に再生成されます。`git pull` 等で個別のPythonファイルが置換されたときは `docker compose -f onprem/compose.uml.yaml up -d --force-recreate uml` でファイルのマウントを更新してください。閲覧画面はMacの `127.0.0.1` のみに公開されます。PlantUMLはSANDBOX設定で動作し、描画時のJavaヒープ上限は256MB、同時描画は1件です。

mainへのpush時はGitHub Actionsがクラス図・パッケージ図を `docs/generated/` に自動更新し、差分がある場合だけ追コミットを作成します。GitHubの定期監視は行いません。`git pull` 後、画面の「コミット生成」からそのPlantUMLソースと図を閲覧できます。シーケンス図など手作成の図は実装変更時に合わせて更新します。

描画や生成に失敗した場合:

```sh
docker compose -f onprem/compose.uml.yaml logs --tail=100 uml
```

停止はUML用Composeだけに作用します。

```sh
docker compose -f onprem/compose.uml.yaml down
```

`onprem/deploy-local.sh` の運用プロジェクトとは分離しており、UMLコンテナの停止で `gate-data` や `gate-models` は削除されません。初回ビルドではJava、Graphviz、日本語フォント、PlantUML JARを取得します。PlantUML 1.2026.8の公式JARはSHA-256を検証して配置します。
