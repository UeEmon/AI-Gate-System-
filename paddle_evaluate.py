"""Evaluate trained plate detection and OCR against held-out manual reviews."""
import argparse
import json
from pathlib import Path
import time

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


def evaluate(root, dataset, plate_weights, recognition_dir):
    import cv2
    import numpy as np
    from ultralytics import YOLO
    from app import parse_plate
    from paddle_training import plate_box, safe_vehicle_image, partition

    manifest = json.loads((Path(dataset) / 'manifest.json').read_text())
    sample_ids = [item['sample'] for item in manifest['samples']
                  if item['partition'] == 'val' and item['label_source'] == 'manual']
    if not sample_ids:
        raise ValueError('手動確認済みの評価画像がありません。')
    detector = YOLO(str(plate_weights))
    reader = PaddleLineReader(str(recognition_dir))
    pipeline = PaddlePlatePipeline(detector, reader, parse_plate)
    recognized = detected = evaluated = 0
    elapsed = 0.0
    with events.connection(root) as db:
        for identifier in sample_ids:
            row = db.execute('''SELECT s.*,o.details_json FROM ocr_samples s
                JOIN observations o ON o.id=s.observation_id WHERE s.id=?''', (identifier,)).fetchone()
            if row is None:
                continue
            record = json.loads(row['details_json'])
            source = safe_vehicle_image(root, record)
            if source is None:
                continue
            vehicle = cv2.imdecode(np.fromfile(source, dtype=np.uint8), cv2.IMREAD_COLOR)
            if vehicle is None:
                continue
            candidates = record.get('plate_candidates', [])
            if row['candidate_index'] >= len(candidates):
                continue
            expected = plate_box(candidates[row['candidate_index']],
                                 (vehicle.shape[1], vehicle.shape[0]))
            if expected is None:
                continue
            started = time.perf_counter()
            found, report = pipeline.run(vehicle)
            elapsed += time.perf_counter() - started
            evaluated += 1
            def overlap(a, b):
                x1, y1 = max(a[0], b[0]), max(a[1], b[1])
                x2, y2 = min(a[2], b[2]), min(a[3], b[3])
                intersect = max(0, x2-x1) * max(0, y2-y1)
                area_a = max(0, a[2]-a[0]) * max(0, a[3]-a[1])
                area_b = max(0, b[2]-b[0]) * max(0, b[3]-b[1])
                return intersect / max(1, area_a + area_b - intersect)
            proposals = report.get('proposals', [])
            detected += int(any(overlap(p['bbox_in_vehicle'], expected) >= .5
                                for p in proposals))
            truth = events.plate_key(parse_plate(row['top_text'] + row['bottom_text']))
            recognized += int(any(overlap(c['bbox_in_vehicle'], expected) >= .5 and
                                  events.plate_key(c['fields']) == truth for c in found))
    if evaluated == 0:
        raise ValueError('読み取り可能な評価用車両画像がありません。')
    report = dict(evaluated=evaluated, plate_recall_at_iou_50=detected/evaluated,
                  exact_plate_accuracy=recognized/evaluated,
                  average_latency_ms=1000*elapsed/evaluated, throughput_fps=evaluated/elapsed)
    (Path(dataset) / 'evaluation.json').write_text(json.dumps(report, indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', required=True)
    parser.add_argument('--dataset', required=True)
    parser.add_argument('--plate-weights', required=True)
    parser.add_argument('--recognition-dir', required=True)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.data, args.dataset, args.plate_weights,
                              args.recognition_dir), ensure_ascii=False))
