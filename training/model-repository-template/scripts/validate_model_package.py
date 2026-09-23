"""Validate the portable model-package contract without loading heavy ML runtimes."""
import hashlib
import json
from pathlib import Path
import sys


def validate(directory, allow_placeholder=False):
    directory = Path(directory).resolve()
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    required = {'model.onnx', 'plate_config.yaml', 'evaluation.json'}
    files = manifest.get('sha256', {})
    evaluation_path = directory / 'evaluation.json'
    evaluation = json.loads(evaluation_path.read_text(encoding='utf-8'))
    if allow_placeholder and evaluation.get('status') == 'placeholder':
        return {'model_id': manifest.get('model_id'), 'version': manifest.get('version'), 'valid': True, 'placeholder': True}
    missing = [name for name in required if not (directory / name).is_file()]
    if missing:
        raise ValueError('missing model-package files: ' + ', '.join(missing))
    if any(value.startswith('REPLACE_') for value in files.values()):
        raise ValueError('manifest contains placeholder checksums')
    for name in required:
        digest = hashlib.sha256((directory / name).read_bytes()).hexdigest()
        if files.get(name) != digest:
            raise ValueError(f'checksum mismatch: {name}')
    metrics = evaluation.get('metrics', {})
    if evaluation.get('status') != 'passed' or metrics.get('cer') is None or metrics.get('plate_accuracy') is None:
        raise ValueError('evaluation.json is not a passed evaluation report')
    return {'model_id': manifest.get('model_id'), 'version': manifest.get('version'), 'valid': True}


if __name__ == '__main__':
    try:
        print(json.dumps(validate(sys.argv[1], '--allow-placeholder' in sys.argv[2:]), ensure_ascii=False))
    except (IndexError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f'INVALID: {exc}', file=sys.stderr)
        raise SystemExit(1)
