# FastPlateOCR 日本向け追加学習

日本のプレート画像と正解文字列から、FastPlateOCRのCCTモデルを追加学習する構成です。
重みは同梱していません。学習画像は、利用許諾を確認したものだけを使用してください。

## 注釈形式

`annotations.csv`に次の列を用意します。

```csv
image_path,plate,plate_region
images/0001.jpg,品川330さ1234,関東
```

同一車両の連続フレームは、train/valにまたがらないよう分離してください。
地名、分類番号、ひらがな、番号が完全に確認できない画像は学習データに入れません。

## 設定生成

```bash
python scripts/prepare_fast_plate_ocr_jp.py \
  --annotations data/fast_plate_ocr_jp/annotations.csv \
  --train-csv data/fast_plate_ocr_jp/train.csv \
  --val-csv data/fast_plate_ocr_jp/val.csv \
  --plate-config data/fast_plate_ocr_jp/plate_config.yaml
```

`plate_config.yaml`のalphabetは注釈から自動生成します。日本語の地名漢字を固定リストで欠落させないためです。

## 学習・検証・出力

```bash
pip install 'fast-plate-ocr[train]'
KERAS_BACKEND=torch fast-plate-ocr train \
  --model-config-file training/fast_plate_ocr_jp/model_config.yaml \
  --plate-config-file data/fast_plate_ocr_jp/plate_config.yaml \
  --annotations data/fast_plate_ocr_jp/train.csv \
  --val-annotations data/fast_plate_ocr_jp/val.csv \
  --epochs 150 --batch-size 32 --validate-dataset error \
  --output-dir data/fast_plate_ocr_jp/trained
```

学習後は公式CLIでONNXへ出力し、ONNXファイルと同じ`plate_config.yaml`をセットで保存します。
本システムでは `GATE_FAST_OCR_MODEL_PATH` と `GATE_FAST_OCR_CONFIG_PATH` に指定し、
モデル設定画面の「FastPlateOCR 日本向け追加学習モデル」を選択します。

この手順は学習を実行する準備です。日本向けの正解画像がない状態で学習済み重みを生成したとは扱いません。
