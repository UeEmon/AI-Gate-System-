# AI Gate System 現行実装のUML

現行のPythonコード、Web画面、`onprem/compose.yaml` をリバースエンジニアリングして作成した図です。図の**出力形式はPlantUML (`.puml`) に統一**しています。UMLコンテナで図の表示、ソース確認、SVG保存ができます。

| 図 | PlantUMLソース | 調査元 |
|---|---|---|
| 主要クラスと依存関係 | [classes.puml](classes.puml) | `web.py`, `app.py`, `model_service.py`, `lipla_pipeline.py` |
| 車両検出から通知まで | [recognition.puml](recognition.puml) | `app.py`, `events.py`, `evidence.py` |
| OCR学習・評価 | [learning.puml](learning.puml) | `ocr_learning.py`, `paddle_auto_worker.py`, `paddle_auto_train.py` |
| 認識ジョブの状態 | [job-state.puml](job-state.puml) | `web.py:JobManager` |
| 保存データの論理関係 | [data-model.puml](data-model.puml) | `web.py`, `app.py`, `events.py`, `ocr_learning.py` のテーブル定義 |
| Dockerコンテナ構成 | [containers.puml](containers.puml) | `onprem/compose.yaml` |
| UML配置図 | [deployment.puml](deployment.puml) | `onprem/compose.yaml` |

UMLコンテナはさらにPyreverseを使い、マウントしたPythonコードから次の図を起動時以降に自動生成します。図を選択すると、ソースが更新されていれば再生成します。画面上の「自動図を再生成」でも更新できます。

- `core`: `app.py`, `web.py`, `model_service.py`, Liplaの経路、通知・映像保存
- `services`: `aigate/` 内のサービスと契約
- `learning`: OCR学習とPaddleOCR学習管理

各領域の**クラス図とパッケージ図**が生成され、すべてPlantUMLソースとして表示・SVG保存できます。Pythonソースは読み取り専用でマウントし、生成物はコンテナの一時領域に保存します。`pyreverse` はクラスやパッケージの静的解析であり、プロセス間通信や実行順序までは自動で再現しません。シーケンス図・状態図・配置図はコードを確認して作成しています。

`main` にコード変更をコミットすると、GitHub Actions の `Update UML from main` がそのコミットを起点にPyreverseを実行し、PlantUMLソースを `docs/generated/` に更新します。生成物に差分がある場合だけ、Actionsが `docs: update UML from main code` という追コミットを作成します。`docs/generated/` の更新ではワークフローを再実行しないため、GitHubを常時監視する必要はありません。UMLコンテナの画面には、コミット生成図と起動時に生成する一時図の両方が表示されます。

運用OCRはLipla-jpだけを使用します。Lipla内部のプレート検出・補正・OCRは一回の呼び出しで、その合計時間を測ります。PaddleOCR学習・評価は別コンテナの処理で、運用モデルを自動的に置き換えません。信頼度98%以上のLipla仮ラベルを含む評価は、人手で独立に確認した正解率を示すものではありません。データ図の点線は識別子または保存時スナップショットによる論理関係であり、SQLiteの外部キー制約を意味しません。

Mac Docker Desktopの起動方法は [UMLコンテナの手順](../onprem/UML_VIEWER.md) を参照してください。図は静的なソース調査結果で、実カメラ・Dockerでの性能計測を示していません。実装を変更したときは、手作成図の呼び出し関係も見直してください。
