"""Opt-in background fine-tuning when reviewed Lipla samples change."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import uuid

import ocr_learning
from paddle_training import export


class PaddleTrainingManager:
    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / 'ocr-learning' / 'paddle' / 'auto-state.json'
        self.lock = threading.RLock()
        self.process = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.status().get('state') == 'running':
            self._save(dict(state='interrupted', error='コンテナ再起動で学習が中断されました。'))

    def status(self):
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return dict(state='idle')

    def _save(self, data):
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        os.replace(temporary, self.path)

    def maybe_start(self):
        if os.getenv('GATE_PADDLE_AUTO_TRAIN') != '1':
            return self.status()
        repo = Path(os.getenv('GATE_PADDLE_TRAIN_REPO', '/training/PaddleOCR'))
        pretrained = Path(os.getenv('GATE_PADDLE_PRETRAINED', '/models/PP-OCRv5_mobile_rec_pretrained.pdparams'))
        if not (repo / 'tools' / 'train.py').is_file() or not pretrained.is_file():
            state = dict(state='unavailable', error='PaddleOCR学習用コード・事前学習重みがありません。')
            with self.lock:
                self._save(state)
            return state
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                return self.status()
            try:
                rows = ocr_learning.dataset_snapshot(self.root)
            except ValueError as error:
                state = dict(state='insufficient_data', error=str(error))
                self._save(state)
                return state
            fingerprint = hashlib.sha256(json.dumps([
                (r['id'], r['image_sha256'], r['top_text'], r['bottom_text'], r['fields_json'])
                for r in rows], ensure_ascii=False).encode()).hexdigest()
            previous = self.status()
            if previous.get('fingerprint') == fingerprint and previous.get('state') in ('completed', 'failed'):
                return previous
            directory = self.path.parent / ('auto-' + uuid.uuid4().hex)
            try:
                report = export(self.root, directory)
            except Exception as error:
                shutil.rmtree(directory, ignore_errors=True)
                state = dict(state='failed', fingerprint=fingerprint, error=str(error))
                self._save(state)
                return state
            if report['det_train'] < 5 or report['det_val'] < 2:
                state = dict(state='insufficient_data', error='検出器に必要な車両画像が不足しています。', report=report)
                self._save(state)
                return state
            log = (directory / 'train.log').open('wb')
            try:
                self.process = subprocess.Popen([
                    sys.executable, str(Path(__file__).with_name('paddle_finetune.py')),
                    '--dataset', str(directory), '--paddle-repo', str(repo),
                    '--pretrained', str(pretrained)], stdout=log, stderr=subprocess.STDOUT)
            finally:
                log.close()
            state = dict(state='running', fingerprint=fingerprint, dataset=str(directory),
                         log=str(directory / 'train.log'), report=report)
            self._save(state)
            threading.Thread(target=self._watch, args=(self.process, state), daemon=True).start()
            return state

    def _watch(self, process, state):
        code = process.wait()
        with self.lock:
            self._save({**state, 'state': 'completed' if code == 0 else 'failed',
                        'exit_code': code})
        if code == 0:
            try:
                self.maybe_start()
            except (OSError, ValueError):
                pass
