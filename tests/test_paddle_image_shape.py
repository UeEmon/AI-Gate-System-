"""Paddle's resize stage requires HWC images even for grayscale OCR variants."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
import numpy as np
from ocr_backends import PaddlePlateReader


class PaddleImageShapeTests(unittest.TestCase):
    def reader(self):
        reader = PaddlePlateReader.__new__(PaddlePlateReader)
        def predict(input, batch_size):
            self.assertEqual(input.ndim, 3)
            self.assertEqual(input.shape[2], 3)
            self.assertTrue(input.flags.c_contiguous)
            self.assertGreater(input.shape[0], 0)
            return [SimpleNamespace(json={'res': {'rec_text': '1234', 'rec_score': .9}})]
        reader.model = Mock(predict=Mock(side_effect=predict))
        return reader

    def test_grayscale_binary_and_single_channel_inputs(self):
        gray = np.arange(240, dtype=np.uint8).reshape(12,20)
        for image in (gray, (gray > 120).astype(np.uint8)*255, gray[..., None]):
            with self.subTest(shape=image.shape):
                reader = self.reader()
                result = reader.readtext(image)
                self.assertEqual(len(result), 2)
                top = reader.model.predict.call_args_list[0].kwargs['input']
                for channel in range(3):
                    np.testing.assert_array_equal(top[:,:,channel], image[:5].reshape(5,20))

    def test_bgr_bgra_and_noncontiguous_input(self):
        for channels in (3,4):
            image = np.arange(12*20*channels, dtype=np.uint8).reshape(12,20,channels)[:,::2]
            reader = self.reader()
            reader.readtext(image)
            np.testing.assert_array_equal(reader.model.predict.call_args_list[0].kwargs['input'], image[:5,:,:3])

    def test_empty_and_one_row_images_do_not_call_predict(self):
        for shape in ((0,20), (1,20,3), (12,0,3)):
            reader = self.reader()
            self.assertEqual(reader.readtext(np.zeros(shape, dtype=np.uint8)), [])
            reader.model.predict.assert_not_called()

    def test_invalid_dimensions_raise_clear_error(self):
        with self.assertRaises(ValueError):
            self.reader().readtext(np.zeros((5,10,2), dtype=np.uint8))
