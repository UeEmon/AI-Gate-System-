"""Benchmark EasyOCR and PaddleOCR on the immutable reviewed validation set."""
import argparse
import json
import os
from pathlib import Path

from ocr_train import metrics


def benchmark(root):
    import cv2
    import easyocr
    import numpy as np
    from ocr_backends import make_readers
    from ocr_learning import dataset_snapshot, training_crops

    samples = [sample for sample in dataset_snapshot(root) if sample['partition'] == 'validation']
    results = {}
    old = os.environ.get('GATE_OCR_BACKEND')
    try:
        for backend in ('easyocr', 'paddle'):
            os.environ['GATE_OCR_BACKEND'] = backend
            reader = make_readers(root, easyocr, os.getenv('GATE_OFFLINE') == '1')[0][1]
            truth, predicted = [], []
            for sample in samples:
                image = cv2.imdecode(np.frombuffer(sample['image'], np.uint8), cv2.IMREAD_COLOR)
                for label, (x1,y1,x2,y2) in training_crops(sample, image.shape[1], image.shape[0]):
                    crop = image[y1:y2,x1:x2]
                    if backend == 'paddle':
                        output = reader.model.predict(input=crop, batch_size=1)
                        text = reader._value(output[0])[0] if output else ''
                    else:
                        text = ''.join(item[1] for item in reader.readtext(
                            crop, detail=1, paragraph=False))
                    predicted.append(text); truth.append(label)
            results[backend] = metrics(truth, predicted)
    finally:
        if old is None: os.environ.pop('GATE_OCR_BACKEND', None)
        else: os.environ['GATE_OCR_BACKEND'] = old
    selected = min(results, key=lambda name: (results[name]['cer'], -results[name]['line_accuracy']))
    report = {'selected': selected, 'backends': results,
              'evaluation': 'held-out reviewed plate identities'}
    destination = Path(root) / 'ocr-learning' / 'benchmark.json'
    destination.parent.mkdir(exist_ok=True)
    temporary = destination.with_suffix('.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, destination)
    return report


if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--root',default='data')
    args=parser.parse_args();print(json.dumps(benchmark(Path(args.root)),ensure_ascii=False,indent=2))
