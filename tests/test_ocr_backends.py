import json
import os
import tempfile
import types
import unittest
from unittest.mock import patch

import numpy as np

from ocr_backends import PaddlePlateReader, make_readers, selected_backend


class Result:
    def __init__(self, text, score):
        self.json = {'res': {'rec_text': text, 'rec_score': score}}


class Recognition:
    def __init__(self, **kwargs):
        self.options = kwargs
        self.calls = []

    def predict(self, input, batch_size):
        self.calls.append(input.shape)
        text = '品川300' if len(self.calls) == 1 else 'さ1234'
        return [Result(text, .9)]


class OCRBackendTests(unittest.TestCase):
    def test_paddle_reader_splits_plate_into_two_rows(self):
        module = types.SimpleNamespace(TextRecognition=Recognition)
        with patch.dict('sys.modules', {'paddleocr': module}):
            reader = PaddlePlateReader()
        items = reader.readtext(np.zeros((100, 200, 3), np.uint8))
        self.assertEqual([item[1] for item in items], ['品川300', 'さ1234'])
        self.assertEqual(reader.model.calls, [(45, 200, 3), (55, 200, 3)])

    def test_auto_uses_benchmark_winner(self):
        with tempfile.TemporaryDirectory() as root:
            folder = os.path.join(root, 'ocr-learning')
            os.mkdir(folder)
            with open(os.path.join(folder, 'benchmark.json'), 'w',
                      encoding='utf-8') as stream:
                json.dump({'selected': 'paddle'}, stream)
            with patch.dict(os.environ, {'GATE_OCR_BACKEND': 'auto'}):
                self.assertEqual(selected_backend(root), 'paddle')

    def test_auto_without_report_keeps_easyocr(self):
        with tempfile.TemporaryDirectory() as root, \
                patch.dict(os.environ, {'GATE_OCR_BACKEND': 'auto'}):
            self.assertEqual(selected_backend(root), 'easyocr')

    def test_easyocr_remains_default(self):
        easyocr = types.SimpleNamespace(Reader=lambda *args, **kwargs: 'easy')
        with tempfile.TemporaryDirectory() as root, \
                patch.dict(os.environ, {}, clear=True), \
                patch('ocr_learning.make_reader', return_value='easy'):
            self.assertEqual(make_readers(root, easyocr), [('easyocr', 'easy')])


if __name__ == '__main__':
    unittest.main()
