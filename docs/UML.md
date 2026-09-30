# AI Gate System 現行実装のUML

2026年9月30日時点の `main` の Python、Web画面、`onprem/compose.yaml` をリバースエンジニアリングして作成した図です。図の矢印はコード上の呼び出し・保持関係を表します。図中の「学習」は運用の推論経路から独立しています。

## 1. 主要クラスと依存関係（クラス図）

```mermaid
classDiagram
direction LR
class JobManager {
  +start(kind, source) job_id
  +stop(job_id) bool
  +list_jobs() list
}
class ModelServiceProcess {
  +address
  +client
  +close()
}
class ModelClient {
  +predict(frame) result
  +read_plate(crop, threshold) candidates
}
class ModelServer {
  +reload(config)
  +handle(operation, payload)
}
class LiplaPlatePipeline {
  +run(vehicle_crop) candidates
}
class ConsecutivePlateBest {
  +select(records) winners
}
class Dispatcher {
  +start()
  +tick()
  +stop()
}
class EvidenceRecorder {
  +feed(frame, seconds)
  +trigger(alert_id, seconds, frame)
}
class PaddleTrainingManager {
  +maybe_start()
  +maybe_compare()
  +status()
}
class PerformanceManager {
  +startup_benchmark()
  +record()
  +summary()
}
class StreamReceiver {
  +settings()
  +status()
  +update(publish, rtmps)
}
JobManager --> ModelServiceProcess : inference socket
ModelServiceProcess --> ModelServer : starts
ModelClient --> ModelServer : local IPC
ModelServer --> LiplaPlatePipeline : recognizes vehicle crop
JobManager --> PerformanceManager : records progress
```

`JobManager` が `app.py` を子プロセスとして起動し、子プロセスの `ModelClient` が常駐 `ModelServer` とローカルソケットで通信します。`PaddleTrainingManager` は保存済みの Lipla 認識結果を読み、Lipla の実行クラスを直接呼び出しません。`Dispatcher` と `EvidenceRecorder` は `alerts` テーブルを介して連動します。これらの処理順序を次の図に示します。

## 2. 入力から通知まで（シーケンス図）

```mermaid
sequenceDiagram
actor User as 利用者
participant UI as Web画面
participant Web as web.py / JobManager
participant Worker as app.py 子プロセス
participant Model as ModelClient / ModelServer
participant DB as gate.db
participant Recorder as EvidenceRecorder
participant Dispatcher as events.Dispatcher
User->>UI: 入力源を選択して開始
UI->>Web: POST /api/jobs
Web->>Worker: app.py を起動
loop 取得した処理対象フレーム
  Worker->>Model: predict(frame) 車両検出
  alt 車両を検出
    loop 車両の切り抜きごと
      Worker->>Model: read_plate(vehicle crop)
      Model-->>Worker: Lipla 検出・補正・OCR結果
    end
    Worker->>Worker: ConsecutivePlateBest で連続フレームを整理
    Worker->>DB: 観測記録・OCR学習候補を保存
    Worker->>DB: 登録車両と照合し必要なら alert 作成
    opt alert を作成した場合
      Worker->>Recorder: trigger(alert_id, frame)
      Recorder->>DB: 保存映像の状態を更新
    end
  end
  Worker->>Web: preview.jpg と progress.json を更新
end
Dispatcher->>DB: 保存済み alert を定期確認
opt 送信設定がある場合
  Dispatcher->>Dispatcher: 通知配信・状態更新
end
UI->>Web: GET /api/observations と /api/alerts
Web-->>UI: 認識履歴と通知
```

運用OCRは `lipla_pipeline.py` の Lipla-jp ネイティブ処理です。専用検出器と PaddleOCR を組み合わせるコードは検証・学習用に残っていますが、この運用シーケンスのモデルには含めません。連続フレームで同じプレートの場合、最も信頼度の高い記録が残るよう既存記録を更新します。通知イベントの配信は設定に応じた別処理で、Web画面は保存された `alerts` を読みます。Lipla 内部の検出・補正・OCRは一回の呼び出しで実行するため、計測できるのはその合計時間です。

## 3. OCR学習と評価（シーケンス図）

```mermaid
sequenceDiagram
participant Worker as 認識ワーカー
participant Learning as ocr_learning.py
participant DB as gate.db / 保存画像
participant Trainer as trainer コンテナ
participant Export as paddle_training.py
participant FineTune as paddle_finetune.py
participant UI as OCR学習画面
Worker->>Learning: queue_observation(Lipla結果)
Learning->>DB: 信頼度50%以上を候補化
opt 信頼度80%以上かつ画像切り出し可能
  Learning->>DB: ocr_samples に自動保存
end
opt 手動修正
  UI->>Learning: 正解4項目と画像範囲を保存
  Learning->>DB: 同一番号の採用データを更新
end
loop trainer の定期確認
  Trainer->>Learning: dataset_snapshot()
  Learning-->>Trainer: 学習・検証・テスト配分
  opt 各群の条件と事前学習重みが揃った場合
    Trainer->>Export: export() スナップショット作成
    Export->>DB: 画像とラベルを読む
    Trainer->>FineTune: 検出器・PaddleOCRを学習
    FineTune-->>Trainer: 重み・ログ
    Trainer->>Trainer: Liplaとの比較を実行
    UI->>Trainer: 学習状態・比較結果を取得
  end
end
```

