"""Plate-first stages operating exclusively inside an original vehicle crop."""
from dataclasses import dataclass
import math
import time

from plate_geometry import rectify_candidate


@dataclass
class PlateDetection:
    regions: list
    source: str
    attempts: list


class PlateDetector:
    """Dedicated AI first, bounded recovery only after a complete miss.

    Callbacks keep inference backends replaceable without loading models here.
    Geometry proposals are explicitly distinguished from trained AI detections.
    """
    def __init__(self, cv2, geometry, bound, learned=None):
        self.cv2, self.geometry, self.bound, self.learned = cv2, geometry, bound, learned

    def detect(self, crop, fallback_only=False):
        cv2 = self.cv2
        height, width = crop.shape[:2]
        attempts = []
        if width < 8 or height < 4:
            return PlateDetection([], 'none', attempts)

        def attempt(source, operation):
            start = time.perf_counter()
            regions = self.bound(operation(), width, height)
            attempts.append(dict(source=source, candidates=len(regions),
                                 elapsed_ms=(time.perf_counter()-start)*1000))
            return regions

        if self.learned is not None and not fallback_only:
            for size in (640, 960):
                source = f'plate_model_{size}'
                regions = attempt(source, lambda: self.learned(crop, size))
                if regions:
                    return PlateDetection(regions, source, attempts)

        regions = attempt('geometry', lambda: self.geometry(crop))
        if regions:
            return PlateDetection(regions, 'geometry', attempts)

        def recovery():
            scale = min(2.0, 960.0/max(width, height))
            resized = cv2.resize(crop, (max(8, round(width*scale)), max(4, round(height*scale))),
                                 interpolation=cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA)
            gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
            contrast = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
            regions = self.geometry(cv2.cvtColor(contrast, cv2.COLOR_GRAY2BGR))
            sx, sy = width/resized.shape[1], height/resized.shape[0]
            return [(math.floor(x*sx), math.floor(y*sy),
                     math.ceil((x+w)*sx)-math.floor(x*sx),
                     math.ceil((y+h)*sy)-math.floor(y*sy)) for x, y, w, h in regions]

        regions = attempt('geometry_contrast_scale', recovery)
        return PlateDetection(regions, 'geometry_contrast_scale' if regions else 'none', attempts)


class PlateRectifier:
    def __init__(self, cv2):
        self.cv2 = cv2

    def prepare(self, crop, bbox):
        return rectify_candidate(crop, bbox, self.cv2)


class PlateRecognitionPipeline:
    """Detect -> rectify -> OCR; retain detection evidence even when OCR fails."""
    def __init__(self, detector, rectifier, recognize):
        self.detector, self.rectifier, self.recognize = detector, rectifier, recognize

    def run(self, crop):
        started = time.perf_counter()
        detection = self.detector.detect(crop)
        metrics = dict(plate_detection_ms=(time.perf_counter()-started)*1000,
                       rectification_ms=0.0, ocr_ms=0.0)
        results, proposals = [], []
        seen = set()

        def read_regions(found):
            for x, y, w, h in found.regions:
                bbox = [x, y, x+w, y+h]
                if tuple(bbox) in seen:
                    continue
                seen.add(tuple(bbox))
                stage = time.perf_counter()
                roi, quad, method = self.rectifier.prepare(crop, bbox)
                metrics['rectification_ms'] += (time.perf_counter()-stage)*1000
                stage = time.perf_counter()
                candidate = self.recognize(roi, bbox, quad, method)
                metrics['ocr_ms'] += (time.perf_counter()-stage)*1000
                proposals.append(dict(bbox_in_vehicle=bbox, quad_in_vehicle=quad,
                                      rectification=method, detection_source=found.source,
                                      text_found=bool(candidate and candidate.get('text'))))
                if candidate and candidate.get('text'):
                    candidate['detection_source'] = found.source
                    results.append(candidate)

        read_regions(detection)
        if detection.source.startswith('plate_model_') and not any(
                c.get('fields') is not None for c in results):
            stage = time.perf_counter()
            fallback = self.detector.detect(crop, fallback_only=True)
            metrics['plate_detection_ms'] += (time.perf_counter()-stage)*1000
            detection.attempts.extend(fallback.attempts)
            read_regions(fallback)
        results.sort(key=lambda c: (c['fields'] is not None, c['confidence']), reverse=True)
        source = results[0]['detection_source'] if results else detection.source
        status = ('not_detected' if not proposals else
                  'text_read' if results else 'detected_unreadable')
        return results, dict(status=status, source=source,
                             proposals=proposals, attempts=detection.attempts, **metrics)
