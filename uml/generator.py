"""Generate PlantUML class and package diagrams from mounted Python sources."""
from pathlib import Path
import subprocess
import threading


SOURCE_ROOT = Path('/source')
OUTPUT_ROOT = Path('/tmp/uml-generated')
GROUPS = {
    'core': ('GateCore', ('app.py', 'web.py', 'model_service.py',
                          'lipla_pipeline.py', 'plate_pipeline.py', 'events.py', 'evidence.py',
                          'vehicle_identity.py')),
    'services': ('GateServices', ('aigate',)),
    'learning': ('GateLearning', ('ocr_learning.py', 'paddle_auto_train.py',
                                  'paddle_training.py', 'ocr_backends.py')),
}
LOCK = threading.Lock()


def titles():
    return {f'auto-{group}-{kind}': f'自動生成・{group} {"クラス図" if kind == "classes" else "パッケージ図"} (Pyreverse)'
            for group in GROUPS for kind in ('classes', 'packages')}


def source(group, kind, force=False):
    """Regenerate after source changes. Never write inside the mounted repository."""
    if group not in GROUPS or kind not in ('classes', 'packages'):
        raise ValueError('不明な図です。')
    project, names = GROUPS[group]
    paths = [SOURCE_ROOT / name for name in names]
    for path in paths:
        if not path.exists():
            raise FileNotFoundError(f'ソースがありません: {path.name}')
    files = [file for path in paths for file in (path.rglob('*.py') if path.is_dir() else [path])]
    if not files:
        raise ValueError('解析するPythonファイルがありません。')
    latest = max(file.stat().st_mtime_ns for file in files)
    output_dir = OUTPUT_ROOT / group
    requested = output_dir / f'{kind}_{project}.puml'
    other = output_dir / f'{"packages" if kind == "classes" else "classes"}_{project}.puml'
    with LOCK:
        if force or not (requested.exists() and other.exists()) or min(
                requested.stat().st_mtime_ns, other.stat().st_mtime_ns) < latest:
            output_dir.mkdir(parents=True, exist_ok=True)
            completed = subprocess.run(
                ['pyreverse', '--output=puml', f'--project={project}',
                 f'--output-directory={output_dir}', *map(str, paths)],
                cwd=SOURCE_ROOT, capture_output=True, text=True, timeout=90, check=False)
            if completed.returncode or not requested.is_file() or not other.is_file():
                diagnostic = (completed.stderr + '\n' + completed.stdout).strip()[-2000:]
                raise RuntimeError(f'Pyreverse失敗 ({group}, 終了コード {completed.returncode}): '
                                   f'{diagnostic or "クラス図またはパッケージ図が生成されませんでした。"}')
        return requested.read_text(encoding='utf-8')
