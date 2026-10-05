import io
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

import app
from aigate.speed_experiment import RecognitionScheduler, configuration
from aigate.speed_benchmark import evaluate, summarize, validate_truth
from aigate.speed_models import conditional_class
from web import create_app, JobManager


FIELDS = dict(region='品川', category='300', kana='あ', serial='1234')
PLATE = dict(bbox_in_vehicle=[20,50,70,75], confidence=.99, fields=FIELDS,
             text='品川 300 あ 1234', ocr_backend='lipla-native')


class TrackingTests(unittest.TestCase):
    def test_stability_requires_two_reads_and_periodic_recheck(self):
        scheduler = RecognitionScheduler(configuration('tracking'))
        image = np.full((100,100,3),125,np.uint8)
        track = scheduler.assign([dict(box=[0,0,100,100],label='car')],0)[0]
        self.assertTrue(scheduler.should_read(track,image,0)[0])
        scheduler.remember(track,[PLATE],image,0)
        self.assertFalse(scheduler.should_read(track,image,.1)[0])
        self.assertTrue(scheduler.should_read(track,image,.2)[0])
        scheduler.remember(track,[PLATE],image,.2)
        self.assertFalse(scheduler.should_read(track,image,.7)[0])
        self.assertTrue(scheduler.should_read(track,image,1.21)[0])

    def test_ambiguous_crossing_cannot_reuse_other_vehicle(self):
        scheduler = RecognitionScheduler(configuration('tracking'))
        old = scheduler.assign([dict(box=[0,0,100,100],label='car'),
                                dict(box=[10,0,110,100],label='car')],0)
        new = scheduler.assign([dict(box=[5,0,105,100],label='car')],.1)[0]
        self.assertNotIn(new.id,[t.id for t in old])

    def test_track_expires_after_gap_and_appearance_resets(self):
        scheduler = RecognitionScheduler(configuration('tracking'))
        track = scheduler.assign([dict(box=[0,0,100,100],label='car')],0)[0]
        image = np.full((100,100,3),100,np.uint8)
        scheduler.should_read(track,image,0)
        scheduler.remember(track,[PLATE],image,0)
        self.assertTrue(scheduler.should_read(track,np.full_like(image,200),.05)[0])
        new = scheduler.assign([dict(box=[0,0,100,100],label='car')],2)[0]
        self.assertNotEqual(new.id,track.id)

    def test_better_frame_triggers_early_recognition(self):
        scheduler = RecognitionScheduler(configuration('quality'))
        track = scheduler.assign([dict(box=[0,0,100,100],label='car')],0)[0]
        image = np.full((100,100,3),125,np.uint8)
        scheduler.remember(track,[PLATE],image,0)
        checker = np.indices((100,100)).sum(axis=0)%2*200+25
        sharp = np.repeat(checker[:,:,None],3,axis=2).astype(np.uint8)
        self.assertTrue(scheduler.should_read(track,sharp,.05)[0])

    def test_real_worker_reduces_calls_without_registering_cached_images(self):
        outputs = []
        for stage in ('baseline','tracking'):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                frame = np.full((120,120,3),125,np.uint8)
                box = types.SimpleNamespace(cls=np.array(0),conf=np.array(.99),
                                            xyxy=np.array([[0,0,100,100]]))
                model = Mock()
                model.predict.return_value=[types.SimpleNamespace(boxes=[box],names={0:'car'})]
                source = root/'input.mp4'
                trace = root/'trace.jsonl'
                argv=['app.py','--source',str(source),'--source-kind','file','--output',tmp,
                      '--every','1','--run-id','test','--recognition-trace',str(trace)]
                def video(*args,**kwargs):
                    for i in range(10): yield i,i*100,frame.copy()
                with patch.dict(sys.modules,{'ultralytics':types.SimpleNamespace(YOLO=Mock())}), \
                     patch.dict(os.environ,{'GATE_SPEED_EXPERIMENT':json.dumps(configuration(stage)),
                                            'GATE_VEHICLE_AI_ENABLED':'0','GATE_MODEL_SERVICE_SOCKET':''}), \
                     patch.object(sys,'argv',argv), patch('builtins.print'), \
                     patch('app.initialize_models',return_value=(model,None,[])), \
                     patch('app.frames',side_effect=video), \
                     patch('app.read_plate',return_value=[PLATE]) as read, \
                     patch('ocr_learning.queue_observation') as teacher, \
                     patch('ocr_learning.save_evaluation_frame'):
                    app.main()
                rows=[json.loads(s) for s in trace.read_text().splitlines()]
                outputs.append(read.call_count)
                if stage=='tracking':
                    self.assertTrue(any(v['reused'] for row in rows for v in row['vehicles']))
                    self.assertLess(teacher.call_count,10)
                    self.assertEqual([row['frame_index'] for row in rows],list(range(10)))
        self.assertEqual(outputs,[10,2])


