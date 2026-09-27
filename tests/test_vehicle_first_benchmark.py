"""Verify vehicle gating and coordinate translation in the benchmark."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

import app
from plate_only_benchmark import analyze_frame
from plate_pipeline import PlateDetector


class VehicleFirstTests(unittest.TestCase):
    def test_dedicated_detector_miss_does_not_invoke_geometry(self):
        geometry = Mock(side_effect=AssertionError('unexpected contour fallback'))
        detector = PlateDetector(Mock(), geometry, lambda regions, width, height: regions,
                                 learned=lambda crop, size: [], geometry_fallback=False)
        result = detector.detect(np.zeros((60, 80, 3), dtype=np.uint8))
        self.assertEqual(result.regions, [])
        geometry.assert_not_called()

    def test_no_vehicle_skips_plate_detection_and_ocr(self):
        model = Mock()
        model.predict.return_value = [SimpleNamespace(boxes=[], names={})]
        with patch.object(app, 'read_plate') as read:
            candidates, report, vehicles = analyze_frame(
                np.zeros((60, 80, 3), dtype=np.uint8), [], Mock(), None,
                model, 'cpu')
        read.assert_not_called()
        self.assertEqual(vehicles, [])
        self.assertEqual(candidates, [])
        self.assertEqual(report['proposals'], [])

    def test_plate_detection_receives_only_vehicle_crop(self):
        box = SimpleNamespace(cls=Mock(item=lambda: 0),
                              xyxy=[Mock(tolist=lambda: [10, 20, 90, 60])],
                              conf=Mock(item=lambda: .8))
        model = Mock()
        model.predict.return_value = [SimpleNamespace(boxes=[box], names={0: 'car'})]
        def recognize(crop, readers, cv2, plate_model=None, diagnostics=None, allow_fallback=True):
            self.assertEqual(crop.shape, (40, 80, 3))
            diagnostics.update(proposals=[dict(bbox_in_vehicle=[2, 3, 20, 12])],
                               plate_detection_ms=3, rectification_ms=1, ocr_ms=2)
            return [dict(bbox_in_vehicle=[2, 3, 20, 12], text='品川')]
        with patch.object(app, 'read_plate', side_effect=recognize) as read:
            candidates, report, vehicles = analyze_frame(
                np.zeros((120, 160, 3), dtype=np.uint8), [], Mock(), None,
                model, 'cpu')
        read.assert_called_once()
        self.assertEqual(candidates[0]['bbox_in_frame'], [12, 23, 30, 32])
        self.assertEqual(report['proposals'][0]['bbox_in_frame'], [12, 23, 30, 32])
        self.assertEqual(vehicles[0]['bbox_in_frame'], [10, 20, 90, 60])
        self.assertEqual(report['ocr_ms'], 2)


if __name__ == '__main__':
    unittest.main()
