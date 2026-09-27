# AI GATE SYSTEM 使用モデル

Web画面とAPIにモデル選択機能はありません。保存済みの旧モデル設定も読み込まれません。プレート処理は環境変数でLipla-jp（既定）または追加学習した専用検出器＋PaddleOCRに切り替えます。

| 用途 | モデル | 備考 |
|---|---|---|
| 車両検出 | YOLO26n | Ultralytics。コードはAGPL-3.0またはEnterprise。重みの条件は別途確認 |
| 日本のプレート検出・四隅補正・OCR | Lipla-jp | EdgeCrafter PoseとPPOCRv6を内蔵。ライブラリはMIT、重みの条件は別途確認 |
| 日本のプレート検出（任意） | 追加学習した1クラスYOLO | 実データにLipla-jpの検出枠を付けて専用学習。運用前に別映像で検証 |
| 日本のプレートOCR（任意） | 追加学習PP-OCRv5_mobile_rec | 修正した正解を優先。未修正の高確度結果は学習用のみ。PaddleOCRのコードはApache-2.0、重みの条件は別途確認 |

画像から車両を検出した領域だけをLipla-jpまたはPaddle方式へ渡します。Paddle方式の学習は実データと学習環境が揃えばバックエンドで開始できます。学習済み重みの運用への適用には、評価と明示的な配置・設定が必要です。過去の学習成果物は保持します。

- https://github.com/ultralytics/ultralytics
- https://github.com/ikeboo/Lipla-jp
- https://github.com/PaddlePaddle/PaddleOCR
