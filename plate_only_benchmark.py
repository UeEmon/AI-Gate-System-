"""Vehicle ROI plate detection and OCR benchmark, with full-frame comparison."""
import argparse
import json
import os
from pathlib import Path
import time
import uuid
from urllib.parse import urlsplit


def is_rtmp(source):
    parsed = urlsplit(source)
    return parsed.scheme.lower() in {'rtmp', 'rtmps'} and bool(parsed.hostname)


def public_source(source):
    """Never write stream credentials or query tokens into benchmark artifacts."""
    if is_rtmp(source):
        parsed = urlsplit(source)
        return f'{parsed.scheme}://{parsed.hostname}' + (f':{parsed.port}' if parsed.port else '') + '/…'
    return str(source)


def vehicle_regions(frame, model, device, confidence, imgsz):
    from app import VEHICLES
    height, width = frame.shape[:2]
    result = model.predict(frame, conf=confidence, imgsz=imgsz, iou=.55,
                           device=device, verbose=False)[0]
    regions = []
    for box in result.boxes:
        label = result.names[int(box.cls.item())]
        if label not in VEHICLES:
            continue
        a, b, c, d = map(int, box.xyxy[0].tolist())
        x1, y1, x2, y2 = max(0, a), max(0, b), min(width, c), min(height, d)
        if x1 < x2 and y1 < y2:
            regions.append(dict(bbox_in_frame=[x1, y1, x2, y2],
                                vehicle_type=label, confidence=float(box.conf.item())))
    return regions


def frame_coordinates(item, x, y):
    item = dict(item)
    a, b, c, d = item['bbox_in_vehicle']
    item['bbox_in_frame'] = [a + x, b + y, c + x, d + y]
    if item.get('quad_in_vehicle') is not None:
        item['quad_in_frame'] = [[px + x, py + y] for px, py in item['quad_in_vehicle']]
    return item


def analyze_frame(frame, readers, cv2, plate_model, vehicle_model, device,
                  confidence=.25, imgsz=960):
    from app import read_plate
    from inference_device import synchronize
    regions = []
    vehicle_ms = 0.0
    if vehicle_model is not None:
        synchronize(device)
        started = time.perf_counter()
        regions = vehicle_regions(frame, vehicle_model, device, confidence, imgsz)
        synchronize(device)
        vehicle_ms = (time.perf_counter() - started) * 1000
    else:
        regions = [dict(bbox_in_frame=[0, 0, frame.shape[1], frame.shape[0]])]
    candidates, proposals = [], []
    report = dict(proposals=proposals, vehicle_detection_ms=vehicle_ms,
                  plate_detection_ms=0.0, rectification_ms=0.0, ocr_ms=0.0)
    for number, region in enumerate(regions):
        x1, y1, x2, y2 = region['bbox_in_frame']
        local = {}
        found = read_plate(frame[y1:y2, x1:x2], readers, cv2,
                           plate_model=plate_model, diagnostics=local)
        for key in ('plate_detection_ms', 'rectification_ms', 'ocr_ms'):
            report[key] += local.get(key, 0.0)
        for source, target in ((found, candidates), (local.get('proposals', []), proposals)):
            for item in source:
                translated = frame_coordinates(item, x1, y1)
                if vehicle_model is not None:
                    translated['vehicle_index'] = number
                target.append(translated)
    return candidates, report, ([] if vehicle_model is None else regions)


