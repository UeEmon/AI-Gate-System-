"""Browser interface for the isolated full-frame plate benchmark."""
import hmac
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import uuid

from flask import Flask, abort, redirect, render_template, request, session, send_from_directory, url_for


def create_app(data_root='/data/plate-web', model_root='/plate-models', password=None):
    app = Flask(__name__)
    password = password or os.environ.get('GATE_ADMIN_PASSWORD', '')
    if not password:
        raise ValueError('GATE_ADMIN_PASSWORDを設定してください。')
    app.secret_key = secrets.token_hex(32)
    app.config.update(MAX_CONTENT_LENGTH=1024 * 1024 * 1024, SESSION_COOKIE_SAMESITE='Strict',
                      SESSION_COOKIE_HTTPONLY=True)
    root, models = Path(data_root), Path(model_root)
    root.mkdir(parents=True, exist_ok=True)
    jobs, lock = {}, threading.Lock()
    backend_labels = {'paddle': 'PaddleOCR', 'easyocr': 'EasyOCR 日本語',
                      'lipla': 'Lipla-jp', 'fastalpr': 'FastALPR（日本向け未学習）'}
    allowed = os.getenv('GATE_BENCHMARK_OCR_CHOICES', 'paddle,easyocr,lipla,fastalpr').split(',')
    backend_choices = {key: value for key, value in backend_labels.items() if key in allowed}

    def model_choices():
        return sorted(p.name for p in models.glob('*.pt') if p.is_file() and not p.is_symlink())

    @app.before_request
    def authenticate():
        if request.path == '/healthz':
            return
        auth = request.authorization
        if not auth or auth.username != 'admin' or not hmac.compare_digest(
                (auth.password or '').encode(), password.encode()):
            return 'ログインしてください', 401, {'WWW-Authenticate': 'Basic realm="Plate benchmark"'}
        if request.method == 'POST' and not hmac.compare_digest(
                request.form.get('csrf', ''), session.get('csrf', '') or 'invalid'):
            abort(403)

    @app.get('/healthz')
    def health():
        return {'status': 'ok'}

    @app.get('/')
    def index():
        session.setdefault('csrf', secrets.token_hex(32))
        return render_template('plate_benchmark.html', models=model_choices(), jobs=list(jobs), job=None, backend_choices=backend_choices)

    @app.post('/run')
    def start():
        file = request.files.get('source')
        suffix = Path(file.filename or '').suffix.lower() if file else ''
        if suffix not in {'.jpg', '.jpeg', '.png', '.bmp', '.webp', '.mp4', '.mov', '.avi', '.mkv'}:
            abort(400, '対応する画像・動画を選択してください。')
        backend = request.form.get('ocr', 'paddle')
        model = request.form.get('model', '')
        device = request.form.get('device', 'cpu')
        if device not in {'cpu', 'auto', 'mps', 'cuda'}:
            abort(400, 'デバイス設定が不正です。')
        if device in {'mps', 'cuda'} and backend != 'easyocr':
            abort(400, 'GPUでOCRを実行する場合はEasyOCRを選択してください。')
        if backend not in backend_choices or (model and model not in model_choices()):
            abort(400, 'モデル設定が不正です。')
        try:
            every = int(request.form.get('every', 1))
            maximum = int(request.form.get('maximum', 100))
            warmup = int(request.form.get('warmup', 0))
            if not (1 <= every <= 100 and 1 <= maximum <= 300 and 0 <= warmup < maximum):
                raise ValueError()
        except ValueError:
            abort(400, '処理フレーム数・間隔を確認してください。')
        with lock:
            if any(job['process'].poll() is None for job in jobs.values()):
                abort(409, '検証を実行中です。完了後に実行してください。')
            identifier = uuid.uuid4().hex
            folder = root / identifier
            folder.mkdir()
            source = folder / ('input' + suffix)
            file.save(source)
            command = [sys.executable, str(Path(__file__).with_name('plate_only_benchmark.py')),
                       '--source', str(source), '--output', str(folder / 'results'),
                       '--data', str(folder), '--every', str(every), '--max-frames', str(maximum),
                       '--warmup', str(warmup), '--save-images']
            if model:
                command += ['--plate-model', str(models / model)]
            env = dict(os.environ, GATE_OCR_BACKEND=backend, GATE_PLATE_MODEL='',
                       GATE_INFERENCE_DEVICE=device,
                       GATE_FAST_OCR_MODEL_PATH='', GATE_FAST_OCR_CONFIG_PATH='')
            with (folder / 'run.log').open('w') as log:
                process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=env)
            jobs[identifier] = {'process': process, 'folder': folder, 'name': file.filename}
        return redirect(url_for('result', identifier=identifier))

    @app.get('/jobs/<identifier>')
    def result(identifier):
        job = jobs.get(identifier)
        if not job:
            abort(404)
        code = job['process'].poll()
        summary, records, result_dir = None, [], None
        if code == 0:
            summaries = list((job['folder'] / 'results').glob('*/summary.json'))
            if summaries:
                result_dir = summaries[0].parent.name
                summary = json.loads(summaries[0].read_text())
                records = [json.loads(line) for line in (summaries[0].parent / 'frames.jsonl').read_text().splitlines()]
        log = (job['folder'] / 'run.log').read_text(errors='replace')[-6000:] if code is not None else ''
        return render_template('plate_benchmark.html', job=job, identifier=identifier, code=code,
                               summary=summary, records=records, result_dir=result_dir, log=log)

    @app.get('/jobs/<identifier>/files/<path:filename>')
    def artifact(identifier, filename):
        job = jobs.get(identifier)
        if not job:
            abort(404)
        return send_from_directory(job['folder'] / 'results', filename)

    return app


if __name__ == '__main__':
    from waitress import serve
    serve(create_app(os.getenv('GATE_BENCHMARK_DATA', '/data/plate-web'),
                     os.getenv('GATE_BENCHMARK_MODELS', '/plate-models')),
          host=os.getenv('GATE_BENCHMARK_HOST', '0.0.0.0'),
          port=int(os.getenv('GATE_BENCHMARK_PORT', '8080')), threads=4)
