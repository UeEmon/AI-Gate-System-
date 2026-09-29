"""Automatic candidates remain provisional and manual truth overrides them."""
import tempfile
import unittest
from pathlib import Path

import events
import ocr_learning
from app import open_database


class AutoQueueTests(unittest.TestCase):
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
                         fields=dict(region='横浜', category='500', kana='あ', serial='5678'))])
                self.assertEqual(ocr_learning.queue_observation(root, record, db), 2)
                self.assertEqual(ocr_learning.queue_observation(root, record, db), 2)
                self.assertEqual([r[0] for r in db.execute(
                    'SELECT status FROM ocr_auto_candidates ORDER BY candidate_index')],
                    ['pseudo', 'pending'])
                db.commit()
                self.assertEqual(len(ocr_learning.auto_signature(root)), 1)
                record['plate_candidates'][1]['confidence'] = .80
                ocr_learning.queue_observation(root, record, db)
                self.assertEqual(db.execute('SELECT status FROM ocr_auto_candidates '
                                            'WHERE candidate_index=1').fetchone()[0], 'pseudo')
                db.execute("UPDATE ocr_auto_candidates SET status='excluded' WHERE candidate_index=0")
                ocr_learning.queue_observation(root, record, db)
                self.assertEqual(db.execute('SELECT status FROM ocr_auto_candidates '
                                            'WHERE candidate_index=0').fetchone()[0], 'excluded')
                db.execute('INSERT INTO ocr_samples VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                           ('manual', 'observation', 1, '横浜|500|あ|9999', '', '横浜500',
                            'あ9999', .45, b'image', 'digest', events.utc()))
                ocr_learning.queue_observation(root, record, db)
                self.assertIsNone(db.execute('SELECT status FROM ocr_auto_candidates '
                                             'WHERE candidate_index=1').fetchone())


if __name__ == '__main__':
    unittest.main()
