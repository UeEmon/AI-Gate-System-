"""Dedicated YOLO plate detector followed by fine-tuned PaddleOCR text lines."""
import time


class PaddlePlatePipeline:
    def __init__(self, detector, reader, parse_plate, imgsz=640):
        if detector is None:
            raise ValueError('専用プレート検出重みが必要です。')
        self.detector, self.reader, self.parse_plate = detector, reader, parse_plate
        self.imgsz = imgsz

    def run(self, vehicle):
        started = time.perf_counter()
        results = self.detector.predict(vehicle, conf=.2, imgsz=self.imgsz,
                                        device='cpu', verbose=False)[0]
        detection_ms = (time.perf_counter() - started) * 1000
        candidates, proposals = [], []
        recognition_ms = 0
        height, width = vehicle.shape[:2]
        for box in results.boxes:
            x1, y1, x2, y2 = (int(v) for v in box.xyxy[0].tolist())
            x1, y1, x2, y2 = max(0, x1), max(0, y1), min(width, x2), min(height, y2)
            if x2-x1 < 48 or y2-y1 < 24:
                continue
            plate = vehicle[y1:y2, x1:x2]
            boundary = round(plate.shape[0] * .45)
            if boundary < 4 or plate.shape[0] - boundary < 4:
                continue
            proposals.append(dict(bbox_in_vehicle=[x1, y1, x2, y2],
                                  detection_source='trained-plate-yolo', text_found=False))
            begin = time.perf_counter()
            top, top_score = self.reader.read(plate[:boundary])
            bottom, bottom_score = self.reader.read(plate[boundary:])
            recognition_ms += (time.perf_counter() - begin) * 1000
            fields = self.parse_plate(top + bottom)
            if not fields:
                continue
            proposals[-1]['text_found'] = True
            candidates.append(dict(bbox_in_vehicle=[x1, y1, x2, y2],
                                   text=top + ' ' + bottom, fields=fields,
                                   confidence=min(top_score, bottom_score),
                                   ocr_backend='paddleocr-finetuned',
                                   detection_source='trained-plate-yolo',
                                   detection_score=float(box.conf.item())))
        report = dict(status='text_read' if candidates else 'not_detected_or_unreadable',
                      source='trained-plate-yolo', proposals=proposals,
                      plate_detection_ms=detection_ms, plate_recognition_ms=recognition_ms,
                      rectification_ms=0, ocr_ms=recognition_ms)
        return candidates, report
