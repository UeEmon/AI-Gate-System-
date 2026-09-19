"""AI Gate System: OpenCV vehicle and Japanese plate recognition prototype."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import unicodedata
import uuid
import os
import threading
import time
import signal

VEHICLES = {'car': '乗用車', 'motorcycle': '二輪車', 'bus': 'バス', 'truck': 'トラック'}


def parse_plate(text):
    """Conservative ordinary Japanese plate parser; a match is NOT verification."""
    normalized = unicodedata.normalize('NFKC', text)
    compact = re.sub(r'\s+', '', normalized).replace('−', '-').replace('ー', '-')
    match = re.fullmatch(r'([一-龥ぁ-んァ-ヶ]{2,8})([0-9][0-9A-Z]{2})([ぁ-ん])([0-9・.\-]{1,7})', compact)
    if not match:
        return None
    region, category, kana, serial = match.groups()
    digits = re.sub(r'[^0-9]', '', serial)
    if not 1 <= len(digits) <= 4:
        return None
    return dict(region=region, category=category, kana=kana, serial=digits)


def open_database(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute('''CREATE TABLE IF NOT EXISTS observations (
        id TEXT PRIMARY KEY, processed_at TEXT NOT NULL,
        run_id TEXT NOT NULL, frame_index INTEGER NOT NULL,
        media_ms REAL, vehicle_type TEXT NOT NULL, confidence REAL NOT NULL,
        image_path TEXT, details_json TEXT NOT NULL)''')
    return db


def save_observation(db, record):
    with db:
        db.execute('INSERT INTO observations VALUES (?,?,?,?,?,?,?,?,?)', (
            record['id'], record['processed_at'], record['run_id'], record['frame_index'],
            record['media_ms'], record['vehicle_type'], record['confidence'],
            record['image_path'], json.dumps(record, ensure_ascii=False)))


def plate_regions(crop, cv2):
    """Heuristic rectangle candidates; no trained plate detector is bundled."""
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 60, 180)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    height, width = gray.shape
    boxes = []
    for contour in sorted(contours, key=cv2.contourArea, reverse=True):
        x, y, w, h = cv2.boundingRect(contour)
        if w < 40 or h < 18 or not 1.1 <= w / h <= 2.7:
            continue
        if not 0.002 <= w * h / (width * height) <= 0.3:
            continue
        if cv2.contourArea(contour) / (w * h) < 0.45:
            continue
        # Suppress almost identical nested contours.
        if any(abs(x-a) < 10 and abs(y-b) < 10 and abs(w-c) < 15 and abs(h-d) < 15
               for a, b, c, d in boxes):
            continue
        boxes.append((x, y, w, h))
        if len(boxes) == 5:
            break
    return boxes


def plate_text(items):
    """Read two rows without merging a tall lower digit into the upper row."""
    rows = []
    for box, text, score in sorted(items, key=lambda item: min(p[1] for p in item[0])):
        if not text.strip():
            continue
        top, bottom = min(p[1] for p in box), max(p[1] for p in box)
        height = max(1, bottom - top)
        row = next((r for r in rows if
                    abs((top + bottom) / 2 - (r['top'] + r['bottom']) / 2) <=
                    0.6 * min(height, r['bottom'] - r['top'])), None)
        if row is None:
            row = {'top': top, 'bottom': max(bottom, top + 1), 'items': []}
            rows.append(row)
        row['items'].append((min(p[0] for p in box), text, float(score)))
    fragments = [item for row in rows for item in sorted(row['items'])]
    return (' '.join(item[1] for item in fragments),
            min((item[2] for item in fragments), default=0.0))


def read_plate(crop, reader, cv2):
    candidates = []
    height, width = crop.shape[:2]
    for x, y, w, h in plate_regions(crop, cv2):
        # Keep a little context so characters touching the contour are not cut.
        px, py = max(2, round(w * 0.04)), max(2, round(h * 0.04))
        roi = crop[max(0, y-py):min(height, y+h+py), max(0, x-px):min(width, x+w+px)]
        scale = max(1.0, min(4.0, 480 / roi.shape[1]))
        roi = cv2.resize(roi, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        attempts = []
        for variant in range(2):
            image = roi
            if variant:
                gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
                image = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
            items = reader.readtext(image, detail=1, paragraph=False,
                                    decoder='beamsearch', beamWidth=5)
            text, confidence = plate_text(items)
            fields = parse_plate(text)
            attempts.append({'bbox_in_vehicle': [x, y, x+w, y+h], 'text': text,
                             'confidence': confidence, 'fields': fields,
                             'preprocessing': 'clahe' if variant else 'color',
                             'status': 'candidate' if fields and confidence >= 0.6 else 'needs_review'})
            # Bound CPU cost: retry only incomplete or low-confidence readings.
            if fields and confidence >= 0.6:
                break
        best = max(attempts, key=lambda c: (c['fields'] is not None, c['confidence']))
        if best['text']:
            candidates.append(best)
    return sorted(candidates, key=lambda c: (c['fields'] is not None, c['confidence']), reverse=True)


def frames(source, cv2, every, on_frame=None):
    path = Path(source)
    if path.suffix.lower() in {'.jpg', '.jpeg', '.png', '.bmp', '.webp', '.tif', '.tiff'}:
        import numpy as np
        frame = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError('画像を読み込めません。')
        if on_frame: on_frame(frame, 0.0)
        yield 0, None, frame
        return
    capture = cv2.VideoCapture(int(source) if source.isdecimal() else source)
    if not capture.isOpened():
        capture.release()
        raise ValueError('動画またはカメラを開けません。入力と接続を確認してください。')
    index = 0
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    if not 0 < fps < 1000: fps = 25.0
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                if index == 0:
                    raise ValueError('入力を開きましたがフレームを取得できません。')
                break
            seconds = index / fps
            if on_frame: on_frame(frame, seconds)
            if index % every == 0:
                yield index, seconds * 1000, frame
            index += 1
    finally:
        capture.release()


def live_frames(source, cv2, every, on_frame=None, stop_event=None):
    """Read continuously and keep one latest frame, rather than a backlog."""
    condition = threading.Condition()
    stop = stop_event or threading.Event()
    state = {'item': None, 'done': False, 'error': None}

    def capture_loop():
        capture = None
        try:
            capture = cv2.VideoCapture(int(source) if source.isdecimal() else source)
            if not capture.isOpened():
                raise ValueError('カメラを開けません。接続と設定を確認してください。')
            index = 0
            started = time.monotonic()
            while not stop.is_set():
                ok, frame = capture.read()
                if not ok:
                    raise ValueError('カメラ映像の取得が停止しました。再接続して開始してください。')
                seconds = time.monotonic() - started
                if on_frame: on_frame(frame, seconds)
                if index % every == 0:
                    with condition:
                        state['item'] = (index, seconds * 1000, frame)
                        condition.notify_all()
                index += 1
        except Exception as exc:
            with condition:
                state['error'] = str(exc)
        finally:
            if capture is not None:
                capture.release()
            with condition:
                state['done'] = True
                condition.notify_all()

    thread = threading.Thread(target=capture_loop, daemon=True)
    thread.start()
    try:
        while True:
            with condition:
                condition.wait_for(lambda: state['item'] is not None or state['done'] or stop.is_set(), timeout=0.5)
                if stop.is_set(): return
                item = state['item']
                state['item'] = None
                done, error = state['done'], state['error']
            if item is not None:
                yield item
            elif done:
                if error:
                    raise ValueError(error)
                return
    finally:
        stop.set()
        thread.join(timeout=1)


def browser_frames(directory, cv2, every, on_frame=None, stop_event=None):
    """Read the latest JPEG uploaded by the operator's browser camera."""
    import numpy as np
    folder = Path(directory)
    stop = stop_event or threading.Event()
    last_mtime = 0
    index = 0
    try:
        while not stop.is_set():
            image = folder / 'latest.jpg'
            try:
                stamp = image.stat().st_mtime_ns
            except FileNotFoundError:
                stop.wait(.1)
                continue
            if stamp == last_mtime:
                stop.wait(.05)
                continue
            last_mtime = stamp
            frame = cv2.imdecode(np.fromfile(image, dtype=np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                continue
            seconds = time.monotonic()
            if on_frame:
                on_frame(frame, seconds)
            if index % every == 0:
                yield index, seconds * 1000, frame
            index += 1
    finally:
        stop.set()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    os.replace(temporary, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, help='写真、動画のパス、カメラ番号（0）またはRTSP URL')
    parser.add_argument('--model', default='yolo11n.pt', help='COCOクラス名を持つUltralytics検出モデル')
    parser.add_argument('--output', default='data')
    parser.add_argument('--every', type=int, default=10, help='動画・カメラをNフレームごとに処理')
    parser.add_argument('--confidence', type=float, default=0.4)
    parser.add_argument('--save-images', action='store_true', help='検出した車両の切り抜き画像を保存')
    parser.add_argument('--source-kind', choices=['auto', 'file', 'camera', 'browser'], default='auto')
    parser.add_argument('--run-id', default=None)
    parser.add_argument('--alerts', action='store_true', help='登録車両照合・通知イベント・映像保存')
    parser.add_argument('--progress', default=None, help='Web管理用の進捗JSON')
    parser.add_argument('--preview', default=None, help='最新の処理済みフレームJPEG')
    args = parser.parse_args()
    if args.every < 1 or not 0 < args.confidence <= 1:
        parser.error('--every は1以上、--confidence は0より大きく1以下です。')
    if args.progress:
        atomic_json(args.progress, {'phase': 'loading', 'frames_processed': 0, 'observations': 0})
    import cv2
    import easyocr
    from ultralytics import YOLO
    offline=os.getenv('GATE_OFFLINE')=='1'
    if offline and not Path(args.model).is_file():
        raise ValueError('オフライン用のYOLOモデルを事前に配置してください。')
    model = YOLO(args.model)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    from ocr_learning import make_reader
    reader = make_reader(out, easyocr, offline)
    run_id = args.run_id or uuid.uuid4().hex
    db = open_database(out / 'gate.db')
    is_live = args.source_kind in ('camera', 'browser') or (args.source_kind == 'auto' and
              (args.source.isdecimal() or args.source.lower().startswith(('rtsp://', 'rtsps://'))))
    stream = (browser_frames(args.source, cv2, args.every) if args.source_kind == 'browser' else
              live_frames(args.source, cv2, args.every) if is_live else frames(args.source, cv2, args.every))
    recorder = None
    stopped = threading.Event()
    old_term = None
    if args.alerts:
        import events
        from evidence import EvidenceRecorder
        events.initialize(out)
        recorder = EvidenceRecorder(out, still=Path(args.source).suffix.lower() in
                    {'.jpg','.jpeg','.png','.bmp','.webp','.tif','.tiff'} and not is_live)
        old_term = signal.signal(signal.SIGTERM, lambda *a: stopped.set())
        stream = (browser_frames(args.source, cv2, args.every, recorder.feed, stopped) if args.source_kind == 'browser' else
                  live_frames(args.source, cv2, args.every, recorder.feed, stopped) if is_live else
                  frames(args.source, cv2, args.every, recorder.feed))
    processed, observations = 0, 0
    try:
        for index, media_ms, frame in stream:
            if stopped.is_set(): break
            canvas = frame.copy() if args.preview else None
            result = model.predict(frame, conf=args.confidence, device='cpu', verbose=False)[0]
            for box in result.boxes:
                label = result.names[int(box.cls.item())]
                if label not in VEHICLES:
                    continue
                x1, y1, x2, y2 = [int(v) for v in box.xyxy[0].tolist()]
                height, width = frame.shape[:2]
                x1, y1, x2, y2 = max(0, x1), max(0, y1), min(width, x2), min(height, y2)
                if x2 <= x1 or y2 <= y1:
                    continue
                crop = frame[y1:y2, x1:x2]
                plates = read_plate(crop, reader, cv2)
                observation_id = uuid.uuid4().hex
                image_path = None
                if args.save_images:
                    folder = out / 'images'
                    folder.mkdir(exist_ok=True)
                    image = folder / (observation_id + '.jpg')
                    ok, encoded = cv2.imencode('.jpg', crop)
                    if not ok:
                        raise OSError('車両画像の保存に失敗しました。')
                    encoded.tofile(image)
                    image_path = str(image.resolve())
                record = dict(id=observation_id, run_id=run_id,
                              processed_at=datetime.now(timezone.utc).isoformat(),
                              frame_index=index, media_ms=media_ms, vehicle_type=label,
                              vehicle_type_ja=VEHICLES[label], confidence=float(box.conf.item()),
                              bbox=[x1, y1, x2, y2], plate_candidates=plates,
                              plate_status='unreadable' if not plates else 'needs_review',
                              image_path=image_path)
                if canvas is not None:
                    cv2.rectangle(canvas, (x1, y1), (x2, y2), (100, 220, 70), 2)
                    cv2.putText(canvas, label + ' ' + format(record['confidence'], '.2f'),
                                (x1, max(20, y1-6)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (100,220,70), 2)
                    for candidate in plates:
                        a, b, c, d = candidate['bbox_in_vehicle']
                        cv2.rectangle(canvas, (x1+a, y1+b), (x1+c, y1+d), (0,200,255), 2)
                save_observation(db, record)
                observations += 1
                if recorder:
                    event_id = events.evaluate(out, record, (media_ms or 0) / 1000)
                    if event_id:
                        try: recorder.trigger(event_id, (media_ms or 0) / 1000, frame)
                        except Exception as error:
                            events.set_media(out,event_id,'failed',error=type(error).__name__)
                print(json.dumps(record, ensure_ascii=False), flush=True)
            processed += 1
            if args.preview:
                preview = Path(args.preview)
                preview.parent.mkdir(parents=True, exist_ok=True)
                # Limit browser transfer size without changing inference resolution.
                scale = min(1.0, 1280 / canvas.shape[1])
                canvas = cv2.resize(canvas, None, fx=scale, fy=scale)
                ok, encoded = cv2.imencode('.jpg', canvas)
                if not ok:
                    raise OSError('プレビュー画像を生成できません。')
                temporary = preview.with_suffix('.tmp')
                encoded.tofile(temporary)
                os.replace(temporary, preview)
            if args.progress:
                atomic_json(args.progress, dict(phase='processing', frames_processed=processed,
                            observations=observations, frame_index=index, media_ms=media_ms))
    finally:
        try:
            stream.close()
            if recorder: recorder.close()
        finally:
            if old_term is not None: signal.signal(signal.SIGTERM, old_term)
            db.close()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
