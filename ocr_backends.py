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



def selected_backend(root):
    requested = os.getenv('GATE_OCR_BACKEND', 'lipla').lower()
    if requested != 'lipla':
        raise ValueError('OCR方式はLipla-jpに固定されています。')
    return requested


def make_readers(root, easyocr=None, offline=False):
    """Load the sole plate reader used by the production pipeline."""
    selected_backend(root)
    return [('lipla-jp', LiplaPlateReader(offline, root))]
