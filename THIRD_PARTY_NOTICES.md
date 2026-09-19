# 第三者ソフト・モデルと配布条件

調査日：2026-09-19。これは主要構成の整理であり、全バイナリのライセンス監査済み証明ではありません。
本体ライセンスは `LICENSE-PROPOSAL.md` の採用待ちです。
今回のZIPは自作ソース・設定・文書・参照ライセンス文だけを含み、Python、wheel、Dockerイメージ、FFmpeg、モデル重みを含みません。

| 対象 | ライセンス・確認先 | 配布で扱う事項 |
|---|---|---|
| Ultralytics / YOLOモデル | [AGPL-3.0またはEnterprise](https://www.ultralytics.com/license) | AGPL案では対応するソースとライセンス。非公開組込みを希望する場合はEnterprise条件を確認 |
| EasyOCR | [Apache-2.0](https://github.com/JaidedAI/EasyOCR/blob/master/LICENSE) | ライセンス・著作権・NOTICEを保持。OCRモデルも実際に取得した配布元を記録 |
| PaddleOCR / PaddlePaddle | [Apache-2.0](https://github.com/PaddlePaddle/PaddleOCR/blob/main/LICENSE) | ライセンス・NOTICEを保持。取得したPP-OCRモデル名・版・SHA-256を記録 |
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