def run(args):
    import cv2
    import easyocr
    import numpy as np
    from app import frames, live_frames
    from ocr_backends import make_readers

    destination = Path(args.output) / uuid.uuid4().hex
    destination.mkdir(parents=True)
    from inference_device import torch_device, synchronize
    device = torch_device()
    started = time.perf_counter()
    plate_model = None
    mode = getattr(args, 'mode', 'plate-only')
    if mode not in {'vehicle-first', 'plate-only'}:
        raise ValueError('検証モードが不正です。')
    vehicle_model = None
    vehicle_path = getattr(args, 'vehicle_model', None) or os.path.join(
        os.getenv('GATE_BENCHMARK_VEHICLE_MODEL_ROOT', '/models'), 'yolo26n.pt')
    if mode == 'vehicle-first':
        from ultralytics import YOLO
        vehicle_model = YOLO(vehicle_path)
    if args.plate_model:
        if not Path(args.plate_model).is_file():
            raise ValueError('専用プレートモデルが見つかりません。')
        from ultralytics import YOLO
        plate_model = YOLO(args.plate_model)
    readers = make_readers(Path(args.data), easyocr, os.getenv('GATE_OFFLINE') == '1')
    initialization_ms = (time.perf_counter() - started) * 1000
    live = is_rtmp(args.source)
    stream = (live_frames(args.source, cv2, args.every) if live else
              frames(args.source, cv2, args.every))
    latencies = []
    stages = {key: [] for key in ('vehicle_detection_ms', 'plate_detection_ms', 'rectification_ms', 'ocr_ms')}
    detected = read = count = vehicle_detected = 0
    processing_started = time.perf_counter()
    try:
        with (destination / 'frames.jsonl').open('w', encoding='utf-8') as output:
            for index, media_ms, frame in stream:
                synchronize(device)
                started = time.perf_counter()
                candidates, report, vehicles = analyze_frame(
                    frame, readers, cv2, plate_model, vehicle_model, device,
                    getattr(args, 'vehicle_confidence', .25),
                    getattr(args, 'vehicle_imgsz', 960))
                synchronize(device)
                inference_ms = (time.perf_counter() - started) * 1000
                record = dict(frame_index=index, media_ms=media_ms,
                              coordinate_space='frame', vehicle_detection=vehicle_model is not None,
                              vehicles=vehicles, inference_ms=inference_ms,
                              candidates=candidates, detection=report)
                if args.save_images:
                    canvas = frame.copy()
                    for vehicle in vehicles:
                        x1, y1, x2, y2 = vehicle['bbox_in_frame']
                        cv2.rectangle(canvas, (x1, y1), (x2, y2), (0, 200, 0), 2)
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
                output.flush()  # Allow the browser to display live results.
                count += 1
                vehicle_detected += bool(vehicles)
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
    summary = dict(mode=mode, vehicle_detection=vehicle_model is not None,
                   vehicle_model=vehicle_path if vehicle_model is not None else None,
                   source=public_source(args.source), live_stream=live,
                   plate_model=args.plate_model or 'opencv-plate-contours',
                   ocr_backends=[name for name, _ in readers],
                   requested_device=os.getenv('GATE_INFERENCE_DEVICE', 'cpu'),
                   detector_device=device if plate_model or vehicle_model else 'cpu',
                   ocr_devices={name: device if name == 'easyocr' else 'backend default (CPU configuration)' for name, _ in readers},
                   initialization_ms=initialization_ms, frames=count,
                   vehicle_detected_frames=vehicle_detected, detected_frames=detected, text_read_frames=read,
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
    parser.add_argument('--mode', choices=('vehicle-first', 'plate-only'), default='vehicle-first')
    parser.add_argument('--vehicle-model', default=None)
    parser.add_argument('--vehicle-confidence', type=float, default=.25)
    parser.add_argument('--vehicle-imgsz', type=int, default=960)
    parser.add_argument('--data', default='/data')
    parser.add_argument('--output', default='/data/plate-only-benchmark')
    parser.add_argument('--every', type=int, default=1)
    parser.add_argument('--warmup', type=int, default=0)
    parser.add_argument('--max-frames', type=int, default=300)
    parser.add_argument('--save-images', action='store_true')
    args = parser.parse_args()
    if args.every < 1 or args.max_frames < 1 or args.warmup < 0:
        parser.error('フレーム数・間隔は正数、warmupは0以上にしてください。')
    if not 0 < args.vehicle_confidence <= 1 or args.vehicle_imgsz < 32:
        parser.error('車両検出の閾値・画像サイズを確認してください。')
    run(args)


if __name__ == '__main__':
    main()