信頼度98%以上の Lipla の仮ラベルまたは手動の正解が検証・テスト対象になります。配分は `dataset_snapshot()` が対象全体の番号を約1対1に振り分けます。学習の結果は比較画面へ出ますが、運用モデルを自動で置き換えません。Lipla の自動ラベルでの比較は、人手で独立に確認した正解率を示すものではありません。

## 4. ジョブ状態（状態図）

```mermaid
stateDiagram-v2
  [*] --> starting : POST /api/jobs
  starting --> running : 子プロセス起動
  starting --> failed : 起動失敗
  running --> completed : 処理終了 code 0
  running --> failed : 処理終了 code != 0
  running --> stopping : 停止要求
  stopping --> stopped : 子プロセス終了
  starting --> interrupted : Web再起動
  running --> interrupted : Web再起動
  stopping --> interrupted : Web再起動
  completed --> [*]
  failed --> [*]
  stopped --> [*]
  interrupted --> [*]
```

起動時にDB内に残っている `starting`・`running`・`stopping` は `interrupted` に更新されます。これはジョブの保存状態の図であり、学習ジョブの状態とは別です。

## 5. 保存データ（クラス図として表現したテーブル関係）

```mermaid
classDiagram
direction LR
class jobs {
  <<SQLite table>>
  id PK
  status
  kind
}
class observations {
  <<SQLite table>>
  id PK
  run_id
  details_json
  image_path
}
class vehicles {
  <<SQLite table>>
  id PK
  plate_key UNIQUE
}
class alerts {
  <<SQLite table>>
  id PK
  observation_id
  run_id
  registry_json
}
class ocr_samples {
  <<SQLite table>>
  id PK
  observation_id
  plate_key UNIQUE
  source
  ocr_confidence
}
class ocr_sample_fields {
  <<SQLite table>>
  sample_id PK
  fields_json
}
class ocr_auto_candidates {
  <<SQLite table>>
  observation_id PK_part
  candidate_index PK_part
  status
}
class ocr_auto_archive {
  <<SQLite table>>
  observation_id PK
  details_json
}
class ocr_plate_partitions {
  <<SQLite table>>
  plate_key PK
  partition
}
jobs "1" ..> "0..*" observations : run_id
observations "1" ..> "0..*" alerts : observation_id
vehicles "0..1" ..> "0..*" alerts : registry snapshot
observations "0..1" ..> "0..*" ocr_samples : observation_id
ocr_samples "1" --> "0..1" ocr_sample_fields : sample_id
observations "0..1" ..> "0..*" ocr_auto_candidates : observation_id
ocr_auto_archive "0..1" ..> "0..*" ocr_samples : archived observation
ocr_plate_partitions "0..1" ..> "0..1" ocr_samples : plate_key
```

点線は識別子または保存時スナップショットによる**論理的な関連**です。SQLite 外部キー制約を表していません。履歴は最新100観測を保持し、必要な学習画像は別途アーカイブします。車両登録 (`vehicles`) とOCR教師データ (`ocr_samples`) は独立しています。

## 6. 配置構成図（補助図）

```mermaid
flowchart TB
  camera["GoPro等のRTMP/RTMPS配信"] --> ingest["rtmp-ingest : MediaMTX / 1935・1936"]
  browser["Webブラウザ"] --> gate["gate : Flask/Waitress / 8080"]
  ingest --> gate
  subgraph gateContainer["gate コンテナ"]
    gate --> worker["app.py ワーカー"]
    worker --> model["常駐ModelServer : YOLO26n + Lipla-jp"]
    gate --> dispatch["通知Dispatcher"]
  end
  init["stream-init : 初期設定して終了"] --> ingest
  gate --> data[("gate-data / gate.db・画像・学習状態")]
  trainer["trainer : PaddleOCR追加学習"] --> data
  model --> models[("gate-models / モデルキャッシュ")]
  trainer --> models
  ingest --> config[("stream-config / MediaMTX設定")]
  gate --> config
```

`onprem/compose.yaml` の起動構成は `gate`、`trainer`、`rtmp-ingest`、初期化後に終了する `stream-init` です。`trainer` は `linux/amd64` 指定で、読み取り専用のローカル `PaddleOCR` ソースもマウントします。ブラウザ側のページはライブ監視、RTMP設定、通知、登録・確認、OCR学習、履歴、システム性能に分かれています。図のポート表記はコンテナ側の値で、ホスト側の公開アドレスは環境変数に従います。

## 調査元と更新方法

- `web.py`: API、ジョブ起動、ModelServiceProcess、通知Dispatcher
- `app.py`、`model_service.py`、`lipla_pipeline.py`: 推論経路と常駐モデル
- `events.py`、`evidence.py`: 登録照合、通知保存、映像保存
- `ocr_learning.py`、`paddle_auto_worker.py`、`paddle_auto_train.py`、`paddle_training.py`: 教師データと学習
- `onprem/compose.yaml`、`templates/index.html`: コンテナと画面

UMLの元データはこのMarkdown内の Mermaid です。GitHub上で図として表示でき、ソースを編集して実装変更に追随できます。配置図の標準的なUML表記は [deployment.puml](deployment.puml) に保存しています。最初に `web.py:create_app` と `app.py:main` の呼び出し先を再確認し、次にテーブル定義と Compose のサービス定義を更新してください。リバースエンジニアリングによる静的な図であり、コンテナを起動して計測した結果ではありません。
