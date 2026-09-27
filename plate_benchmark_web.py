"""Browser interface for vehicle-first and full-frame plate benchmarks."""
import hmac
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import uuid
from urllib.parse import urlsplit

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

    def paddle_model_choices():
        root = Path(os.getenv('GATE_BENCHMARK_PADDLE_MODELS', '/paddle-models'))
        return sorted(p.name for p in root.iterdir() if p.is_dir() and not p.is_symlink()) if root.is_dir() else []

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
        return render_template('plate_benchmark.html', models=model_choices(), jobs=list(jobs),
                               job=None, backend_choices=backend_choices,
                               paddle_models=paddle_model_choices(),
                               local_rtmp_url=os.getenv('GATE_RTMP_LOCAL_URL', ''))

    @app.post('/run')
    def start():
        source_kind = request.form.get('source_kind', 'file')
        if source_kind not in {'file', 'rtmp'}:
            abort(400, '入力方式が不正です。')
        file = request.files.get('source') if source_kind == 'file' else None
        suffix = Path(file.filename or '').suffix.lower() if file else ''
        if source_kind == 'file' and suffix not in {
                '.jpg', '.jpeg', '.png', '.bmp', '.webp', '.mp4', '.mov', '.avi', '.mkv'}:
            abort(400, '対応する画像・動画を選択してください。')
        stream_url = request.form.get('stream_url', '').strip() if source_kind == 'rtmp' else ''
        if source_kind == 'rtmp':
            try:
                parsed = urlsplit(stream_url)
                valid = (parsed.scheme.lower() in {'rtmp', 'rtmps'} and bool(parsed.hostname)
                         and parsed.port != 0 and not parsed.fragment)
            except ValueError:
                valid = False
            if not valid or len(stream_url) > 2048 or any(ord(char) < 32 for char in stream_url):
                abort(400, 'rtmp:// または rtmps:// のURLを指定してください。')
        backend = request.form.get('ocr', 'paddle')
        mode = request.form.get('mode', 'vehicle-first')
        if mode not in {'vehicle-first', 'plate-only'}:
            abort(400, '検証モードが不正です。')
        model = request.form.get('model', '')
        paddle_model = request.form.get('paddle_model', '')
        device = request.form.get('device', 'cpu')
        if device not in {'cpu', 'auto', 'mps', 'cuda'}:
            abort(400, 'デバイス設定が不正です。')
        if backend != 'paddle' or model not in model_choices() or (
                paddle_model and paddle_model not in paddle_model_choices()):
            abort(400, '専用プレート検出重みとPaddleOCRモデルを選択してください。')
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
            source = stream_url if source_kind == 'rtmp' else folder / ('input' + suffix)
            if file:
                file.save(source)
            command = [sys.executable, str(Path(__file__).with_name('plate_only_benchmark.py')),
                       '--source', str(source), '--output', str(folder / 'results'),
                       '--data', str(folder), '--every', str(every), '--max-frames', str(maximum),
                       '--warmup', str(warmup), '--mode', mode, '--save-images']
            if model:
                command += ['--plate-model', str(models / model)]
            env = dict(os.environ, GATE_OCR_BACKEND=backend, GATE_PLATE_MODEL='',
                       GATE_INFERENCE_DEVICE=device,
                       GATE_PADDLE_MODEL_DIR=(str(Path(os.getenv('GATE_BENCHMARK_PADDLE_MODELS', '/paddle-models')) / paddle_model)
                                              if paddle_model else ''),
                       GATE_FAST_OCR_MODEL_PATH='', GATE_FAST_OCR_CONFIG_PATH='')
            with (folder / 'run.log').open('w') as log:
                process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=env)
            jobs[identifier] = {'process': process, 'folder': folder,
                                'name': 'RTMPライブ配信' if source_kind == 'rtmp' else file.filename,
                                'live': source_kind == 'rtmp'}
        return redirect(url_for('result', identifier=identifier))

    @app.get('/jobs/<identifier>')
    def result(identifier):
        job = jobs.get(identifier)
        if not job:
            abort(404)
        code = job['process'].poll()
        summary, records, result_dir = None, [], None
        results = list((job['folder'] / 'results').glob('*/frames.jsonl'))
        if results:
            result_dir = results[0].parent.name
            # Ignore an incomplete final line while the child is writing.
            with results[0].open(encoding='utf-8') as stream:
                for line in stream:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        break
            summary_file = results[0].parent / 'summary.json'
            if summary_file.is_file() and code == 0:
                summary = json.loads(summary_file.read_text())
        total_frames = len(records)
        if code is None:
            records = records[-10:]
        # FFmpeg can embed stream credentials in an error; keep raw logs server-side.
        log = ('RTMP接続または処理に失敗しました。接続先・ログをサーバーで確認してください。'
               if job['live'] and code not in (None, 0) else
               (job['folder'] / 'run.log').read_text(errors='replace')[-6000:]
               if code is not None else '')
        return render_template('plate_benchmark.html', job=job, identifier=identifier, code=code,
                               summary=summary, records=records, result_dir=result_dir,
                               total_frames=total_frames, log=log)

    @app.post('/jobs/<identifier>/stop')
    def stop(identifier):
        job = jobs.get(identifier)
        if not job:
            abort(404)
        with lock:
            if job['process'].poll() is None:
                job['process'].terminate()
                job['stopped'] = True
        return redirect(url_for('result', identifier=identifier))

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
