# 第三者ソフト・モデルと配布条件

更新日：2026-09-25。これは主要構成の整理であり、全バイナリのライセンス監査済み証明ではありません。
本体ライセンスは `LICENSE-PROPOSAL.md` の採用待ちです。
今回のZIPは自作ソース・設定・文書・参照ライセンス文だけを含み、Python、wheel、Dockerイメージ、FFmpeg、モデル重みを含みません。

| 対象 | ライセンス・確認先 | 配布で扱う事項 |
|---|---|---|
| Ultralytics / YOLOモデル | [AGPL-3.0またはEnterprise](https://www.ultralytics.com/license) | AGPL案では対応するソースとライセンス。非公開組込みを希望する場合はEnterprise条件を確認 |
| EasyOCR | [Apache-2.0](https://github.com/JaidedAI/EasyOCR/blob/master/LICENSE) | ライセンス・著作権・NOTICEを保持。OCRモデルも実際に取得した配布元を記録 |
| PaddleOCR / PaddlePaddle | [Apache-2.0](https://github.com/PaddlePaddle/PaddleOCR/blob/main/LICENSE) | ライセンス・NOTICEを保持。取得したPP-OCRモデル名・版・SHA-256を記録 |
| Lipla-jp | [MIT](https://github.com/ikeboo/Lipla-jp/blob/main/LICENSE) | EdgeCrafter Pose・PPOCRv6を含む日本ナンバープレート検出・認識ライブラリ。採用版とモデル重みの取得元・SHA-256を記録 |
| lprs-jp | [LICENSE未確認](https://github.com/eepj/lprs-jp) | READMEは研究目的と記載。利用許諾を確認できないためレジストリから削除。コード・重みは本システムへ取り込まない |
| dyama/alpr_jp | [MIT](https://github.com/dyama/alpr_jp/blob/master/LICENSE) | OpenALPR/OpenCV/Tesseract向け日本プレート学習素材。READMEは収録画像もMIT配布と明記。実行モデルではないため選択一覧から削除。OpenALPR本体の条件とは別 |
| FastALPR / fast-plate-ocr | [MIT](https://github.com/ankandrew/fast-alpr/blob/master/LICENSE) / [MIT](https://github.com/ankandrew/fast-plate-ocr/blob/master/LICENSE) | ONNX検出・OCR基盤。標準重みは日本向け未評価。追加学習済み重みと設定を別管理 |
| OpenCV | [Apache-2.0](https://github.com/opencv/opencv/blob/4.x/LICENSE) | wheel内に含む第三者ライブラリの条件も確認 |
| opencv-pythonパッケージ | [配布ライセンス](https://github.com/opencv/opencv-python/blob/master/LICENSE.txt) | パッケージと同梱バイナリは同一条件とは限らない |
| NumPy | [BSD-3-Clause等](https://github.com/numpy/numpy/blob/main/LICENSE.txt) | 同梱BLASなどの表示も保持 |
| PyTorch / torchvision | [PyTorch](https://github.com/pytorch/pytorch/blob/main/LICENSE) / [torchvision](https://github.com/pytorch/vision/blob/main/LICENSE) | BSD系の本体に加え、同梱第三者コードの表示を保持 |
| Flask / Waitress | [Flask](https://github.com/pallets/flask/blob/main/LICENSE.txt) / [Waitress](https://github.com/Pylons/waitress/blob/main/LICENSE.txt) | BSD系/ZPL系。採用バージョンの全文を保持 |
| boto3 / botocore | [boto3](https://github.com/boto/boto3/blob/develop/LICENSE) | Apache-2.0。AWS使用時のサービス契約とは別 |
| Python / Debian | [Python](https://docs.python.org/3/license.html) / 各Debianパッケージのcopyright | 実行環境を配布する場合に収集 |
| FFmpeg / x264 | [FFmpeg法務案内](https://ffmpeg.org/legal.html) | ビルド構成によりLGPL/GPLが変わる。libx264有効ビルド等の対応ソース提供義務を確認 |
| Caddy（AWS構成） | [Apache-2.0](https://github.com/caddyserver/caddy/blob/master/LICENSE) | 同梱する場合にライセンス・NOTICEを保持 |

## リリースに含める記録

1. 専用の仮想環境へ導入し、`scripts/dependency_inventory.py` で全インストール版とライセンスファイルを収集する。
2. `data/dependencies/installed-versions.txt` と `inventory.json` を採用版記録にする。これは再現確認済みロックファイルではない。
3. ライセンス欄がUNKNOWN・空欄、またはライセンスファイルがない依存先を配布元で確認する。
4. Docker/OSバイナリを配布する場合は、イメージdigest、OSパッケージ一覧、`/usr/share/doc/*/copyright`、FFmpegビルド設定を収集する。
5. 重みを配布する場合はファイル名・取得元・バージョン・SHA-256・ライセンス・必要な学習関連資料を記録する。
6. 採用方式の義務に応じ、実際の配布版に対応するソース・ビルド資料・ライセンス・NOTICEを同じ受領者へ提供する。

参照用に `licenses/AGPL-3.0.txt`（Ultralyticsリポジトリ）、`licenses/Apache-2.0.txt`（EasyOCRリポジトリ）、
opencv-python、NumPy、PyTorch、torchvision、Flask、Waitress、boto3、Caddyの公開リポジトリにあるライセンス文を収録しています。
参照文は調査日の既定ブランチから取得したもので、将来インストールする各バージョンの表示を代替しません。
これらの収録だけで本体のライセンス採用やバイナリ配布条件を満たしたことにはなりません。

## 2026-09-25 モデル選択の整理

コードのライセンス、配布重みのライセンス、教師データの利用条件を区別する。
MIT/Apacheというコードの表示だけでは、任意の重みの再配布を承認したことにならない。
レジストリの`available`は実行環境の状態であり、利用条件への適合を保証しない。
APIの`license_scope`と`license_review`にこの区別を追加した。

| 対象 | 処置・理由 |
|---|---|
| lprs-jp | 削除。公開READMEは研究目的と記載し、リポジトリの利用許諾を確認できない |
| alpr_jp | モデル登録を削除。MITの学習素材であり、禁止モデルという判断ではない |
| RT-DETR、MMDetection、YOLOX、Detectron2、Paddle文字検出、Tesseract、RapidOCR、MMOCR、docTR、TrOCR | 選択一覧から削除。現構成に実行アダプターがなく、パッケージの存在だけで利用可能と表示されていた。ライセンス違反という判断ではない |
| Ultralytics公式YOLO | 条件付きで維持。AGPL-3.0またはEnterprise。非公開組込み等は公式条件に照らして運用方式を確定する必要がある。本体ライセンス案は未採用のまま |
| 専用YOLO重み | 接続機能を維持。Ultralyticsの条件に加え、個別重みと学習データの許諾確認が必要 |
| EasyOCR、PaddleOCR、OpenCV | 実行機能を維持。コードはApache-2.0。重み・同梱依存物の条件は別途管理 |
| Lipla-jp、FastALPR、fast-plate-ocr | コードはMIT。使用する検出/OCR重みの条件をMITと一括認定しない |
| 日本向け追加学習モデル | 学習・接続機能を維持。学習済み重みを今回新たに配布しない。基盤重み・教師データ・公開許諾を個別に確認 |

削除対象にはインストール済みアダプターやrequirementsの直接依存がないため、
requirementsの変更は不要。既存のモデルキャッシュや学習データは削除していない。
これは主要モデル登録の整理であり、全依存物・全重みの利用許諾確定ではない。

確認先：
- https://github.com/eepj/lprs-jp （README、ファイル一覧）
- https://github.com/dyama/alpr_jp （READMEの著作権欄、MIT）
- https://www.ultralytics.com/license （公式提供条件）
- https://github.com/ikeboo/Lipla-jp/blob/main/LICENSE （MIT）
- https://github.com/ankandrew/fast-alpr （MIT、外部モデル構成）
- https://github.com/ankandrew/open-image-models （MIT、検出重み一覧）
- https://github.com/ankandrew/fast-plate-ocr （MIT、学習機能）
