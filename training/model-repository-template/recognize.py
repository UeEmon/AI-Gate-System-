"""Portable inference adapter. Uses local files; does not activate a Gate model."""
import argparse
import json
from pathlib import Path
import re
import unicodedata

from plate_rules import KANA_PATTERN
from paddle_plate_pipeline import PaddlePlatePipeline


def parse_plate(text):
    compact = re.sub(r'\s+','',unicodedata.normalize('NFKC',text)).replace('−','-').replace('ー','-')
    match = re.fullmatch(rf'([一-龥ぁ-んァ-ヶ]{{2,8}})([0-9][0-9A-Z]{{2}})({KANA_PATTERN})([0-9・.\-]{{1,7}})',compact)
    if not match:
        return None
    region,category,kana,serial = match.groups()
    serial = re.sub(r'[^0-9]','',serial)
    return dict(region=region,category=category,kana=kana,serial=serial) if 1<=len(serial)<=4 else None


class LineReader:
    def __init__(self,directory):
        from paddleocr import TextRecognition
        self.model = TextRecognition(model_name='PP-OCRv5_mobile_rec',model_dir=str(directory),device='cpu')

    def read(self,image):
        import numpy as np
        value = next(iter(self.model.predict(input=np.ascontiguousarray(image),batch_size=1))).json
        value = value.get('res',value)
        return str(value.get('rec_text') or '').strip(),float(value.get('rec_score') or 0)


class JapanesePlateRecognizer:
    def __init__(self,root=None):
        from ultralytics import YOLO
        root = Path(root or Path(__file__).resolve().parent)
        self.pipeline = PaddlePlatePipeline(YOLO(str(root/'models'/'plate.pt')),
                                            LineReader(root/'models'/'paddle-inference'),parse_plate)

    def recognize(self,vehicle_bgr):
        return self.pipeline.run(vehicle_bgr)


if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('vehicle_image');args=parser.parse_args()
    import cv2
    import numpy as np
    image=cv2.imdecode(np.fromfile(args.vehicle_image,dtype=np.uint8),cv2.IMREAD_COLOR)
    if image is None:
        parser.error('車両画像を読み込めません。')
    candidates,report=JapanesePlateRecognizer().recognize(image)
    print(json.dumps(dict(candidates=candidates,report=report),ensure_ascii=False))
