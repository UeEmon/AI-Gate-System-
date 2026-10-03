# AI-Gate-JP-Models

AI GATE SYSTEMで追加学習した日本ナンバープレート検出器＋PaddleOCRを、他のPythonプロジェクトで再利用するためのパッケージです。運用のLipla-jpを変更する機能ではありません。

## Web画面から出力

OCR学習画面の「再利用パッケージをダウンロード」を押します。現在の学習が完了し、そのモデルで未使用テスト画像2件以上の比較が完了している場合にZIPを出力します。画像・ナンバーの正解ラベル・登録情報・通知・APIキーは含めません。

ZIPには `models/plate.pt`、`models/paddle-inference/`、`recognize.py`、依存関係、ライセンス資料、評価の集計、全ファイルのSHA-256を含む `manifest.json` が入ります。重み・読み込みコード・評価集計のハッシュに基づくバージョンを使います。検証用データはモデル選択に、テスト用データは最終評価に使用します。Lipla-jpの仮ラベルを含む評価は独立した正解による精度測定ではありません。

## 別プロジェクトで利用

Python 3.11の独立環境でZIPを展開し、`requirements.txt`を導入してください。PaddlePaddleに対応するCPU環境が必要です。Apple SiliconのDockerでは学習コンテナ同様のamd64環境を使用します。

```python
from recognize import JapanesePlateRecognizer
recognizer = JapanesePlateRecognizer('/path/to/unpacked-package')
candidates, diagnostics = recognizer.recognize(vehicle_bgr_image)
```

入力は空でないBGR車両切り出し画像です。モデルを起動時に一度生成して繰り返し利用できます。全画角の場合は利用側の車両検出後に渡してください。

## GitHubで管理

`models/<manifestのversion>/`にパッケージの内容を保存します。同じバージョンは変更せず、新しい重みには新しいバージョンを使います。大きい重みはGit LFS又はGitHub Releaseで管理し、取得後に `manifest.json` のチェックサムを検証してください。

WebからのGitHub登録は `GATE_MODEL_REPO_TOKEN`、`GATE_MODEL_REPOSITORY`（既定 `UeEmon/AI-Gate-JP-Models`）、`GATE_MODEL_EXPORT_LICENSE` を設定した場合に利用できます。100 MiB未満の各ファイルをGitデータAPIでバージョン別に登録し、既存バージョンは上書きしません。APIキーをGitHubへ登録しません。

## 利用条件

検出器のUltralytics、OCRのPaddleOCR、基盤重み、教師データの条件をそれぞれ確認してください。`manifest.license=UNRESOLVED`は、出力した重みの再配布条件が未確定であることを示します。出力機能は第三者の権利や本体のライセンスを変更しません。`GATE_MODEL_EXPORT_LICENSE`には確認済みの追加学習モデルの条件を記載します。
