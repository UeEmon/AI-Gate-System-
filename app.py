"""AI Gate System: OpenCV vehicle and Japanese plate recognition prototype."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import unicodedata
import uuid

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
    db = sqlite3.connect(path)
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


def read_plate(crop, reader, cv2):
    candidates = []
    for x, y, w, h in plate_regions(crop, cv2):
        roi = crop[y:y+h, x:x+w]
        roi = cv2.resize(roi, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        items = reader.readtext(roi, detail=1, paragraph=False)
        # Group OCR fragments into rows, then read each row left to right.
        rows = []
        for box, text, score in sorted(items, key=lambda item: min(p[1] for p in item[0])):
            cy = sum(p[1] for p in box) / 4
            bh = max(p[1] for p in box) - min(p[1] for p in box)
            row = next((r for r in rows if abs(r['y'] - cy) <= max(bh, r['h']) * 0.5), None)
            if row is None:
                row = {'y': cy, 'h': bh, 'items': []}
                rows.append(row)
            row['items'].append((min(p[0] for p in box), text, float(score)))
        fragments = [item for row in rows for item in sorted(row['items'])]
        if not fragments:
            continue
        text = ' '.join(item[1] for item in fragments)
        confidence = min(item[2] for item in fragments)
        fields = parse_plate(text)
        candidates.append({'bbox_in_vehicle': [x, y, x+w, y+h], 'text': text,
                           'confidence': confidence, 'fields': fields,
                           'status': 'candidate' if fields and confidence >= 0.6 else 'needs_review'})
    return sorted(candidates, key=lambda c: (c['fields'] is not None, c['confidence']), reverse=True)


def frames(source, cv2, every):
    path = Path(source)
    if path.suffix.lower() in {'.jpg', '.jpeg', '.png', '.bmp', '.webp', '.tif', '.tiff'}:
        import numpy as np
        frame = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError('画像を読み込めません。')
        yield 0, None, frame
        return
    capture = cv2.VideoCapture(int(source) if source.isdecimal() else source)
    if not capture.isOpened():
        capture.release()
        raise ValueError('動画またはカメラを開けません。入力と接続を確認してください。')
    index = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                if index == 0:
                    raise ValueError('入力を開きましたがフレームを取得できません。')
                break
            if index % every == 0:
                media_ms = float(capture.get(cv2.CAP_PROP_POS_MSEC)) if path.is_file() else None
                yield index, media_ms, frame
            index += 1
    finally:
        capture.release()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, help='写真、動画のパス、カメラ番号（0）またはRTSP URL')
    parser.add_argument('--model', default='yolo11n.pt', help='COCOクラス名を持つUltralytics検出モデル')
    parser.add_argument('--output', default='data')
    parser.add_argument('--every', type=int, default=10, help='動画・カメラをNフレームごとに処理')
    parser.add_argument('--confidence', type=float, default=0.4)
    parser.add_argument('--save-images', action='store_true', help='検出した車両の切り抜き画像を保存')
    args = parser.parse_args()
    if args.every < 1 or not 0 < args.confidence <= 1:
        parser.error('--every は1以上、--confidence は0より大きく1以下です。')
    import cv2
    import easyocr
    from ultralytics import YOLO
    model = YOLO(args.model)
    reader = easyocr.Reader(['ja', 'en'], gpu=False)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex
    db = open_database(out / 'gate.db')
    stream = frames(args.source, cv2, args.every)
    try:
        for index, media_ms, frame in stream:
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
                save_observation(db, record)
                print(json.dumps(record, ensure_ascii=False), flush=True)
    finally:
        stream.close()
        db.close()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
