"""Fine-tune a plate YOLO detector and PP-OCRv5 Japanese-capable recognizer.

Requires a local PaddleOCR training checkout and pretrained checkpoints. Does
not modify the production Lipla configuration or activate untested weights.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys


REC_CONFIG = 'configs/rec/PP-OCRv5/PP-OCRv5_mobile_rec.yml'


def run(dataset, repository, pretrained, epochs=20, imgsz=960):
    dataset, repository, pretrained = (Path(p).resolve() for p in
                                       (dataset, repository, pretrained))
    if not (dataset / 'rec' / 'train.txt').is_file() or not (dataset / 'rec' / 'val.txt').is_file():
        raise ValueError('まずpaddle_training.pyで学習データを作成してください。')
    if not (repository / 'tools' / 'train.py').is_file() or not (repository / REC_CONFIG).is_file():
        raise ValueError('PaddleOCRの公式リポジトリとPP-OCRv5設定が必要です。')
    if not pretrained.is_file():
        raise ValueError('PP-OCRv5_mobile_recの学習用事前学習重みを配置してください。')
    summary = json.loads((dataset / 'manifest.json').read_text())['report']
    if summary['det_train'] < 5 or summary['det_val'] < 2:
        raise ValueError('専用検出器には手動確認済みの学習用5件・評価用2件以上が必要です。')
    if not 1 <= epochs <= 200:
        raise ValueError('エポック数は1〜200です。')
    from vision_train import train
    detector_weights = dataset / 'weights' / 'plate.pt'
    print('STAGE: detector', flush=True)
    train(dataset / 'det' / 'dataset.yaml', detector_weights, 'plate', epochs, imgsz)
    rec = dataset / 'rec'
    output = dataset / 'weights' / 'paddle'
    options = [f'Global.pretrained_model={pretrained}',
               f'Global.save_model_dir={output}',
               f'Global.epoch_num={epochs}',
               'Global.use_gpu=False',
               'Global.eval_batch_step=[0,10]',
               'Global.save_epoch_step=1',
               'Train.loader.batch_size_per_card=4',
               'Eval.loader.batch_size_per_card=4',
               f'Train.dataset.data_dir={rec}',
               f'Train.dataset.label_file_list=[{rec / "train.txt"}]',
               f'Eval.dataset.data_dir={rec}',
               f'Eval.dataset.label_file_list=[{rec / "val.txt"}]']
    print('STAGE: recognizer', flush=True)
    subprocess.run([sys.executable, 'tools/train.py', '-c', REC_CONFIG,
                    '-o', *options], cwd=repository, check=True)
    best = output / 'best_accuracy'
    if not best.with_suffix('.pdparams').is_file():
        raise RuntimeError('PaddleOCRの評価済み重みが出力されませんでした。')
    model_options = [o for o in options if not o.startswith('Global.pretrained_model=')]
    model_options.append(f'Global.pretrained_model={best}')
    print('STAGE: validation', flush=True)
    subprocess.run([sys.executable, 'tools/eval.py', '-c', REC_CONFIG,
                    '-o', *model_options], cwd=repository, check=True)
    exported = dataset / 'weights' / 'paddle-inference'
    print('STAGE: export', flush=True)
    subprocess.run([sys.executable, 'tools/export_model.py', '-c', REC_CONFIG,
                    '-o', *model_options, f'Global.save_inference_dir={exported}'],
                   cwd=repository, check=True)
    if not (exported / 'inference.pdiparams').is_file():
        raise RuntimeError('PaddleOCRの推論重みが出力されませんでした。')
    return dict(detector=str(detector_weights), recognizer=str(exported),
                status='trained_not_activated')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', required=True)
    parser.add_argument('--paddle-repo', required=True)
    parser.add_argument('--pretrained', required=True)
    parser.add_argument('--epochs', type=int, default=20)
    parser.add_argument('--imgsz', type=int, default=960)
    args = parser.parse_args()
    print(json.dumps(run(args.dataset, args.paddle_repo, args.pretrained,
                         args.epochs, args.imgsz), ensure_ascii=False))
