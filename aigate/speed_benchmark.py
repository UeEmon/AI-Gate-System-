"""Authenticated Web video comparison, isolated from operational evidence."""
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import sys
import threading
import uuid

from .speed_experiment import STAGES, configuration, iou
from resource_lock import ResourceLease


def validate_truth(raw):
    from events import plate_key
    if not isinstance(raw, dict) or not isinstance(raw.get('frames'), list):
        raise ValueError('正解JSONにはframes配列が必要です。')
    result = {}
    for row in raw['frames']:
        if not isinstance(row, dict):
            raise ValueError('正解フレームはオブジェクトにしてください。')
        index = row.get('frame_index')
        if type(index) is not int or index < 0 or index in result:
            raise ValueError('frame_indexは重複しない0以上の整数にしてください。')
        plates = row.get('plates')
        if not isinstance(plates, list):
            raise ValueError('plates配列が必要です。空配列はプレート無しの正解です。')
        result[index] = []
        for plate in plates:
            if not isinstance(plate, dict):
                raise ValueError('プレート正解はオブジェクトにしてください。')
            box = plate.get('box', [])
            if (len(box)!=4 or any(type(v) not in (int,float) or not math.isfinite(v) or v<0 for v in box)
                    or box[2]<=box[0] or box[3]<=box[1]):
                raise ValueError('boxはフルフレーム内の[x1,y1,x2,y2]にしてください。')
            key = plate_key(plate['fields'])
            event_id = plate.get('event_id', key)
            if not isinstance(event_id,str) or not event_id or len(event_id)>128:
                raise ValueError('event_idは128文字以下の文字列にしてください。')
            result[index].append(dict(box=box, key=key, event_id=event_id))
    if not result:
        raise ValueError('正解フレームがありません。')
    return result


def evaluate(traces, truth):
    from events import plate_key
    expected = detected = correct = fresh_detected = false_positive = labelled_frames = 0
    events = {}
    seen_frames = set()
    for row in traces:
        if row['frame_index'] not in truth:
            continue
        labelled_frames += 1
        seen_frames.add(row['frame_index'])
        targets = truth[row['frame_index']]
        for target in targets:
            event_id = f"{row['repeat']}:{target['event_id']}" if 'repeat' in row else target['event_id']
            events.setdefault(event_id,dict(first_media_ms=row.get('media_ms'),
                first_processing_ms=row.get('processing_start_ms'),correct_media_delay_ms=None,
                correct_offline_processing_delay_ms=None))
        expected += len(targets)
        predictions = []
        for vehicle in row['vehicles']:
            x,y = vehicle['bbox'][:2]
            for proposal in vehicle.get('proposals', vehicle['candidates']):
                candidates = [c for c in vehicle['candidates']
                              if iou(c['bbox_in_vehicle'],proposal['bbox_in_vehicle'])>=.5]
                candidate = max(candidates,key=lambda c:c.get('confidence',0),default={})
                a,b,c,d = proposal['bbox_in_vehicle']
                try:
                    key = plate_key(candidate['fields']) if candidate.get('confidence',0)>=.9 else None
                except (ValueError, KeyError, TypeError):
                    key = None
                predictions.append(dict(box=[x+a,y+b,x+c,y+d], key=key, reused=vehicle['reused']))
        pairs = sorted(((iou(p['box'],t['box']), j,k) for j,p in enumerate(predictions)
                        for k,t in enumerate(targets)), reverse=True)
        used_predictions, used_targets = set(), set()
        for score,j,k in pairs:
            if score < .5: break
            if j in used_predictions or k in used_targets: continue
            used_predictions.add(j); used_targets.add(k)
            detected += 1
            fresh_detected += int(not predictions[j]['reused'])
            correct += int(predictions[j]['key'] == targets[k]['key'])
            if predictions[j]['key'] == targets[k]['key']:
                event_id = f"{row['repeat']}:{targets[k]['event_id']}" if 'repeat' in row else targets[k]['event_id']
                event = events[event_id]
                if event['correct_media_delay_ms'] is None and row.get('media_ms') is not None:
                    event['correct_media_delay_ms'] = row['media_ms']-event['first_media_ms']
                    if row.get('processing_end_ms') is not None and event['first_processing_ms'] is not None:
                        event['correct_offline_processing_delay_ms'] = row['processing_end_ms']-event['first_processing_ms']
        false_positive += len(predictions)-len(used_predictions)
    return dict(labelled_frames=labelled_frames, expected_plates=expected,
                continuity_detection_recall=detected/expected if expected else None,
                exact_plate_accuracy=correct/expected if expected else None,
                fresh_detection_fraction=fresh_detected/expected if expected else None,
                false_positive_plates=false_positive,
                first_correct_by_event=events,
                unprocessed_truth_frames=sorted(set(truth)-seen_frames),
                scope='labelled_frames_including_explicitly_marked_reused_results')


