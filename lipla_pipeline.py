"""Japanese plate detection and recognition using Lipla's native vehicle-image API."""
import time

import numpy as np


class LiplaPlatePipeline:
    """Keep Lipla's own detector, rectifier and OCR together on one vehicle ROI."""

    def __init__(self, recognizer, parser=None):
        self.recognizer = recognizer
        self.parser = parser

    def run(self, image):
        if image.ndim != 3 or image.shape[2] != 3 or not image.shape[0] or not image.shape[1]:
            raise ValueError('Liplaには空でないBGR車両画像を指定してください。')
        started = time.perf_counter()
        results = self.recognizer(np.ascontiguousarray(image))
        combined_ms = (time.perf_counter() - started) * 1000
        candidates, proposals = [], []
        for result in results:
            points = np.asarray(result.vertices, dtype=float).reshape(4, 2)
            if not np.isfinite(points).all():
                continue
            x1, y1 = np.floor(points.min(axis=0)).astype(int)
            x2, y2 = np.ceil(points.max(axis=0)).astype(int)
            x1, y1 = max(0, int(x1)), max(0, int(y1))
            x2, y2 = min(image.shape[1], int(x2)), min(image.shape[0], int(y2))
            if x2 <= x1 or y2 <= y1:
                continue
            bbox = [x1, y1, x2, y2]
            # Lipla returns TL, BL, BR, TR; the rest of this app uses TL, TR, BR, BL.
            quad = points[[0, 3, 2, 1]].tolist()
            raw_fields = dict(region=str(result.area), category=str(result.class_number),
                              kana=str(result.kana), serial=str(result.number))
            display = f"{raw_fields['region']} {raw_fields['category']} {raw_fields['kana']} {raw_fields['serial']}"
            confidence = min(float(getattr(result, key)) for key in
                             ('area_score', 'class_number_score', 'kana_score', 'number_score'))
            fields = self.parser(display) if self.parser else raw_fields
            candidates.append(dict(bbox_in_vehicle=bbox, quad_in_vehicle=quad,
                                   text=display, fields=fields, confidence=confidence,
                                   ocr_backend='lipla-native', detection_source='lipla-native',
                                   detection_score=float(result.score), rectification='lipla-native'))
            proposals.append(dict(bbox_in_vehicle=bbox, quad_in_vehicle=quad,
                                  detection_source='lipla-native', text_found=True))
        report = dict(status='text_read' if candidates else 'not_detected_or_unreadable',
                      source='lipla-native', proposals=proposals, attempts=[],
                      timing_scope='lipla_detection_rectification_ocr_combined',
                      plate_recognition_ms=combined_ms,
                      plate_detection_ms=0.0, rectification_ms=0.0, ocr_ms=combined_ms)
        return candidates, report
