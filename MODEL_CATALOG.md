# AI GATE SYSTEM 使用モデル

Web画面とAPIにモデル選択機能はありません。保存済みの旧モデル設定も読み込まれません。運用のプレート検出とOCRはLipla-jpに固定しています。

| 用途 | モデル | 備考 |
|---|---|---|
| 車両検出 | YOLO26n | Ultralytics。コードはAGPL-3.0またはEnterprise。重みの条件は別途確認 |
| 日本のプレート検出・四隅補正・OCR | Lipla-jp | EdgeCrafter PoseとPPOCRv6を内蔵。ライブラリはMIT、重みの条件は別途確認 |
| 学習・評価専用のプレート検出 | 追加学習した1クラスYOLO | 実データにLipla-jpの検出枠を付けて専用学習。運用モデルには接続しない |
| 学習・評価専用のプレートOCR | 追加学習PP-OCRv5_mobile_rec | 修正した正解を優先。未修正の高確度結果は学習用のみ。PaddleOCRのコードはApache-2.0、重みの条件は別途確認 |

画像から車両を検出した領域だけをLipla-jpに渡します。Paddle方式の学習と評価は、実データと学習環境が揃えば別のバックエンドで実行できます。学習済み重みは運用モデルに適用しません。過去の学習成果物は保持します。

- https://github.com/ultralytics/ultralytics
- https://github.com/ikeboo/Lipla-jp
- https://github.com/PaddlePaddle/PaddleOCR