def summarize(rows, traces, truth=None):
    if not rows:
        raise ValueError('処理フレームがありません。')
    values = sorted(r['frame_ms'] for r in rows)
    total = sum(values)
    return dict(frames=len(rows), mean_ms=statistics.mean(values),
                p95_ms=values[math.ceil(len(values)*.95)-1],
                fps=len(rows)*1000/total if total else 0,
                ocr_calls=sum(r.get('ocr_calls',0) for r in rows),
                skipped_ocr_calls=sum(r.get('skipped_ocr_calls',0) for r in rows),
                conditional_fast_count=sum(r.get('conditional_fast_count',0) for r in rows),
                conditional_fallback_count=sum(r.get('conditional_fallback_count',0) for r in rows),
                plate_ms=statistics.mean(r['plate_recognition_ms'] for r in rows),
                vehicle_ms=statistics.mean(r['detection_ms'] for r in rows),
                memory_rss_bytes=max((r['resources'].get('memory_rss_bytes') or 0 for r in rows), default=0) or None,
                cpu_percent=max((r['resources'].get('cpu_percent') or 0 for r in rows), default=0) or None,
                accuracy=evaluate(traces, truth) if truth is not None else None)


def worker(source, out, service, config, imgsz, limit, cancel):
    root = Path(__file__).resolve().parents[1]
    out.mkdir(parents=True)
    env = dict(os.environ,GATE_SPEED_EXPERIMENT=json.dumps(config),
               GATE_MODEL_SERVICE_SOCKET=service.address,GATE_MODEL_SERVICE_KEY=service.key,
               GATE_VEHICLE_AI_ENABLED='0',GATE_EMAIL_FROM='',GATE_EMAIL_TO='',GATE_S3_BUCKET='')
    command = [sys.executable,str(root/'app.py'),'--source',str(source),'--source-kind','file',
               '--output',str(out),'--run-id','speed','--every','1','--imgsz',str(imgsz),
               '--max-frames',str(limit),'--recognition-trace',str(out/'trace.jsonl'),
               '--save-images','--alerts','--preview',str(out/'preview.jpg')]
    with (out/'worker.log').open('wb') as log:
        process = subprocess.Popen(command,env=env,cwd=root,stdout=log,stderr=subprocess.STDOUT)
        import time
        deadline = time.monotonic()+1800
        try:
            while process.poll() is None:
                if cancel.wait(.25) or time.monotonic()>deadline:
                    process.terminate()
                    try: process.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait()
                    raise RuntimeError('検証を中止または処理時間上限30分に到達しました。')
            if process.returncode:
                raise RuntimeError(f'処理失敗（終了コード {process.returncode}）。検証ログを確認してください。')
        finally:
            if process.poll() is None:
                process.kill(); process.wait()
    rows = [json.loads(s) for s in (out/'jobs/speed/performance.jsonl').read_text().splitlines()]
    traces = [json.loads(s) for s in (out/'trace.jsonl').read_text().splitlines()]
    return rows,traces


