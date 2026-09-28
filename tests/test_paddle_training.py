"""Manual corrections win and pseudo-labels never enter held-out validation."""
from io import BytesIO
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

import events
import ocr_learning
from paddle_training import export, partition


class PaddleTrainingTests(unittest.TestCase):
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
                    db.execute('''INSERT INTO ocr_samples VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
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


if __name__ == '__main__':
    unittest.main()
