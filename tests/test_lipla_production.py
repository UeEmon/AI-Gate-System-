"""The production vehicle ROI uses Lipla's integrated detector and OCR."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

import app


class LiplaProductionTests(unittest.TestCase):
    def test_native_recognizer_receives_full_vehicle_and_reports_combined_time(self):
        result = SimpleNamespace(vertices=[[3, 4], [3, 15], [26, 15], [26, 4]],
                                 area='品川', class_number='300', kana='あ', number=1234,
                                 area_score=.9, class_number_score=.8, kana_score=.85,
                                 number_score=.95, score=.75)
        native = Mock(return_value=[result]); report = {}
        image = np.zeros((40, 80, 3), dtype=np.uint8)
        with patch.object(app, 'make_plate_detector', side_effect=AssertionError('bypassed')):
            found = app.read_plate(image, [('lipla-jp', SimpleNamespace(model=native))], Mock(),
                                   diagnostics=report)
        native.assert_called_once()
        np.testing.assert_array_equal(native.call_args.args[0], image)
        self.assertEqual(found[0]['bbox_in_vehicle'], [3, 4, 26, 15])
        self.assertEqual(found[0]['quad_in_vehicle'], [[3, 4], [26, 4], [26, 15], [3, 15]])
        self.assertEqual(found[0]['fields']['serial'], '1234')
        self.assertEqual(found[0]['confidence'], .8)
        self.assertEqual(report['timing_scope'], 'lipla_detection_rectification_ocr_combined')
        self.assertGreaterEqual(report['plate_recognition_ms'], 0)

    def test_initialize_models_does_not_load_separate_plate_weights_for_lipla(self):
        yolo = Mock()
        with patch('ocr_backends.make_readers', return_value=[('lipla-jp', Mock())]):
            model, plate, readers = app.initialize_models('vehicle.pt', 'plate.pt', '/tmp', Mock(), yolo,
                                                            offline=True)
        yolo.assert_called_once_with('vehicle.pt')
        self.assertIsNone(plate)


if __name__ == '__main__':
    unittest.main()
