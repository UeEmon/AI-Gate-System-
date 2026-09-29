"""Automatic candidates remain provisional and manual truth overrides them."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import events
import ocr_learning
from app import open_database


class AutoQueueTests(unittest.TestCase):
    def test_migration_deduplicates_by_confidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with open_database(root / 'gate.db') as db:
                db.execute('''CREATE TABLE ocr_samples (
                    id TEXT PRIMARY KEY,observation_id TEXT,candidate_index INTEGER,plate_key TEXT,
                    original_text TEXT,top_text TEXT,bottom_text TEXT,split REAL,image BLOB,
                    image_sha256 TEXT,created_at TEXT)''')
                db.execute('''CREATE TABLE ocr_auto_candidates (
                    observation_id TEXT,candidate_index INTEGER,plate_key TEXT,confidence REAL,
                    status TEXT,created_at TEXT,PRIMARY KEY(observation_id,candidate_index))''')
                for name, score, timestamp in [('best', .99, 'older'), ('newer', .81, 'recent')]:
                    db.execute('INSERT INTO ocr_samples VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                               (name, name, 0, '品川|300|あ|1234', '', '品川300', 'あ1234',
                                .45, b'crop', name, timestamp))
                    db.execute('INSERT INTO ocr_auto_candidates VALUES (?,?,?,?,?,?)',
                               (name, 0, '品川|300|あ|1234', score, 'pseudo', timestamp))
                db.commit()
            ocr_learning.initialize(root)
            with open_database(root / 'gate.db') as db:
                self.assertEqual(db.execute('SELECT id,ocr_confidence FROM ocr_samples').fetchall(),
                                 [('best', .99)])
                self.assertEqual(db.execute("SELECT status FROM ocr_auto_candidates WHERE observation_id='newer'").fetchone()[0],
                                 'excluded')

    def test_same_plate_keeps_higher_confidence_then_newer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'images').mkdir()
            ocr_learning.initialize(root)
            with open_database(root / 'gate.db') as db:
                for identifier, confidence in [('first', .80), ('second', .95), ('third', .95),
                                               ('low', .85)]:
                    path = root / 'images' / (identifier + '.jpg')
                    path.write_bytes(b'image')
                    record = dict(id=identifier, image_path=str(path), plate_candidates=[dict(
                        ocr_backend='lipla-native', confidence=confidence,
                        fields=dict(region='品川', category='300', kana='あ', serial='1234'))])
                    with patch.object(ocr_learning, 'sample_image', return_value=(identifier.encode(), '')):
                        ocr_learning.queue_observation(root, record, db)
                kept = db.execute('SELECT observation_id,ocr_confidence FROM ocr_samples').fetchall()
                self.assertEqual(kept, [('third', .95)])
                self.assertEqual(db.execute("SELECT status FROM ocr_auto_candidates WHERE observation_id='low'").fetchone()[0],
                                 'excluded')

    def test_high_confidence_creates_real_auto_sample_and_manual_can_replace_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'images').mkdir()
            image = root / 'images' / 'vehicle.jpg'
            image.write_bytes(b'photo')
            with open_database(root / 'gate.db') as db:
                ocr_learning.initialize(root)
                serial = next(str(n) for n in range(1, 100) if
                              ocr_learning.validation_group(f'品川|330|さ|{n}'))
                record = dict(id='observation', image_path=str(image), plate_candidates=[
                    dict(ocr_backend='lipla-native', confidence=.80,
                         fields=dict(region='品川', category='330', kana='さ', serial=serial))])
                with patch.object(ocr_learning, 'sample_image', return_value=(b'plate-png', '品川330さ1234')):
                    ocr_learning.queue_observation(root, record, db)
                    ocr_learning.queue_observation(root, record, db)
                rows = db.execute('SELECT source,image FROM ocr_samples').fetchall()
                self.assertEqual(len(rows), 1)
                self.assertEqual((rows[0][0], rows[0][1]), ('automatic', b'plate-png'))
                sample = db.execute('SELECT plate_key,source FROM ocr_samples').fetchone()
                self.assertTrue(ocr_learning.validation_group(sample[0]))
                db.execute("UPDATE ocr_samples SET source='manual',bottom_text='さ4321' WHERE observation_id='observation'")
                ocr_learning.queue_observation(root, record, db)
                self.assertEqual(db.execute('SELECT source,bottom_text FROM ocr_samples').fetchone(),
                                 ('manual', 'さ4321'))

    def test_high_and_low_confidence_and_review_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'images').mkdir()
            image = root / 'images' / 'vehicle.jpg'
            image.write_bytes(b'image')
            with open_database(root / 'gate.db') as db:
                ocr_learning.initialize(root)
                record = dict(id='observation', image_path=str(image), plate_candidates=[
                    dict(ocr_backend='lipla-native', confidence=.99,
                         fields=dict(region='品川', category='330', kana='さ', serial='1234')),
                    dict(ocr_backend='lipla-native', confidence=.79,
                         fields=dict(region='横浜', category='500', kana='あ', serial='5678')),
                    dict(ocr_backend='lipla-native', confidence=.49,
                         fields=dict(region='横浜', category='500', kana='あ', serial='0001')),
                    dict(ocr_backend='lipla-native', confidence=.50,
                         fields=dict(region='横浜', category='500', kana='あ', serial='0002'))])
                self.assertEqual(ocr_learning.queue_observation(root, record, db), 3)
                self.assertEqual(ocr_learning.queue_observation(root, record, db), 3)
                self.assertEqual([r[0] for r in db.execute(
                    'SELECT status FROM ocr_auto_candidates ORDER BY candidate_index')],
                    ['pseudo', 'pending', 'pending'])
                db.execute('''INSERT INTO ocr_auto_candidates
                    (observation_id,candidate_index,plate_key,confidence,status,created_at)
                    VALUES (?,?,?,?,?,?)''', ('legacy', 0, '品川|300|あ|1', .49, 'pending', 'old'))
                db.commit()
                ocr_learning.initialize(root)
                self.assertIsNone(db.execute("SELECT 1 FROM ocr_auto_candidates WHERE observation_id='legacy'").fetchone())
                self.assertEqual(len(ocr_learning.auto_signature(root)), 1)
                record['plate_candidates'][1]['confidence'] = .80
                ocr_learning.queue_observation(root, record, db)
                self.assertEqual(db.execute('SELECT status FROM ocr_auto_candidates '
                                            'WHERE candidate_index=1').fetchone()[0], 'pseudo')
                db.execute("UPDATE ocr_auto_candidates SET status='excluded' WHERE candidate_index=0")
                ocr_learning.queue_observation(root, record, db)
                self.assertEqual(db.execute('SELECT status FROM ocr_auto_candidates '
                                            'WHERE candidate_index=0').fetchone()[0], 'excluded')
                self.assertIsNone(db.execute('SELECT 1 FROM ocr_samples WHERE candidate_index=0').fetchone())
                db.execute('INSERT INTO ocr_samples (id,observation_id,candidate_index,plate_key,original_text,top_text,bottom_text,split,image,image_sha256,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                           ('manual', 'observation', 1, '横浜|500|あ|9999', '', '横浜500',
                            'あ9999', .45, b'image', 'digest', events.utc()))
                ocr_learning.queue_observation(root, record, db)
                self.assertIsNone(db.execute('SELECT status FROM ocr_auto_candidates '
                                             'WHERE candidate_index=1').fetchone())


if __name__ == '__main__':
    unittest.main()
