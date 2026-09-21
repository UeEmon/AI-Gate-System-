"""Validate bounded proposal recovery and the real worker's vehicle gate."""
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np
import app


class VehiclePlateTests(unittest.TestCase):
    def test_clip_and_deduplicate(self):
        result = app.bounded_plate_regions(
            [(-10, 10, 100, 50), (0, 10, 90, 50), (200, 0, 30, 10),
             (float('nan'), 0, 20, 10)], 160, 100)
        self.assertEqual(result, [(0, 10, 90, 50)])

    def test_detector_hit_skips_contours(self):
        crop = np.zeros((100, 160, 3), np.uint8)
        with patch('app.learned_plate_regions', return_value=[(30, 40, 60, 30)]), \
                patch('app.plate_regions') as contours:
            self.assertEqual(app.vehicle_plate_regions(crop, cv2, object()), [(30, 40, 60, 30)])
            contours.assert_not_called()

    def test_detector_miss_recovers_contour_candidate(self):
        crop = np.zeros((100, 160, 3), np.uint8)
        with patch('app.learned_plate_regions', return_value=[]), \
                patch('app.plate_regions', return_value=[(30, 40, 60, 30)]) as contours:
            self.assertEqual(app.vehicle_plate_regions(crop, cv2, object()), [(30, 40, 60, 30)])
            self.assertEqual(contours.call_count, 1)

    def test_scale_retry_maps_to_original_coordinates(self):
        crop = np.zeros((100, 160, 3), np.uint8)
        with patch('app.plate_regions', side_effect=[[], [(40, 60, 80, 40)]]) as detector:
            self.assertEqual(app.vehicle_plate_regions(crop, cv2), [(20, 30, 40, 20)])
            self.assertEqual(detector.call_args.args[0].shape, (200, 320, 3))

    def test_no_candidate_skips_ocr(self):
        reader = Mock()
        with patch('app.plate_regions', return_value=[]) as proposals:
            self.assertEqual(app.read_plate(np.zeros((100, 160, 3), np.uint8), reader, cv2), [])
        self.assertEqual(proposals.call_count, 2)
        reader.readtext.assert_not_called()

    def test_worker_only_reads_detected_vehicle_roi(self):
        box = types.SimpleNamespace(cls=np.array(0), conf=np.array(.9),
                                    xyxy=np.array([[20, 30, 120, 90]]))
        cases = [('empty', [], 'car'), ('person', [box], 'person'), ('vehicle', [box], 'car')]
        for name, boxes, label in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / 'frame.png'
                cv2.imwrite(str(source), np.zeros((100, 160, 3), np.uint8))
                detector = Mock()
                detector.predict.return_value = [types.SimpleNamespace(boxes=boxes, names={0: label})]
                modules = {'easyocr': types.SimpleNamespace(), 'ultralytics': types.SimpleNamespace(YOLO=Mock())}
                argv = ['app.py', '--source', str(source), '--output', str(root), '--source-kind', 'file']
                with patch.dict(sys.modules, modules), patch.dict(os.environ, {'GATE_OFFLINE': '0'}), \
                        patch.object(sys, 'argv', argv), patch('builtins.print'), \
                        patch('app.initialize_models', return_value=(detector, None, Mock())), \
                        patch('app.read_plate', return_value=[]) as read:
                    app.main()
                if name == 'vehicle':
                    read.assert_called_once()
                    self.assertEqual(read.call_args.args[0].shape, (60, 100, 3))
                else:
                    read.assert_not_called()
