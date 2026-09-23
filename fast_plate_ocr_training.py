"""Backend orchestration for reviewed Japanese FastPlateOCR training runs."""
import csv
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import uuid

import events


class FastPlateOCRTrainingManager:
    """Create an immutable dataset snapshot and run FastPlateOCR out of process."""

    backend = 'fast-plate-ocr'

    def __init__(self, root):
        self.root = Path(root)
        self.lock = threading.RLock()
        self.process = None
        from ocr_learning import initialize
        initialize(root)

    def _insert_run(self, identifier):
        with events.connection(self.root) as db:
            try:
                db.execute('INSERT INTO ocr_training_runs VALUES (?,?,?,NULL,NULL)',
                           (identifier, 'running', events.utc()))
            except Exception as exc:
                raise ValueError('再学習はすでに実行中です。') from exc

    def _write_snapshot(self, directory, samples):
        manifest = []
        annotations = []
        for sample in samples:
            image = sample.pop('image')
            filename = sample['id'] + '.png'
            (directory / filename).write_bytes(image)
            manifest.append(sample)
            # The reviewer-corrected four fields are the supervision target.
            plate = ''.join((sample['top_text'], sample['bottom_text']))
            annotations.append((str((directory / filename).resolve()), plate, sample['partition']))
        (directory / 'dataset.json').write_text(json.dumps(manifest, ensure_ascii=False))
        with (directory / 'annotations.csv').open('w', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(['image_path', 'plate', 'partition'])
            writer.writerows(annotations)

    def start(self):
        from ocr_learning import dataset_snapshot, model_dir
        with self.lock:
            if self.process and self.process.poll() is None:
                raise ValueError('再学習はすでに実行中です。')
            samples = dataset_snapshot(self.root)
            identifier = uuid.uuid4().hex
            self._insert_run(identifier)
            try:
                directory = model_dir(self.root, identifier)
                directory.mkdir()
                self._write_snapshot(directory, samples)
                command = [sys.executable, str(Path(__file__).with_name('fast_plate_ocr_train.py')),
                           '--root', str(self.root), '--run', identifier,
                           '--epochs', os.getenv('GATE_FAST_OCR_TRAIN_EPOCHS', '20'),
                           '--batch-size', os.getenv('GATE_FAST_OCR_TRAIN_BATCH_SIZE', '8')]
                baseline = os.getenv('GATE_FAST_OCR_BASELINE_MODEL', '').strip()
                if baseline:
                    command += ['--baseline-model', baseline]
                log = (directory / 'train.log').open('w')
                try:
                    self.process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
                finally:
                    log.close()
                threading.Thread(target=self._wait, args=(identifier, self.process), daemon=True).start()
            except Exception as exc:
                self._finish(identifier, 'failed', error=str(exc))
                raise
            return identifier

    def _finish(self, identifier, status, report=None, error=None):
        with events.connection(self.root) as db:
            db.execute('UPDATE ocr_training_runs SET status=?,report_json=?,error=? WHERE id=?',
                       (status, json.dumps(report, ensure_ascii=False) if report else None, error, identifier))

    def _wait(self, identifier, process):
        from ocr_learning import model_dir
        try:
            code = process.wait(timeout=24 * 3600)
            directory = model_dir(self.root, identifier)
            if code:
                raise ValueError((directory / 'train.log').read_text()[-4000:])
            report = json.loads((directory / 'report.json').read_text())
            self._finish(identifier, 'completed', report)
        except Exception as exc:
            if process.poll() is None:
                process.kill()
                process.wait()
            self._finish(identifier, 'failed', error=str(exc))
        finally:
            with self.lock:
                if self.process is process:
                    self.process = None

    def shutdown(self):
        with self.lock:
            if self.process and self.process.poll() is None:
                self.process.terminate()
