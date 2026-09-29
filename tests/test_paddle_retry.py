import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from paddle_auto_train import PaddleTrainingManager


class PaddleRetryTests(unittest.TestCase):
    def test_failed_run_needs_explicit_retry_for_same_data(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            manager = PaddleTrainingManager(root)
            repo = root / 'PaddleOCR'
            (repo / 'tools').mkdir(parents=True)
            (repo / 'tools' / 'train.py').touch()
            pretrained = root / 'weights.pdparams'
            pretrained.write_bytes(b'weights')
            sample = dict(id='1', image_sha256='image', top_text='品川330',
                          bottom_text='さ1234', fields_json='{}', source='manual')
            options = {'GATE_PADDLE_AUTO_TRAIN': '1',
                       'GATE_PADDLE_TRAIN_REPO': str(repo),
                       'GATE_PADDLE_PRETRAINED': str(pretrained)}
            def export_sample(_root, directory):
                directory.mkdir(parents=True)
                return dict(det_train=5, det_val=2, det_test=2)
            with patch.dict('os.environ', options), patch('ocr_learning.dataset_snapshot', return_value=[sample]), \
                    patch('paddle_auto_train.export', side_effect=export_sample) as export, \
                    patch('paddle_auto_train.subprocess.Popen') as popen, \
                    patch('paddle_auto_train.threading.Thread'):
                popen.return_value.poll.return_value = None
                first = manager.maybe_start()
                manager.process = None
                manager._save({**first, 'state': 'failed'})
                self.assertEqual(manager.maybe_start()['state'], 'failed')
                self.assertEqual(export.call_count, 1)
                request = manager.path.parent / 'retry-request.json'
                request.write_text(json.dumps({'requested_at': 'now'}))
                self.assertEqual(manager.maybe_start()['state'], 'running')
                self.assertEqual(export.call_count, 2)
                self.assertFalse(request.exists())


if __name__ == '__main__':
    unittest.main()
