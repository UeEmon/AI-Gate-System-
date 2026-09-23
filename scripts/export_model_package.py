"""Export a validated FastPlateOCR run as a portable model-repository package."""
import argparse
import hashlib
import json
import re
import math
from pathlib import Path
import shutil


def export(root, run, output, version):
    root = Path(root).resolve()
    if not re.fullmatch(r'[0-9a-f]{32}', run):
        raise ValueError('モデルIDが不正です。')
    run_dir = root / 'ocr-learning' / run
    report_path = run_dir / 'report.json'
    report = json.loads(report_path.read_text(encoding='utf-8'))
    if report.get('backend') != 'fast-plate-ocr' or not report.get('eligible'):
        raise ValueError('評価に合格したFastPlateOCR学習ランだけをエクスポートできます。')
    model = (run_dir / report['candidate_model']).resolve()
    config = (run_dir / report['candidate_config']).resolve()
    if not model.is_relative_to(run_dir) or not config.is_relative_to(run_dir):
        raise ValueError('モデルのパスが学習ディレクトリ外を指しています。')
    metrics = report.get('candidate', {})
    if (not isinstance(metrics.get('plates'), int) or metrics['plates'] < 1 or
            any(type(metrics.get(k)) not in (int, float) or not math.isfinite(metrics[k])
                for k in ('cer', 'plate_accuracy'))):
        raise ValueError('有効な実測評価値が必要です。')
    if not model.is_file() or not config.is_file():
        raise ValueError('学習済みONNXまたはplate_config.yamlが見つかりません。')

    destination = Path(output).resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('出力先は空のディレクトリまたは新しいバージョンを指定してください。')
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(model, destination / 'model.onnx')
    shutil.copy2(config, destination / 'plate_config.yaml')
    evaluation = {
        'status': 'passed', 'backend': report['backend'], 'run_id': run,
        'metrics': {'cer': report.get('candidate', {}).get('cer'),
                    'plate_accuracy': report.get('candidate', {}).get('plate_accuracy'),
                    'validation_plates': report.get('candidate', {}).get('plates')},
        'baseline': report.get('baseline'), 'source_dataset_sha256': report.get('dataset_sha256'),
    }
    (destination / 'evaluation.json').write_text(json.dumps(evaluation, ensure_ascii=False, indent=2), encoding='utf-8')
    checksums = {name: hashlib.sha256((destination / name).read_bytes()).hexdigest()
                 for name in ('model.onnx', 'plate_config.yaml', 'evaluation.json')}
    manifest = {
        'model_id': 'fast-plate-ocr-jp', 'version': version,
        'task': 'japanese_license_plate_ocr', 'framework': 'fast-plate-ocr', 'format': 'onnx',
        'files': {'model': 'model.onnx', 'plate_config': 'plate_config.yaml', 'evaluation': 'evaluation.json'},
        'training_data': {'source': 'AI-Gate-System reviewed observations', 'images_in_repository': False,
                          'split': 'plate_identity_grouped', 'label_policy': 'human_confirmed_four_fields'},
        'license': {'model': 'pending_owner_approval', 'base_framework': 'MIT - verify current upstream license',
                    'training_data': 'restricted_operational_data'},
        'sha256': checksums,
    }
    (destination / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--run', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--version', required=True)
    args = parser.parse_args()
    print(json.dumps(export(args.root, args.run, args.output, args.version), ensure_ascii=False, indent=2))
