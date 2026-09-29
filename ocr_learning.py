"""Reviewed OCR samples and immutable training runs; no inference-time substitutions."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import threading
import unicodedata
import uuid

import events
from plate_rules import KANA_PATTERN


def initialize(root):
    with events.connection(root) as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS ocr_samples (
          id TEXT PRIMARY KEY, observation_id TEXT NOT NULL, candidate_index INTEGER NOT NULL,
          plate_key TEXT NOT NULL, original_text TEXT NOT NULL, top_text TEXT NOT NULL,
          bottom_text TEXT NOT NULL, split REAL NOT NULL, image BLOB NOT NULL,
          image_sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
          UNIQUE(observation_id,candidate_index));
        CREATE TABLE IF NOT EXISTS ocr_sample_fields (
          sample_id TEXT PRIMARY KEY, fields_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS ocr_auto_candidates (
          observation_id TEXT NOT NULL, candidate_index INTEGER NOT NULL,
          plate_key TEXT NOT NULL, confidence REAL NOT NULL,
          status TEXT NOT NULL, created_at TEXT NOT NULL,
          PRIMARY KEY(observation_id,candidate_index));
        CREATE TABLE IF NOT EXISTS ocr_auto_archive (
          observation_id TEXT PRIMARY KEY, details_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS ocr_training_runs (
          id TEXT PRIMARY KEY, status TEXT NOT NULL, created_at TEXT NOT NULL,
          report_json TEXT, error TEXT);
        CREATE UNIQUE INDEX IF NOT EXISTS ocr_one_run ON ocr_training_runs(status)
          WHERE status='running';
        ''')
        columns = {row[1] for row in db.execute('PRAGMA table_info(ocr_samples)')}
        if 'source' not in columns:
            db.execute("ALTER TABLE ocr_samples ADD COLUMN source TEXT NOT NULL DEFAULT 'manual'")
        auto_columns = {row[1] for row in db.execute('PRAGMA table_info(ocr_auto_candidates)')}
        if 'last_error' not in auto_columns:
            db.execute('ALTER TABLE ocr_auto_candidates ADD COLUMN last_error TEXT')
        db.execute('CREATE INDEX IF NOT EXISTS ocr_samples_retention ON ocr_samples(source,created_at DESC,id DESC)')
        # Apply the current threshold to candidates recorded before the change.
        db.execute("UPDATE ocr_auto_candidates SET status='pseudo' WHERE status='pending' AND confidence>=0.80")
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='observations'").fetchone():
            records = db.execute('''SELECT DISTINCT o.details_json FROM observations o
                JOIN ocr_auto_candidates c ON c.observation_id=o.id
                WHERE c.status='pseudo' AND NOT EXISTS
                (SELECT 1 FROM ocr_samples s WHERE s.observation_id=o.id AND s.candidate_index=c.candidate_index)''').fetchall()
            for row in records:
                queue_observation(root, json.loads(row[0]), db)
        archived = db.execute('''SELECT a.details_json FROM ocr_auto_archive a
            JOIN ocr_auto_candidates c ON c.observation_id=a.observation_id
            WHERE c.status='pseudo' AND NOT EXISTS
            (SELECT 1 FROM ocr_samples s WHERE s.observation_id=a.observation_id
             AND s.candidate_index=c.candidate_index)''').fetchall()
        for row in archived:
            queue_observation(root, json.loads(row[0]), db)
        limit_automatic_samples(db, root)


def queue_observation(root, record, db, minimum=.80):
    """Record eligible Lipla labels as automatic training samples, never evaluation truth."""
    if not record.get('image_path') or not Path(record['image_path']).is_file():
        return 0
    accepted = 0
    for index, candidate in enumerate(record.get('plate_candidates', [])):
        if db.execute("SELECT 1 FROM ocr_samples WHERE observation_id=? AND candidate_index=? AND source='manual'",
                      (record['id'], index)).fetchone():
            db.execute('DELETE FROM ocr_auto_candidates WHERE observation_id=? AND candidate_index=?',
                       (record['id'], index))
            continue
        if candidate.get('ocr_backend') not in ('lipla-native', 'lipla-jp') or not candidate.get('fields'):
            continue
        try:
            key = events.plate_key(candidate['fields'])
            confidence = float(candidate.get('confidence') or 0)
        except (ValueError, TypeError, KeyError):
            continue
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            continue
        status = 'pseudo' if confidence >= minimum else 'pending'
        db.execute('''INSERT INTO ocr_auto_candidates (observation_id,candidate_index,plate_key,confidence,status,created_at) VALUES (?,?,?,?,?,?)
            ON CONFLICT(observation_id,candidate_index) DO UPDATE SET
            plate_key=excluded.plate_key,confidence=excluded.confidence,status=excluded.status
            WHERE NOT EXISTS (SELECT 1 FROM ocr_samples s WHERE
              s.observation_id=excluded.observation_id AND s.candidate_index=excluded.candidate_index
              AND s.source='manual')
              AND ocr_auto_candidates.status!='excluded' ''',
                   (record['id'], index, key, confidence, status, events.utc()))
        persisted = db.execute('SELECT status FROM ocr_auto_candidates WHERE observation_id=? AND candidate_index=?',
                               (record['id'], index)).fetchone()
        if persisted and persisted[0] == 'excluded':
            continue
        if status == 'pseudo':
            try:
                image, original = sample_image(root, record['id'], index, db)
                parts = candidate['fields']
                db.execute('''INSERT INTO ocr_samples
                    (id,observation_id,candidate_index,plate_key,original_text,top_text,bottom_text,
                     split,image,image_sha256,created_at,source)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(observation_id,candidate_index) DO NOTHING''',
                    (uuid.uuid4().hex, record['id'], index, key, original,
                     parts['region']+parts['category'], parts['kana']+parts['serial'], .45,
                     sqlite3.Binary(image), hashlib.sha256(image).hexdigest(), events.utc(), 'automatic'))
                db.execute('''UPDATE ocr_auto_candidates SET last_error=NULL
                    WHERE observation_id=? AND candidate_index=?''', (record['id'], index))
            except (ValueError, KeyError, OSError, ImportError) as error:
                db.execute('''UPDATE ocr_auto_candidates SET last_error=?
                    WHERE observation_id=? AND candidate_index=?''',
                    (str(error)[:200], record['id'], index))
        accepted += 1
    limit_automatic_samples(db, root)
    return accepted


def limit_automatic_samples(db, root, limit=500):
    """Keep manual truth indefinitely; bound unreviewed crops and archived vehicle frames."""
    old = db.execute('''SELECT id,observation_id,candidate_index FROM ocr_samples
        WHERE source='automatic' ORDER BY created_at DESC,id DESC LIMIT -1 OFFSET ?''',
                     (limit,)).fetchall()
    for identifier, observation_id, index in old:
        db.execute('DELETE FROM ocr_samples WHERE id=? AND source=\'automatic\'', (identifier,))
        db.execute("UPDATE ocr_auto_candidates SET status='excluded' WHERE observation_id=? AND candidate_index=?",
                   (observation_id, index))
        remaining = db.execute("SELECT 1 FROM ocr_auto_candidates WHERE observation_id=? AND status='pseudo' LIMIT 1",
                               (observation_id,)).fetchone()
        if not remaining:
            archived = db.execute('SELECT details_json FROM ocr_auto_archive WHERE observation_id=?',
                                  (observation_id,)).fetchone()
            if archived:
                db.execute('DELETE FROM ocr_auto_archive WHERE observation_id=?', (observation_id,))
                path = Path(json.loads(archived[0])['image_path']).resolve()
                if path.is_relative_to((Path(root) / 'images' / 'learning').resolve()):
                    path.unlink(missing_ok=True)
    return len(old)


def auto_signature(root):
    try:
        with events.connection(root) as db:
            return [tuple(row) for row in db.execute('''SELECT observation_id,candidate_index,plate_key,confidence
                FROM ocr_auto_candidates WHERE status='pseudo' ORDER BY observation_id,candidate_index''')]
    except sqlite3.OperationalError:
        return []


def sample_image(root, observation_id, candidate_index, db):
    import cv2
    import numpy as np
    row = db.execute('SELECT details_json FROM observations WHERE id=?', (observation_id,)).fetchone()
    if row is None:
        row = db.execute('SELECT details_json FROM ocr_auto_archive WHERE observation_id=?',
                         (observation_id,)).fetchone()
    if row is None:
        raise ValueError('元の認識結果が見つかりません。')
    record = json.loads(row[0])
    candidates = record.get('plate_candidates', [])
    if type(candidate_index) is not int or not 0 <= candidate_index < len(candidates):
        raise ValueError('画像のあるナンバー候補を選択してください。')
    source = record.get('image_path')
    if not source:
        raise ValueError('元画像がありません。画像保存を有効にして再撮影してください。')
    path = Path(source).resolve()
    if not path.is_relative_to(Path(root).resolve() / 'images') or not path.is_file():
        raise ValueError('元画像を読み込めません。')
    image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError('元画像を読み込めません。')
    bounds = candidates[candidate_index].get('bbox_in_vehicle', [])
    if len(bounds) != 4 or any(type(v) is not int for v in bounds):
        raise ValueError('候補の画像範囲が不正です。')
    x1, y1, x2, y2 = bounds
    if not (0 <= x1 < x2 <= image.shape[1] and 0 <= y1 < y2 <= image.shape[0]):
        raise ValueError('候補の画像範囲が不正です。')
    plate = image[y1:y2, x1:x2]
    if candidates[candidate_index].get('quad_in_vehicle') is not None:
        from plate_geometry import warp_plate
        try:
            plate = warp_plate(image, candidates[candidate_index]['quad_in_vehicle'], cv2)
        except ValueError:
            pass  # Retain the detected rectangle when perspective correction fails.
    if plate.shape[0] < 16 or plate.shape[1] < 32:
        raise ValueError('学習画像が小さすぎます。近づいて再撮影してください。')
    if plate.shape[0] < 24 or plate.shape[1] < 48:
        plate = cv2.resize(plate, (max(48, plate.shape[1]), max(24, plate.shape[0])),
                           interpolation=cv2.INTER_CUBIC)
    ok, encoded = cv2.imencode('.png', plate)
    if not ok:
        raise ValueError('画像の保存に失敗しました。')
    return encoded.tobytes(), candidates[candidate_index].get('text', '')


def save_sample(root, data, fields, db):
    from app import parse_plate
    if not isinstance(data, dict) or data.get('confirmed') is not True:
        raise ValueError('各項目の画像と正解を確認してください。')
    field_data = validate_fields(data.get('fields'), fields) if 'fields' in data else None
    if field_data:
        data = dict(data, top_text=field_data['region']['text'] + field_data['category']['text'],
                    bottom_text=field_data['kana']['text'] + field_data['serial']['text'])
    top = unicodedata.normalize('NFKC', str(data.get('top_text', '')))
    bottom = unicodedata.normalize('NFKC', str(data.get('bottom_text', '')))
    top, bottom = re.sub(r'\s+', '', top), re.sub(r'\s+', '', bottom)
    parsed = parse_plate(top + bottom)
    if (not re.fullmatch(r'[一-龥ぁ-んァ-ヶ]{2,8}[0-9][0-9A-Z]{2}', top) or
            not re.fullmatch(rf'{KANA_PATTERN}[0-9]{{1,4}}', bottom) or not parsed or
            events.plate_key(parsed) != events.plate_key(fields)):
        raise ValueError('学習用の正解と登録ナンバーを一致させてください。')
    split = data.get('split', .45)
    if type(split) not in (int, float) or not math.isfinite(split) or not .25 <= split <= .65:
        raise ValueError('上下段の境界を25〜65%で指定してください。')
    observation_id, index = data.get('observation_id'), data.get('candidate_index')
    if not isinstance(observation_id, str):
        raise ValueError('元画像を指定してください。')
    image, original = sample_image(root, observation_id, index, db)
    identifier = uuid.uuid4().hex
    db.execute('''INSERT INTO ocr_samples
        (id,observation_id,candidate_index,plate_key,original_text,top_text,bottom_text,
         split,image,image_sha256,created_at,source) VALUES (?,?,?,?,?,?,?,?,?,?,?, 'manual')
        ON CONFLICT(observation_id,candidate_index) DO UPDATE SET
        plate_key=excluded.plate_key,top_text=excluded.top_text,bottom_text=excluded.bottom_text,
        split=excluded.split,image=excluded.image,image_sha256=excluded.image_sha256,
        created_at=excluded.created_at,source='manual' ''',
        (identifier, observation_id, index, events.plate_key(fields), original, top, bottom,
         float(split), sqlite3.Binary(image), hashlib.sha256(image).hexdigest(), events.utc()))
    identifier = db.execute('SELECT id FROM ocr_samples WHERE observation_id=? AND candidate_index=?',
                            (observation_id, index)).fetchone()[0]
    db.execute('DELETE FROM ocr_auto_candidates WHERE observation_id=? AND candidate_index=?',
               (observation_id, index))
    if field_data:
        db.execute('INSERT INTO ocr_sample_fields VALUES (?,?) ON CONFLICT(sample_id) DO UPDATE SET fields_json=excluded.fields_json',
                   (identifier, json.dumps(field_data, ensure_ascii=False)))
    else:
        db.execute('DELETE FROM ocr_sample_fields WHERE sample_id=?', (identifier,))
    return identifier


def validation_group(key):
    return int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) % 10 in (2, 3)


def test_group(key):
    return int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) % 10 in (0, 1)


def dataset_snapshot(root, minimum_train=5):
    with events.connection(root) as db:
        rows = [dict(r) for r in db.execute("SELECT s.*,f.fields_json FROM ocr_samples s LEFT JOIN ocr_sample_fields f ON f.sample_id=s.id ORDER BY CASE WHEN s.source='manual' THEN 0 ELSE 1 END,s.id")]
    # Identical images with incompatible labels must never become supervision.
    seen = {}
    samples = []
    for row in rows:
        signature = (row['top_text'], row['bottom_text'], row['split'], row['fields_json'])
        digest = row['image_sha256']
        if digest in seen:
            if seen[digest][0] != signature and row['source'] == 'manual':
                raise ValueError('同一画像に異なる正解があります。学習データ一覧から誤ったデータを削除してください。')
            continue
        seen[digest] = (signature, row['source'])
        row['partition'] = ('train' if row['source'] == 'automatic' else
                            'test' if test_group(row['plate_key']) else
                            'validation' if validation_group(row['plate_key']) else 'train')
        samples.append(row)
    train = {r['plate_key'] for r in samples if r['partition'] == 'train'}
    valid = {r['plate_key'] for r in samples if r['partition'] == 'validation' and r['source'] == 'manual'}
    test = {r['plate_key'] for r in samples if r['partition'] == 'test' and r['source'] == 'manual'}
    if len(train) < minimum_train or len(valid) < 2 or len(test) < 2:
        raise ValueError(f'異なるナンバーが不足しています（手動確認済み学習用 {len(train)}/{minimum_train}、検証用 {len(valid)}/2、未使用テスト用 {len(test)}/2）。ナンバーごとに固定で約6:2:2に振り分けます。')
    return samples


def learning_dir(root):
    path = Path(root) / 'ocr-learning'
    path.mkdir(exist_ok=True)
    return path


def active_model(root):
    path = learning_dir(root) / 'active.json'
    return json.loads(path.read_text())['id'] if path.exists() else None


def model_dir(root, identifier):
    if not isinstance(identifier, str) or not re.fullmatch('[0-9a-f]{32}', identifier):
        raise ValueError('モデルIDが不正です。')
    return learning_dir(root) / identifier


def set_active(root, identifier):
    if identifier is not None:
        directory = model_dir(root, identifier)
        with events.connection(root) as db:
            row = db.execute('SELECT status,report_json FROM ocr_training_runs WHERE id=?', (identifier,)).fetchone()
        if not row or row['status'] != 'completed' or not json.loads(row['report_json']).get('eligible'):
            raise ValueError('評価に合格したモデルだけを適用できます。')
        if not (directory / 'weights.pth').is_file():
            raise ValueError('学習済みモデルが見つかりません。')
    path = learning_dir(root) / 'active.json'
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps({'id': identifier}))
    os.replace(temporary, path)
    # A benchmark made before changing EasyOCR weights is no longer valid.
    (learning_dir(root) / 'benchmark.json').unlink(missing_ok=True)


def active_fast_model(root):
    path = learning_dir(root) / 'active-fast.json'
    return json.loads(path.read_text()) if path.exists() else None


def set_active_fast(root, identifier):
    if identifier is not None:
        directory = model_dir(root, identifier)
        with events.connection(root) as db:
            row = db.execute('SELECT status,report_json FROM ocr_training_runs WHERE id=?', (identifier,)).fetchone()
        report = json.loads(row['report_json']) if row and row['report_json'] else {}
        if not row or row['status'] != 'completed' or report.get('backend') != 'fast-plate-ocr' or not report.get('eligible'):
            raise ValueError('評価に合格した日本語FastPlateOCRモデルだけを適用できます。')
        directory = directory.resolve()
        model = (directory / report.get('candidate_model', '')).resolve()
        config = (directory / report.get('candidate_config', '')).resolve()
        if not model.is_file() or not config.is_file() or not model.is_relative_to(directory) or not config.is_relative_to(directory):
            raise ValueError('FastPlateOCR学習済みモデルまたは設定が見つかりません。')
        value = {'id': identifier, 'model': str(model), 'config': str(config)}
    else:
        value = None
    path = learning_dir(root) / 'active-fast.json'
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False))
    os.replace(temporary, path)


def load_weights(reader, directory):
    import torch
    metadata = json.loads((directory / 'model.json').read_text())
    if metadata['character_sha256'] != hashlib.sha256(reader.character.encode()).hexdigest():
        raise ValueError('学習モデルの文字集合が現在のOCRと一致しません。')
    weights = torch.load(directory / 'weights.pth', map_location='cpu', weights_only=True)
    reader.recognizer.load_state_dict(weights, strict=True)
    reader.recognizer.eval()


def make_reader(root, easyocr, offline=False):
    identifier = active_model(root)
    from inference_device import torch_device
    device = torch_device()
    options = dict(gpu=False if device == 'cpu' else device, download_enabled=not offline)
    if identifier:
        options['quantize'] = False
    reader = easyocr.Reader(['ja', 'en'], **options)
    if identifier:
        load_weights(reader, model_dir(root, identifier))
    return reader


class TrainingManager:
    def __init__(self, root):
        self.root = Path(root)
        self.lock = threading.RLock()
        self.process = None
        self._fast = None
        initialize(root)
        with events.connection(root) as db:
            db.execute("UPDATE ocr_training_runs SET status='interrupted',error='サーバー再起動で学習が中断されました。' WHERE status='running'")

    def start(self):
        if os.getenv('GATE_OCR_TRAINING_BACKEND', 'easyocr').strip().lower() in ('fast-plate-ocr', 'fastplateocr'):
            from fast_plate_ocr_training import FastPlateOCRTrainingManager
            with self.lock:
                if self._fast is None:
                    self._fast = FastPlateOCRTrainingManager(self.root)
                return self._fast.start()
        with self.lock:
            samples = dataset_snapshot(self.root)
            identifier = uuid.uuid4().hex
            with events.connection(self.root) as db:
                try:
                    db.execute('INSERT INTO ocr_training_runs VALUES (?,?,?,NULL,NULL)',
                               (identifier, 'running', events.utc()))
                except sqlite3.IntegrityError:
                    raise ValueError('再学習はすでに実行中です。')
            try:
                directory = model_dir(self.root, identifier)
                directory.mkdir()
                manifest = []
                for sample in samples:
                    image = sample.pop('image')
                    (directory / (sample['id'] + '.png')).write_bytes(image)
                    manifest.append(sample)
                (directory / 'dataset.json').write_text(json.dumps(manifest, ensure_ascii=False))
                baseline = active_model(self.root)
                command = [sys.executable, str(Path(__file__).with_name('ocr_train.py')),
                           '--root', str(self.root), '--run', identifier]
                if baseline:
                    command += ['--baseline', baseline]
                log = (directory / 'train.log').open('w')
                try:
                    self.process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
                finally:
                    log.close()
                threading.Thread(target=self._wait, args=(identifier, self.process), daemon=True).start()
            except Exception as exc:
                self._finish(identifier, 'failed', error=str(exc))
                raise
            return identifier

    def _finish(self, identifier, status, report=None, error=None):
        with events.connection(self.root) as db:
            db.execute('UPDATE ocr_training_runs SET status=?,report_json=?,error=? WHERE id=?',
                       (status, json.dumps(report) if report else None, error, identifier))

    def _wait(self, identifier, process):
        try:
            code = process.wait(timeout=7200)
            directory = model_dir(self.root, identifier)
            if code:
                raise ValueError((directory / 'train.log').read_text()[-3000:])
            report = json.loads((directory / 'report.json').read_text())
            self._finish(identifier, 'completed', report)
        except Exception as exc:
            if process.poll() is None:
                process.kill()
                process.wait()
            self._finish(identifier, 'failed', error=str(exc))
        finally:
            with self.lock:
                if self.process is process:
                    self.process = None

    def shutdown(self):
        with self.lock:
            if self.process and self.process.poll() is None:
                self.process.terminate()
            if self._fast:
                self._fast.shutdown()

    def maybe_start_auto(self):
        """Start one background run after enough reviewed samples are available."""
        if os.getenv('GATE_OCR_TRAINING_AUTO', '0').strip() not in ('1', 'true', 'yes'):
            return None
        minimum = int(os.getenv('GATE_OCR_TRAINING_MIN_SAMPLES', '7'))
        with events.connection(self.root) as db:
            count = db.execute('SELECT count(*) FROM ocr_samples').fetchone()[0]
            running = db.execute("SELECT 1 FROM ocr_training_runs WHERE status='running'").fetchone()
        if count < minimum or running:
            return None
        try:
            with self.lock:
                samples = dataset_snapshot(self.root)
                fingerprint = hashlib.sha256(json.dumps([
                    {k: s.get(k) for k in ('id', 'image_sha256', 'top_text', 'bottom_text', 'fields_json', 'split')}
                    for s in samples], sort_keys=True).encode()).hexdigest()
                marker = learning_dir(self.root) / 'auto-dataset.json'
                if marker.exists() and json.loads(marker.read_text()).get('sha256') == fingerprint:
                    return None
                identifier = self.start()
                marker.write_text(json.dumps({'sha256': fingerprint, 'run': identifier}))
                return identifier
        except ValueError:
            return None


FIELD_NAMES = ('region', 'category', 'kana', 'serial')


def validate_fields(value, registered):
    if not isinstance(value, dict) or set(value) != set(FIELD_NAMES):
        raise ValueError('地名・分類番号・ひらがな・一連指定番号の4項目を指定してください。')
    normalized = {}
    for name in FIELD_NAMES:
        item = value[name]
        if not isinstance(item, dict):
            raise ValueError('項目ごとの正解と画像範囲を確認してください。')
        text = re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(item.get('text', ''))))
        box = item.get('box')
        if (not isinstance(box, list) or len(box) != 4 or
                any(type(v) not in (int, float) or not math.isfinite(v) for v in box) or
                not 0 <= box[0] < box[2] <= 1 or not 0 <= box[1] < box[3] <= 1 or
                box[2]-box[0] < .03 or box[3]-box[1] < .1):
            raise ValueError('学習画像の範囲が不正です。')
        normalized[name] = dict(text=text, box=box)
    if events.plate_key({k: normalized[k]['text'] for k in FIELD_NAMES}) != events.plate_key(registered):
        raise ValueError('4項目の正解と登録ナンバーを一致させてください。')
    return normalized


def training_crops(sample, width, height):
    fields = json.loads(sample.get('fields_json') or 'null')
    if fields:
        result = []
        for name in FIELD_NAMES:
            item = fields[name]
            x1, y1, x2, y2 = item['box']
            box = (round(x1*width), round(y1*height), round(x2*width), round(y2*height))
            if box[2] <= box[0] or box[3] <= box[1]:
                raise ValueError('学習範囲が小さすぎます。')
            result.append((item['text'], box))
        return result
    boundary = round(height * sample['split'])
    return [(sample['top_text'], (0, 0, width, boundary)),
            (sample['bottom_text'], (0, boundary, width, height))]
