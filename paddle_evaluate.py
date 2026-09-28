"""Evaluate trained plate detection and OCR against held-out manual reviews."""
import argparse
import json
import os
from pathlib import Path
import statistics
import time
import unicodedata

import events
from paddle_plate_pipeline import PaddlePlatePipeline


class PaddleLineReader:
    """Standalone evaluation reader; never imported by the production worker."""
    def __init__(self, directory):
        from paddleocr import TextRecognition
        self.model = TextRecognition(model_name='PP-OCRv5_mobile_rec',
                                     model_dir=str(directory), device='cpu')

    def read(self, image):
        import numpy as np
        value = next(iter(self.model.predict(input=np.ascontiguousarray(image), batch_size=1))).json
        value = value.get('res', value)
        return str(value.get('rec_text') or '').strip(), float(value.get('rec_score') or 0)


def overlap(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0, x2-x1) * max(0, y2-y1)
    area_a = max(0, a[2]-a[0]) * max(0, a[3]-a[1])
    area_b = max(0, b[2]-b[0]) * max(0, b[3]-b[1])
    return intersection / max(1, area_a + area_b - intersection)


def score_pipeline(pipeline, vehicle, expected, truth):
    started = time.perf_counter()
    found, report = pipeline.run(vehicle)
    elapsed = time.perf_counter() - started
    detected = any(overlap(p['bbox_in_vehicle'], expected) >= .5
                   for p in report.get('proposals', []))
    recognized = False
    for candidate in found:
        if overlap(candidate['bbox_in_vehicle'], expected) < .5 or not candidate.get('fields'):
            continue
        try:
            if events.plate_key(candidate['fields']) == truth:
                recognized = True
                break
        except (ValueError, KeyError, TypeError):
            continue
    return dict(detected=detected, recognized=recognized, latency_ms=elapsed*1000)


