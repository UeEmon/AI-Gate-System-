"""Vehicle-local plate detector and two-line PaddleOCR inference."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

from app import parse_plate
from paddle_plate_pipeline import PaddlePlatePipeline


class PaddlePlatePipelineTests(unittest.TestCase):
    def test_vehicle_crop_passes_to_detector_and_both_plate_rows_to_ocr(self):
        detector = Mock()
        box = SimpleNamespace(xyxy=[np.array([10, 10, 130, 70])], conf=np.array([.95]))
        detector.predict.return_value = [SimpleNamespace(boxes=[box])]
        reader = Mock()
        reader.read.side_effect = [('品川300', .99), ('あ1234', .94)]
        vehicle = np.zeros((100, 180, 3), dtype=np.uint8)
        candidates, report = PaddlePlatePipeline(detector, reader, parse_plate).run(vehicle)
        np.testing.assert_array_equal(detector.predict.call_args.args[0], vehicle)
        self.assertEqual(reader.read.call_count, 2)
        self.assertEqual([call.args[0].shape for call in reader.read.call_args_list],
                         [(27, 120, 3), (33, 120, 3)])
        self.assertEqual(candidates[0]['fields']['serial'], '1234')
        self.assertEqual(candidates[0]['confidence'], .94)
        self.assertEqual(report['source'], 'trained-plate-yolo')


if __name__ == '__main__':
    unittest.main()
