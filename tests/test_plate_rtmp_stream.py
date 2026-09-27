"""RTMP streaming uses live capture without loading the vehicle detector."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

import app
import ocr_backends
import plate_only_benchmark


class RTMPStreamTests(unittest.TestCase):
    def test_live_input_drops_vehicle_stage_and_redacts_credentials(self):
        url = 'rtmp://account:secret@example.net:1935/live/plate?token=hidden'
        frame = np.zeros((16, 24, 3), dtype=np.uint8)

        def fake_read(_frame, _readers, _cv2, plate_model=None, diagnostics=None):
            diagnostics.update(proposals=[], plate_detection_ms=1,
                               rectification_ms=0, ocr_ms=0)
            return []

        with tempfile.TemporaryDirectory() as folder, \
                patch.dict(sys.modules, {'cv2': Mock(), 'easyocr': Mock()}), \
                patch.object(app, 'frames', side_effect=AssertionError('file path used')), \
                patch.object(app, 'live_frames',
                             side_effect=lambda source, cv, every: (x for x in [(4, 130, frame)])) as live, \
                patch.object(app, 'read_plate', side_effect=fake_read), \
                patch.object(ocr_backends, 'make_readers', return_value=[('easyocr', None)]):
            plate_only_benchmark.run(SimpleNamespace(
                source=url, output=folder, data=folder, plate_model=None,
                every=1, warmup=0, max_frames=1, save_images=False))
            live.assert_called_once()
            result = next(Path(folder).iterdir())
            summary_text = (result / 'summary.json').read_text()
            self.assertNotIn('secret', summary_text)
            self.assertNotIn('hidden', summary_text)
            self.assertTrue(json.loads(summary_text)['live_stream'])
            self.assertEqual(json.loads((result / 'frames.jsonl').read_text())['frame_index'], 4)
