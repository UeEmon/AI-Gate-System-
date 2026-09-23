"""Run the official FastPlateOCR training CLI and record reproducible artifacts."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import csv


def edit_distance(left, right):
    row = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        nxt = [i]
        for j, b in enumerate(right, 1):
            nxt.append(min(nxt[-1] + 1, row[j] + 1, row[j - 1] + (a != b)))
        row = nxt
    return row[-1]


def evaluate(model_path, config_path, validation_csv, directory):
    from fast_plate_ocr import LicensePlateRecognizer
    recognizer = LicensePlateRecognizer(onnx_model_path=str(model_path), plate_config_path=str(config_path))
    truth, predictions = [], []
    with Path(validation_csv).open(encoding='utf-8', newline='') as stream:
        for row in csv.DictReader(stream):
            result = recognizer.run(str(directory / row['image_path']))[0]
            predictions.append(''.join(str(getattr(result, 'plate', result)).split()))
            truth.append(''.join(str(row['plate']).split()))
    errors = sum(edit_distance(a, b) for a, b in zip(truth, predictions))
    length = max(1, sum(map(len, truth)))
    return {'cer': errors / length, 'plate_accuracy': sum(a == b for a, b in zip(truth, predictions)) / max(1, len(truth)),
            'plates': len(truth)}


def train(root, run, epochs=20, batch_size=8, baseline_model=None):
    directory = (Path(root) / 'ocr-learning' / run).resolve()
    helper = Path(__file__).with_name('scripts') / 'prepare_fast_plate_ocr_jp.py'
    config = Path(__file__).with_name('training') / 'fast_plate_ocr_jp' / 'model_config.yaml'
    output = directory / 'fast-plate-ocr-output'
    output.mkdir(exist_ok=True)
    executable = shutil.which('fast-plate-ocr')
    if not executable:
        raise RuntimeError('fast-plate-ocr CLIがインストールされていません。requirements.txtを再構築してください。')

    subprocess.run([sys.executable, str(helper), '--annotations', str(directory / 'annotations.csv'),
                    '--output-dir', str(directory / 'prepared')], check=True)
    command = [executable, 'train', '--model-config-file', str(config),
               '--plate-config-file', str(directory / 'prepared' / 'plate_config.yaml'),
               '--annotations', str(directory / 'prepared' / 'train.csv'),
               '--val-annotations', str(directory / 'prepared' / 'val.csv'),
               '--epochs', str(epochs), '--batch-size', str(batch_size), '--output-dir', str(output)]
    environment = os.environ.copy()
    environment.setdefault('KERAS_BACKEND', 'torch')
    subprocess.run(command, check=True, env=environment)

    checkpoints = sorted(output.rglob('best*.keras'))
    if len(checkpoints) != 1:
        raise RuntimeError('best Keras checkpointを一意に特定できません。train.logを確認してください。')
    subprocess.run([executable, 'export', '--model', str(checkpoints[0]),
                    '--plate-config-file', str(directory / 'prepared' / 'plate_config.yaml'),
                    '--format', 'onnx'], check=True, env=environment)

    models = sorted(output.rglob('*.onnx'))
    if not models:
        raise RuntimeError('FastPlateOCR学習は完了しましたがONNXモデルが出力されませんでした。')
    candidate = checkpoints[0].with_suffix('.onnx')
    if candidate not in models:
        raise RuntimeError('best checkpointに対応するONNXがありません。')
    validation_csv = directory / 'prepared' / 'val.csv'
    candidate_metrics = evaluate(candidate, directory / 'prepared' / 'plate_config.yaml', validation_csv, directory)
    baseline_metrics = None
    if baseline_model:
        baseline_path = Path(baseline_model)
        baseline_config = os.getenv('GATE_FAST_OCR_BASELINE_CONFIG', '').strip()
        if baseline_path.is_file() and baseline_config and Path(baseline_config).is_file():
            baseline_metrics = evaluate(baseline_path, baseline_config, validation_csv, directory)
    minimum_accuracy = float(os.getenv('GATE_FAST_OCR_MIN_EXACT_ACCURACY', '0.80'))
    if baseline_metrics:
        eligible = (candidate_metrics['plate_accuracy'] >= minimum_accuracy and
                    candidate_metrics['cer'] < baseline_metrics['cer'] and
                    candidate_metrics['plate_accuracy'] >= baseline_metrics['plate_accuracy'])
    else:
        eligible = candidate_metrics['plate_accuracy'] >= minimum_accuracy
    report = {
        'backend': 'fast-plate-ocr',
        'eligible': eligible,
        'candidate_model': str(candidate.relative_to(directory)),
        'candidate_config': str((directory / 'prepared' / 'plate_config.yaml').relative_to(directory)),
        'baseline_model': baseline_model,
        'epochs': epochs,
        'batch_size': batch_size,
        'baseline': baseline_metrics,
        'candidate': candidate_metrics,
        'minimum_exact_accuracy': minimum_accuracy,
        'validation': 'FastPlateOCR公式CLIの検証分割を使用; 異なるナンバー単位の固定分割',
        'dataset_sha256': hashlib.sha256((directory / 'dataset.json').read_bytes()).hexdigest(),
        'note': '候補モデルは検証合格後も明示的な適用操作まで推論に使用しません。',
    }
    (directory / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--run', required=True)
    parser.add_argument('--epochs', type=int, default=20)
    parser.add_argument('--batch-size', type=int, default=8)
    parser.add_argument('--baseline-model')
    args = parser.parse_args()
    train(Path(args.root), args.run, args.epochs, args.batch_size, args.baseline_model)
