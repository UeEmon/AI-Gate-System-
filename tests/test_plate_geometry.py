import unittest
from unittest.mock import Mock, patch
import cv2
import numpy as np
from plate_geometry import warp_plate, rectify_candidate
from app import read_plate


class GeometryTests(unittest.TestCase):
    def image(self):
        image = np.zeros((240, 400, 3), np.uint8)
        quad = np.float32([[60, 80], [290, 40], [325, 165], [80, 205]])
        cv2.fillConvexPoly(image, quad.astype(np.int32), (240, 240, 240))
        return image, quad

    def test_perspective_rectification_and_corner_order(self):
        image, quad = self.image()
        result = warp_plate(image, quad[[2, 0, 3, 1]], cv2)
        self.assertGreater(result.mean(), 220)
        self.assertGreater(result.shape[1], result.shape[0])
        np.testing.assert_array_equal(result, warp_plate(image, quad, cv2))

    def test_inference_reads_rectified_image_and_preserves_quad(self):
        image, _ = self.image()
        roi, quad, method = rectify_candidate(image, [60, 40, 326, 206], cv2)
        self.assertEqual(method, 'perspective')
        self.assertIsNotNone(quad)
        self.assertGreater(roi.mean(), 220)
        reader = Mock()
        reader.readtext.return_value = [([[0,0],[100,0],[100,30],[0,30]], '品川330さ1234', .9)]
        with patch('app.plate_regions', return_value=[(60,40,266,166)]):
            candidate = read_plate(image, reader, cv2)[0]
        self.assertEqual(candidate['rectification'], 'perspective')
        np.testing.assert_allclose(candidate['quad_in_vehicle'], quad)
        self.assertGreater(reader.readtext.call_args.args[0].mean(), 220)

    def test_uncertain_and_invalid_borders(self):
        roi, quad, method = rectify_candidate(np.zeros((80,160,3), np.uint8), [10,10,130,60], cv2)
        self.assertIsNone(quad)
        self.assertEqual(method, 'none')
        with self.assertRaises(ValueError):
            warp_plate(np.zeros((80,160,3), np.uint8), [[-1,0],[100,0],[100,50],[0,50]], cv2)
