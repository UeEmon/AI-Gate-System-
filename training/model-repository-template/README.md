# AI-Gate-JP-Models

AI GATE SYSTEMで確認済みの日本のナンバープレート実データから追加学習した、再利用可能なOCRモデルを管理するリポジトリです。

このリポジトリには、推論に必要なモデル本体、プレート設定、評価結果、ライセンス、SHA-256チェックサムを登録します。実画像、登録車両情報、履歴、個人情報は原則として登録しません。教師データは非公開のAI-Gate-System側に保持します。

## リポジトリ構成

```text
models/
└── fast-plate-ocr-jp/
    └── 1.0.0/
        ├── model.onnx
        ├── plate_config.yaml
        ├── manifest.json
        └── evaluation.json
scripts/
└── validate_model_package.py
```

各モデルは変更せず、バージョンディレクトリを追加して管理します。`manifest.json`のチェックサムと評価結果を確認してから、利用側プロジェクトへ導入してください。

## AI GATE SYSTEMへの導入

```bash
export GATE_OCR_BACKEND=fastalpr
export GATE_FAST_OCR_MODEL_PATH="$PWD/models/fast-plate-ocr-jp/1.0.0/model.onnx"
export GATE_FAST_OCR_CONFIG_PATH="$PWD/models/fast-plate-ocr-jp/1.0.0/plate_config.yaml"
```

FastPlateOCR公式の`LicensePlateRecognizer`が要求するONNXモデルと対応設定ファイルの組み合わせを保持します。設定ファイルはモデルと同じバージョンのものを使用してください。

## 検証

```bash
python scripts/validate_model_package.py models/fast-plate-ocr-jp/1.0.0
```

## 公開方針

実データを用いたモデルであるため、当面はGitHub Private Repositoryで管理します。公開する場合は、学習画像を含めず、第三者ライセンス、モデルの評価範囲、利用制限を確認してから公開します。

## ライセンス

追加学習モデル固有のライセンスは未確定です。FastPlateOCR等の基盤ライセンスと、学習データの利用許諾を分離して管理してください。
