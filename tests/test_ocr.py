"""OCR plumbing regressions; synthetic responses do not measure model accuracy."""
import unittest
from unittest.mock import Mock, patch
import cv2
import numpy as np
from app import plate_text, read_plate


def item(x, y, w, h, text, confidence=.9):
    return ([[x, y], [x+w, y], [x+w, y+h], [x, y+h]], text, confidence)


class OCRTests(unittest.TestCase):
    def test_tall_lower_digits_do_not_join_upper_row(self):
        fragments = [item(50, 5, 80, 60, '12-34'),
                     item(60, 0, 50, 20, '330'),
                     item(0, 20, 20, 30, 'さ'), item(0, 0, 50, 20, '品川')]
        self.assertEqual(plate_text(fragments), ('品川 330 さ 12-34', .9))

    @patch('app.plate_regions', return_value=[(0, 0, 100, 50)])
    def test_contrast_retry_and_edge_padding(self, regions):
        reader = Mock()
        reader.readtext.side_effect = [[item(0, 0, 100, 30, '12-34')],
                                      [item(0, 0, 100, 30, '品川330さ12-34', .8)]]
        result = read_plate(np.full((60, 110, 3), 100, np.uint8), reader, cv2)[0]
        self.assertEqual(result['fields']['serial'], '1234')
        self.assertEqual(result['preprocessing'], 'clahe')
        self.assertEqual(result['bbox_in_vehicle'], [0, 0, 100, 50])
        self.assertEqual(result['confidence'], .8)
        self.assertEqual(reader.readtext.call_args_list[0].args[0].shape, (208, 416, 3))
        self.assertEqual(reader.readtext.call_args_list[1].args[0].ndim, 2)

    @patch('app.plate_regions', return_value=[(5, 5, 100, 50)])
    def test_good_read_skips_retry(self, regions):
        reader = Mock()
        reader.readtext.return_value = [item(0, 0, 100, 30, '品川330さ12-34')]
        self.assertEqual(read_plate(np.zeros((80, 120, 3), np.uint8), reader, cv2)[0]['status'], 'candidate')
        reader.readtext.assert_called_once()

    @patch('app.plate_regions', return_value=[(0, 0, 100, 50)])
    def test_low_confidence_not_promoted_and_empty_not_emitted(self, regions):
        reader = Mock()
        reader.readtext.return_value = [item(0, 0, 100, 30, '品川330さ12-34', .4)]
        result = read_plate(np.zeros((60, 110, 3), np.uint8), reader, cv2)[0]
        self.assertEqual(result['status'], 'needs_review')
        self.assertEqual(result['confidence'], .4)
        reader.readtext.return_value = []
        self.assertEqual(read_plate(np.zeros((60, 110, 3), np.uint8), reader, cv2), [])
