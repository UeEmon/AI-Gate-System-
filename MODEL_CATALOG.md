# AI GATE SYSTEM 使用モデル

Web画面とAPIにモデル選択機能はありません。保存済みの旧モデル設定も読み込まれません。

| 用途 | 固定モデル | 備考 |
|---|---|---|
| 車両検出 | YOLO26n | Ultralytics。コードはAGPL-3.0またはEnterprise。重みの条件は別途確認 |
| 日本のプレート検出・四隅補正・OCR | Lipla-jp | EdgeCrafter PoseとPPOCRv6を内蔵。ライブラリはMIT、重みの条件は別途確認 |

画像から車両を検出した領域だけをLipla-jpへ渡します。確認済みの正解画像は保存できますが、追加学習モデルの自動生成・適用は提供していません。過去の学習成果物は保持します。

- https://github.com/ultralytics/ultralytics
- https://github.com/ikeboo/Lipla-jp
