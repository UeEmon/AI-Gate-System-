import json
from pathlib import Path
import tempfile
import unittest

from app import open_database, save_observation, prune_observations, delete_observations
import ocr_learning


class ObservationRetentionTests(unittest.TestCase):
    def test_prune_keeps_newest_and_reviewed_samples(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            images = root / 'images'
            images.mkdir()
            ocr_learning.initialize(root)
            with open_database(root / 'gate.db') as db:
                for n in range(102):
                    path = images / f'{n}.jpg'
                    path.write_bytes(b'photo')
                    save_observation(db, dict(id=str(n), processed_at=f'{n:04d}',
                        run_id='run', frame_index=n, media_ms=0, vehicle_type='car',
                        confidence=.9, image_path=str(path)))
                db.execute('INSERT INTO ocr_auto_candidates VALUES (?,?,?,?,?,?)',
                           ('0', 0, '品川|300|あ|1', .99, 'pseudo', 'now'))
                db.execute('INSERT INTO ocr_samples VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                           ('manual', '0', 0, '品川|300|あ|1', '', '品川300', 'あ1',
                            .45, b'crop', 'hash', 'now'))
                db.commit()
                self.assertEqual(prune_observations(db, root), 2)
                self.assertEqual(db.execute('SELECT count(*) FROM observations').fetchone()[0], 100)
                self.assertFalse((images / '0.jpg').exists())
                self.assertIsNone(db.execute('SELECT 1 FROM ocr_auto_candidates').fetchone())
                self.assertEqual(db.execute('SELECT count(*) FROM ocr_samples').fetchone()[0], 1)
                rows = db.execute("SELECT id,image_path FROM observations WHERE id='2'").fetchall()
                self.assertEqual(delete_observations(db, root, rows), 1)
                self.assertFalse((images / '2.jpg').exists())


if __name__ == '__main__':
    unittest.main()
