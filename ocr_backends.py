"""Interchangeable EasyOCR and PaddleOCR plate-line readers."""
import json
import os
from pathlib import Path
from statistics import mean


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
        import numpy as np
        image = np.asarray(image)
        if image.ndim == 2:
            image = np.repeat(image[..., None], 3, axis=2)
        elif image.ndim == 3 and image.shape[2] == 1:
            image = np.repeat(image, 3, axis=2)
        elif image.ndim == 3 and image.shape[2] == 4:
            image = image[..., :3]
        if image.ndim != 3 or image.shape[2] != 3:
            raise ValueError('PaddleOCRにはグレー・BGR・BGRA画像を指定してください。')
        height, width = image.shape[:2]
        if height < 2 or width == 0:
            return []
        boundary = max(1, min(height-1, round(height * .45)))
        output = []
        for top, bottom in ((0, boundary), (boundary, height)):
            results = self.model.predict(input=np.ascontiguousarray(image[top:bottom]), batch_size=1)
            if not results:
                continue
            text, score = self._value(results[0])
            if text.strip():
                output.append(([[0,top],[width,top],[width,bottom],[0,bottom]], text, score))
        return output


class LiplaPlateReader:
    """Adapt Lipla-jp's Japanese plate recognizer to the local OCR interface."""
    name = 'lipla-jp'

    def __init__(self, offline=False, cache_dir=None):
        import lipla
        options = {'local_files_only': True} if offline else {}
        if cache_dir:
            options['cache_dir'] = str(Path(cache_dir) / 'lipla')
        self.model = lipla.Recognizer(**options)

    @staticmethod
    def _text(result):
        return ''.join(str(getattr(result, name, '') or '')
                       for name in ('area', 'class_number', 'kana', 'number')).strip()

    def readtext(self, image, **_options):
        output = []
        height, width = image.shape[:2]
        for result in self.model(image):
            text = self._text(result)
            if not text:
                continue
            scores = [float(getattr(result, name, 0) or 0) for name in
                      ('area_score', 'class_number_score', 'kana_score', 'number_score')]
            score = min(scores) if scores else 0.0
            output.append(([[0, 0], [width, 0], [width, height], [0, height]], text, score))
        return output


class FastALPRReader:
    """Use FastALPR's detector plus fast-plate-ocr on the prepared plate ROI."""
    name = 'fast-alpr'

    def __init__(self, cache_dir=None):
        from fast_alpr import ALPR
        kwargs = {
            'detector_model': os.getenv('GATE_FAST_ALPR_DETECTOR',
                                       'yolo-v9-t-384-license-plate-end2end'),
            'ocr_model': os.getenv('GATE_FAST_OCR_MODEL', 'cct-xs-v2-global-model'),
            'ocr_device': os.getenv('GATE_FAST_OCR_DEVICE', 'cpu'),
        }
        custom_model = os.getenv('GATE_FAST_OCR_MODEL_PATH', '')
        custom_config = os.getenv('GATE_FAST_OCR_CONFIG_PATH', '')
        if custom_model:
            kwargs['ocr_model'] = None
            kwargs['ocr_model_path'] = custom_model
        if custom_config:
            kwargs['ocr_config_path'] = custom_config
        self.model = ALPR(**kwargs)

    def readtext(self, image, **_options):
        output = []
        height, width = image.shape[:2]
        for item in self.model.predict(image):
            ocr = getattr(item, 'ocr', None)
            text = str(getattr(ocr, 'text', '') or '') if ocr else ''
            if not text:
                continue
            confidence = getattr(ocr, 'confidence', 0.0) or 0.0
            if isinstance(confidence, (list, tuple)):
                confidence = mean(confidence) if confidence else 0.0
            output.append(([[0, 0], [width, 0], [width, height], [0, height]],
                           text, float(confidence)))
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
    if backend not in {'easyocr', 'paddle', 'lipla', 'fastalpr', 'compare'}:
        raise ValueError('GATE_OCR_BACKEND は easyocr、paddle、lipla、fastalpr、compare、auto から選択してください。')
    readers = []
    if backend in {'easyocr', 'compare'}:
        readers.append(('easyocr', make_reader(root, easyocr, offline)))
    if backend in {'paddle', 'compare'}:
        readers.append(('paddle', PaddlePlateReader(offline)))
    if backend in {'lipla', 'compare'}:
        readers.append(('lipla-jp', LiplaPlateReader(offline, root)))
    if backend in {'fastalpr', 'compare'}:
        readers.append(('fast-alpr', FastALPRReader(root)))
    return readers