class SpeedBenchmark:
    def __init__(self, jobs):
        self.jobs = jobs
        self.root = jobs.root/'speed-experiments'
        self.path = self.root/'state.json'
        self.lease = ResourceLease(jobs.root)
        self.cancel = threading.Event()
        self.thread = None

    def status(self):
        try:
            result = json.loads(self.path.read_text())
            if result['state']=='running' and (not self.thread or not self.thread.is_alive()):
                result['state']='interrupted'
            return result
        except (ValueError,OSError):
            return dict(state='idle')

    def save(self, report):
        self.root.mkdir(parents=True,exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(report,ensure_ascii=False,indent=2))
        os.replace(temporary,self.path)

    def start(self, video, truth_file=None, limit=120, repeats=2):
        if not 1 <= limit <= 600 or not 1 <= repeats <= 3:
            raise ValueError('フレーム上限は1〜600、反復は1〜3です。')
        suffix = Path(video.filename).suffix.lower()
        if suffix not in {'.mp4','.mov','.avi','.mkv','.m4v','.webm'}:
            raise ValueError('検証動画を指定してください。')
        truth = None
        if truth_file and truth_file.filename:
            content = truth_file.stream.read(8*1024*1024+1)
            if len(content)>8*1024*1024:
                raise ValueError('正解JSONは8MB以下にしてください。')
            truth = validate_truth(json.loads(content))
        if truth and any(i>=limit for i in truth):
            raise ValueError('正解frame_indexは処理フレーム上限未満にしてください。')
        with self.jobs.lock:
            if self.jobs.optimizing or self.jobs.processes:
                raise RuntimeError('監視・処理・性能測定を停止してください。')
            if self.jobs.model_service is None:
                raise ValueError('常駐モデルが未準備です。')
            if not self.lease.acquire():
                raise RuntimeError('学習・比較が完了するまで待ってください。')
            directory = self.root/uuid.uuid4().hex
            try:
                directory.mkdir(parents=True)
                source = directory/('input'+suffix)
                video.save(source)
                with source.open('rb') as stream:
                    checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
                self.cancel.clear()
                self.jobs.optimizing = True
                report = dict(state='running',run_id=directory.name,source_sha256=checksum,
                              max_frames=limit,repeats=repeats,results=[],accuracy_available=truth is not None,
                              notice='再利用結果は診断用のみ。教師データ・登録・通知への再登録は行いません。'
                                     '基準との一致は精度の正解とは見なしません。実機で測定します。')
                self.save(report)
                self.thread = threading.Thread(target=self.run,args=(source,directory,truth,limit,repeats,report),daemon=True)
                self.thread.start()
            except BaseException:
                self.jobs.optimizing=False
                self.lease.release()
                raise
        return self.status()

    def run(self, source, directory, truth, limit, repeats, report):
        original = self.jobs.model_configuration()
        reference = None
        threads = 2
        openvino = False
        try:
            for stage,label in STAGES:
                for count in ((1,2,4) if stage=='threads' else (threads,)):
                    if self.cancel.is_set(): raise RuntimeError('検証を中止しました。')
                    name = stage+(f'-{count}' if stage=='threads' else '')
                    cfg = configuration(stage,count)
                    # Stage 5 is independent of the optional OpenVINO export.
                    if stage=='conditional': cfg['openvino']=openvino
                    result = dict(stage=stage,label=label,threads=cfg['threads'],config=cfg,state='running')
                    report['current']=name
                    self.save(report)
                    try:
                        import importlib.util
                        if cfg['openvino'] and importlib.util.find_spec('openvino') is None:
                            raise ValueError('OpenVINO依存未導入。compose.speed.yamlを使用してください。')
                        if cfg['conditional']:
                            from importlib.metadata import version
                            if version('lipla-jp') != '0.4.1':
                                raise ValueError('条件付きOCRはLipla-jp 0.4.1が必要です。')
                        config = dict(original,environment={**original['environment'],'GATE_SPEED_EXPERIMENT':json.dumps(cfg)})
                        self.jobs.model_service.reload(config)
                        warm_rows, _ = worker(source,directory/name/'warmup',self.jobs.model_service,cfg,original['imgsz'],1,self.cancel)
                        result['warmup_ocr_calls'] = sum(r.get('ocr_calls',0) for r in warm_rows)
                        all_rows, all_traces = [],[]
                        for repeat in range(repeats):
                            rows,traces = worker(source,directory/name/str(repeat),self.jobs.model_service,cfg,original['imgsz'],limit,self.cancel)
                            for trace in traces:
                                trace['repeat'] = repeat
                            all_rows.extend(rows); all_traces.extend(traces)
                        result.update(state='completed',**summarize(all_rows,all_traces,truth))
                        from importlib.metadata import version
                        result['versions'] = {p:version(p) for p in ('lipla-jp','torch','onnxruntime')}
                        if reference is None:
                            reference = result
                        result['speedup'] = reference['mean_ms']/result['mean_ms']
                        base = reference.get('accuracy')
                        current = result.get('accuracy')
                        result['accuracy_preserved'] = (
                            base['exact_plate_accuracy'] > 0 and
                            base['continuity_detection_recall'] > 0 and
                            not current['unprocessed_truth_frames'] and not base['unprocessed_truth_frames'] and
                            current['labelled_frames']==base['labelled_frames'] and
                            current['expected_plates'] == base['expected_plates'] and
                            current['exact_plate_accuracy'] is not None and
                            current['exact_plate_accuracy']>=base['exact_plate_accuracy'] and
                            current['continuity_detection_recall']>=base['continuity_detection_recall'] and
                            current['false_positive_plates']<=base['false_positive_plates']
                        ) if base and base['expected_plates'] else None
                        if stage=='openvino': openvino = result['accuracy_preserved'] is True
                    except Exception as error:
                        result.update(state='failed',error=str(error))
                    report['results'].append(result)
                    self.save(report)
                    if stage=='baseline' and result['state']!='completed':
                        raise RuntimeError('基準測定に失敗しました。検証ログを確認してください。')
                if stage=='threads':
                    candidates = [r for r in report['results'] if r['stage']=='threads'
                                  and r['state']=='completed' and r.get('accuracy_preserved') is True]
                    if candidates: threads=min(candidates,key=lambda r:r['p95_ms'])['threads']
            report.update(state='completed',completed_at=datetime.now(timezone.utc).isoformat(),
                          applied=False,scope='offline_video_e2e_processing_not_live_transport_latency')
        except Exception as error:
            report.update(state='cancelled' if self.cancel.is_set() else 'failed',error=str(error),applied=False)
        finally:
            try:
                original['environment']['GATE_SPEED_EXPERIMENT']=json.dumps(configuration())
                self.jobs.model_service.reload(original)
            except Exception as error:
                report.update(state='failed',restore_error=str(error))
            try:
                self.save(report)
            finally:
                with self.jobs.lock:
                    self.jobs.optimizing=False
                    self.lease.release()
