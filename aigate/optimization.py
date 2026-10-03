"""Measure the real worker in an isolated output folder; never tune on held-out test."""
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading


def evaluation_samples(root, maximum=8):
    import events
    import ocr_learning

    rows = ocr_learning.dataset_snapshot(root, minimum_train=0, require_ready=False)
    output = []
    full_frames = []
    with events.connection(root) as db:
        for row in rows:
            if row['partition'] != 'validation':
                continue
            record_row = db.execute('SELECT details_json FROM observations WHERE id=? UNION ALL SELECT details_json FROM ocr_auto_archive WHERE observation_id=? LIMIT 1',
                                    (row['observation_id'],row['observation_id'])).fetchone()
            if not record_row:
                continue
            record = json.loads(record_row[0])
            source = Path(record.get('image_path') or '').resolve()
            if not source.is_relative_to((Path(root)/'images').resolve()) or not source.is_file():
                continue
            index = row['candidate_index']
            if not source or index >= len(record.get('plate_candidates', [])):
                continue
            bbox = record['plate_candidates'][index].get('bbox_in_vehicle')
            if not bbox or len(bbox) != 4:
                continue
            metadata=dict(source=str(source),box=bbox,truth=row['plate_key'],label_source=row['source'],scope='stored_vehicle_crops',
                          image_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
            saved=db.execute('SELECT image FROM ocr_evaluation_frames WHERE observation_id=?',(row['observation_id'],)).fetchone()
            if saved and len(record.get('bbox',[]))==4:
                x,y=record['bbox'][:2]
                full_frames.append(dict(metadata,image_bytes=bytes(saved[0]),box=[bbox[0]+x,bbox[1]+y,bbox[2]+x,bbox[3]+y],
                                        scope='full_frame',image_sha256=hashlib.sha256(saved[0]).hexdigest()))
            else:
                output.append(metadata)
    return full_frames[:maximum] if len(full_frames)>=2 else output[:maximum]


def run_sample(sample, imgsz, service):
    """Use the production input/detection/OCR/decision/storage/notification path."""
    import events
    from .evaluation import box_iou as overlap
    root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ, GATE_MODEL_SERVICE_SOCKET=service.address,
                       GATE_MODEL_SERVICE_KEY=service.key, GATE_VEHICLE_AI_ENABLED='0',
                       GATE_EMAIL_FROM='', GATE_EMAIL_TO='', GATE_S3_BUCKET='')
    with tempfile.TemporaryDirectory(prefix='gate-benchmark-') as directory:
        out = Path(directory)
        source=sample['source']
        if sample.get('image_bytes'):
            source=str(out/'benchmark-input.jpg')
            Path(source).write_bytes(sample['image_bytes'])
        command = [sys.executable, str(root/'app.py'), '--source', source,
                   '--source-kind', 'file', '--output', directory, '--run-id', 'benchmark',
                   '--every', '1', '--imgsz', str(imgsz), '--save-images', '--alerts',
                   '--preview', str(out/'preview.jpg')]
        # Disabled external delivery and training, using a temporary DB for all writes.
        subprocess.run(command, env=environment, cwd=root, capture_output=True,
                       text=True, check=True, timeout=180)
        row = json.loads((out/'jobs'/'benchmark'/'performance.jsonl').read_text().splitlines()[0])
        from model_service import ModelClient
        model_resources = ModelClient(service.address,service.key).request('resources')
        worker_resources = row.get('resources',{})
        worker_resources['gpu_utilization_percent'] = model_resources.get('gpu_utilization_percent')
        worker_resources['gpu_memory_bytes'] = model_resources.get('gpu_memory_bytes')
        row['resources'] = worker_resources
        matched = exact = False
        with sqlite3.connect(out/'gate.db') as db:
            records = [json.loads(x[0]) for x in db.execute('SELECT details_json FROM observations')]
        for record in records:
            x, y = record['bbox'][:2]
            for candidate in record.get('plate_detection', {}).get('proposals', []):
                a,b,c,d = candidate['bbox_in_vehicle']
                if overlap([x+a,y+b,x+c,y+d], sample['box']) >= .5:
                    matched = True
            for candidate in record.get('plate_candidates', []):
                a,b,c,d = candidate['bbox_in_vehicle']
                if (not record.get('result_eligible') or candidate.get('confidence',0) < record.get('result_thresholds',{}).get('ocr',.9)
                        or overlap([x+a,y+b,x+c,y+d], sample['box']) < .5):
                    continue
                try:
                    exact |= events.plate_key(candidate['fields']) == sample['truth']
                except (KeyError, ValueError, TypeError):
                    pass
        return dict(row, detected=matched, recognized=exact)


