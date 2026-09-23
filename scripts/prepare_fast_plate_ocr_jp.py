"""Create Japanese FastPlateOCR CSV/config files from reviewed annotations."""
import argparse
import csv
import hashlib
import json
import unicodedata
from pathlib import Path
import yaml


def prepare(source, train_csv, val_csv, config_path, region_names=None):
    with Path(source).open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    required = {'image_path', 'plate'}
    if not rows or not required.issubset(rows[0]):
        raise ValueError('annotations.csvには image_path,plate 列が必要です。')
    clean = []
    chars = set('0123456789')
    for row in rows:
        image_path = row['image_path'].strip()
        plate = unicodedata.normalize('NFKC', row['plate']).replace(' ', '')
        if not image_path or not plate or len(plate) > 12:
            raise ValueError(f'不正な注釈です: {row}')
        chars.update(plate)
        clean.append({'image_path': str((Path(source).parent / image_path).resolve()), 'plate': plate,
                      'partition': row.get('partition', ''),
                      **({'plate_region': row['plate_region'].strip()} if row.get('plate_region') else {})})
    explicit = any(r['partition'] for r in clean)
    if explicit:
        if any(r['partition'] not in ('train', 'validation') for r in clean):
            raise ValueError('全画像の学習・評価区分を指定してください。')
    else:
        identities = sorted({r['plate'] for r in clean}, key=lambda p: hashlib.sha256(p.encode()).hexdigest())
        if len(identities) < 2:
            raise ValueError('学習・評価には異なるナンバーが必要です。')
        validation = set(identities[:max(1, round(len(identities) * .2))])
        for row in clean:
            row['partition'] = 'validation' if row['plate'] in validation else 'train'
    training = [r for r in clean if r['partition'] == 'train']
    validation = [r for r in clean if r['partition'] == 'validation']
    if not training or not validation or {r['plate'] for r in training} & {r['plate'] for r in validation}:
        raise ValueError('学習・評価には重複しないナンバーを指定してください。')
    Path(train_csv).parent.mkdir(parents=True, exist_ok=True)
    fields = ['image_path', 'plate'] + (['plate_region'] if any('plate_region' in r for r in clean) else [])
    for target, subset in ((train_csv, training), (val_csv, validation)):
        with Path(target).open('w', encoding='utf-8', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore'); writer.writeheader(); writer.writerows(subset)
    config = {
        'max_plate_slots': max(len(r['plate']) for r in clean),
        'alphabet': ''.join(sorted(chars)), 'pad_char': '_', 'img_height': 64,
        'img_width': 192, 'keep_aspect_ratio': True, 'interpolation': 'linear',
        'image_color_mode': 'grayscale', 'padding_color': 114,
    }
    if region_names:
        config['plate_regions'] = region_names
    Path(config_path).write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding='utf-8')
    return {'samples': len(clean), 'train': len(training), 'val': len(validation),
            'alphabet': config['alphabet']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--annotations', type=Path, required=True)
    parser.add_argument('--train-csv', type=Path)
    parser.add_argument('--val-csv', type=Path)
    parser.add_argument('--plate-config', type=Path)
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        args.train_csv = args.output_dir / 'train.csv'
        args.val_csv = args.output_dir / 'val.csv'
        args.plate_config = args.output_dir / 'plate_config.yaml'
    if not all((args.train_csv, args.val_csv, args.plate_config)):
        parser.error('--train-csv, --val-csv, --plate-config または --output-dir が必要です。')
    print(json.dumps(prepare(args.annotations, args.train_csv, args.val_csv, args.plate_config), ensure_ascii=False))
