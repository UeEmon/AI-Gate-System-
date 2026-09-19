"""Train higher-accuracy vehicle or dedicated plate YOLO26 models."""
import argparse
from pathlib import Path


def train(data, output, task, epochs, imgsz):
    from ultralytics import YOLO
    base = 'yolo26s.pt' if task == 'vehicle' else 'yolo26n.pt'
    model = YOLO(base)
    result = model.train(data=str(data), epochs=epochs, imgsz=imgsz,
                         project=str(Path(output).parent), name=Path(output).stem,
                         device='cpu', exist_ok=True)
    best = Path(result.save_dir) / 'weights' / 'best.pt'
    if not best.is_file():
        raise RuntimeError('学習済み重みが生成されませんでした。')
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_bytes(best.read_bytes())


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--task',choices=['vehicle','plate'],required=True)
    parser.add_argument('--data',type=Path,required=True,help='Ultralytics形式dataset.yaml')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--epochs',type=int,default=100)
    parser.add_argument('--imgsz',type=int,default=960)
    args=parser.parse_args();train(args.data,args.output,args.task,args.epochs,args.imgsz)
