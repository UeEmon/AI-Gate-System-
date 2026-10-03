"""Export only evaluated inference artifacts, with portable adapter and checksums."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent


def summary(report):
    keys = ('evaluated','skipped','ground_truth','evaluation_partition','iou_threshold',
            'ocr_isolated_exact','paddle','lipla','paired')
    return {k:report[k] for k in keys if k in report}


def package_from_state(root):
    area = (Path(root)/'ocr-learning'/'paddle').resolve()
    try:
        state = json.loads((area/'auto-state.json').read_text())
        comparison = json.loads((area/'comparison-state.json').read_text())
    except (OSError,ValueError) as error:
        raise ValueError('学習と未使用テストでの比較を完了してから出力してください。') from error
    dataset = Path(state.get('dataset','')).resolve()
    if not dataset.is_relative_to(area) or not dataset.is_dir():
        raise ValueError('学習済みモデルの保存先が不正です。')
    if state.get('state') != 'completed' or comparison.get('state') != 'completed' or comparison.get('dataset') != str(dataset):
        raise ValueError('現在の学習済みモデルの比較が完了していません。')
    report = comparison.get('report',{})
    if report.get('evaluation_partition') != 'test' or report.get('evaluated',0) < 2:
        raise ValueError('今回の学習に使用しないテスト画像2件以上で評価してください。')
    weights = dataset/'weights'
    required = [weights/'plate.pt', weights/'paddle-inference'/'inference.pdiparams']
    graph = [weights/'paddle-inference'/name for name in ('inference.json','inference.pdmodel')]
    if any(not p.is_file() or not p.stat().st_size for p in required) or not any(p.is_file() and p.stat().st_size for p in graph):
        raise ValueError('推論用検出重み・OCR重み・モデルグラフが不足しています。')
    artifacts = {'models/plate.pt':required[0].read_bytes()}
    for path in (weights/'paddle-inference').iterdir():
        if path.is_symlink() or not path.resolve().is_relative_to(weights.resolve()):
            raise ValueError('管理範囲外のモデル参照は出力できません。')
        if path.is_file() and path.suffix in ('.pdiparams','.pdmodel','.json','.yml','.yaml'):
            artifacts['models/paddle-inference/'+path.name] = path.read_bytes()
    artifacts['validate_model_package.py'] = (ROOT/'training/model-repository-template/scripts/validate_model_package.py').read_bytes()
    artifacts['recognize.py'] = (ROOT/'training/model-repository-template/recognize.py').read_bytes()
    artifacts['paddle_plate_pipeline.py'] = (ROOT/'paddle_plate_pipeline.py').read_bytes()
    artifacts['plate_rules.py'] = (ROOT/'plate_rules.py').read_bytes()
    artifacts['evaluation.json'] = json.dumps(summary(report),ensure_ascii=False,indent=2).encode()
    artifacts['requirements.txt'] = b'ultralytics>=8.3,<9\npaddlepaddle>=3,<4\npaddleocr>=3,<4\nopencv-python-headless>=4.10,<5\nnumpy>=1.26,<3\n'
    for name in ('AGPL-3.0.txt','Apache-2.0.txt'):
        artifacts['licenses/'+name] = (ROOT/'licenses'/name).read_bytes()
    artifacts['NOTICE.md'] = (ROOT/'THIRD_PARTY_NOTICES.md').read_bytes()
    artifacts['README.md'] = (ROOT/'training/model-repository-template/README.md').read_bytes()
    identity=hashlib.sha256(b''.join(hashlib.sha256(v).digest() for k,v in sorted(artifacts.items()))
                            +(os.getenv('GATE_MODEL_EXPORT_LICENSE') or 'UNRESOLVED').encode()).hexdigest()
    version='paddle-jp-'+identity[:16]
    manifest = dict(schema_version=1,model_id='jp-plate-yolo-paddleocr',version=version,
                    created_at=datetime.now(timezone.utc).isoformat(),status='evaluated_not_activated',
                    license=os.getenv('GATE_MODEL_EXPORT_LICENSE') or 'UNRESOLVED',
                    code_licenses={'detector':'AGPL-3.0-or-Enterprise','recognizer':'Apache-2.0'},
                    upstream={'detector':'Ultralytics YOLO','recognizer':'PP-OCRv5_mobile_rec'},
                    evaluation_scope='held-out vehicle crops; Lipla pseudo-labels are not independent ground truth',
                    training_fingerprint=state.get('fingerprint'),
                    files={name:dict(sha256=hashlib.sha256(value).hexdigest(),size=len(value)) for name,value in artifacts.items()})
    artifacts['manifest.json'] = json.dumps(manifest,ensure_ascii=False,indent=2).encode()
    folder = area/'packages';folder.mkdir(exist_ok=True)
    destination = folder/(version+'.zip')
    fd, temporary = tempfile.mkstemp(dir=folder,suffix='.zip');os.close(fd)
    try:
        with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED) as archive:
            for name,data in artifacts.items():
                archive.writestr(name,data)
        verify_package(temporary)
        os.replace(temporary,destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return destination, manifest


def verify_package(path):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(set(names)) != len(names):
            raise ValueError('パッケージ内のファイル名が重複しています。')
        for name in names:
            p = PurePosixPath(name)
            if p.is_absolute() or '..' in p.parts or '\\' in name:
                raise ValueError('不正なパッケージのパスです。')
        manifest = json.loads(archive.read('manifest.json'))
        if manifest.get('schema_version') != 1 or not re.fullmatch(r'paddle-jp-[a-f0-9]{16}',manifest.get('version','')):
            raise ValueError('モデル形式またはバージョンが不正です。')
        required={'models/plate.pt','models/paddle-inference/inference.pdiparams','recognize.py','evaluation.json','validate_model_package.py'}
        if not required.issubset(manifest['files']) or not any(n in manifest['files'] for n in ('models/paddle-inference/inference.json','models/paddle-inference/inference.pdmodel')):
            raise ValueError('推論用ファイルが不足しています。')
        if set(names) != set(manifest['files']) | {'manifest.json'}:
            raise ValueError('記録されていないファイルが含まれています。')
        for name,expected in manifest['files'].items():
            data = archive.read(name)
            if len(data) != expected['size'] or hashlib.sha256(data).hexdigest() != expected['sha256']:
                raise ValueError('チェックサムが一致しません: '+name)
        return manifest
