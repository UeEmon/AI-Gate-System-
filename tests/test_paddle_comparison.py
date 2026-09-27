import unittest
from unittest.mock import patch

from paddle_evaluate import score_pipeline, summarize


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


if __name__ == '__main__':
    unittest.main()
