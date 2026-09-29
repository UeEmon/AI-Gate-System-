"""Export reviewed Lipla results to PaddleOCR and dedicated plate YOLO datasets.

The review label always wins over a Lipla prediction. Unreviewed predictions
may be used for training only at high confidence; validation is human reviewed.
"""
import argparse
from io import BytesIO
import json
from pathlib import Path

from PIL import Image

import events
import ocr_learning


def partition(key):
    return ('test' if ocr_learning.test_group(key) else
            'val' if ocr_learning.validation_group(key) else 'train')


def safe_vehicle_image(root, record):
    source = record.get('image_path')
    if not source:
        return None
    path = Path(source).resolve()
    if not path.is_relative_to((Path(root) / 'images').resolve()) or not path.is_file():
        return None
    return path


def plate_box(candidate, size):
    box = candidate.get('bbox_in_vehicle', [])
    if len(box) != 4 or any(type(v) not in (int, float) for v in box):
        return None
    x1, y1, x2, y2 = box
    width, height = size
    if not 0 <= x1 < x2 <= width or not 0 <= y1 < y2 <= height:
        return None
    return (x1, y1, x2, y2)


def export(root, output, pseudo_confidence=.80):
    if not 0.8 <= pseudo_confidence <= 1:
        raise ValueError('未修正のLipla教師データには80%以上の信頼度を指定してください。')
    root, output = Path(root), Path(output)
    if output.exists():
        raise FileExistsError('既存の学習データは上書きしません。別の出力先を指定してください。')
    reviewed = ocr_learning.dataset_snapshot(root, minimum_train=0)
    with events.connection(root) as db:
        observations = {row['id']: json.loads(row['details_json']) for row in
                        db.execute('SELECT id, details_json FROM observations')}
        observations.update({row['observation_id']: json.loads(row['details_json']) for row in
                             db.execute('SELECT observation_id,details_json FROM ocr_auto_archive')})
        auto_status = {(row['observation_id'], row['candidate_index']): row['status'] for row in
                       db.execute('SELECT observation_id,candidate_index,status FROM ocr_auto_candidates')}
    output.mkdir(parents=True)
    rec_lines = {'train': [], 'val': [], 'test': []}
    detector = {'train': {}, 'val': {}, 'test': {}}
    manual_ids = {(r['observation_id'], r['candidate_index']) for r in reviewed}
    reviewed_keys = {r['plate_key'] for r in reviewed if r['source'] == 'manual'}
    val_keys = {r['plate_key'] for r in reviewed if r['source'] == 'manual' and partition(r['plate_key']) == 'val'}
    audit = []
    pseudo_count = 0
    train_keys = set()

    def add_detector(record, candidate, split, source):
        image_path = safe_vehicle_image(root, record)
        if image_path is None:
            return
        try:
            with Image.open(image_path) as im:
                size = im.size
                box = plate_box(candidate, size)
                if box is None:
                    return
                # A vehicle can have multiple plate candidates: only reviewed or
                # high-confidence examples are eligible for this export.
                vehicle_id = record['id']
                row = detector[split].setdefault(vehicle_id, dict(path=image_path, size=size,
                                                                   boxes=[], sources=[]))
                if box not in row['boxes']:
                    row['boxes'].append(box)
                    row['sources'].append(source)
        except (OSError, ValueError):
            return

    for row in reviewed:
        split = 'val' if row['partition'] == 'validation' else row['partition']
        if row['source'] == 'automatic' and row['plate_key'] in reviewed_keys:
            continue
        if split == 'train':
            train_keys.add(row['plate_key'])
        if row['source'] == 'automatic':
            pseudo_count += 1
        with Image.open(BytesIO(row['image'])) as im:
            image = im.convert('RGB')
        for index, (label, box) in enumerate(ocr_learning.training_crops(row, *image.size)):
            if not label or '\t' in label or '\n' in label or not label.isprintable():
                raise ValueError('学習正解に不正な文字があります。')
            path = output / 'rec' / split / f"{row['id']}-{index}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            image.crop(box).save(path)
            rec_lines[split].append(f'{split}/{path.name}\t{label}')
        audit.append(dict(sample=row['id'], partition=split, label_source=row['source'],
                          original=row['original_text'], corrected=row['top_text']+row['bottom_text']))
        record = observations.get(row['observation_id'])
        if record and row['candidate_index'] < len(record.get('plate_candidates', [])):
            add_detector(record, record['plate_candidates'][row['candidate_index']], split, row['source'])

    for record in observations.values():
        for index, candidate in enumerate(record.get('plate_candidates', [])):
            if (record['id'], index) in manual_ids:
                continue
            if auto_status.get((record['id'], index)) != 'pseudo':
                continue
            if candidate.get('ocr_backend') not in ('lipla-native', 'lipla-jp'):
                continue
            if float(candidate.get('confidence') or 0) < pseudo_confidence:
                continue
            try:
                key = events.plate_key(candidate['fields'])
            except (KeyError, ValueError, TypeError):
                continue
            # A reviewed identity anywhere in the dataset overrides every
            # Unreviewed predictions cannot enter validation or held-out test.
            if key in reviewed_keys or key in val_keys:
                continue
            source = safe_vehicle_image(root, record)
            if source is None:
                continue
            try:
                with Image.open(source) as vehicle:
                    box = plate_box(candidate, vehicle.size)
                    if box is None:
                        continue
                    plate = vehicle.crop(box).convert('RGB')
                    boundary = round(plate.height * .45)
                    if boundary < 8 or plate.height-boundary < 8:
                        continue
                    fields = candidate['fields']
                    labels = [fields['region'] + fields['category'],
                              fields['kana'] + fields['serial']]
                    for part, (region, label) in enumerate(zip(
                        ((0, 0, plate.width, boundary),
                         (0, boundary, plate.width, plate.height)), labels)):
                        if not label or any(c in label for c in '\t\n'):
                            raise ValueError('不正なOCR文字です。')
                        name = f"pseudo-{record['id']}-{index}-{part}.png"
                        path = output / 'rec' / 'train' / name
                        path.parent.mkdir(parents=True, exist_ok=True)
                        plate.crop(region).save(path)
                        rec_lines['train'].append(f'train/{name}\t{label}')
                    add_detector(record, candidate, 'train', 'lipla-pseudo')
                    pseudo_count += 1
                    train_keys.add(key)
            except (OSError, ValueError, TypeError):
                continue

    if not rec_lines['val'] or not rec_lines['train']:
        raise ValueError('学習用画像と手動確認済みの検証用画像が必要です。')
    for split, lines in rec_lines.items():
        (output / 'rec' / f'{split}.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    for split, rows in detector.items():
        for identifier, row in rows.items():
            image_path = output / 'det' / 'images' / split / f'{identifier}.jpg'
            image_path.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(row['path']) as image:
                image.convert('RGB').save(image_path)
            width, height = row['size']
            labels = [f'0 {(a+c)/2/width:.6f} {(b+d)/2/height:.6f} '
                      f'{(c-a)/width:.6f} {(d-b)/height:.6f}'
                      for a, b, c, d in row['boxes']]
            label_path = output / 'det' / 'labels' / split / f'{identifier}.txt'
            label_path.parent.mkdir(parents=True, exist_ok=True)
            label_path.write_text('\n'.join(labels) + '\n', encoding='ascii')
    (output / 'det' / 'dataset.yaml').write_text(
        f'path: {json.dumps(str((output / "det").resolve()))}\n'
        'train: images/train\nval: images/val\nnames:\n  0: plate\n')
    report = dict(reviewed=sum(r['source'] == 'manual' for r in reviewed), unreviewed_pseudo=pseudo_count,
                  train_unique_plates=len(train_keys),
                  rec_train=len(rec_lines['train']), rec_val=len(rec_lines['val']),
                  det_train=len(detector['train']), det_val=len(detector['val']),
                  det_test=len(detector['test']), rec_test=len(rec_lines['test']),
                  pseudo_confidence=pseudo_confidence)
    (output / 'manifest.json').write_text(json.dumps(dict(report=report, samples=audit),
                                                    ensure_ascii=False, indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--pseudo-confidence', type=float, default=.80)
    args = parser.parse_args()
    print(json.dumps(export(args.data, args.output, args.pseudo_confidence), ensure_ascii=False))
