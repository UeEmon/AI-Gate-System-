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
        self.comparison_path = self.path.parent / 'comparison-state.json'
        self.lock = threading.RLock()
        self.process = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.status().get('state') == 'running':
            self._save(dict(state='interrupted', error='コンテナ再起動で学習が中断されました。'))
        if self.comparison_status().get('state') == 'running':
            self._save_comparison(dict(state='interrupted', error='コンテナ再起動で比較が中断されました。'))

    def comparison_status(self):
        try:
            return json.loads(self.comparison_path.read_text())
        except (OSError, ValueError):
            return dict(state='idle')

    def _save_comparison(self, data):
        temporary = self.comparison_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        os.replace(temporary, self.comparison_path)

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
        missing = []
        if not (repo / 'tools' / 'train.py').is_file():
            missing.append(f'学習用コード: {repo / "tools" / "train.py"}')
        if not pretrained.is_file() or pretrained.stat().st_size == 0:
            missing.append(f'事前学習重み: {pretrained}')
        if missing:
            state = dict(state='unavailable', error='未配置: ' + ' / '.join(missing),
                         missing=missing)
            with self.lock:
                self._save(state)
            return state
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                return self.status()
            if self.comparison_status().get('state') == 'running':
                return self.status()
            try:
                rows = ocr_learning.dataset_snapshot(self.root)
            except ValueError as error:
                state = dict(state='insufficient_data', error=str(error))
                self._save(state)
                return state
            fingerprint = hashlib.sha256(('partition-v2-test-holdout:' + json.dumps([
                (r['id'], r['image_sha256'], r['top_text'], r['bottom_text'], r['fields_json'])
                for r in rows], ensure_ascii=False)).encode()).hexdigest()
            previous = self.status()
            retry_path = self.path.parent / 'retry-request.json'
            requested = retry_path.is_file() and previous.get('state') == 'failed'
            if previous.get('fingerprint') == fingerprint and previous.get('state') in ('completed', 'failed') and not requested:
                return previous
            if requested:
                retry_path.unlink(missing_ok=True)
            directory = self.path.parent / ('auto-' + uuid.uuid4().hex)
            try:
                report = export(self.root, directory)
            except Exception as error:
                shutil.rmtree(directory, ignore_errors=True)
                state = dict(state='failed', fingerprint=fingerprint, error=str(error))
                self._save(state)
                return state
            if report['det_train'] < 5 or report['det_val'] < 2 or report['det_test'] < 2:
                state = dict(state='insufficient_data', error='検出器に必要な学習・検証・未使用テスト画像が不足しています。', report=report)
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
                        'exit_code': code,
                        **({'error': '学習プロセスがSIGKILLで停止しました。メモリ不足などによる強制終了の可能性があります。'} if code == -9 else {})})
        if code == 0:
            try:
                self.maybe_compare()
            except (OSError, ValueError):
                pass

    def maybe_compare(self):
        with self.lock:
            training = self.status()
            if training.get('state') != 'completed' or (self.process is not None and self.process.poll() is None):
                return self.comparison_status()
            dataset = Path(training['dataset']).resolve()
            if not dataset.is_relative_to(self.path.parent.resolve()) or not dataset.is_dir():
                return self.comparison_status()
            weights = dataset / 'weights'
            if not (weights / 'plate.pt').is_file() or not (weights / 'paddle-inference' / 'inference.pdiparams').is_file():
                return self.comparison_status()
            request = self.path.parent / 'compare-request.json'
            previous = self.comparison_status()
            requested = request.is_file()
            if not requested and previous.get('dataset') == str(dataset) and previous.get('state') in ('completed', 'failed', 'running'):
                return previous
            if requested:
                request.unlink(missing_ok=True)
            state = dict(state='running', dataset=str(dataset), log=str(dataset / 'comparison.log'))
            self._save_comparison(state)
            with (dataset / 'comparison.log').open('wb') as log:
                process = subprocess.Popen([
                    sys.executable, str(Path(__file__).with_name('paddle_evaluate.py')),
                    '--data', str(self.root), '--dataset', str(dataset),
                    '--plate-weights', str(weights / 'plate.pt'),
                    '--recognition-dir', str(weights / 'paddle-inference'), '--compare-lipla'],
                    stdout=log, stderr=subprocess.STDOUT)
            self.process = process
            threading.Thread(target=self._watch_comparison, args=(process, state), daemon=True).start()
            return state

    def _watch_comparison(self, process, state):
        code = process.wait()
        with self.lock:
            result = {**state, 'state': 'completed' if code == 0 else 'failed', 'exit_code': code}
            if code == 0:
                try:
                    result['report'] = json.loads((Path(state['dataset']) / 'comparison.json').read_text())
                except (OSError, ValueError) as error:
                    result.update(state='failed', error=str(error))
            self._save_comparison(result)
