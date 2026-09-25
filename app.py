"""AI Gate System: OpenCV vehicle and Japanese plate recognition prototype."""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sqlite3
import unicodedata
import uuid
import os
import threading
import time
from collections import deque
import signal
import sys

from plate_rules import KANA_PATTERN, OCR_RESULT_CONFIDENCE, VEHICLE_RESULT_CONFIDENCE

VEHICLES = {'car': '乗用車', 'kei': '軽自動車', 'motorcycle': '二輪車',
            'bus': 'バス', 'truck': 'トラック'}


def parse_plate(text):
    """Conservative ordinary Japanese plate parser; a match is NOT verification."""
    normalized = unicodedata.normalize('NFKC', text)
    compact = re.sub(r'\s+', '', normalized).replace('−', '-').replace('ー', '-')
    match = re.fullmatch(
        rf'([一-龥ぁ-んァ-ヶ]{{2,8}})([0-9][0-9A-Z]{{2}})({KANA_PATTERN})([0-9・.\-]{{1,7}})',
        compact)
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
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 50, 170)
    # Dense vertical character strokes recover low-contrast plates whose
    # outer border is not strong enough for Canny alone.
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (17, 5))
    blackhat = cv2.morphologyEx(blurred, cv2.MORPH_BLACKHAT, kernel)
    gradient = cv2.convertScaleAbs(cv2.Sobel(blackhat, cv2.CV_32F, 1, 0, ksize=3))
    _, strokes = cv2.threshold(gradient, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    strokes = cv2.morphologyEx(strokes, cv2.MORPH_CLOSE, kernel)
    strokes = cv2.dilate(strokes, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
    contours = []
    for mask in (edges, strokes):
        found, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        contours.extend(found)
    height, width = gray.shape
    boxes = []
    for contour in sorted(contours, key=cv2.contourArea, reverse=True):
        x, y, w, h = cv2.boundingRect(contour)
        if w < 40 or h < 18 or not 1.1 <= w / h <= 2.7:
            continue
        if not 0.002 <= w * h / (width * height) <= 0.3:
            continue
        if cv2.contourArea(contour) / (w * h) < 0.20:
            continue
        # Suppress almost identical nested contours.
        if any(abs(x-a) < 10 and abs(y-b) < 10 and abs(w-c) < 15 and abs(h-d) < 15
               for a, b, c, d in boxes):
            continue
        boxes.append((x, y, w, h))
        if len(boxes) == 5:
            break
    return boxes


def learned_plate_regions(crop, model, imgsz=960):
    """Plate boxes from a dedicated one-class YOLO model."""
    from inference_device import torch_device
    result = model.predict(crop, conf=.20, imgsz=imgsz, iou=.5, device=torch_device(), verbose=False)[0]
    boxes = []
    for box in result.boxes:
        x1, y1, x2, y2 = [round(v) for v in box.xyxy[0].tolist()]
        if x2 > x1 and y2 > y1:
            boxes.append((x1, y1, x2-x1, y2-y1))
    return boxes[:5]


def bounded_plate_regions(regions, width, height, limit=5):
    """Clip proposals to the vehicle ROI and suppress overlapping OCR work."""
    selected = []
    for x, y, w, h in regions:
        if not all(math.isfinite(float(v)) for v in (x, y, w, h)):
            continue
        left, top = max(0, int(x)), max(0, int(y))
        right, bottom = min(width, int(x+w)), min(height, int(y+h))
        if right-left < 8 or bottom-top < 4:
            continue
        area = (right-left)*(bottom-top)
        duplicate = False
        for a, b, c, d in selected:
            intersection = max(0, min(right, a+c)-max(left, a))*max(0, min(bottom, b+d)-max(top, b))
            if intersection / (area+c*d-intersection) >= .5:
                duplicate = True
                break
        if not duplicate:
            selected.append((left, top, right-left, bottom-top))
        if len(selected) >= limit:
            break
    return selected


def make_plate_detector(cv2, plate_model=None):
    from plate_pipeline import PlateDetector
    return PlateDetector(cv2, lambda crop: plate_regions(crop, cv2), bounded_plate_regions,
                         (lambda crop, size: learned_plate_regions(crop, plate_model, size))
                         if plate_model is not None else None)


def vehicle_plate_regions(crop, cv2, plate_model=None):
    """Compatibility entry point for vehicle-local proposal detection."""
    return make_plate_detector(cv2, plate_model).detect(crop).regions


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


def plate_appearance(roi, cv2):
    """Classify plate appearance conservatively; color is only a vehicle hint."""
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    hue, saturation, value = cv2.split(hsv)
    yellow = ((hue >= 15) & (hue <= 40) & (saturation >= 80) & (value >= 70))
    green = ((hue >= 35) & (hue <= 95) & (saturation >= 65) & (value >= 50))
    dark = value <= 75
    bright = (value >= 145) & (saturation <= 90)
    colorful = (saturation >= 90) & (value >= 60) & ~yellow & ~green
    ratios = {name: round(float(mask.mean()), 3) for name, mask in
              [('yellow', yellow), ('green', green), ('dark', dark),
               ('bright', bright), ('colorful', colorful)]}
    if ratios['yellow'] >= .20:
        style, kei, strength = 'kei_yellow', True, 'strong'
    elif ratios['dark'] >= .45 and ratios['yellow'] >= .035:
        style, kei, strength = 'kei_black', True, 'strong'
    elif ratios['bright'] >= .30 and ratios['yellow'] >= .025:
        # Graphic kei plates can have a yellow border. Keep this as a review
        # hint because arbitrary artwork can contain the same color.
        style, kei, strength = 'kei_graphic_candidate', True, 'review'
    elif ratios['green'] >= .20:
        style, kei, strength = 'commercial_green', False, None
    elif ratios['colorful'] >= .08:
        style, kei, strength = 'graphic_candidate', False, None
    elif ratios['bright'] >= .30:
        style, kei, strength = 'white', False, None
    else:
        style, kei, strength = 'unknown', False, None
    return dict(style=style, kei_candidate=kei, kei_strength=strength, ratios=ratios)


def ocr_variants(roi, appearance, cv2):
    """Images for yellow, dark and graphic plates without changing labels."""
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    _, otsu = cv2.threshold(clahe, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    adaptive = cv2.adaptiveThreshold(clahe, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                     cv2.THRESH_BINARY, 31, 9)
    denoised = cv2.fastNlMeansDenoising(clahe, None, 7, 7, 21)
    sharpened = cv2.addWeighted(clahe, 1.7, cv2.GaussianBlur(clahe, (0, 0), 1.2), -.7, 0)
    variants = [('color', roi), ('clahe', clahe), ('denoised', denoised),
                ('sharpened', sharpened)]
    if appearance['style'] in ('kei_black', 'commercial_green'):
        variants += [('otsu_inverted', cv2.bitwise_not(otsu)), ('otsu', otsu),
                     ('adaptive_inverted', cv2.bitwise_not(adaptive)), ('adaptive', adaptive)]
    else:
        # Finish with the bright foreground representation. This avoids a final
        # audit image that is almost black on ordinary white plates.
        variants += [('otsu_inverted', cv2.bitwise_not(otsu)), ('adaptive', adaptive),
                     ('adaptive_inverted', cv2.bitwise_not(adaptive)), ('otsu', otsu)]
    return variants


def vehicle_type_from_plates(detected_type, candidates, ocr_threshold=OCR_RESULT_CONFIDENCE):
    """Only strong plate-color evidence may refine a COCO car to kei."""
    if detected_type == 'car' and any(c.get('kei_strength') == 'strong' and c.get('fields')
                                      and c.get('confidence', 0) >= ocr_threshold
                                      for c in candidates):
        return 'kei'
    return detected_type


def result_is_eligible(vehicle_confidence, candidates,
                       vehicle_threshold=VEHICLE_RESULT_CONFIDENCE,
                       ocr_threshold=OCR_RESULT_CONFIDENCE):
    return vehicle_confidence >= vehicle_threshold and any(
        candidate.get('fields') and candidate.get('confidence', 0) >= ocr_threshold
        for candidate in candidates)


def recognize_plate_roi(roi, bbox, quad, rectification, reader, cv2, ocr_threshold):
    """OCR a prepared plate only; never search for vehicles or plates here."""
    readers = reader if isinstance(reader, list) else [('easyocr', reader)]
    scale = max(1.0, min(4.0, 480 / roi.shape[1]))
    roi = cv2.resize(roi, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    appearance = plate_appearance(roi, cv2)
    attempts = []
    for backend, active_reader in readers:
        for preprocessing, image in ocr_variants(roi, appearance, cv2):
            items = active_reader.readtext(image, detail=1, paragraph=False,
                                           decoder='beamsearch', beamWidth=5)
            text, confidence = plate_text(items)
            fields = parse_plate(text)
            attempts.append({'bbox_in_vehicle': bbox, 'text': text,
                             'confidence': confidence, 'fields': fields,
                             'ocr_backend': backend, 'preprocessing': preprocessing,
                             'quad_in_vehicle': quad, 'rectification': rectification,
                             'plate_style': appearance['style'],
                             'kei_candidate': appearance['kei_candidate'],
                             'kei_strength': appearance['kei_strength'],
                             'appearance_ratios': appearance['ratios'],
                             'status': 'candidate' if fields and confidence >= ocr_threshold else 'needs_review'})
    # Prefer a valid result independently reproduced by image variants.
    # Confidence remains EasyOCR's measured value and is not inflated.
    votes = {}
    for attempt in attempts:
        if attempt['fields']:
            key = tuple(attempt['fields'][name] for name in ('region','category','kana','serial'))
            votes[key] = votes.get(key, 0) + 1
    for attempt in attempts:
        key = (tuple(attempt['fields'][name] for name in ('region','category','kana','serial'))
               if attempt['fields'] else None)
        attempt['variant_votes'] = votes.get(key, 0)
    best = max(attempts, key=lambda c: (c['fields'] is not None,
                                        c['variant_votes'], c['confidence']))
    return best


def read_plate(crop, reader, cv2, ocr_threshold=OCR_RESULT_CONFIDENCE, plate_model=None,
               diagnostics=None):
    from plate_pipeline import PlateRecognitionPipeline, PlateRectifier
    pipeline = PlateRecognitionPipeline(
        make_plate_detector(cv2, plate_model), PlateRectifier(cv2),
        lambda roi, bbox, quad, method: recognize_plate_roi(
            roi, bbox, quad, method, reader, cv2, ocr_threshold))
    candidates, report = pipeline.run(crop)
    if diagnostics is not None:
        diagnostics.update(report)
    return candidates


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


def initialize_models(model_path, plate_model_path, root, easyocr, yolo_class, offline=False,
                      timeout=None):
    """Prepare inference models, retrying transient first-download failures."""
    if timeout is None:
        try:
            timeout = float(os.getenv('GATE_MODEL_INIT_TIMEOUT', '900'))
        except ValueError:
            timeout = 900
    timeout = max(30, timeout)
    deadline = time.monotonic() + timeout
    while True:
        try:
            model = yolo_class(model_path)
            plate_model = yolo_class(plate_model_path) if plate_model_path else None
            from ocr_backends import make_readers
            readers = make_readers(root, easyocr, offline)
            return model, plate_model, readers
        except Exception as error:
            if offline or time.monotonic() + 5 >= deadline:
                raise RuntimeError(
                    f'モデルの準備が{int(timeout)}秒以内に完了しませんでした: {error}'
                ) from error
            print(f'モデル準備を再試行します: {error}', file=sys.stderr, flush=True)
            time.sleep(5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, help='写真、動画のパス、カメラ番号（0）またはRTSP URL')
    parser.add_argument('--model', default='yolo26s.pt', help='COCOクラス名を持つUltralytics検出モデル')
    parser.add_argument('--plate-model', default=os.getenv('GATE_PLATE_MODEL'),
                        help='追加学習した一クラスのナンバープレートYOLOモデル')
    parser.add_argument('--output', default='data')
    parser.add_argument('--every', type=int, default=10, help='動画・カメラをNフレームごとに処理')
    parser.add_argument('--confidence', type=float, default=0.4)
    parser.add_argument('--vehicle-threshold', type=float, default=VEHICLE_RESULT_CONFIDENCE)
    parser.add_argument('--ocr-threshold', type=float, default=OCR_RESULT_CONFIDENCE)
    parser.add_argument('--imgsz', type=int, default=960,
                        help='YOLO入力画像サイズ。小さい車両の検出精度を優先する既定値は960')
    parser.add_argument('--save-images', action='store_true', help='検出した車両の切り抜き画像を保存')
    parser.add_argument('--source-kind', choices=['auto', 'file', 'camera', 'browser'], default='auto')
    parser.add_argument('--run-id', default=None)
    parser.add_argument('--alerts', action='store_true', help='登録車両照合・通知イベント・映像保存')
    parser.add_argument('--progress', default=None, help='Web管理用の進捗JSON')
    parser.add_argument('--preview', default=None, help='最新の処理済みフレームJPEG')
    args = parser.parse_args()
    if (args.every < 1 or not 0 < args.confidence <= 1 or
            not 0 < args.vehicle_threshold <= 1 or not 0 < args.ocr_threshold <= 1 or
            not 320 <= args.imgsz <= 1920):
        parser.error('処理間隔・信頼度・入力画像サイズを確認してください。')
    if args.progress:
        atomic_json(args.progress, {'phase': 'loading', 'frames_processed': 0, 'observations': 0})
    import cv2
    import easyocr
    from ultralytics import YOLO
    offline=os.getenv('GATE_OFFLINE')=='1'
    if offline and not Path(args.model).is_file():
        raise ValueError('オフライン用のYOLOモデルを事前に配置してください。')
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    model, plate_model, reader = initialize_models(
        args.model, args.plate_model, out, easyocr, YOLO, offline)
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
    timings = deque(maxlen=120)
    try:
        for index, media_ms, frame in stream:
            if stopped.is_set(): break
            frame_started = time.perf_counter()
            canvas = frame.copy() if args.preview else None
            detection_started = time.perf_counter()
            result = model.predict(frame, conf=args.confidence, imgsz=args.imgsz,
                                   iou=.55, device='cpu', verbose=False)[0]
            detection_ms = (time.perf_counter() - detection_started) * 1000
            ocr_ms = storage_ms = decision_ms = notification_ms = 0.0
            plate_detection_ms = rectification_ms = 0.0
            for box in result.boxes:
                label = result.names[int(box.cls.item())]
                if label not in VEHICLES:
                    continue
                vehicle_confidence = float(box.conf.item())
                if vehicle_confidence < args.vehicle_threshold:
                    continue
                x1, y1, x2, y2 = [int(v) for v in box.xyxy[0].tolist()]
                height, width = frame.shape[:2]
                x1, y1, x2, y2 = max(0, x1), max(0, y1), min(width, x2), min(height, y2)
                if x2 <= x1 or y2 <= y1:
                    continue
                crop = frame[y1:y2, x1:x2]
                plate_report = {}
                plates = read_plate(crop, reader, cv2, args.ocr_threshold, plate_model,
                                    diagnostics=plate_report)
                plate_detection_ms += plate_report.get('plate_detection_ms', 0.0)
                rectification_ms += plate_report.get('rectification_ms', 0.0)
                ocr_ms += plate_report.get('ocr_ms', 0.0)
                decision_started = time.perf_counter()
                vehicle_type = vehicle_type_from_plates(label, plates, args.ocr_threshold)
                result_eligible = result_is_eligible(vehicle_confidence, plates,
                                                     args.vehicle_threshold, args.ocr_threshold)
                decision_ms += (time.perf_counter() - decision_started) * 1000
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
                              frame_index=index, media_ms=media_ms, vehicle_type=vehicle_type,
                              vehicle_type_ja=VEHICLES[vehicle_type], confidence=vehicle_confidence,
                              bbox=[x1, y1, x2, y2], plate_candidates=plates,
                              plate_detection=plate_report,
                              plate_status='unreadable' if not plates else 'needs_review',
                              image_path=image_path, result_eligible=result_eligible,
                              result_thresholds={'vehicle': args.vehicle_threshold,
                                                 'ocr': args.ocr_threshold})
                if canvas is not None:
                    cv2.rectangle(canvas, (x1, y1), (x2, y2), (100, 220, 70), 2)
                    cv2.putText(canvas, vehicle_type + ' ' + format(record['confidence'], '.2f'),
                                (x1, max(20, y1-6)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (100,220,70), 2)
                    for candidate in plate_report.get('proposals', plates):
                        a, b, c, d = candidate['bbox_in_vehicle']
                        cv2.rectangle(canvas, (x1+a, y1+b), (x1+c, y1+d), (0,200,255), 2)
                storage_started = time.perf_counter()
                save_observation(db, record)
                storage_ms += (time.perf_counter() - storage_started) * 1000
                observations += int(result_eligible)
                if recorder:
                    notification_started = time.perf_counter()
                    event_id = events.evaluate(out, record, (media_ms or 0) / 1000)
                    if event_id:
                        try: recorder.trigger(event_id, (media_ms or 0) / 1000, frame)
                        except Exception as error:
                            events.set_media(out,event_id,'failed',error=type(error).__name__)
                    notification_ms += (time.perf_counter() - notification_started) * 1000
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
            frame_ms = (time.perf_counter() - frame_started) * 1000
            timings.append(frame_ms)
            if args.progress:
                atomic_json(args.progress, dict(phase='processing', frames_processed=processed,
                            observations=observations, frame_index=index, media_ms=media_ms,
                            performance=dict(frame_ms=round(frame_ms, 2),
                                detection_ms=round(detection_ms, 2),
                                plate_detection_ms=round(plate_detection_ms, 2),
                                rectification_ms=round(rectification_ms, 2), ocr_ms=round(ocr_ms, 2),
                                decision_ms=round(decision_ms, 2), storage_ms=round(storage_ms, 2),
                                notification_ms=round(notification_ms, 2),
                                rolling_fps=round(1000 / (sum(timings) / len(timings)), 2))))
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
