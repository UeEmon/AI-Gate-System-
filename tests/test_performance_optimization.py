import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from aigate.performance import PerformanceManager
from aigate.telemetry import FrameTelemetry, TimedInput
from aigate.optimization import select_configuration, OptimizationManager
from aigate.settings import SettingsManager
from web import JobManager, create_app


class PerformanceTests(unittest.TestCase):
    def test_every_frame_recorded_without_browser_and_ingest_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'jobs'/'example'/'performance.jsonl'
            logger=FrameTelemetry(path)
            for index in range(20):
                logger.record(index,dict(frame_ms=20+index,preprocess_ms=2,processing_ms=18+index,capture_ms=2))
            logger.close()
            manager=PerformanceManager(root,maximum_samples=8)
            report=manager.summary()
            self.assertEqual(report['total_samples'],20)
            self.assertEqual(report['samples'],8)
            self.assertEqual(report['jobs']['example']['frames'],20)
            self.assertEqual(report['latency_ms']['frame_ms']['p95'],39)
            self.assertEqual(manager.summary()['total_samples'],20)
            restarted=PerformanceManager(root)
            self.assertEqual(restarted.summary()['total_samples'],20)

    def test_partial_final_row_is_retried_and_finished_job_is_included(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'jobs'/'done'/'performance.jsonl';path.parent.mkdir(parents=True)
            line=json.dumps(dict(frame_ms=10,elapsed_ms=30))
            path.write_text(line)
            manager=PerformanceManager(root)
            self.assertEqual(manager.summary()['samples'],0)
            with path.open('a') as stream:stream.write('\n')
            self.assertEqual(manager.summary()['samples'],1)

    def test_input_decode_time_is_measured(self):
        value=TimedInput(iter([('frame',)]))
        with patch('aigate.telemetry.time.perf_counter',side_effect=[10,10.03]):
            self.assertEqual(next(value),('frame',))
        self.assertAlmostEqual(value.capture_ms,30)

    def test_fast_inaccurate_setting_is_rejected_and_warmup_excluded(self):
        calls=[]
        def runner(sample,size):
            calls.append(size)
            return dict(frame_ms={640:10,960:60,1280:90}[size],detected=True,recognized=size!=640)
        report=select_configuration([dict(truth='a'),dict(truth='b')],runner)
        self.assertEqual(report['selected']['imgsz'],960)
        self.assertEqual(len(calls),15)
        self.assertTrue(report['realtime'])
        self.assertEqual(report['evaluation_partition'],'validation')

    def test_no_success_cannot_claim_optimal_and_slow_settings_are_not_realtime(self):
        rows=[{},{}]
        with self.assertRaisesRegex(ValueError,'基準設定'):
            select_configuration(rows,lambda sample,size:dict(frame_ms=20,detected=False,recognized=False))
        report=select_configuration(rows,lambda sample,size:dict(frame_ms=250,detected=True,recognized=True))
        self.assertFalse(report['realtime'])
        self.assertEqual(report['recommendation']['frame_stride'],8)

    def test_failed_measurement_preserves_current_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs=JobManager(directory);settings=SettingsManager(directory)
            settings.update(dict(imgsz=1280));manager=OptimizationManager(jobs,settings)
            with patch('aigate.optimization.run_sample',side_effect=RuntimeError('inference unavailable')):
                manager._run([{},{}])
            self.assertEqual(settings.read()['imgsz'],1280)
            self.assertEqual(manager.status()['state'],'failed')
            jobs.shutdown()

    def test_benchmark_auth_csrf_busy_and_no_model_are_reported(self):
        with tempfile.TemporaryDirectory() as root:
            manager=JobManager(root);app=create_app(manager=manager,password='');client=app.test_client();client.get('/')
            with client.session_transaction() as session:headers={'X-CSRF-Token':session['csrf']}
            self.assertEqual(client.post('/api/system/benchmark').status_code,403)
            self.assertEqual(client.post('/api/system/benchmark',headers=headers).status_code,400)
            manager.optimizing=True
            self.assertEqual(client.post('/api/system/benchmark',headers=headers).status_code,409)
            with self.assertRaisesRegex(RuntimeError,'停止'):
                app.extensions['optimization'].start()
            manager.shutdown()


class WorkerBenchmarkIntegrationTests(unittest.TestCase):
    def test_actual_worker_uses_resident_models_and_isolates_benchmark_writes(self):
        import os
        import threading
        from multiprocessing.connection import Listener
        import numpy as np
        import cv2
        from model_service import ModelServer, _reply
        from aigate.optimization import run_sample
        class Detector:
            def predict(self,frame,**options):
                h,w=frame.shape[:2]
                box=SimpleNamespace(cls=np.array(0),conf=np.array(.95),xyxy=[np.array([0,0,w,h])])
                return [SimpleNamespace(boxes=[box],names={0:'car'})]
        result=SimpleNamespace(vertices=[[12,25],[12,78],[148,78],[148,25]],area='品川',
                               class_number='330',kana='さ',number='1234',score=.99,
                               area_score=.99,class_number_score=.99,kana_score=.99,number_score=.99)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);image=root/'source.jpg';cv2.imwrite(str(image),np.zeros((80,160,3),np.uint8))
            address=str(root/'model.sock')
            server=ModelServer(root,loader=lambda config:(Detector(),None,[('lipla-jp',SimpleNamespace(model=lambda frame:[result]))]))
            server.reload(dict(warmup=False))
            try:
                listener=Listener(address,family='AF_UNIX',authkey=b'benchmark-key')
            except PermissionError:
                self.skipTest('Unix socket unavailable')
            with listener:
                def reply():
                    for _ in range(4):
                        _reply(listener.accept(),server)
                thread=threading.Thread(target=reply,daemon=True);thread.start()
                service=SimpleNamespace(address=address,key='benchmark-key')
                row=run_sample(dict(source=str(image),box=[12,25,148,78],truth='品川|330|さ|1234'),640,service)
                thread.join(timeout=2)
            self.assertTrue(row['detected']);self.assertTrue(row['recognized'])
            self.assertGreater(row['frame_ms'],0)
            self.assertGreater(row['storage_ms'],0)
            self.assertGreater(row['notification_ms'],0)
            self.assertFalse((root/'gate.db').exists())

    def test_compute_lease_releases_and_prevents_competing_training(self):
        from resource_lock import ResourceLease
        with tempfile.TemporaryDirectory() as root:
            first,second=ResourceLease(root),ResourceLease(root)
            self.assertTrue(first.acquire());self.assertFalse(second.acquire())
            first.release();self.assertTrue(second.acquire());second.release()