class EvaluationTests(unittest.TestCase):
    def test_truth_rejects_duplicates_and_invalid_box(self):
        with self.assertRaises(ValueError):
            validate_truth({'frames':[{'frame_index':0,'plates':[]},{'frame_index':0,'plates':[]}]})
        with self.assertRaises(ValueError):
            validate_truth({'frames':[{'frame_index':0,'plates':[{'box':[0,0,-1,2],'fields':FIELDS}]}]})

    def test_one_to_one_matching_and_reused_metrics(self):
        truth=validate_truth({'frames':[{'frame_index':0,'plates':[{'box':[20,50,70,75],'fields':FIELDS}]}]})
        trace=[dict(frame_index=0,vehicles=[dict(bbox=[0,0,100,100],reused=True,candidates=[PLATE,PLATE])])]
        scores=evaluate(trace,truth)
        self.assertEqual(scores['exact_plate_accuracy'],1)
        self.assertEqual(scores['fresh_detection_fraction'],0)
        self.assertEqual(scores['false_positive_plates'],1)
        rows=[dict(frame_ms=100,plate_recognition_ms=80,detection_ms=10,resources={},ocr_calls=1)]
        self.assertIsNone(summarize(rows,trace)['accuracy'])

    def test_speed_api_requires_csrf_and_model_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager=JobManager(tmp)
            application=create_app(manager=manager,password='')
            client=application.test_client()
            client.get('/')
            with client.session_transaction() as s: headers={'X-CSRF-Token':s['csrf']}
            self.assertEqual(client.post('/api/system/speed-experiment/cancel').status_code,403)
            self.assertEqual(client.get('/api/system/speed-experiment').json['state'],'idle')
            response=client.post('/api/system/speed-experiment',headers=headers,
                                 data={'video':(io.BytesIO(b'video'),'test.mp4')})
            self.assertEqual(response.status_code,400)
            manager.shutdown()


class ConditionalTests(unittest.TestCase):
    def make_model(self, score):
        result=types.SimpleNamespace(area='品川',class_number='300',kana='あ',number=1234,
               area_score=score,class_number_score=score,kana_score=score,number_score=score)
        class Base:
            def _recognize_detection(self,*args): return 'local-standard'
            def parse_ocr_result(self,*args,**kwargs):
                return result if not kwargs.get('field_candidates') else types.SimpleNamespace(
                    **{**result.__dict__,'area_score':.99})
        module=types.SimpleNamespace(Recognizer=Base,as_bgr=lambda x:x,pad_for_ocr=lambda x:(x,0))
        with patch.dict(sys.modules,{'lipla.core.license_plate_recognizer':module}), \
             patch('aigate.speed_models.version',return_value='0.4.1'):
            model=conditional_class()()
        model.plate_normalizer=types.SimpleNamespace(normalize=Mock(return_value=np.zeros((20,40,3),np.uint8)))
        model.ocr_model=Mock(return_value='ocr')
        model.field_recognizer=types.SimpleNamespace(recognize=Mock(return_value={'area':['corrected']}))
        return model

    def test_high_confidence_skips_field_recognition(self):
        model=self.make_model(.99)
        result=model._recognize_detection(np.zeros((20,40,3),np.uint8),[0,0,0,20,40,20,40,0],.9,'ordinary')
        self.assertEqual(result.number,1234)
        model.field_recognizer.recognize.assert_not_called()
        self.assertEqual(model.ocr_model.call_count,1)

    def test_low_confidence_uses_existing_ocr_for_fallback(self):
        model=self.make_model(.8)
        result=model._recognize_detection(np.zeros((20,40,3),np.uint8),[0,0,0,20,40,20,40,0],.9,'ordinary')
        model.field_recognizer.recognize.assert_called_once()
        self.assertEqual(model.ocr_model.call_count,1)
        self.assertEqual(result.area_score,.99)
        self.assertEqual(model._recognize_detection(None,None,.9,'local'),'local-standard')

    def test_unknown_version_is_not_silently_adapted(self):
        with patch('aigate.speed_models.version',return_value='0.5.0'):
            with self.assertRaises(RuntimeError): conditional_class()

