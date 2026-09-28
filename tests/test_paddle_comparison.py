import unittest
from unittest.mock import Mock, patch
import sys
from types import SimpleNamespace
import numpy as np

from paddle_evaluate import score_pipeline, summarize, detector_diagnostic, ocr_diagnostic


class ComparisonTests(unittest.TestCase):
    def test_identical_reviewed_image_scores_both_methods(self):
        class Pipeline:
            def __init__(self, serial):
                self.serial = serial

            def run(self, image):
                box = [0, 0, 100, 50]
                fields = dict(region='品川', category='330', kana='さ', serial=self.serial)
                return ([dict(bbox_in_vehicle=box, fields=fields)],
                        dict(proposals=[dict(bbox_in_vehicle=box)]))

        with patch('paddle_evaluate.time.perf_counter', side_effect=[1, 1.02, 2, 2.03]):
            lipla = score_pipeline(Pipeline('1234'), object(), [0, 0, 100, 50], '品川|330|さ|1234')
            paddle = score_pipeline(Pipeline('9999'), object(), [0, 0, 100, 50], '品川|330|さ|1234')
        self.assertTrue(lipla['detected'])
        self.assertTrue(lipla['recognized'])
        self.assertTrue(paddle['detected'])
        self.assertFalse(paddle['recognized'])
        self.assertEqual(summarize([lipla])['exact_plate_accuracy'], 1)
        self.assertEqual(summarize([paddle])['exact_plate_accuracy'], 0)

    def test_diagnostic_distinguishes_confidence_size_and_misalignment(self):
        detector = Mock()
        def box(rect, confidence):
            return SimpleNamespace(xyxy=[np.array(rect)], conf=SimpleNamespace(item=lambda: confidence))
        detector.predict.return_value = [SimpleNamespace(boxes=[
            box([0, 0, 100, 50], .05), box([0, 0, 30, 20], .9),
            box([110, 0, 210, 50], .9), box([0, 0, 100, 50], .9)])]
        cv2 = Mock(FONT_HERSHEY_SIMPLEX=0)
        cv2.imwrite.return_value = True
        with patch.dict(sys.modules, {'cv2': cv2}):
            result = detector_diagnostic(detector, np.zeros((80, 220, 3), dtype=np.uint8),
                                         [0, 0, 100, 50], SimpleNamespace(name='overlay.jpg'))
        self.assertEqual([b['reason'] for b in result['boxes']],
                         ['low_confidence', 'small_box', 'low_iou', 'matched'])
        self.assertTrue(result['matched'])
        self.assertEqual(detector.predict.call_args.kwargs['conf'], .01)

    def test_ocr_diagnostic_uses_manual_four_crops(self):
        fields = {name: {'text': value, 'box': bounds} for name, value, bounds in [
            ('region', '品川', [0, 0, .5, .5]), ('category', '330', [.5, 0, 1, .5]),
            ('kana', 'さ', [0, .5, .5, 1]), ('serial', '1234', [.5, .5, 1, 1])]}
        import json
        cv2 = Mock(IMREAD_COLOR=1)
        cv2.imdecode.return_value = np.zeros((80, 200, 3), dtype=np.uint8)
        reader = Mock()
        reader.read.side_effect = [(text, .9) for text in ('品川', '330', 'さ', '1234')]
        with patch.dict(sys.modules, {'cv2': cv2}):
            result = ocr_diagnostic(reader, b'plate', {'fields_json': json.dumps(fields)})
        self.assertEqual(result['layout'], 'four_fields')
        self.assertTrue(result['exact'])
        self.assertEqual(reader.read.call_count, 4)


if __name__ == '__main__':
    unittest.main()
