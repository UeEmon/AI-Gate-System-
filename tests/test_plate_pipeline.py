"""Stage order and evidence retention, without pretending to measure AI accuracy."""
import unittest
import tempfile
from unittest.mock import Mock, patch
import cv2
import numpy as np

import app
from plate_pipeline import PlateDetector, PlateRecognitionPipeline, PlateRectifier


class PlatePipelineTests(unittest.TestCase):
    def setUp(self):
        self.crop = np.full((100, 160, 3), 180, np.uint8)

    def test_ai_hit_skips_retry_and_geometry(self):
        learned, geometry = Mock(return_value=[(20, 30, 80, 40)]), Mock()
        detector = PlateDetector(cv2, geometry, app.bounded_plate_regions, learned)
        result = detector.detect(self.crop)
        self.assertEqual(result.source, 'plate_model_640')
        self.assertEqual(learned.call_count, 1)
        self.assertIs(learned.call_args.args[0], self.crop)
        self.assertEqual(learned.call_args.args[1], 640)
        geometry.assert_not_called()

    def test_ai_miss_retries_original_crop_at_higher_resolution(self):
        learned = Mock(side_effect=[[], [(20, 30, 80, 40)]])
        geometry = Mock()
        result = PlateDetector(cv2, geometry, app.bounded_plate_regions, learned).detect(self.crop)
        self.assertEqual(result.source, 'plate_model_960')
        self.assertEqual([c.args[1] for c in learned.call_args_list], [640, 960])
        self.assertTrue(all(c.args[0] is self.crop for c in learned.call_args_list))
        geometry.assert_not_called()

    def test_total_miss_never_rectifies_or_reads_text(self):
        learned, geometry = Mock(return_value=[]), Mock(return_value=[])
        rectifier, reader = Mock(), Mock()
        pipeline = PlateRecognitionPipeline(
            PlateDetector(cv2, geometry, app.bounded_plate_regions, learned), rectifier, reader)
        results, report = pipeline.run(self.crop)
        self.assertEqual(results, [])
        self.assertEqual(report['status'], 'not_detected')
        self.assertEqual(len(report['attempts']), 4)
        self.assertEqual(report['ocr_ms'], 0)
        rectifier.prepare.assert_not_called()
        reader.assert_not_called()

    def test_ocr_failure_retains_plate_location_and_source(self):
        reader = Mock()
        reader.readtext.return_value = []
        report = {}
        with patch('app.plate_regions', return_value=[(20, 30, 80, 40)]):
            results = app.read_plate(self.crop, reader, cv2, diagnostics=report)
        self.assertEqual(results, [])
        self.assertEqual(report['status'], 'detected_unreadable')
        self.assertEqual(report['proposals'][0]['bbox_in_vehicle'], [20, 30, 100, 70])
        self.assertEqual(report['source'], 'geometry')
        for name in ('plate_detection_ms', 'rectification_ms', 'ocr_ms'):
            self.assertGreaterEqual(report[name], 0)

    def test_rectification_precedes_ocr_and_keeps_vehicle_coordinates(self):
        geometry = Mock(return_value=[(20, 30, 80, 40)])
        corrected = np.full((40, 80, 3), 100, np.uint8)
        quad = [[20, 30], [99, 30], [99, 69], [20, 69]]
        order = []
        rectifier = Mock()
        def prepare(crop, bbox):
            order.append('rectify')
            self.assertIs(crop, self.crop)
            return corrected, quad, 'perspective'
        rectifier.prepare.side_effect = prepare
        def recognize(roi, bbox, points, method):
            order.append('ocr')
            self.assertIs(roi, corrected)
            self.assertEqual(bbox, [20, 30, 100, 70])
            self.assertEqual(points, quad)
            self.assertEqual(method, 'perspective')
            return dict(text='品川330さ1234', confidence=.9, fields={'serial': '1234'})
        results, report = PlateRecognitionPipeline(
            PlateDetector(cv2, geometry, app.bounded_plate_regions), rectifier, recognize).run(self.crop)
        self.assertEqual(order, ['rectify', 'ocr'])
        self.assertEqual(report['status'], 'text_read')
        self.assertEqual(results[0]['detection_source'], 'geometry')

    def test_duplicate_candidates_do_not_repeat_ocr(self):
        detector = PlateDetector(cv2, lambda crop: [(20, 30, 80, 40)]*5,
                                 app.bounded_plate_regions)
        reader = Mock(return_value=None)
        _, report = PlateRecognitionPipeline(detector, PlateRectifier(cv2), reader).run(self.crop)
        reader.assert_called_once()
        self.assertEqual(len(report['proposals']), 1)

    def test_performance_summary_keeps_plate_stages_separate(self):
        from aigate.performance import PerformanceManager
        with tempfile.TemporaryDirectory() as root:
            performance = PerformanceManager(root)
            performance.record(job_id='test', frame_ms=100, plate_ms=20,
                               rectification_ms=5, ocr_ms=50)
            summary = performance.summary()['latency_ms']
            self.assertEqual(summary['plate_ms']['p95'], 20)
            self.assertEqual(summary['rectification_ms']['p95'], 5)
            self.assertEqual(summary['ocr_ms']['p95'], 50)
