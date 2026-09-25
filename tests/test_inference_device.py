import unittest
from unittest.mock import Mock, patch
from inference_device import resolve


class DeviceTests(unittest.TestCase):
    def test_selection_and_unavailable_gpu(self):
        for cuda, mps, expected in [(True,True,'cuda'),(False,True,'mps'),(False,False,'cpu')]:
            with self.subTest(expected=expected):
                torch = Mock()
                torch.cuda.is_available.return_value=cuda
                torch.backends.mps.is_available.return_value=mps
                resolve.cache_clear()
                with patch.dict('sys.modules', {'torch':torch}):
                    self.assertEqual(resolve('auto'),expected)
                    self.assertEqual(resolve('cpu'),'cpu')
                    for name, available in [('cuda',cuda),('mps',mps)]:
                        if available:
                            self.assertEqual(resolve(name), name)
                        else:
                            with self.assertRaises(ValueError):
                                resolve(name)
        resolve.cache_clear()

    def test_invalid_device_rejected(self):
        with self.assertRaises(ValueError):
            resolve('invalid')
