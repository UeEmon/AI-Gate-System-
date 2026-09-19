import tempfile
import unittest
from unittest.mock import patch

from app import initialize_models


class ModelInitializationTests(unittest.TestCase):
    def test_transient_failure_is_retried(self):
        calls = []

        def yolo(path):
            calls.append(path)
            if len(calls) == 1:
                raise OSError('temporary download failure')
            return 'vehicle-model'

        with tempfile.TemporaryDirectory() as root, \
                patch('time.sleep'), \
                patch('ocr_backends.make_readers', return_value=['ocr-reader']):
            model, plate, readers = initialize_models(
                'vehicle.pt', None, root, object(), yolo, timeout=60)
        self.assertEqual((model, plate, readers), ('vehicle-model', None, ['ocr-reader']))
        self.assertEqual(calls, ['vehicle.pt', 'vehicle.pt'])

    def test_offline_failure_is_immediate(self):
        def yolo(_path):
            raise FileNotFoundError('missing model')

        with tempfile.TemporaryDirectory() as root, \
                patch('time.sleep') as sleep:
            with self.assertRaisesRegex(RuntimeError, '30秒以内'):
                initialize_models('missing.pt', None, root, object(), yolo,
                                  offline=True, timeout=1)
        sleep.assert_not_called()


if __name__ == '__main__':
    unittest.main()
