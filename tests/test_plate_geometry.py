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
        self.assertGreater(roi[8:-8, 12:-12].mean(), 220)
        reader = Mock()
        reader.readtext.return_value = [([[0,0],[100,0],[100,30],[0,30]], '品川330さ1234', .9)]
        with patch('app.plate_regions', return_value=[(60,40,266,166)]):
            candidate = read_plate(image, reader, cv2)[0]
        self.assertEqual(candidate['rectification'], 'perspective')
        np.testing.assert_allclose(candidate['quad_in_vehicle'], quad)
        self.assertGreater(reader.readtext.call_args.args[0][20:-20, 25:-25].mean(), 220)

    def test_margin_preserves_the_original_plate_interior(self):
        image, quad = self.image()
        plain = warp_plate(image, quad, cv2)
        padded = warp_plate(image, quad, cv2, margin=.03)
        mx, my = round(plain.shape[1]*.03), round(plain.shape[0]*.03)
        self.assertEqual(padded.shape[:2], (plain.shape[0]+2*my, plain.shape[1]+2*mx))
        np.testing.assert_allclose(padded[my:-my, mx:-mx], plain, atol=1)

    def test_prefers_detector_alignment_over_larger_surrounding_frame(self):
        image = np.full((200, 320, 3), 200, np.uint8)
        # Search ROI origin is (67, 64). Both contours are valid quadrilaterals.
        outer = np.int32([[[3, 0]], [[183, 0]], [[183, 91]], [[3, 91]]])
        plate = np.int32([[[13, 6]], [[172, 6]], [[172, 85]], [[13, 85]]])
        with patch.object(cv2, 'findContours', return_value=([outer, plate], None)):
            _, points, method = rectify_candidate(image, [80, 70, 240, 150], cv2)
        self.assertEqual(method, 'perspective')
        np.testing.assert_allclose(points, [[80, 70], [239, 70], [239, 149], [80, 149]])

    def test_search_is_bounded_and_blank_image_retries_only_once(self):
        image = np.zeros((1600, 2400, 3), np.uint8)
        with patch.object(cv2, 'findContours', wraps=cv2.findContours) as contours:
            _, points, method = rectify_candidate(image, [100, 100, 2300, 1500], cv2)
        self.assertEqual(contours.call_count, 2)
        self.assertLessEqual(max(contours.call_args.args[0].shape), 960)
        self.assertIsNone(points)
        self.assertEqual(method, 'none')

    def test_invalid_box_and_margin_are_rejected(self):
        image, quad = self.image()
        with self.assertRaises(ValueError):
            rectify_candidate(image, [500, 20, 550, 100], cv2)
        with self.assertRaises(ValueError):
            warp_plate(image, quad, cv2, margin=-.01)

    def test_contrast_retry_recovers_four_corners(self):
        image = np.full((200, 320, 3), 200, np.uint8)
        plate = np.int32([[[13, 6]], [[172, 6]], [[172, 85]], [[13, 85]]])
        with patch.object(cv2, 'findContours', side_effect=[([], None), ([plate], None)]) as contours:
            _, points, method = rectify_candidate(image, [80, 70, 240, 150], cv2)
        self.assertEqual(contours.call_count, 2)
        self.assertEqual(method, 'perspective')
        np.testing.assert_allclose(points, [[80, 70], [239, 70], [239, 149], [80, 149]])

    def test_large_image_corners_map_back_to_original_pixels(self):
        image, quad = self.image()
        image = cv2.resize(image, None, fx=5, fy=5)
        corrected, points, method = rectify_candidate(image, [300, 200, 1630, 1030], cv2)
        self.assertEqual(method, 'perspective')
        np.testing.assert_allclose(points, quad*5, atol=8)
        self.assertGreater(corrected.shape[1], 1000)

    def test_uncertain_and_invalid_borders(self):
        roi, quad, method = rectify_candidate(np.zeros((80,160,3), np.uint8), [10,10,130,60], cv2)
        self.assertIsNone(quad)
        self.assertEqual(method, 'none')
        with self.assertRaises(ValueError):
            warp_plate(np.zeros((80,160,3), np.uint8), [[-1,0],[100,0],[100,50],[0,50]], cv2)
