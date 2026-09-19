"""Opt-in real EasyOCR/PyTorch training smoke test, not an accuracy benchmark."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest


@unittest.skipUnless(os.getenv('GATE_TEST_OCR_TRAINING') == '1', 'requires EasyOCR weights and PyTorch')
class RealTrainingTests(unittest.TestCase):
    def test_pretrained_gradient_training_checkpoint_and_reload(self):
        import easyocr
        from PIL import Image, ImageDraw
        import torch
        from ocr_learning import load_weights
        from ocr_train import train
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run = 'b'*32
            directory = root / 'ocr-learning' / run
            directory.mkdir(parents=True)
            manifest = []
            for index, partition in enumerate(['train', 'train', 'validation']):
                image = Image.new('L', (160, 80), 255)
                draw = ImageDraw.Draw(image)
                draw.text((10, 4), '1234', fill=0, font_size=26)
                draw.text((10, 44), '5678', fill=0, font_size=26)
                path = directory / f'{index}.png'; image.save(path)
                manifest.append(dict(id=str(index), partition=partition, split=.5,
                                     top_text='1234', bottom_text='5678',
                                     image_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
            (directory / 'dataset.json').write_text(json.dumps(manifest))
            report = train(root, run, epochs=1)
            self.assertEqual(report['train_lines'], 4)
            self.assertEqual(report['validation_lines'], 2)
            self.assertGreater(report['history'][0]['loss'], 0)
            self.assertTrue((directory / 'weights.pth').is_file())
            reader = easyocr.Reader(['ja','en'], gpu=False, detector=False,
                                    quantize=False, download_enabled=False, verbose=False)
            load_weights(reader, directory)
            self.assertFalse(reader.recognizer.training)
            with torch.no_grad():
                output = reader.recognizer(torch.zeros(1, 1, 64, 384), None)
            self.assertTrue(torch.isfinite(output).all())
            self.assertEqual(output.shape[2], len(reader.character)+1)
