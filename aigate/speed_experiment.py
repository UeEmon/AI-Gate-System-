"""Opt-in staged experiments; baseline preserves the production pipeline."""
import copy
import math
import time
import json
import os
from dataclasses import dataclass, field

STAGES = [
    ('baseline', '基準'),
    ('tracking', '1: 車両追跡'),
    ('quality', '2: フレーム選別'),
    ('threads', '3: CPUスレッド調整'),
    ('openvino', '4: OpenVINO車両検出'),
    ('conditional', '5: 条件付きLipla OCR'),
]


def configuration(stage='baseline', threads=2):
    names = [x[0] for x in STAGES]
    if stage not in names or type(threads) is not int or not 1 <= threads <= 16:
        raise ValueError('検証段階またはスレッド数が不正です。')
    level = names.index(stage)
    return dict(stage=stage, tracking=level >= 1, quality=level >= 2,
                threads=threads if level >= 3 else 0, openvino=level >= 4,
                conditional=level >= 5)


def environment_config():
    raw = json.loads(os.getenv('GATE_SPEED_EXPERIMENT', '{}'))
    config = configuration(raw.get('stage', 'baseline'), raw.get('threads', 2) or 2)
    # Conditional OCR can be measured independently when OpenVINO is unavailable.
    if 'openvino' in raw:
        if type(raw['openvino']) is not bool:
            raise ValueError('OpenVINO設定が不正です。')
        config['openvino'] = raw['openvino']
    return config


def iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0, x2-x1)*max(0, y2-y1)
    union = (a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-intersection
    return intersection/union if union > 0 else 0


def quality(image):
    import cv2
    gray = cv2.cvtColor(cv2.resize(image, (96, 64)), cv2.COLOR_BGR2GRAY)
    contrast = float(gray.std())
    clipping = float(((gray < 8) | (gray > 247)).mean())
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    return max(0.01, math.log1p(sharpness)*max(0.01, contrast/64)*(1-clipping))


@dataclass
class Track:
    id: int
    box: list
    label: str
    seen: float
    attempted: float = -math.inf
    best_quality: float = 0
    area: float = 0
    stable: int = 0
    key: str = ''
    candidates: list = field(default_factory=list)
    image: object = None


class RecognitionScheduler:
    """Conservative IoU association; ambiguous matches never reuse recognition."""
    def __init__(self, config):
        self.config = config
        self.tracks = {}
        self.next_id = 0
        self.frame = 0

    def assign(self, detections, now):
        self.frame += 1
        self.tracks = {k:t for k,t in self.tracks.items() if now-t.seen <= 1.5}
        scores = [[iou(d['box'], t.box) if d['label'] == t.label else 0
                   for t in self.tracks.values()] for d in detections]
        old = list(self.tracks.values())
        assignments = []
        for index, d in enumerate(detections):
            ranked = sorted(((value, j) for j,value in enumerate(scores[index])), reverse=True)
            match = None
            if ranked and ranked[0][0] >= .5:
                value, j = ranked[0]
                other = max((scores[k][j] for k in range(len(scores)) if k != index), default=0)
                runner_up = ranked[1][0] if len(ranked)>1 else 0
                if value-other >= .1 and value-runner_up >= .1:
                    match = old[j]
            if match is None:
                self.next_id += 1
                match = Track(self.next_id, list(d['box']), d['label'], now)
                self.tracks[match.id] = match
            match.box, match.seen = list(d['box']), now
            assignments.append(match)
        return assignments

    def should_read(self, track, image, now):
        import cv2
        import numpy as np
        if not self.config['tracking']:
            return True, 'baseline'
        thumb = cv2.resize(image, (32, 24))
        appearance_change = track.image is not None and float(np.abs(
            thumb.astype(float)-track.image.astype(float)).mean()) > 35
        if appearance_change:
            track.stable, track.key, track.candidates = 0, '', []
            track.attempted = -math.inf
        track.image = thumb
        if not math.isfinite(track.attempted):
            return True, 'new_vehicle'
        interval = 1.0 if track.stable >= 2 else .15
        q = quality(image) if self.config['quality'] else 0
        area = image.shape[0]*image.shape[1]
        if self.config['quality'] and q > track.best_quality*1.25 and area >= track.area:
            return True, 'quality_improved'
        if now-track.attempted < interval:
            return False, 'stable_interval' if track.stable>=2 else 'retry_interval'
        if self.config['quality'] and q < track.best_quality*.6 and now-track.attempted < .4:
            return False, 'blurred_frame'
        return True, 'periodic_retry'

    def remember(self, track, candidates, image, now):
        from events import plate_key
        track.attempted = now
        if self.config['quality']:
            track.best_quality = max(track.best_quality, quality(image))
        track.area = image.shape[0]*image.shape[1]
        best = max(candidates, key=lambda x:x.get('confidence', 0), default=None)
        try:
            key = plate_key(best['fields']) if best and best.get('confidence',0)>=.98 else ''
        except (KeyError, ValueError, TypeError):
            key = ''
        track.stable = track.stable+1 if key and key==track.key else (1 if key else 0)
        track.key = key
        # Store coordinates relative to the vehicle size for diagnostic continuity only.
        track.candidates = copy.deepcopy(candidates)
        for item in track.candidates:
            item['bbox_fraction'] = [v/(image.shape[1] if i%2==0 else image.shape[0])
                                     for i,v in enumerate(item['bbox_in_vehicle'])]

    def cached(self, track, image):
        candidates = copy.deepcopy(track.candidates)
        for item in candidates:
            item['bbox_in_vehicle'] = [int(v*(image.shape[1] if i%2==0 else image.shape[0]))
                                      for i,v in enumerate(item.pop('bbox_fraction'))]
        return candidates
