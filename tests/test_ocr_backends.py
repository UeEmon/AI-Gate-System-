"""Only Lipla-jp may be selected for plate recognition."""
import os
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import numpy as np

from ocr_backends import LiplaPlateReader, make_readers, selected_backend


class OCRBackendTests(unittest.TestCase):
    def test_unset_backend_defaults_to_lipla(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {}, clear=True):
            self.assertEqual(selected_backend(root), 'lipla')

    def test_alternative_backend_is_rejected(self):
        for backend in ('auto', 'easyocr', 'paddle', 'compare', 'fastalpr'):
            with self.subTest(backend=backend), patch.dict(os.environ, {'GATE_OCR_BACKEND': backend}):
                with self.assertRaises(ValueError):
                    make_readers('/tmp/models', offline=True)

    def test_lipla_is_loaded_once(self):
        recognizer = Mock(return_value=[])
        module = types.SimpleNamespace(Recognizer=Mock(return_value=recognizer))
        with patch.dict('sys.modules', {'lipla': module}), patch.dict(os.environ, {'GATE_OCR_BACKEND':'lipla'}):
            readers = make_readers('/tmp/models', offline=True)
        self.assertEqual(readers[0][0], 'lipla-jp')
        module.Recognizer.assert_called_once()

    def test_lipla_accepts_grayscale_binary_and_noncontiguous_images(self):
        reader = LiplaPlateReader.__new__(LiplaPlateReader)
        reader.model = Mock(return_value=[])
        gray = np.arange(800, dtype=np.uint8).reshape(40, 20)
        for image in (gray, (gray > 128).astype(np.uint8) * 255,
                      gray[..., None], np.dstack([gray] * 4)[:, ::2],
                      np.dstack([gray] * 3)[:, ::2]):
            reader.readtext(image)
            prepared = reader.model.call_args.args[0]
            self.assertEqual(prepared.shape[2], 3)
            self.assertTrue(prepared.flags.c_contiguous)

    def test_invalid_image_rejected(self):
        reader = LiplaPlateReader.__new__(LiplaPlateReader)
        reader.model = Mock()
        self.assertEqual(reader.readtext(np.empty((0, 40), dtype=np.uint8)), [])
        reader.model.assert_not_called()
        with self.assertRaises(ValueError):
            reader.readtext(np.zeros((40, 40, 2), dtype=np.uint8))


if __name__ == '__main__':
    unittest.main()