def summarize(items):
    durations = sorted(item['latency_ms'] for item in items)
    n = len(items)
    return dict(evaluated=n, plate_recall_at_iou_50=sum(x['detected'] for x in items)/n,
                exact_plate_accuracy=sum(x['recognized'] for x in items)/n,
                average_latency_ms=statistics.mean(durations),
                p95_latency_ms=durations[max(0, (95*n+99)//100-1)],
                throughput_fps=1000*n/sum(durations) if sum(durations) else 0)


def detector_diagnostic(detector, vehicle, expected, image_path, imgsz=960):
    """Keep every low-score box and draw it before production filtering."""
    import cv2
    result = detector.predict(vehicle, conf=.01, imgsz=imgsz, device='cpu', verbose=False)[0]
    canvas = vehicle.copy()
    cv2.rectangle(canvas, (int(expected[0]), int(expected[1])),
                  (int(expected[2]), int(expected[3])), (0, 255, 0), 2)
    height, width = vehicle.shape[:2]
    boxes = []
    for item in result.boxes:
        x1, y1, x2, y2 = (int(v) for v in item.xyxy[0].tolist())
        x1, y1, x2, y2 = max(0, x1), max(0, y1), min(width, x2), min(height, y2)
        score = float(item.conf.item())
        iou = overlap([x1, y1, x2, y2], expected)
        reason = ('low_confidence' if score < .2 else
                  'small_box' if x2-x1 < 48 or y2-y1 < 24 else
                  'low_iou' if iou < .5 else 'matched')
        boxes.append(dict(box=[x1, y1, x2, y2], confidence=round(score, 4),
                          iou=round(iou, 4), reason=reason))
        cv2.rectangle(canvas, (x1, y1), (x2, y2), (255, 0, 0), 2)
        cv2.putText(canvas, f'{score:.2f} IoU {iou:.2f}', (x1, max(12, y1-4)),
                    cv2.FONT_HERSHEY_SIMPLEX, .45, (255, 0, 0), 1)
    if not cv2.imwrite(str(image_path), canvas):
        raise OSError('診断画像を保存できません。')
    return dict(image=image_path.name, boxes=boxes, max_iou=max((b['iou'] for b in boxes), default=0),
                matched=any(b['reason'] == 'matched' for b in boxes), imgsz=imgsz)


def ocr_diagnostic(reader, image_bytes, sample):
    """Read human-confirmed crops without using the trained detector."""
    import cv2
    import numpy as np
    import ocr_learning
    image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError('手動確認済みのプレート画像を読み込めません。')
    result = []
    crops = ocr_learning.training_crops(sample, image.shape[1], image.shape[0])
    for truth, (x1, y1, x2, y2) in crops:
        crop = image[y1:y2, x1:x2]
        if not crop.size:
            raise ValueError('OCRの切り出し範囲が空です。')
        prediction, confidence = reader.read(crop)
        prediction = unicodedata.normalize('NFKC', prediction).strip()
        result.append(dict(truth=truth, prediction=prediction, confidence=confidence,
                           exact=truth == prediction))
    return dict(crops=result, exact=all(item['exact'] for item in result),
                layout='four_fields' if sample.get('fields_json') else 'two_lines')


def evaluate(root, dataset, plate_weights, recognition_dir, compare=False,
             paddle_pipeline=None, lipla_pipeline=None):
    import cv2
    import numpy as np
    from ultralytics import YOLO
    from app import parse_plate
    from paddle_training import plate_box, safe_vehicle_image

    manifest = json.loads((Path(dataset) / 'manifest.json').read_text())
    partition = 'test' if any(item['partition'] == 'test' for item in manifest['samples']) else 'val'
    sample_ids = [item['sample'] for item in manifest['samples']
                  if item['partition'] == partition and item['label_source'] == 'manual']
    if not sample_ids:
        raise ValueError('手動確認済みの評価画像がありません。')
    if paddle_pipeline is None:
        paddle_pipeline = PaddlePlatePipeline(YOLO(str(plate_weights)),
                                              PaddleLineReader(str(recognition_dir)), parse_plate)
    if compare and lipla_pipeline is None:
        import lipla
        from lipla_pipeline import LiplaPlatePipeline
        lipla_pipeline = LiplaPlatePipeline(lipla.Recognizer(
            cache_dir=str(Path(os.getenv('GATE_MODEL_ROOT', '/models')) / 'lipla')), parse_plate)
    scores = {'paddle': [], 'lipla': []}
    diagnostics = []
    diagnostic_dir = Path(dataset) / 'diagnostics'
    diagnostic_dir.mkdir(exist_ok=True)
    skipped = 0
    with events.connection(root) as db:
        for identifier in sample_ids:
            row = db.execute('''SELECT s.*,f.fields_json,o.details_json FROM ocr_samples s
                LEFT JOIN ocr_sample_fields f ON f.sample_id=s.id
                JOIN observations o ON o.id=s.observation_id WHERE s.id=?''', (identifier,)).fetchone()
            if row is None:
                skipped += 1
                continue
            record = json.loads(row['details_json'])
            source = safe_vehicle_image(root, record)
            if source is None:
                skipped += 1
                continue
            vehicle = cv2.imdecode(np.fromfile(source, dtype=np.uint8), cv2.IMREAD_COLOR)
            if vehicle is None:
                skipped += 1
                continue
            candidates = record.get('plate_candidates', [])
            if row['candidate_index'] >= len(candidates):
                skipped += 1
                continue
            expected = plate_box(candidates[row['candidate_index']],
                                 (vehicle.shape[1], vehicle.shape[0]))
            if expected is None:
                skipped += 1
                continue
            truth = events.plate_key(parse_plate(row['top_text'] + row['bottom_text']))
            # Alternate ordering to avoid a consistent warm-cache advantage.
            methods = [('paddle', paddle_pipeline)]
            if compare:
                methods = [('paddle', paddle_pipeline), ('lipla', lipla_pipeline)]
                if len(scores['paddle']) % 2:
                    methods.reverse()
            for name, pipeline in methods:
                scores[name].append(score_pipeline(pipeline, vehicle, expected, truth))
            if compare:
                detector = paddle_pipeline.detector
                detection = detector_diagnostic(detector, vehicle, expected,
                                                diagnostic_dir / (identifier + '.jpg'),
                                                imgsz=paddle_pipeline.imgsz)
                ocr = ocr_diagnostic(paddle_pipeline.reader, row['image'], dict(row))
                diagnostics.append(dict(sample=identifier, detection=detection, ocr=ocr))
    if not scores['paddle']:
        raise ValueError('読み取り可能な評価用車両画像がありません。')
    if compare:
        report = dict(evaluated=len(scores['paddle']), skipped=skipped,
                      ground_truth='manual_review', evaluation_partition=partition,
                      iou_threshold=.5, diagnostics=diagnostics,
                      ocr_isolated_exact=sum(d['ocr']['exact'] for d in diagnostics)/len(diagnostics),
                      paddle=summarize(scores['paddle']), lipla=summarize(scores['lipla']),
                      paired=dict(both_correct=sum(a['recognized'] and b['recognized'] for a,b in zip(scores['paddle'],scores['lipla'])),
                                  paddle_only=sum(a['recognized'] and not b['recognized'] for a,b in zip(scores['paddle'],scores['lipla'])),
                                  lipla_only=sum(b['recognized'] and not a['recognized'] for a,b in zip(scores['paddle'],scores['lipla']))))
        destination = Path(dataset) / 'comparison.json'
    else:
        report = summarize(scores['paddle'])
        destination = Path(dataset) / 'evaluation.json'
    temporary = destination.with_suffix('.tmp')
    temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    os.replace(temporary, destination)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', required=True)
    parser.add_argument('--dataset', required=True)
    parser.add_argument('--plate-weights', required=True)
    parser.add_argument('--recognition-dir', required=True)
    parser.add_argument('--compare-lipla', action='store_true')
    args = parser.parse_args()
    print(json.dumps(evaluate(args.data, args.dataset, args.plate_weights,
                              args.recognition_dir, compare=args.compare_lipla), ensure_ascii=False))
