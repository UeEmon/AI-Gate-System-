"""Train custom YOLO26 vehicle or Japanese license-plate detectors."""
import argparse
from pathlib import Path
import shutil


BASE_MODELS = {'vehicle': 'yolo26s.pt', 'plate': 'yolo26n.pt'}


def train(data, output, task, epochs=100, imgsz=960, device='cpu'):
    if epochs < 1:
        raise ValueError('epochsは1以上で指定してください。')
    if not 320 <= imgsz <= 1920:
        raise ValueError('imgszは320〜1920で指定してください。')
    data = Path(data)
    output = Path(output)
    if not data.is_file():
        raise FileNotFoundError(f'データセット設定が見つかりません: {data}')
    from ultralytics import YOLO
    model = YOLO(BASE_MODELS[task])
    result = model.train(
        data=str(data), epochs=epochs, imgsz=imgsz,
        project=str(output.parent / 'runs'), name=output.stem,
        device=device, exist_ok=True)
    best = Path(result.save_dir) / 'weights' / 'best.pt'
    if not best.is_file():
        raise RuntimeError('学習済み重みbest.ptが生成されませんでした。')
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(best, output)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', choices=sorted(BASE_MODELS), required=True)
    parser.add_argument('--data', type=Path, required=True,
                        help='Ultralytics形式のdataset.yaml')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--epochs', type=int, default=100)
    parser.add_argument('--imgsz', type=int, default=960)
    parser.add_argument('--device', default='cpu',
                        help='cpu、0、0,1などUltralyticsのdevice指定')
    args = parser.parse_args()
    destination = train(args.data, args.output, args.task,
                        args.epochs, args.imgsz, args.device)
    print(f'学習済みモデル: {destination}')


if __name__ == '__main__':
    main()
