import csv
import tempfile
import unittest
from pathlib import Path

from scripts.prepare_fast_plate_ocr_jp import prepare


class FastPlateOCRTrainingTests(unittest.TestCase):
    def test_explicit_split_preserved_and_leakage_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'annotations.csv'
            source.write_text('image_path,plate,partition\na.png,品川330さ1234,train\nb.png,横浜500あ5678,validation\nc.png,品川330さ1234,train\n')
            prepare(source, root/'train.csv', root/'val.csv', root/'config.yaml')
            self.assertNotIn('品川', (root/'val.csv').read_text())
            self.assertIn(str(root/'a.png'), (root/'train.csv').read_text())
            source.write_text(source.read_text().replace('c.png,品川330さ1234,train', 'c.png,品川330さ1234,validation'))
            with self.assertRaises(ValueError):
                prepare(source, root/'train.csv', root/'val.csv', root/'config.yaml')

    def test_prepare_normalizes_labels_and_generates_yaml_config(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = root / 'annotations.csv'
            with source.open('w', encoding='utf-8', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=['image_path', 'plate'])
                writer.writeheader()
                writer.writerow({'image_path': 'a.jpg', 'plate': '品川 330 さ 1234'})
                writer.writerow({'image_path': 'b.jpg', 'plate': '横浜500あ5678'})
            result = prepare(source, root/'train.csv', root/'val.csv', root/'plate_config.yaml')
            self.assertEqual(result['samples'], 2)
            self.assertIn('品', result['alphabet'])
            self.assertIn('あ', result['alphabet'])
            self.assertIn('max_plate_slots:', (root/'plate_config.yaml').read_text())
            self.assertIn('品川330さ1234', (root/'train.csv').read_text() + (root/'val.csv').read_text())
