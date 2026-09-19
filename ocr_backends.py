"""Interchangeable EasyOCR and PaddleOCR plate-line readers."""
import json
import os
from pathlib import Path


class PaddlePlateReader:
    """Expose PaddleOCR TextRecognition through EasyOCR's readtext shape."""
    name = 'paddle'

    def __init__(self, offline=False):
        if offline:
            os.environ.setdefault('PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK', 'True')
        from paddleocr import TextRecognition
        self.model = TextRecognition(model_name=os.getenv('GATE_PADDLE_MODEL', 'PP-OCRv6_medium_rec'),
                                     device='cpu')

    @staticmethod
    def _value(result):
        value = result.json
        if isinstance(value, str):
            value = json.loads(value)
        value = value.get('res', value)
        return str(value.get('rec_text', '')), float(value.get('rec_score', 0))

    def readtext(self, image, **_options):
        height, width = image.shape[:2]
        boundary = max(1, min(height-1, round(height * .45)))
        output = []
        for top, bottom in ((0, boundary), (boundary, height)):
            results = self.model.predict(input=image[top:bottom], batch_size=1)
            if not results:
                continue
            text, score = self._value(results[0])
            if text.strip():
                output.append(([[0,top],[width,top],[width,bottom],[0,bottom]], text, score))
        return output


def selected_backend(root):
    requested = os.getenv('GATE_OCR_BACKEND', 'auto').lower()
    if requested != 'auto':
        return requested
    path = Path(root) / 'ocr-learning' / 'benchmark.json'
    if path.is_file():
        return json.loads(path.read_text(encoding='utf-8')).get('selected', 'paddle')
    active = Path(root) / 'ocr-learning' / 'active.json'
    if active.is_file() and json.loads(active.read_text(encoding='utf-8')).get('id'):
        return 'easyocr'
    return 'paddle'


def make_readers(root, easyocr, offline=False):
    """Return named readers. compare runs both without hiding disagreements."""
    from ocr_learning import make_reader
    backend = selected_backend(root)
    if backend not in {'easyocr', 'paddle', 'compare'}:
        raise ValueError('GATE_OCR_BACKEND は easyocr、paddle、compare、auto から選択してください。')
    readers = []
    if backend in {'easyocr', 'compare'}:
        readers.append(('easyocr', make_reader(root, easyocr, offline)))
    if backend in {'paddle', 'compare'}:
        readers.append(('paddle', PaddlePlateReader(offline)))
    return readers
