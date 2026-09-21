# AI GATE SYSTEM モデルカタログ

最終確認日: 2026-09-21

「利用可能な全モデル」は新規公開や派生重みまで含めると境界が定まらないため、本システムでは公開ソースとライセンスを確認できる主要なオープンソース系統を登録し、日本のナンバープレートで実行可能なアダプターがあるモデルだけを選択可能にします。未導入モデルも候補から消さず、必要な依存関係と利用不可理由を表示します。学習済み重みのライセンスはフレームワークとは別に確認します。

## 車両・物体検出

| 系統 | 状態 | 用途 | ライセンス上の注意 |
|---|---|---|---|
| Ultralytics YOLO26 / YOLO11 / YOLOv8 | 導入済み | リアルタイム車両検出 | AGPL-3.0またはEnterprise |
| RT-DETR / RT-DETRv2 | 候補登録 | 高精度リアルタイム検出 | Apache-2.0 |
| MMDetection | 候補登録 | 多数の検出モデル比較 | Apache-2.0、重みは別途確認 |
| YOLOX | 候補登録 | 軽量リアルタイム検出 | Apache-2.0 |
| Detectron2 | 候補登録 | Faster/Mask R-CNN等 | Apache-2.0、重みは別途確認 |

## ナンバープレート検出

| 系統 | 状態 | 用途 |
|---|---|---|
| OpenCV輪郭抽出 | 導入済み | 専用重みがない場合のフォールバック |
| 専用YOLOプレートモデル | 重み指定時に利用可能 | 日本プレート用に追加学習した一クラス検出 |
| PaddleOCR text detector | 導入済み候補 | 自然画像中の文字領域検出 |

## OCR

| 系統 | 日本語 | 状態 | 主用途 |
|---|---:|---|---|
| PaddleOCR PP-OCRv6 / PP-OCRv5 | 対応 | 導入済み | 既定の高精度候補 |
| EasyOCR | 対応 | 導入済み | 比較・追加学習 |
| Tesseract LSTM | 対応重みあり | 候補登録 | CPU比較 |
| RapidOCR | 対応モデルあり | 候補登録 | ONNX高速推論 |
| MMOCR | モデル依存 | 候補登録 | OCRモデル群の比較 |
| docTR | 主に文書向け | 候補登録 | 比較対象 |
| TrOCR | 標準重みは日本語非対応 | 候補登録 | 専用学習時の比較対象 |

## 一次情報

- Ultralytics: https://github.com/ultralytics/ultralytics
- RT-DETR: https://github.com/lyuwenyu/RT-DETR
- MMDetection: https://github.com/open-mmlab/mmdetection
- YOLOX: https://github.com/Megvii-BaseDetection/YOLOX
- Detectron2: https://github.com/facebookresearch/detectron2
- PaddleOCR: https://github.com/PaddlePaddle/PaddleOCR
- EasyOCR: https://github.com/JaidedAI/EasyOCR
- Tesseract: https://github.com/tesseract-ocr/tesseract
- RapidOCR: https://github.com/RapidAI/RapidOCR
- MMOCR: https://github.com/open-mmlab/mmocr
- docTR: https://github.com/mindee/doctr
- TrOCR: https://github.com/microsoft/unilm/tree/master/trocr

Web画面のモデル一覧は `aigate/model_registry.py` を正本とします。新規モデルは推論アダプター、ライセンス、固定評価データによる完全一致率・CER・速度の検証後に選択可能とします。
