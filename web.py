"""Local/LAN web management. Run one server process per data directory."""
import argparse
from contextlib import contextmanager
import atexit
from datetime import datetime, timezone
import hmac
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
from urllib.parse import urlsplit
import uuid

from flask import Flask, abort, jsonify, render_template, request, send_file, session
from werkzeug.exceptions import HTTPException
from app import open_database
import events
import registry_csv
import ocr_learning
from plate_rules import OCR_RESULT_CONFIDENCE

ROOT = Path(__file__).resolve().parent
EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp', '.webp', '.tif', '.tiff', '.mp4', '.avi', '.mov', '.mkv', '.m4v', '.webm'}
ACTIVE = ('starting', 'running', 'stopping')


def now():
    return datetime.now(timezone.utc).isoformat()


class BusyError(Exception):
    pass


class JobManager:
    def __init__(self, root, model='yolo11n.pt', popen=subprocess.Popen):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.model = model
        self.popen = popen
        self.lock = threading.RLock()
        self.process = None
        self.active_id = None
        events.initialize(self.root)
        with events.connection(self.root) as db:
            db.execute("UPDATE alerts SET media_status='failed',s3_status=CASE WHEN s3_status='waiting' THEN 'failed' ELSE s3_status END,media_error='録画完了前にサーバーが停止しました。' WHERE media_status='recording'")
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, label TEXT NOT NULL,
                status TEXT NOT NULL, created_at TEXT NOT NULL,
                ended_at TEXT, error TEXT, every INTEGER NOT NULL,
                confidence REAL NOT NULL)''')
            db.execute("UPDATE jobs SET status='interrupted', ended_at=?, error=? WHERE status IN ('starting','running','stopping')",
                       (now(), 'サーバー再起動により処理状態をリセットしました。'))

    @contextmanager
    def connect(self):
        db = open_database(self.root / 'gate.db')
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def folder(self, job_id):
        if len(job_id) != 32 or any(c not in '0123456789abcdef' for c in job_id):
            raise ValueError('処理IDが不正です。')
        return self.root / 'jobs' / job_id

    def start(self, kind, source, label, every, confidence, upload=None):
        with self.lock:
            if self.active_id:
                raise BusyError('処理中です。現在の処理を停止してから開始してください。')
            job_id = uuid.uuid4().hex
            folder = self.folder(job_id)
            folder.mkdir(parents=True)
            if kind == 'browser':
                source = folder / 'browser'
                source.mkdir()
            if upload is not None:
                source = folder / ('input' + Path(upload.filename).suffix.lower())
                upload.save(source)
            with self.connect() as db:
                db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?)',
                           (job_id, kind, label, 'starting', now(), None, None, every, confidence))
            command = [sys.executable, '-u', str(ROOT / 'app.py'), '--source', str(source),
                       '--source-kind', kind, '--output', str(self.root), '--run-id', job_id,
                       '--model', self.model, '--every', str(every), '--confidence', str(confidence),
                       '--save-images', '--alerts', '--progress', str(folder / 'progress.json'),
                       '--preview', str(folder / 'preview.jpg')]
            log = (folder / 'worker.log').open('wb')
            try:
                process = self.popen(command, stdout=subprocess.DEVNULL, stderr=log, cwd=ROOT)
            except Exception:
                with self.connect() as db:
                    db.execute("UPDATE jobs SET status='failed', ended_at=?, error=? WHERE id=?",
                               (now(), '処理プロセスを開始できませんでした。', job_id))
                raise
            finally:
                log.close()
            self.process, self.active_id = process, job_id
            with self.connect() as db:
                db.execute("UPDATE jobs SET status='running' WHERE id=?", (job_id,))
            threading.Thread(target=self._watch, args=(job_id, process), daemon=True).start()
            return job_id

    def _watch(self, job_id, process):
        code = process.wait()
        with self.lock:
            with self.connect() as db:
                row = db.execute('SELECT status FROM jobs WHERE id=?', (job_id,)).fetchone()
                status = 'stopped' if row['status'] == 'stopping' else ('completed' if code == 0 else 'failed')
                error = None if status != 'failed' else '入力・接続・モデル・依存パッケージを確認してください。詳細は処理PCのworker.logにあります。'
                db.execute('UPDATE jobs SET status=?, ended_at=?, error=? WHERE id=?', (status, now(), error, job_id))
            with events.connection(self.root) as db:
                db.execute("UPDATE alerts SET media_status='failed',s3_status=CASE WHEN s3_status='waiting' THEN 'failed' ELSE s3_status END,media_error='処理終了時に録画を確定できませんでした。' WHERE run_id=? AND media_status='recording'",(job_id,))
            if self.active_id == job_id:
                self.active_id, self.process = None, None

    def stop(self, job_id):
        with self.lock:
            if self.active_id != job_id or self.process is None:
                return False
            with self.connect() as db:
                db.execute("UPDATE jobs SET status='stopping' WHERE id=?", (job_id,))
            process = self.process
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            threading.Thread(target=self._kill_if_needed, args=(process,), daemon=True).start()
            return True

    @staticmethod
    def _kill_if_needed(process):
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except ProcessLookupError:
                pass

    def shutdown(self):
        with self.lock:
            job_id, process = self.active_id, self.process
        if job_id:
            self.stop(job_id)
            self._kill_if_needed(process)

    def list_jobs(self):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM jobs ORDER BY created_at DESC LIMIT 100').fetchall()
        result = []
        for row in rows:
            item = dict(row)
            folder = self.folder(item['id'])
            try:
                item['progress'] = json.loads((folder / 'progress.json').read_text(encoding='utf-8'))
            except (FileNotFoundError, ValueError):
                item['progress'] = {}
            item['has_preview'] = (folder / 'preview.jpg').is_file()
            result.append(item)
        return result

    def history_summary(self):
        with self.connect() as db:
            return {
                'processing': db.execute('SELECT count(*) FROM jobs').fetchone()[0],
                'recognition': db.execute('SELECT count(*) FROM observations').fetchone()[0]
            }

    def delete_history(self, scopes):
        """Delete selected history tables and their narrowly scoped files."""
        with self.lock:
            if self.active_id:
                raise BusyError('認識処理を停止してから履歴を削除してください。')
            with self.connect() as db:
                job_ids = ([row[0] for row in db.execute('SELECT id FROM jobs')]
                           if 'processing' in scopes else [])
                image_paths = ([row[0] for row in db.execute(
                    'SELECT DISTINCT image_path FROM observations WHERE image_path IS NOT NULL')]
                    if 'recognition' in scopes else [])
                deleted = {'processing': len(job_ids),
                           'recognition': db.execute('SELECT count(*) FROM observations').fetchone()[0]
                           if 'recognition' in scopes else 0}
                if 'recognition' in scopes:
                    db.execute('DELETE FROM observations')
                if 'processing' in scopes:
                    db.execute('DELETE FROM jobs')
            errors = []
            jobs_root = (self.root / 'jobs').resolve()
            images_root = (self.root / 'images').resolve()
            for job_id in job_ids:
                try:
                    folder = self.folder(job_id).resolve()
                    if not folder.is_relative_to(jobs_root):
                        raise ValueError('処理フォルダーが管理範囲外です。')
                    if folder.exists():
                        shutil.rmtree(folder)
                except (OSError, ValueError) as exc:
                    errors.append(f'処理 {job_id}: {type(exc).__name__}')
            for value in image_paths:
                try:
                    path = Path(value).resolve()
                    if not path.is_relative_to(images_root):
                        raise ValueError('認識画像が管理範囲外です。')
                    path.unlink(missing_ok=True)
                except (OSError, ValueError) as exc:
                    errors.append(f'認識画像: {type(exc).__name__}')
            return dict(deleted=deleted, file_errors=errors)


def create_app(data_dir='data', model='yolo11n.pt', password=None, manager=None):
    app = Flask(__name__)
    app.config.update(SECRET_KEY=secrets.token_hex(32), MAX_CONTENT_LENGTH=512 * 1024 * 1024,
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict')
    manager = manager or JobManager(data_dir, model)
    app.extensions['jobs'] = manager
    trainer = ocr_learning.TrainingManager(manager.root)
    app.extensions['ocr_training'] = trainer
    atexit.register(trainer.shutdown)
    password = password if password is not None else os.environ.get('GATE_ADMIN_PASSWORD')

    @app.before_request
    def protect():
        if request.path == '/healthz' and request.method == 'GET':
            return None
        if password:
            credentials = request.authorization
            if not credentials or credentials.username != 'admin' or not hmac.compare_digest(
                    (credentials.password or '').encode(), password.encode()):
                return ('ログインしてください。', 401, {'WWW-Authenticate': 'Basic realm="AI Gate System"'})
        elif request.remote_addr not in ('127.0.0.1', '::1') or urlsplit(request.host_url).hostname not in ('127.0.0.1', 'localhost', '::1'):
            abort(403, description='外部からのアクセスには管理パスワードの設定が必要です。')
        if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            expected = session.get('csrf')
            if not expected or not hmac.compare_digest(expected, request.headers.get('X-CSRF-Token', '')):
                abort(403, description='画面を再読み込みしてから操作してください。')

    @app.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        return response

    @app.errorhandler(HTTPException)
    def http_error(error):
        message = 'ファイルは512MB以下にしてください。' if error.code == 413 else error.description
        return jsonify(error=message), error.code

    @app.get('/healthz')
    def health():
        return jsonify(status='ok')

    @app.get('/')
    def index():
        session.setdefault('csrf', secrets.token_hex(32))
        return render_template('index.html', csrf=session['csrf'])

    @app.get('/api/jobs')
    def jobs():
        return jsonify(jobs=manager.list_jobs(), active_id=manager.active_id)

    @app.get('/api/history/summary')
    def history_summary():
        summary = manager.history_summary()
        with events.connection(manager.root) as db:
            summary['alerts_retained'] = db.execute('SELECT count(*) FROM alerts').fetchone()[0]
            summary['learning_samples_retained'] = db.execute('SELECT count(*) FROM ocr_samples').fetchone()[0]
            summary['vehicles_retained'] = db.execute('SELECT count(*) FROM vehicles').fetchone()[0]
        return jsonify(summary)

    @app.delete('/api/history')
    def delete_history():
        data = request.get_json(silent=True)
        scopes = data.get('scopes') if isinstance(data, dict) else None
        if (not isinstance(scopes, list) or not scopes or len(scopes) != len(set(scopes)) or
                not set(scopes) <= {'processing', 'recognition'}):
            abort(400, description='削除対象は処理履歴・認識履歴から選択してください。')
        if data.get('confirmation') != 'DELETE HISTORY':
            abort(400, description='削除確認が一致しません。')
        try:
            result = manager.delete_history(set(scopes))
        except BusyError as exc:
            abort(409, description=str(exc))
        return jsonify(result)

    @app.post('/api/jobs')
    def start():
        kind = request.form.get('kind')
        if kind not in ('file', 'camera', 'browser'):
            abort(400, description='入力方式を選択してください。')
        try:
            every = int(request.form.get('every', '1'))
            confidence = float(request.form.get('confidence', '0.4'))
            if not 1 <= every <= 1000 or not 0 < confidence <= 1:
                raise ValueError
        except ValueError:
            abort(400, description='処理間隔は1〜1000、検出しきい値は0より大きく1以下です。')
        upload = None
        if kind == 'file':
            upload = request.files.get('file')
            if not upload or Path(upload.filename).suffix.lower() not in EXTENSIONS:
                abort(400, description='対応する画像または動画ファイルを選択してください。')
            source = None
            label = upload.filename.replace('\\', '/').split('/')[-1][:200]
        elif kind == 'camera':
            source = request.form.get('source', '').strip()
            try:
                parsed = urlsplit(source)
            except ValueError:
                abort(400, description='カメラURLが不正です。')
            if source.isdecimal() and len(source) <= 2:
                label = 'USBカメラ ' + source
            elif parsed.scheme in ('rtsp', 'rtsps') and parsed.hostname and not any(c.isspace() for c in source):
                label = 'ネットワークカメラ'  # Never return credentials or URL to the browser/history.
            else:
                abort(400, description='カメラ番号（0〜99）またはRTSP URLを指定してください。')
        else:
            source = None
            label = '操作端末のWebカメラ'
        try:
            job_id = manager.start(kind, source, label, every, confidence, upload)
        except BusyError as error:
            abort(409, description=str(error))
        except Exception:
            app.logger.exception('Worker launch failed')
            abort(500, description='処理を開始できませんでした。サーバーのログを確認してください。')
        return jsonify(id=job_id), 201

    @app.post('/api/jobs/<job_id>/browser-frame')
    def browser_frame(job_id):
        """Accept one JPEG from a browser camera; worker consumes latest.jpg."""
        try:
            folder = manager.folder(job_id) / 'browser'
        except ValueError:
            abort(404)
        with manager.connect() as db:
            row = db.execute("SELECT kind,status FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row or row['kind'] != 'browser' or row['status'] not in ('starting', 'running'):
            abort(409, description='Webカメラ処理は実行中ではありません。')
        if not request.mimetype.startswith('image/'):
            abort(400, description='JPEG画像を送信してください。')
        payload = request.get_data(cache=False)
        if not 100 <= len(payload) <= 4 * 1024 * 1024:
            abort(400, description='画像サイズが不正です。')
        temporary = folder / 'latest.tmp'
        temporary.write_bytes(payload)
        os.replace(temporary, folder / 'latest.jpg')
        return jsonify(status='accepted')

    @app.post('/api/jobs/<job_id>/stop')
    def stop(job_id):
        if not manager.stop(job_id):
            abort(409, description='この処理はすでに終了しています。')
        return jsonify(status='stopping')

    @app.get('/api/jobs/<job_id>/preview')
    def preview(job_id):
        try:
            path = manager.folder(job_id) / 'preview.jpg'
        except ValueError:
            abort(404)
        if not path.is_file():
            abort(404)
        # Read bytes once so atomic replacement cannot race send_file's path handling.
        from io import BytesIO
        return send_file(BytesIO(path.read_bytes()), mimetype='image/jpeg')

    @app.get('/api/observations')
    def observations():
        try:
            page = max(1, int(request.args.get('page', 1)))
        except ValueError:
            abort(400, description='ページ番号が不正です。')
        clauses, values = ["json_extract(details_json,'$.result_eligible') = 1"], []
        for argument, column in [('job', 'run_id'), ('vehicle', 'vehicle_type')]:
            if request.args.get(argument):
                clauses.append(column + ' = ?')
                values.append(request.args[argument])
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        with manager.connect() as db:
            total = db.execute('SELECT count(*) FROM observations' + where, values).fetchone()[0]
            rows = db.execute('SELECT details_json FROM observations' + where +
                              ' ORDER BY processed_at DESC, id DESC LIMIT 30 OFFSET ?', values + [(page-1)*30]).fetchall()
        result = []
        for row in rows:
            item = json.loads(row[0])
            item['has_image'] = bool(item.pop('image_path', None))
            result.append(item)
        return jsonify(items=result, total=total, page=page, page_size=30)

    @app.get('/api/registration-feed')
    def registration_feed():
        job = request.args.get('job', '')
        try:
            after = int(request.args.get('after', '0'))
            if after < 0 or after > 9223372036854775807: raise ValueError
        except ValueError:
            abort(400, description='読取位置が不正です。')
        with manager.connect() as db:
            if not db.execute('SELECT id FROM jobs WHERE id=?', (job,)).fetchone(): abort(404)
            rows = db.execute('SELECT rowid AS cursor,details_json FROM observations WHERE run_id=? AND rowid>? ORDER BY rowid LIMIT 100', (job, after)).fetchall()
            items = []
            for row in rows:
                record = json.loads(row['details_json'])
                item = dict(cursor=row['cursor'], draft=None)
                for candidate in record.get('plate_candidates', []):
                    if not candidate.get('fields') or candidate.get('confidence', 0) < OCR_RESULT_CONFIDENCE: continue
                    try: key = events.plate_key(candidate['fields'])
                    except (ValueError, KeyError, TypeError): continue
                    registered = db.execute('SELECT id FROM vehicles WHERE plate_key=?', (key,)).fetchone()
                    if not registered:
                        item['draft'] = dict(key=key, fields=candidate['fields'], vehicle_type=record['vehicle_type'],
                            confidence=candidate['confidence'], observation_id=record['id'], has_image=bool(record.get('image_path')))
                    break
                items.append(item)
        return jsonify(items=items)

    @app.get('/api/observations/<observation_id>/registration')
    def registration_draft(observation_id):
        # Read a fixed observation: live polling must not replace a draft under review.
        with manager.connect() as db:
            row = db.execute('SELECT details_json FROM observations WHERE id=?',
                             (observation_id,)).fetchone()
        if not row:
            abort(404, description='読み取り結果が見つかりません。')
        item = json.loads(row[0])
        return jsonify(observation_id=item['id'], run_id=item['run_id'],
                       processed_at=item['processed_at'], frame_index=item['frame_index'],
                       vehicle_type=item['vehicle_type'], confidence=item['confidence'],
                       plate_candidates=item.get('plate_candidates', []),
                       has_image=bool(item.get('image_path')))

    @app.get('/api/observations/<observation_id>/image')
    def observation_image(observation_id):
        with manager.connect() as db:
            row = db.execute('SELECT image_path FROM observations WHERE id=?', (observation_id,)).fetchone()
        if not row or not row[0]:
            abort(404)
        path = Path(row[0]).resolve()
        if not path.is_relative_to(manager.root / 'images') or not path.is_file():
            abort(404)
        return send_file(path, mimetype='image/jpeg')

    @app.get('/api/vehicles')
    def vehicles():
        with events.connection(manager.root) as db:
            rows=db.execute('SELECT * FROM vehicles ORDER BY updated_at DESC').fetchall()
        return jsonify(items=[dict(r) for r in rows])

    @app.get('/api/vehicles/export.csv')
    def export_vehicles():
        from io import BytesIO
        return send_file(BytesIO(registry_csv.export_registry(manager.root)),
                         mimetype='text/csv; charset=utf-8',as_attachment=True,download_name='vehicles.csv')

    @app.post('/api/vehicles/import.csv')
    def import_vehicles():
        uploaded=request.files.get('file')
        if not uploaded: abort(400,description='CSVファイルを選択してください。')
        preview=request.form.get('preview','1')
        if preview not in ('0','1'): abort(400,description='確認方式が不正です。')
        try:
            result=registry_csv.import_registry(manager.root,uploaded.stream.read(registry_csv.MAX_BYTES+1),
                mode=request.form.get('mode','add'),preview=preview=='1')
        except ValueError as exc: abort(400,description=str(exc))
        return jsonify(result)

    @app.get('/api/ocr-learning')
    def learning_status():
        with events.connection(manager.root) as db:
            samples = [dict(r) for r in db.execute("SELECT s.id,s.observation_id,s.candidate_index,s.plate_key,s.top_text,s.bottom_text,s.original_text,s.created_at,f.fields_json,CASE WHEN o.id IS NULL THEN 0 ELSE 1 END AS has_observation FROM ocr_samples s LEFT JOIN ocr_sample_fields f ON f.sample_id=s.id LEFT JOIN observations o ON o.id=s.observation_id ORDER BY s.created_at DESC LIMIT 200")]
            count = db.execute('SELECT count(*) FROM ocr_samples').fetchone()[0]
            runs = [dict(r) for r in db.execute('SELECT * FROM ocr_training_runs ORDER BY created_at DESC LIMIT 20')]
        for sample in samples:
            sample['fields'] = json.loads(sample.pop('fields_json') or 'null')
        for run in runs:
            run['report'] = json.loads(run.pop('report_json') or 'null')
        return jsonify(samples=samples, count=count, runs=runs, active=ocr_learning.active_model(manager.root))

    @app.get('/api/ocr-learning/preview/<observation_id>/<int:candidate_index>')
    def learning_preview(observation_id, candidate_index):
        from io import BytesIO
        try:
            with events.connection(manager.root) as db:
                image, _ = ocr_learning.sample_image(manager.root, observation_id, candidate_index, db)
        except ValueError as exc:
            abort(400, description=str(exc))
        return send_file(BytesIO(image), mimetype='image/png')

    @app.post('/api/ocr-learning/samples')
    def learning_save():
        data = request.get_json() or {}
        try:
            with events.connection(manager.root) as db:
                identifier = ocr_learning.save_sample(manager.root, data.get('learning'), data, db)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            abort(400, description=str(exc))
        return jsonify(id=identifier), 201

    @app.delete('/api/ocr-learning/samples/<identifier>')
    def learning_delete(identifier):
        with events.connection(manager.root) as db:
            db.execute('DELETE FROM ocr_sample_fields WHERE sample_id=?', (identifier,))
            db.execute('DELETE FROM ocr_samples WHERE id=?', (identifier,))
        return jsonify(status='deleted')

    @app.post('/api/ocr-learning/train')
    def learning_train():
        try:
            identifier = trainer.start()
        except ValueError as exc:
            abort(400, description=str(exc))
        return jsonify(id=identifier), 202

    @app.post('/api/ocr-learning/activate')
    def learning_activate():
        data = request.get_json()
        if not isinstance(data, dict) or 'id' not in data:
            abort(400, description='適用するモデルを指定してください。')
        try:
            with trainer.lock:
                ocr_learning.set_active(manager.root, data['id'])
        except ValueError as exc:
            abort(400, description=str(exc))
        return jsonify(active=data['id'], message='次に開始する認識処理から適用します。')

    @app.post('/api/vehicles')
    def add_vehicle():
        data = request.get_json() or {}
        try:
            with events.connection(manager.root) as db:
                vehicle_id = events.register_vehicle(manager.root, data, database=db)
                if data.get('learning') is not None:
                    ocr_learning.save_sample(manager.root, data['learning'], data, db)
        except (ValueError,KeyError,TypeError,sqlite3.IntegrityError) as error:
            abort(400,description='登録できません: ' + str(error))
        return jsonify(id=vehicle_id),201

    @app.post('/api/vehicles/batch')
    def add_vehicle_batch():
        data = request.get_json()
        items = data.get('items') if isinstance(data, dict) else None
        if not isinstance(items, list) or not 1 <= len(items) <= 100:
            abort(400, description='一括登録は1〜100件で指定してください。')
        added, skipped = [], []
        try:
            with events.connection(manager.root) as db:
                db.execute('BEGIN IMMEDIATE')
                for item in items:
                    try:
                        identifier = events.register_vehicle(manager.root, item, database=db)
                        added.append(dict(id=identifier, key=events.plate_key(item)))
                    except sqlite3.IntegrityError:
                        # Existing registrations, including disabled entries, are never overwritten.
                        key = events.plate_key(item)
                        if not db.execute('SELECT id FROM vehicles WHERE plate_key=?', (key,)).fetchone(): raise
                        skipped.append(key)
        except (ValueError, KeyError, TypeError, sqlite3.IntegrityError):
            abort(400, description='登録内容が不正なため、一括登録を取り消しました。内容を確認してください。')
        return jsonify(added=added, skipped=skipped), 201

    @app.delete('/api/vehicles/<vehicle_id>')
    def delete_vehicle(vehicle_id):
        with events.connection(manager.root) as db:
            cursor = db.execute('DELETE FROM vehicles WHERE id=?', (vehicle_id,))
            if cursor.rowcount == 0:
                abort(404, description='登録車両が見つかりません。')
        return jsonify(status='deleted')

    @app.post('/api/vehicles/<vehicle_id>')
    def update_vehicle(vehicle_id):
        with events.connection(manager.root) as db:
            if not db.execute('SELECT id FROM vehicles WHERE id=?',(vehicle_id,)).fetchone(): abort(404)
        try:
            data = request.get_json() or {}
            with events.connection(manager.root) as db:
                events.register_vehicle(manager.root, data, vehicle_id, database=db)
                if data.get('learning') is not None:
                    ocr_learning.save_sample(manager.root, data['learning'], data, db)
        except (ValueError,KeyError,TypeError,sqlite3.IntegrityError):
            abort(400,description='登録内容を確認してください。同じナンバーは重複登録できません。')
        return jsonify(id=vehicle_id)

    @app.get('/api/alerts')
    def alerts():
        with events.connection(manager.root) as db:
            total=db.execute('SELECT count(*) FROM alerts').fetchone()[0]
            unread=db.execute('SELECT count(*) FROM alerts WHERE acknowledged=0').fetchone()[0]
            # Limit by recency first, then put unacknowledged items first within
            # those ten while retaining newest-first order in each group.
            rows=db.execute('''SELECT * FROM (
                SELECT * FROM alerts ORDER BY created_at DESC,id DESC LIMIT 10
                ) recent ORDER BY acknowledged ASC,created_at DESC,id DESC''').fetchall()
        items=[]
        for row in rows:
            item=dict(row)
            item['reason_label']=events.REASONS[item['reason']]
            item['has_media']=bool(item.pop('media_path'))
            item.pop('dedupe_key')
            items.append(item)
        return jsonify(items=items,total=total,unread=unread,hidden=max(0,total-len(items)),limit=10,
                       email_configured=events.email_configured(),s3_configured=bool(os.getenv('GATE_S3_BUCKET')))

    @app.post('/api/alerts/ack-all')
    def acknowledge_all():
        with events.connection(manager.root) as db:
            changed=db.execute('UPDATE alerts SET acknowledged=1 WHERE acknowledged=0').rowcount
        return jsonify(status='acknowledged', changed=changed)

    @app.post('/api/alerts/<alert_id>/ack')
    def acknowledge(alert_id):
        with events.connection(manager.root) as db:
            changed=db.execute('UPDATE alerts SET acknowledged=1 WHERE id=?',(alert_id,)).rowcount
            if not changed: abort(404)
        return jsonify(status='acknowledged')

    @app.post('/api/alerts/<alert_id>/retry-email')
    def retry_email(alert_id):
        if not events.email_configured(): abort(400,description='メール送信元・宛先が未設定です。')
        with events.connection(manager.root) as db:
            changed=db.execute("UPDATE alerts SET email_status='pending',email_attempts=0,email_next=0,email_error=NULL WHERE id=? AND email_status IN ('failed','disabled')",(alert_id,)).rowcount
            if not changed: abort(409,description='このメールは再送対象ではありません。')
        return jsonify(status='pending')

    @app.get('/api/alerts/<alert_id>/media')
    def alert_media(alert_id):
        with events.connection(manager.root) as db:
            row=db.execute('SELECT media_path,media_status FROM alerts WHERE id=?',(alert_id,)).fetchone()
        if not row or not row['media_path'] or row['media_status'] not in ('ready','partial'): abort(404)
        path=Path(row['media_path']).resolve()
        if not path.is_relative_to((manager.root/'events').resolve()) or not path.is_file(): abort(404)
        return send_file(path,conditional=True,as_attachment=path.suffix=='.avi')

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--data', default='data')
    parser.add_argument('--model', default='yolo11n.pt')
    args = parser.parse_args()
    if args.host not in ('127.0.0.1', 'localhost', '::1') and not os.environ.get('GATE_ADMIN_PASSWORD'):
        parser.error('LAN公開には環境変数GATE_ADMIN_PASSWORDを設定してください。')
    app = create_app(args.data, args.model)
    manager = app.extensions['jobs']
    dispatcher = events.Dispatcher(manager.root)
    dispatcher.start()
    atexit.register(dispatcher.stop)
    atexit.register(manager.shutdown)
    def terminate_server(signum, frame):
        manager.shutdown()
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, terminate_server)
    from waitress import serve
    print(f'AI Gate System: http://{args.host}:{args.port}', flush=True)
    try:
        serve(app, host=args.host, port=args.port, threads=8)
    finally:
        manager.shutdown()
        dispatcher.stop()


if __name__ == '__main__':
    main()
