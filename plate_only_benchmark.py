"""Full-frame plate detection and OCR benchmark without vehicle inference."""
import argparse
import json
import os
from pathlib import Path
import time
import uuid


def run(args):
    import cv2
    import easyocr
    import numpy as np
    from app import frames, read_plate
    from ocr_backends import make_readers

    destination = Path(args.output) / uuid.uuid4().hex
    destination.mkdir(parents=True)
    started = time.perf_counter()
    plate_model = None
    if args.plate_model:
        if not Path(args.plate_model).is_file():
            raise ValueError('専用プレートモデルが見つかりません。')
        from ultralytics import YOLO
        plate_model = YOLO(args.plate_model)
    # No vehicle model is constructed or called.
    readers = make_readers(Path(args.data), easyocr, os.getenv('GATE_OFFLINE') == '1')
    initialization_ms = (time.perf_counter() - started) * 1000
    stream = frames(args.source, cv2, args.every)
    latencies = []
    stages = {key: [] for key in ('plate_detection_ms', 'rectification_ms', 'ocr_ms')}
    detected = read = count = 0
    processing_started = time.perf_counter()
    try:
        with (destination / 'frames.jsonl').open('w', encoding='utf-8') as output:
            for index, media_ms, frame in stream:
                started = time.perf_counter()
                report = {}
                candidates = read_plate(frame, readers, cv2, plate_model=plate_model,
                                        diagnostics=report)
                inference_ms = (time.perf_counter() - started) * 1000
                # Existing pipeline names its local coordinates bbox_in_vehicle;
                # here its input is the complete frame, so these are frame coordinates.
                for item in candidates + report['proposals']:
                    item['bbox_in_frame'] = item.pop('bbox_in_vehicle')
                    if 'quad_in_vehicle' in item:
                        item['quad_in_frame'] = item.pop('quad_in_vehicle')
                record = dict(frame_index=index, media_ms=media_ms,
                              coordinate_space='frame', vehicle_detection=False,
                              inference_ms=inference_ms, candidates=candidates, detection=report)
                if args.save_images:
                    canvas = frame.copy()
                    for number, proposal in enumerate(report['proposals']):
                        x1, y1, x2, y2 = proposal['bbox_in_frame']
                        cv2.rectangle(canvas, (x1, y1), (x2, y2), (0, 200, 255), 2)
                        cv2.putText(canvas, str(number), (x1, max(15, y1-4)),
                                    cv2.FONT_HERSHEY_SIMPLEX, .6, (0, 200, 255), 2)
                    ok, encoded = cv2.imencode('.jpg', canvas)
                    if not ok:
                        raise OSError('検証画像を保存できません。')
                    encoded.tofile(destination / f'{index:08d}.jpg')
                output.write(json.dumps(record, ensure_ascii=False) + '\n')
                count += 1
                detected += bool(report['proposals'])
                read += bool(candidates)
                if count > args.warmup:
                    latencies.append(inference_ms)
                    for key in stages:
                        stages[key].append(report[key])
                if count >= args.max_frames:
                    break
    finally:
        stream.close()
    elapsed = time.perf_counter() - processing_started
    summary = dict(vehicle_detection=False, source=args.source,
                   plate_model=args.plate_model or 'opencv-plate-contours',
                   ocr_backends=[name for name, _ in readers],
                   initialization_ms=initialization_ms, frames=count,
                   detected_frames=detected, text_read_frames=read,
                   measured_frames=len(latencies), warmup_frames=min(count, args.warmup),
                   inference_fps=1000 / np.mean(latencies) if latencies else None,
                   inference_p95_ms=float(np.percentile(latencies, 95)) if latencies else None,
                   end_to_end_fps=count / elapsed if count else None,
                   mean_stage_ms={key: float(np.mean(values)) if values else None
                                  for key, values in stages.items()},
                   accuracy=None, accuracy_note='正解ラベル未指定。検出件数は検出率・正解率ではありません。')
    (destination / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(dict(output=str(destination), **summary), ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--plate-model', default=os.getenv('GATE_PLATE_MODEL') or None)
    parser.add_argument('--data', default='/data')
    parser.add_argument('--output', default='/data/plate-only-benchmark')
    parser.add_argument('--every', type=int, default=1)
    parser.add_argument('--warmup', type=int, default=0)
    parser.add_argument('--max-frames', type=int, default=300)
    parser.add_argument('--save-images', action='store_true')
    args = parser.parse_args()
    if args.every < 1 or args.max_frames < 1 or args.warmup < 0:
        parser.error('フレーム数・間隔は正数、warmupは0以上にしてください。')
    run(args)


if __name__ == '__main__':
    main()
