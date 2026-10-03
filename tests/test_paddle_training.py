"""Manual corrections win and pseudo-labels never enter held-out validation."""
from io import BytesIO
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

import events
import ocr_learning
from app import delete_observations
from paddle_training import export, partition


class PaddleTrainingTests(unittest.TestCase):
    def test_skewed_existing_evaluation_pool_is_balanced(self):
        for explicitly_designated in (False, True):
            with self.subTest(explicit=explicitly_designated), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                ocr_learning.initialize(root)
                keys = [f'品川|300|あ|{n}' for n in range(1, 500)
                        if ocr_learning.validation_group(f'品川|300|あ|{n}')][:5]
                training_key = next(f'品川|300|あ|{n}' for n in range(1, 500)
                                    if partition(f'品川|300|あ|{n}') == 'train')
                with events.connection(root) as db:
                    for n, key in enumerate(keys + [training_key]):
                        db.execute("""INSERT INTO ocr_samples
                            (id,observation_id,candidate_index,plate_key,original_text,top_text,bottom_text,
                             split,image,image_sha256,created_at,source,ocr_confidence)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (str(n), str(n), 0, key, '', '品川300', 'あ1', .45,
                             b'crop', str(n), events.utc(), 'automatic', .99))
                    if explicitly_designated:
                        db.executemany('INSERT INTO ocr_plate_partitions VALUES (?,?)',
                                       [(key, 'validation') for key in keys])
                    # Removed records must not affect the effective distribution.
                    db.execute('INSERT INTO ocr_plate_partitions VALUES (?,?)', ('deleted', 'test'))
                rows = ocr_learning.dataset_snapshot(root, minimum_train=1)
                self.assertEqual([sum(r['partition'] == group for r in rows)
                                  for group in ('train', 'validation', 'test')], [1, 3, 2])
                before = {r['plate_key']: r['partition'] for r in rows}
                ocr_learning.initialize(root)
                after = {r['plate_key']: r['partition'] for r in
                         ocr_learning.dataset_snapshot(root, require_ready=False)}
                self.assertEqual(before, after)
                self.assertEqual(after[training_key], 'train')

    def test_small_evaluation_pool_and_reverse_skew(self):
        for size in range(1, 7):
            for group in ('validation', 'test'):
                rows = [{'plate_key': str(n), 'partition': group} for n in range(size)]
                ocr_learning.balance_evaluation_samples(rows)
                self.assertLessEqual(abs(sum(r['partition'] == 'validation' for r in rows) -
                                         sum(r['partition'] == 'test' for r in rows)), 1)
                original = {r['plate_key']: r['partition'] for r in rows}
                ocr_learning.balance_evaluation_samples(rows)
                self.assertEqual(original, {r['plate_key']: r['partition'] for r in rows})

    def test_designated_evaluation_is_balanced_and_stable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ocr_learning.initialize(root)
            with events.connection(root) as db:
                keys = [f'品川|300|あ|{n}' for n in range(1, 5)]
                assignments = [ocr_learning.assign_evaluation_partition(db, key) for key in keys]
                self.assertEqual(assignments, ['validation', 'test', 'validation', 'test'])
                self.assertEqual(ocr_learning.assign_evaluation_partition(db, keys[0]), 'validation')
                for n, key in enumerate(keys):
                    identifier = f'{n+1:032x}'
                    db.execute('''INSERT INTO ocr_samples
                        (id,observation_id,candidate_index,plate_key,original_text,top_text,bottom_text,
                         split,image,image_sha256,created_at,source,ocr_confidence)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                        (identifier, identifier, 0, key, '', '品川300', f'あ{n+1}', .45,
                         b'crop'+bytes([n]), identifier, events.utc(), 'automatic', .99))
            self.assertEqual([r['partition'] for r in ocr_learning.dataset_snapshot(root, minimum_train=0)],
                             ['validation', 'test', 'validation', 'test'])
            ocr_learning.initialize(root)
            with events.connection(root) as db:
                self.assertEqual([db.execute('SELECT partition FROM ocr_plate_partitions WHERE plate_key=?',
                                             (key,)).fetchone()[0] for key in keys], assignments)

    def test_old_validation_designations_are_migrated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with events.connection(root) as db:
                db.execute('''CREATE TABLE ocr_plate_partitions (plate_key TEXT PRIMARY KEY,
                    partition TEXT NOT NULL CHECK(partition='validation'))''')
                db.executemany('INSERT INTO ocr_plate_partitions VALUES (?,?)',
                               [(f'品川|300|あ|{n}', 'validation') for n in range(1, 5)])
            ocr_learning.initialize(root)
            with events.connection(root) as db:
                self.assertEqual([r[0] for r in db.execute('SELECT partition FROM ocr_plate_partitions ORDER BY plate_key')],
                                 ['validation', 'test', 'validation', 'test'])

    def test_explicit_validation_moves_a_whole_plate_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ocr_learning.initialize(root)
            keys = [(n, partition(f'品川|300|あ|{n}')) for n in range(1, 100)]
            train = next(n for n, group in keys if group == 'train')
            val = [n for n, group in keys if group == 'val'][:2]
            test = [n for n, group in keys if group == 'test'][:2]
            with events.connection(root) as db:
                for n in [train] + val + test:
                    key = f'品川|300|あ|{n}'
                    source = 'automatic' if n == train else 'manual'
                    identifier = f'{n:032x}'
                    db.execute('''INSERT INTO ocr_samples
                        (id,observation_id,candidate_index,plate_key,original_text,top_text,bottom_text,
                         split,image,image_sha256,created_at,source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                        (identifier, identifier, 0, key, '', '品川300', f'あ{n}', .45,
                         b'crop'+bytes([n]), identifier, events.utc(), source))
                    if source == 'automatic':
                        db.execute('''INSERT INTO ocr_auto_candidates
                            (observation_id,candidate_index,plate_key,confidence,status,created_at)
                            VALUES (?,?,?,?,?,?)''', (identifier, 0, key, .99, 'pseudo', events.utc()))
                db.execute('INSERT INTO ocr_plate_partitions VALUES (?,?)',
                           (f'品川|300|あ|{train}', 'validation'))
            rows = ocr_learning.dataset_snapshot(root, minimum_train=0)
            self.assertEqual(next(r['partition'] for r in rows if r['plate_key'].endswith(f'|{train}')),
                             'validation')

    def test_98_percent_auto_labels_supply_validation_and_test_without_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'images').mkdir()
            ocr_learning.initialize(root)
            keys = [(n, partition(f'品川|300|あ|{n}')) for n in range(1, 100)]
            numbers = ([n for n, group in keys if group == 'train'][:5] +
                       [n for n, group in keys if group == 'val'][:2] +
                       [n for n, group in keys if group == 'test'][:2])
            with events.connection(root) as db:
                db.execute('CREATE TABLE observations (id TEXT PRIMARY KEY, details_json TEXT)')
                for n in numbers:
                    identifier = f'{n:032x}'
                    photo = Image.new('RGB', (120, 60), (n, 100, 100))
                    path = root / 'images' / f'{identifier}.jpg'
                    photo.save(path)
                    image = BytesIO()
                    photo.save(image, 'PNG')
                    record = dict(id=identifier, image_path=str(path), plate_candidates=[dict(
                        ocr_backend='lipla-native', confidence=.99, bbox_in_vehicle=[0, 0, 120, 60],
                        fields=dict(region='品川', category='300', kana='あ', serial=str(n)))])
                    db.execute('INSERT INTO observations VALUES (?,?)', (identifier,
                               json.dumps(record, ensure_ascii=False)))
                    with patch.object(ocr_learning, 'sample_image', return_value=(image.getvalue(), '')):
                        ocr_learning.queue_observation(root, record, db)
            self.assertEqual({r['source'] for r in ocr_learning.dataset_snapshot(root, minimum_train=0)},
                             {'automatic'})
            report = export(root, root / 'export')
            self.assertEqual((report['train_unique_plates'], report['auto_eval_val'], report['auto_eval_test']),
                             (5, 2, 2))
            self.assertEqual((report['det_train'], report['det_val'], report['det_test']), (5, 2, 2))

    def test_auto_candidates_fill_training_threshold_without_entering_evaluation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'images').mkdir()
            ocr_learning.initialize(root)
            keys = [(n, partition(f'品川|300|あ|{n}')) for n in range(1, 100)]
            train = [n for n, split in keys if split == 'train'][:5]
            val = [n for n, split in keys if split == 'val'][:2]
            test = [n for n, split in keys if split == 'test'][:2]
            with events.connection(root) as db:
                db.execute('CREATE TABLE observations (id TEXT PRIMARY KEY, details_json TEXT)')
                for n in train + val + test:
                    identifier = f'{n:032x}'
                    image = Image.new('RGB', (120, 60), (n, 255-n, 140))
                    path = root / 'images' / f'{identifier}.jpg'
                    image.save(path)
                    record = dict(id=identifier, image_path=str(path), plate_candidates=[dict(
                        ocr_backend='lipla-native', confidence=.80, bbox_in_vehicle=[0, 0, 120, 60],
                        fields=dict(region='品川', category='300', kana='あ', serial=str(n)))])
                    db.execute('INSERT INTO observations VALUES (?,?)', (identifier,
                               json.dumps(record, ensure_ascii=False)))
                    if n in train:
                        ocr_learning.queue_observation(root, record, db)
                    else:
                        data = BytesIO()
                        image.save(data, 'PNG')
                        db.execute('INSERT INTO ocr_samples (id,observation_id,candidate_index,plate_key,original_text,top_text,bottom_text,split,image,image_sha256,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                                   (identifier, identifier, 0, f'品川|300|あ|{n}',
                                    '', '品川300', f'あ{n}', .45, data.getvalue(),
                                    hashlib.sha256(data.getvalue()).hexdigest(), events.utc()))
            for split in ('val', 'test'):
                n = next(n for n, group in keys if group == split and n not in val + test)
                identifier = f'{n+1000:032x}'
                image = Image.new('RGB', (120, 60), 'white')
                path = root / 'images' / f'{identifier}.jpg'
                image.save(path)
                record = dict(id=identifier, image_path=str(path), plate_candidates=[dict(
                    ocr_backend='lipla-native', confidence=.85, bbox_in_vehicle=[0, 0, 120, 60],
                    fields=dict(region='品川', category='300', kana='あ', serial=str(n)))])
                with events.connection(root) as db:
                    db.execute('INSERT INTO observations VALUES (?,?)',
                               (identifier, json.dumps(record, ensure_ascii=False)))
                    data = BytesIO()
                    image.save(data, 'PNG')
                    with patch.object(ocr_learning, 'sample_image', return_value=(data.getvalue(), '')):
                        ocr_learning.queue_observation(root, record, db)
            report = export(root, root / 'export')
            self.assertEqual(report['train_unique_plates'], 7)
            self.assertEqual(report['unreviewed_pseudo'], 7)
            self.assertEqual((report['det_train'], report['det_val'], report['det_test']), (7, 2, 2))
            self.assertEqual(len(list((root / 'export' / 'rec' / 'val').glob('pseudo-*'))), 0)

    def test_corrected_label_overrides_lipla_and_validation_is_reviewed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'images').mkdir()
            ocr_learning.initialize(root)
            keys = [(str(serial), partition(f'品川|300|あ|{serial}')) for serial in range(1, 90)]
            train = [int(s) for s, part in keys if part == 'train'][:5]
            val = [int(s) for s, part in keys if part == 'val'][:2]
            test = [int(s) for s, part in keys if part == 'test'][:2]
            self.assertEqual((len(train), len(val), len(test)), (5, 2, 2))
            for n in train + val + test:
                plate = Image.new('RGB', (120, 60), (n, 255-n, 140))
                data = BytesIO()
                plate.save(data, 'PNG')
                identifier = f'{n:032x}'
                image_path = root / 'images' / f'{identifier}.jpg'
                plate.save(image_path)
                record = dict(id=identifier, image_path=str(image_path), plate_candidates=[dict(
                    text='品川 300 あ 9999', ocr_backend='lipla-native', confidence=.99,
                    bbox_in_vehicle=[0, 0, 120, 60],
                    fields=dict(region='品川', category='300', kana='あ', serial='9999'))])
                with events.connection(root) as db:
                    db.execute('CREATE TABLE IF NOT EXISTS observations (id TEXT PRIMARY KEY, details_json TEXT)')
                    db.execute('INSERT INTO observations VALUES (?,?)', (identifier,
                               json.dumps(record, ensure_ascii=False)))
                    db.execute('''INSERT INTO ocr_samples (id,observation_id,candidate_index,plate_key,original_text,top_text,bottom_text,split,image,image_sha256,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                               (identifier, identifier, 0, f'品川|300|あ|{n}',
                                '品川300あ9999', '品川300', f'あ{n}', .45,
                                data.getvalue(), hashlib.sha256(data.getvalue()).hexdigest(), events.utc()))
            output = root / 'export'
            report = export(root, output)
            self.assertEqual(report['reviewed'], 9)
            self.assertEqual(report['unreviewed_pseudo'], 0)
            labels = (output / 'rec' / 'val.txt').read_text()
            self.assertNotIn('9999', labels)
            self.assertIn(f'あ{val[0]}', labels)
            for n in val:
                self.assertTrue((output / 'det' / 'labels' / 'val' / f'{n:032x}.txt').is_file())
            self.assertEqual(len(list((output / 'det' / 'labels' / 'val').glob('*.txt'))), 2)
            self.assertEqual(len(list((output / 'det' / 'labels' / 'test').glob('*.txt'))), 2)
            pseudo_serial = next(n for n in range(90, 200) if partition(f'品川|300|あ|{n}') == 'train')
            observation_id = f'{pseudo_serial:032x}'
            image_path = root / 'images' / f'{observation_id}.jpg'
            Image.new('RGB', (120, 60), 'white').save(image_path)
            pseudo_record = dict(id=observation_id, image_path=str(image_path),
                                 plate_candidates=[dict(ocr_backend='lipla-native', confidence=.99,
                                                        bbox_in_vehicle=[0, 0, 120, 60],
                                                        fields=dict(region='品川', category='300',
                                                                    kana='あ', serial=str(pseudo_serial)))])
            with events.connection(root) as db:
                db.execute('INSERT INTO observations VALUES (?,?)',
                           (observation_id, json.dumps(pseudo_record, ensure_ascii=False)))
                ocr_learning.queue_observation(root, pseudo_record, db)
            self.assertEqual(export(root, root / 'with-pseudo')['unreviewed_pseudo'], 1)
            with events.connection(root) as db:
                # The synthetic observations table in this test contains only JSON.
                db.execute('ALTER TABLE observations ADD COLUMN image_path TEXT')
                db.execute('UPDATE observations SET image_path=? WHERE id=?',
                           (str(image_path), observation_id))
                delete_observations(db, root, [(observation_id, str(image_path))], archive=True)
            self.assertEqual(export(root, root / 'archived-pseudo')['unreviewed_pseudo'], 1)
            with events.connection(root) as db:
                db.execute("DELETE FROM ocr_samples WHERE observation_id=? AND source='automatic'", (observation_id,))
                db.execute("UPDATE ocr_auto_candidates SET status='excluded' WHERE observation_id=?",
                           (observation_id,))
            self.assertEqual(export(root, root / 'excluded-pseudo')['unreviewed_pseudo'], 0)


if __name__ == '__main__':
    unittest.main()
