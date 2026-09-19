"""OCR plumbing regressions; synthetic responses do not measure model accuracy."""
import unittest
from unittest.mock import Mock, patch
import cv2
import numpy as np
from app import plate_appearance, ocr_variants, plate_text, read_plate, vehicle_type_from_plates


def item(x, y, w, h, text, confidence=.9):
    return ([[x, y], [x+w, y], [x+w, y+h], [x, y+h]], text, confidence)


class OCRTests(unittest.TestCase):
    def test_kei_yellow_and_black_plate_hints(self):
        yellow = np.full((80, 160, 3), (0, 220, 240), np.uint8)
        appearance = plate_appearance(yellow, cv2)
        self.assertEqual(appearance['style'], 'kei_yellow')
        self.assertEqual(appearance['kei_strength'], 'strong')
        candidate = {'kei_strength': appearance['kei_strength'],
                     'fields': {'region': '品川'}, 'confidence': .9}
        self.assertEqual(vehicle_type_from_plates('car', [candidate]), 'kei')
        self.assertEqual(vehicle_type_from_plates('truck', [candidate]), 'truck')
        self.assertEqual(vehicle_type_from_plates('car', [dict(candidate, confidence=.59)]), 'car')
        self.assertEqual(vehicle_type_from_plates('car', [dict(candidate, fields=None)]), 'car')

        black = np.zeros((80, 160, 3), np.uint8)
        black[:, :20] = (0, 220, 240)
        appearance = plate_appearance(black, cv2)
        self.assertEqual(appearance['style'], 'kei_black')
        self.assertEqual([name for name, _ in ocr_variants(black, appearance, cv2)][:4],
                         ['color', 'clahe', 'otsu_inverted', 'otsu'])

    def test_graphic_plate_uses_extra_preprocessing_without_forcing_kei(self):
        graphic = np.full((80, 160, 3), 230, np.uint8)
        graphic[:, :30] = (200, 50, 180)
        appearance = plate_appearance(graphic, cv2)
        self.assertEqual(appearance['style'], 'graphic_candidate')
        self.assertFalse(appearance['kei_candidate'])
        self.assertEqual(len(ocr_variants(graphic, appearance, cv2)), 5)

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