class ValidationSourceTests(unittest.TestCase):
    def test_only_validation_full_frames_are_used_and_boxes_map_to_full_input(self):
        import cv2
        import numpy as np
        import events
        import ocr_learning
        from aigate.optimization import evaluation_samples
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);ocr_learning.initialize(root);(root/'images').mkdir()
            from app import open_database
            open_database(root/'gate.db').close()
            rows=[]
            with events.connection(root) as db:
                for index in range(3):
                    identifier=str(index);source=root/'images'/(identifier+'.jpg');cv2.imwrite(str(source),np.zeros((60,100,3),np.uint8))
                    record=dict(id=identifier,image_path=str(source),bbox=[40,30,140,90],plate_candidates=[dict(bbox_in_vehicle=[10,20,90,50])])
                    # Only the source lookups and partitions are relevant in this fixture.
                    db.execute('INSERT INTO observations (id,run_id,processed_at,frame_index,vehicle_type,confidence,details_json) VALUES (?,?,?,?,?,?,?)',
                               (identifier,'test','2026-10-03',index,'car',.99,json.dumps(record)))
                    ok,image=cv2.imencode('.jpg',np.zeros((120,180,3),np.uint8))
                    db.execute('INSERT INTO ocr_evaluation_frames VALUES (?,?,?)',(identifier,image.tobytes(),'now'))
                    rows.append(dict(observation_id=identifier,candidate_index=0,partition='validation' if index<2 else 'test',
                                     plate_key='key-'+identifier,source='automatic'))
            with patch('ocr_learning.dataset_snapshot',return_value=rows):samples=evaluation_samples(root)
            self.assertEqual(len(samples),2)
            self.assertTrue(all(x['scope']=='full_frame' for x in samples))
            self.assertEqual(samples[0]['box'],[50,50,130,80])
            self.assertEqual({x['truth'] for x in samples},{'key-0','key-1'})

    def test_full_frame_storage_requires_98_percent_and_is_removed_with_teacher(self):
        import sqlite3
        import numpy as np
        import cv2
        from ocr_learning import save_evaluation_frame
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE ocr_samples (observation_id TEXT)');db.execute("INSERT INTO ocr_samples VALUES ('source')")
            record=dict(id='source',plate_candidates=[dict(fields={'region':'品川'},ocr_backend='lipla-native',confidence=.979)])
            save_evaluation_frame(db,record,np.zeros((30,40,3),np.uint8),cv2)
            self.assertEqual(db.execute('SELECT count(*) FROM ocr_evaluation_frames').fetchone()[0],0)
            record['plate_candidates'][0]['confidence']=.98
            save_evaluation_frame(db,record,np.zeros((30,40,3),np.uint8),cv2)
            self.assertEqual(db.execute('SELECT count(*) FROM ocr_evaluation_frames').fetchone()[0],1)
            db.execute('DELETE FROM ocr_samples')
            save_evaluation_frame(db,record,np.zeros((30,40,3),np.uint8),cv2)
            self.assertEqual(db.execute('SELECT count(*) FROM ocr_evaluation_frames').fetchone()[0],0)
