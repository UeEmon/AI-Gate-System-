"""Lipla-jp is the sole plate recognition backend."""
import os
from pathlib import Path


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
        import numpy as np
        image = np.asarray(image)
        if image.ndim == 2:
            image = np.repeat(image[..., None], 3, axis=2)
        elif image.ndim == 3 and image.shape[2] == 1:
            image = np.repeat(image, 3, axis=2)
        elif image.ndim == 3 and image.shape[2] == 4:
            image = image[..., :3]
        if image.ndim != 3 or image.shape[2] != 3:
            raise ValueError('Lipla-jpにはグレー・BGR・BGRA画像を指定してください。')
        output = []
        height, width = image.shape[:2]
        if not height or not width:
            return []
        for result in self.model(np.ascontiguousarray(image)):
            text = self._text(result)
            if not text:
                continue
            scores = [float(getattr(result, name, 0) or 0) for name in
                      ('area_score', 'class_number_score', 'kana_score', 'number_score')]
            score = min(scores) if scores else 0.0
            output.append(([[0, 0], [width, 0], [width, height], [0, height]], text, score))
        return output


class PaddleLineReader:
    """Use a fine-tuned PP-OCRv5 inference model for one plate text line."""
    def __init__(self, directory):
        import paddleocr
        directory = Path(directory)
        if not (directory / 'inference.pdiparams').is_file():
            raise FileNotFoundError(f'PaddleOCR推論モデルがありません: {directory}')
        self.model = paddleocr.TextRecognition(model_name='PP-OCRv5_mobile_rec',
                                                model_dir=str(directory), device='cpu')

    def read(self, image):
        import numpy as np
        values = self.model.predict(input=np.ascontiguousarray(image), batch_size=1)
        result = next(iter(values))
        data = result.json
        data = data.get('res', data)
        return str(data.get('rec_text') or '').strip(), float(data.get('rec_score') or 0)



def selected_backend(root):
    requested = os.getenv('GATE_OCR_BACKEND', 'lipla').lower()
    if requested != 'lipla':
        raise ValueError('OCR方式はLipla-jpに固定されています。')
    return requested


def make_readers(root, easyocr=None, offline=False):
    """Load the configured plate recognition pipeline."""
    mode = os.getenv('GATE_PLATE_PIPELINE', 'lipla').lower()
    if mode not in ('lipla', 'paddle'):
        raise ValueError('プレート認識方式はliplaまたはpaddleを指定してください。')
    if mode == 'paddle':
        directory = os.getenv('GATE_PADDLE_REC_MODEL_DIR')
        if not directory:
            raise ValueError('PaddleOCR推論モデルの場所を指定してください。')
        return [('paddle-plate', PaddleLineReader(directory))]
    selected_backend(root)
    return [('lipla-jp', LiplaPlateReader(offline, root))]