class BenchmarkLifecycleTests(unittest.TestCase):
    def test_all_stages_restore_models_and_never_apply_settings(self):
        import threading
        from aigate.speed_benchmark import SpeedBenchmark
        with tempfile.TemporaryDirectory() as tmp:
            original=dict(imgsz=960,environment={})
            service=types.SimpleNamespace(reload=Mock())
            jobs=types.SimpleNamespace(root=Path(tmp),lock=threading.RLock(),
                optimizing=True,model_service=service,model_configuration=lambda:original)
            benchmark=SpeedBenchmark(jobs)
            benchmark.lease=Mock()
            row=dict(frame_ms=100,detection_ms=10,plate_recognition_ms=80,
                     ocr_calls=1,resources={})
            truth=validate_truth({'frames':[{'frame_index':0,'plates':[{'box':[20,50,70,75],'fields':FIELDS}]}]})
            trace=dict(frame_index=0,vehicles=[dict(bbox=[0,0,100,100],reused=False,candidates=[PLATE])])
            report=dict(state='running',results=[])
            with patch('aigate.speed_benchmark.worker',side_effect=lambda *a:([row],[dict(trace)])), \
                 patch('importlib.util.find_spec',return_value=object()), \
                 patch('importlib.metadata.version',return_value='0.4.1'):
                benchmark.run(Path(tmp)/'test.mp4',Path(tmp),truth,1,1,report)
            self.assertEqual(report['state'],'completed')
            self.assertEqual(len(report['results']),8)
            self.assertFalse(report['applied'])
            self.assertFalse(jobs.optimizing)
            self.assertEqual(json.loads(service.reload.call_args.args[0]['environment']['GATE_SPEED_EXPERIMENT'])['stage'],'baseline')
            benchmark.lease.release.assert_called_once()

    def test_failure_still_restores_models_and_releases_resources(self):
        import threading
        from aigate.speed_benchmark import SpeedBenchmark
        with tempfile.TemporaryDirectory() as tmp:
            service=types.SimpleNamespace(reload=Mock())
            jobs=types.SimpleNamespace(root=Path(tmp),lock=threading.RLock(),optimizing=True,
                model_service=service,model_configuration=lambda:dict(imgsz=960,environment={}))
            benchmark=SpeedBenchmark(jobs)
            benchmark.lease=Mock()
            report=dict(state='running',results=[])
            with patch('aigate.speed_benchmark.worker',side_effect=RuntimeError('failed')):
                benchmark.run(Path(tmp)/'test.mp4',Path(tmp),None,1,1,report)
            self.assertEqual(report['state'],'failed')
            self.assertFalse(jobs.optimizing)
            benchmark.lease.release.assert_called_once()
            self.assertEqual(service.reload.call_count,2)

class NativeDetectionTests(unittest.TestCase):
    def test_detection_without_ocr_is_still_measured(self):
        from lipla_pipeline import LiplaPlatePipeline
        native=types.SimpleNamespace(kpts=[[5,10,5,30,45,30,45,10]],
                                     scores=[.8],class_names=['ordinary'])
        recognizer=Mock(return_value=[])
        recognizer.pose_model=types.SimpleNamespace(last_result=native)
        recognizer.fast_count=recognizer.fallback_count=0
        upstream=types.SimpleNamespace(suppress_duplicate_detections=lambda items:items)
        with patch.dict(sys.modules,{'lipla.core.license_plate_recognizer':upstream}):
            candidates,report=LiplaPlatePipeline(recognizer).run(np.zeros((50,60,3),np.uint8))
        self.assertEqual(candidates,[])
        self.assertEqual(report['proposals'][0]['bbox_in_vehicle'],[5,10,45,30])
        truth=validate_truth({'frames':[{'frame_index':0,'plates':[{'box':[5,10,45,30],'fields':FIELDS}]}]})
        scores=evaluate([dict(frame_index=0,vehicles=[dict(bbox=[0,0,60,50],reused=False,
                        proposals=report['proposals'],candidates=[])])],truth)
        self.assertEqual(scores['continuity_detection_recall'],1)
        self.assertEqual(scores['exact_plate_accuracy'],0)