def select_configuration(samples, runner, baseline=960, target_fps=10, repeats=2, source_fps=30):
    if not (math.isfinite(target_fps) and math.isfinite(source_fps) and 0 < target_fps <= source_fps <= 240):
        raise ValueError('入力FPS・目標FPSは0より大きく240以下、目標は入力FPS以下にしてください。')
    if len(samples) < 2:
        raise ValueError('画像を取得できる検証用ナンバーが2件以上必要です。テスト用画像は使用しません。')
    results = []
    sizes = [baseline] + [n for n in (640,960,1280) if n != baseline]
    for size in sizes:
        runner(samples[0], size)  # Warm-up excluded from measured percentiles.
        rows = []
        for repeat in range(repeats):
            for sample in (samples if repeat % 2 == 0 else list(reversed(samples))):
                rows.append(runner(sample,size))
        durations = sorted(x['frame_ms'] for x in rows)
        results.append(dict(imgsz=size, frames=len(rows), detection_recall=sum(x['detected'] for x in rows)/len(rows),
                            exact_plate_accuracy=sum(x['recognized'] for x in rows)/len(rows),
                            mean_ms=statistics.mean(durations), p95_ms=durations[math.ceil(len(rows)*.95)-1],
                            fps=1000/statistics.mean(durations),
                            stages={key:statistics.mean(x.get(key,0) for x in rows) for key in
                                    ('capture_ms','preprocess_ms','detection_ms','plate_recognition_ms','decision_ms','storage_ms','notification_ms')},
                            resources={key:max((x.get('resources',{}).get(key) for x in rows if x.get('resources',{}).get(key) is not None), default=None)
                                       for key in ('memory_rss_bytes','cpu_percent','gpu_utilization_percent','gpu_memory_bytes')}))
    reference = results[0]
    if not reference['detection_recall'] or not reference['exact_plate_accuracy']:
        raise ValueError('基準設定で検出またはOCRに成功していないため、精度を保つ自動最適化はできません。')
    eligible = [x for x in results if x['detection_recall'] >= reference['detection_recall']
                and x['exact_plate_accuracy'] >= reference['exact_plate_accuracy']]
    best = min(eligible, key=lambda x:(x['p95_ms'],x['mean_ms']))
    return dict(state='completed', measured_at=datetime.now(timezone.utc).isoformat(),
                evaluation_partition='validation', evaluation_scope=samples[0].get('scope','stored_vehicle_crops'),
                ground_truth='manual_and_or_high_confidence_lipla_pseudo',
                sample_count=len(samples), repeats=repeats, warmup_excluded=True,
                baseline=baseline, candidates=results, selected=best, target_fps=target_fps, source_fps=source_fps,
                realtime=best['p95_ms'] <= 1000/target_fps,
                recommendation=dict(imgsz=best['imgsz'], frame_stride=max(1,math.ceil(source_fps/target_fps),math.ceil(best['p95_ms']*source_fps/1000))),
                notice='評価範囲はevaluation_scopeを参照してください。車両切り出し画像のみの場合は自動適用しません。フレーム間引きは推論FPSを増やしません。外部AIと非同期配送は測定対象外です。')


class OptimizationManager:
    def __init__(self, jobs, settings):
        self.jobs, self.settings = jobs, settings
        self.path = jobs.root/'optimization.json'
        self.thread = None
        self.lock = threading.Lock()
        from resource_lock import ResourceLease
        self.lease = ResourceLease(jobs.root)

    def status(self):
        try:
            value = json.loads(self.path.read_text())
            if value.get('state') == 'running' and not self.jobs.optimizing:
                return dict(value,state='interrupted')
            return value
        except (OSError, ValueError):
            return dict(state='idle')

    def save(self, report):
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(report,ensure_ascii=False,indent=2))
        os.replace(temporary,self.path)

    def start(self):
        with self.jobs.lock:
            if self.jobs.processes or self.jobs.optimizing:
                raise RuntimeError('監視・ファイル処理・性能測定を停止してから実行してください。')
            if self.jobs.model_service is None:
                raise ValueError('常駐モデルが準備されていません。')
            samples = evaluation_samples(self.jobs.root)
            if len(samples) < 2:
                raise ValueError('利用できる検証用画像が2件以上必要です。')
            if not self.lease.acquire():
                raise RuntimeError('学習・比較処理が実行中です。完了後に性能測定を開始してください。')
            self.jobs.optimizing = True
            try:
                self.save(dict(state='running',sample_count=len(samples)))
                self.thread = threading.Thread(target=self._run,args=(samples,),daemon=True)
                self.thread.start()
            except BaseException:
                self.jobs.optimizing = False
                self.lease.release()
                raise
        return self.status()

    def _run(self,samples):
        try:
            baseline = self.settings.read()['imgsz']
            report = select_configuration(samples, lambda sample,size:run_sample(sample,size,self.jobs.model_service), baseline, target_fps=float(os.getenv('GATE_TARGET_FPS','10')), source_fps=float(os.getenv('GATE_SOURCE_FPS','30')))
            if self.settings.read()['profile'] == 'auto' and report['evaluation_scope']=='full_frame':
                self.settings.update(dict(report['recommendation'],profile='auto'))
                report['applied'] = True
            else:
                report['applied'] = False
            self.save(report)
        except Exception as error:
            self.save(dict(state='failed',error=str(error)))
        finally:
            self.lease.release()
            with self.jobs.lock:
                self.jobs.optimizing = False
