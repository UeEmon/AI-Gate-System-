import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from vision_train import BASE_MODELS, train


class TrainingTests(unittest.TestCase):
    def test_vehicle_training_copies_best_weight(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / 'dataset.yaml'
            data.write_text('names: {0: car}')
            save_dir = root / 'result'
            best = save_dir / 'weights' / 'best.pt'
            best.parent.mkdir(parents=True)
            best.write_bytes(b'weight')
            calls = {}

            class Model:
                def __init__(self, base):
                    calls['base'] = base

                def train(self, **kwargs):
                    calls.update(kwargs)
                    return types.SimpleNamespace(save_dir=save_dir)

            module = types.SimpleNamespace(YOLO=Model)
            output = root / 'models' / 'vehicle.pt'
            with patch.dict('sys.modules', {'ultralytics': module}):
                self.assertEqual(train(data, output, 'vehicle', 2, 640), output)
            self.assertEqual(output.read_bytes(), b'weight')
            self.assertEqual(calls['base'], BASE_MODELS['vehicle'])
            self.assertEqual(calls['epochs'], 2)
            self.assertEqual(calls['imgsz'], 640)

    def test_missing_dataset_is_rejected(self):
        with self.assertRaises(FileNotFoundError):
            train('missing.yaml', 'model.pt', 'plate')


if __name__ == '__main__':
    unittest.main()
