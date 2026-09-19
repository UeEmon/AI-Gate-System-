"""CPU fine-tuning of EasyOCR's Japanese recognizer with reviewed line crops."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import re


def edit_distance(a, b):
    row = list(range(len(b) + 1))
    for i, left in enumerate(a, 1):
        nxt = [i]
        for j, right in enumerate(b, 1):
            nxt.append(min(nxt[-1] + 1, row[j] + 1, row[j-1] + (left != right)))
        row = nxt
    return row[-1]


def metrics(truth, predictions):
    predictions = [re.sub(r'\s+', '', p) for p in predictions]
    return {'cer': sum(edit_distance(a, b) for a, b in zip(truth, predictions)) / sum(map(len, truth)),
            'line_accuracy': sum(a == b for a, b in zip(truth, predictions)) / len(truth),
            'lines': len(truth)}


def eligible(baseline, candidate):
    return candidate['cer'] < baseline['cer'] and candidate['line_accuracy'] >= baseline['line_accuracy']


def train(root, run, baseline=None, epochs=10):
    import easyocr
    import numpy as np
    from PIL import Image
    import torch
    from easyocr.recognition import AlignCollate
    from easyocr.config import imgH
    from ocr_learning import model_dir, load_weights, training_crops

    torch.set_num_threads(2)
    random.seed(42)
    np.random.seed(42)
    torch.manual_seed(42)
    directory = model_dir(root, run)
    manifest = json.loads((directory / 'dataset.json').read_text())
    reader = easyocr.Reader(['ja', 'en'], gpu=False, detector=False, quantize=False,
                            download_enabled=os.getenv('GATE_OFFLINE') != '1')
    if baseline:
        load_weights(reader, model_dir(root, baseline))
    model, converter = reader.recognizer, reader.converter
    partitions = {'train': [], 'validation': []}
    for sample in manifest:
        path = directory / (sample['id'] + '.png')
        if hashlib.sha256(path.read_bytes()).hexdigest() != sample['image_sha256']:
            raise ValueError('学習画像のチェックサムが一致しません。')
        with Image.open(path) as source:
            image = source.convert('L')
        for label, bounds in training_crops(sample, image.width, image.height):
            unknown = sorted(set(label) - set(reader.character))
            if unknown:
                raise ValueError('現在の日本語モデルで未対応の文字: ' + ''.join(unknown))
            partitions[sample['partition']].append((image.crop(bounds), label))
    training, validation = partitions['train'], partitions['validation']
    if not training or not validation:
        raise ValueError('学習・評価データの両方が必要です。')
    collate = AlignCollate(imgH=imgH, imgW=384, keep_ratio_with_pad=True)

    def evaluate():
        predictions, truth = [], []
        model.eval()
        with torch.no_grad():
            for start in range(0, len(validation), 4):
                batch = validation[start:start+4]
                logits = model(collate([v[0] for v in batch]), None)
                indices = logits.argmax(2).reshape(-1).cpu().numpy()
                lengths = torch.IntTensor([logits.shape[1]] * len(batch))
                predictions += converter.decode_greedy(indices, lengths)
                truth += [v[1] for v in batch]
        return metrics(truth, predictions)

    original = evaluate()
    best = original
    best_epoch = 0
    # Always save a valid initial checkpoint, even if training never improves.
    torch.save(model.state_dict(), directory / 'weights.pth')
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-5)
    loss_fn = torch.nn.CTCLoss(blank=0, reduction='mean', zero_infinity=False)
    history = []
    for epoch in range(1, epochs + 1):
        model.train()
        random.shuffle(training)
        losses = []
        for start in range(0, len(training), 4):
            batch = training[start:start+4]
            labels = [v[1] for v in batch]
            images = collate([v[0] for v in batch])
            targets, target_lengths = converter.encode(labels)
            logits = model(images, None)
            lengths = torch.full((len(batch),), logits.shape[1], dtype=torch.long)
            loss = loss_fn(logits.log_softmax(2).permute(1, 0, 2), targets, lengths, target_lengths)
            if not torch.isfinite(loss):
                raise ValueError('学習損失が不正です。画像と正解の長さを確認してください。')
            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 5)
            optimizer.step()
            losses.append(loss.item())
        score = evaluate()
        history.append(dict(epoch=epoch, loss=sum(losses)/len(losses), **score))
        print(json.dumps(history[-1]), flush=True)
        if eligible(original, score) and (best_epoch == 0 or score['cer'] < best['cer']):
            best, best_epoch = score, epoch
            torch.save(model.state_dict(), directory / 'weights.pth')
    (directory / 'model.json').write_text(json.dumps({
        'character_sha256': hashlib.sha256(reader.character.encode()).hexdigest(),
        'easyocr_version': easyocr.__version__, 'network': 'japanese_g2', 'baseline': baseline}))
    report = dict(baseline=original, candidate=best, eligible=eligible(original, best),
                  best_epoch=best_epoch, epochs=epochs, history=history,
                  train_lines=len(training), validation_lines=len(validation), baseline_model=baseline,
                  evaluation='held-out plate identities; reviewed field crops and legacy line crops; greedy decoder',
                  dataset_sha256=hashlib.sha256((directory / 'dataset.json').read_bytes()).hexdigest())
    (directory / 'report.json').write_text(json.dumps(report, ensure_ascii=False))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--run', required=True)
    parser.add_argument('--baseline')
    args = parser.parse_args()
    train(Path(args.root), args.run, args.baseline)
