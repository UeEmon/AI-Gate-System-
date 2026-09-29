import json
from pathlib import Path
import tempfile
import unittest

from app import open_database, save_observation, prune_observations, delete_observations
import ocr_learning


class ObservationRetentionTests(unittest.TestCase):
    def test_bounded_auto_samples_keep_manual_truth(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            ocr_learning.initialize(root)
            with open_database(root / 'gate.db') as db:
                for name, source in [('old', 'automatic'), ('new', 'automatic'), ('truth', 'manual')]:
                    db.execute('''INSERT INTO ocr_samples
                        (id,observation_id,candidate_index,plate_key,original_text,top_text,bottom_text,
                         split,image,image_sha256,created_at,source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                         (name, name, 0, f'品川|300|あ|{name}', '', '品川300', 'あ1', .45,
                          b'crop', name, {'old':'1','new':'2','truth':'3'}[name], source))
                    db.execute('INSERT INTO ocr_auto_candidates (observation_id,candidate_index,plate_key,confidence,status,created_at) VALUES (?,?,?,?,?,?)',
                               (name, 0, f'品川|300|あ|{name}', .99, 'pseudo', name))
                self.assertEqual(ocr_learning.limit_automatic_samples(db, root, limit=1), 1)
                self.assertEqual(db.execute("SELECT status FROM ocr_auto_candidates WHERE observation_id='old'").fetchone()[0], 'excluded')
                self.assertEqual([r[0] for r in db.execute('SELECT id FROM ocr_samples ORDER BY id')],
                                 ['new', 'truth'])

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
                db.execute('INSERT INTO ocr_auto_candidates (observation_id,candidate_index,plate_key,confidence,status,created_at) VALUES (?,?,?,?,?,?)',
                           ('0', 0, '品川|300|あ|1', .99, 'pseudo', 'now'))
                db.execute('INSERT INTO ocr_samples (id,observation_id,candidate_index,plate_key,original_text,top_text,bottom_text,split,image,image_sha256,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                           ('manual', '0', 1, '品川|300|あ|1', '', '品川300', 'あ1',
                            .45, b'crop', 'hash', 'now'))
                db.commit()
                self.assertEqual(prune_observations(db, root), 2)
                self.assertEqual(db.execute('SELECT count(*) FROM observations').fetchone()[0], 100)
                self.assertFalse((images / '0.jpg').exists())
                self.assertIsNotNone(db.execute('SELECT 1 FROM ocr_auto_candidates').fetchone())
                self.assertIsNotNone(db.execute('SELECT 1 FROM ocr_auto_archive').fetchone())
                self.assertTrue((images / 'learning' / '0.jpg').is_file())
                self.assertEqual(db.execute('SELECT count(*) FROM ocr_samples').fetchone()[0], 1)
                rows = db.execute("SELECT id,image_path FROM observations WHERE id='2'").fetchall()
                self.assertEqual(delete_observations(db, root, rows), 1)
                self.assertFalse((images / '2.jpg').exists())


if __name__ == '__main__':
    unittest.main()
