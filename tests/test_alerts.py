import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import cv2
import numpy as np
import events
from evidence import EvidenceRecorder
from web import JobManager, create_app

FIELDS=dict(region='品川',category='３００',kana='あ',serial='1234')

def observation(**changes):
    value=dict(id='observation',run_id='run',vehicle_type='car',
               plate_candidates=[dict(fields=FIELDS,confidence=.9)])
    value.update(changes)
    return value

class AlertTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.env=patch.dict('os.environ',{'GATE_EMAIL_FROM':'','GATE_EMAIL_TO':'','GATE_S3_BUCKET':''})
        self.env.start(); events.initialize(self.root)
    def tearDown(self):
        self.env.stop(); self.tmp.cleanup()
    def row(self,identifier):
        with events.connection(self.root) as db:
            return dict(db.execute('SELECT * FROM alerts WHERE id=?',(identifier,)).fetchone())
    def register(self,**changes):
        data=dict(FIELDS,vehicle_type='car',label='来訪車'); data.update(changes)
        return events.register_vehicle(self.root,data)
    def test_pair_matching_and_live_registry_update(self):
        vehicle=self.register()
        self.assertIsNone(events.evaluate(self.root,observation(),0))
        mismatch=events.evaluate(self.root,observation(vehicle_type='truck'),1)
        self.assertEqual(self.row(mismatch)['reason'],'type_mismatch')
        events.register_vehicle(self.root,dict(FIELDS,vehicle_type='car',watch=True,label='指定'),vehicle)
        watched=events.evaluate(self.root,observation(),2)
        self.assertEqual(self.row(watched)['reason'],'watch')
        events.register_vehicle(self.root,dict(FIELDS,vehicle_type='car',enabled=False),vehicle)
        self.assertEqual(json.loads(self.row(watched)['registry_json'])['label'],'指定')
        self.assertEqual(self.row(events.evaluate(self.root,observation(),3))['reason'],'unknown')
    def test_normalization_and_duplicate_validation(self):
        self.assertEqual(events.plate_key(FIELDS),'品川|300|あ|1234')
        for serial in ['１２３４', '12-34', '・・12', '12345']:
            with self.subTest(serial=serial), self.assertRaises(ValueError):
                events.plate_key(dict(FIELDS, serial=serial))
        for kana in ['ぁ', 'が', 'ぱ', 'お', 'し', 'へ', 'ん']:
            with self.subTest(kana=kana), self.assertRaises(ValueError):
                events.plate_key(dict(FIELDS, kana=kana))
        self.register()
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError): self.register(serial='1234',category='300')
        for changes in [dict(serial='0000'),dict(kana='ABC'),dict(watch='false')]:
            with self.assertRaises(ValueError): self.register(**changes)
    def test_unknown_unreadable_and_persistent_cooldown(self):
        identifier=events.evaluate(self.root,observation(),0)
        self.assertEqual(self.row(identifier)['reason'],'unknown')
        events.initialize(self.root)
        self.assertIsNone(events.evaluate(self.root,observation(),59.9))
        self.assertIsNotNone(events.evaluate(self.root,observation(),60))
        self.assertIsNotNone(events.evaluate(self.root,observation(run_id='other'),0))
        low=observation(plate_candidates=[dict(fields=FIELDS,confidence=.59)])
        self.assertEqual(self.row(events.evaluate(self.root,low,1))['reason'],'unreadable')
        self.assertIsNone(events.evaluate(self.root,observation(plate_candidates=[]),2))
    @patch.dict('os.environ',{'GATE_EMAIL_FROM':'from@example.test','GATE_EMAIL_TO':'one@example.test,two@example.test','GATE_S3_BUCKET':'evidence-test'})
    def test_email_and_s3_success(self):
        identifier=events.evaluate(self.root,observation(),0)
        path=self.root/'events'/'test.jpg'; path.parent.mkdir(); path.write_bytes(b'image')
        events.set_media(self.root,identifier,'ready',path)
        client=Mock(); client.send_email.return_value={'MessageId':'ses-1'}
        events.Dispatcher(self.root,lambda service:client).tick()
        row=self.row(identifier)
        self.assertEqual((row['email_status'],row['s3_status']),('sent','uploaded'))
        payload=client.send_email.call_args.kwargs
        self.assertEqual(len(payload['Destination']['ToAddresses']),2)
        self.assertIn('品川 300 あ 1234',payload['Message']['Body']['Text']['Data'])
        self.assertEqual(client.upload_file.call_args.kwargs['ExtraArgs'],{'ServerSideEncryption':'AES256'})
    @patch.dict('os.environ',{'GATE_EMAIL_FROM':'from@example.test','GATE_EMAIL_TO':'to@example.test'})
    def test_email_retries_failure_and_crash_recovery(self):
        identifier=events.evaluate(self.root,observation(),0)
        client=Mock(); client.send_email.side_effect=RuntimeError('provider error')
        dispatcher=events.Dispatcher(self.root,lambda service:client)
        for attempt in range(1,6):
            dispatcher.tick(); row=self.row(identifier)
            self.assertEqual(row['email_attempts'],attempt)
            self.assertEqual(row['email_status'],'failed' if attempt==5 else 'retry')
            with events.connection(self.root) as db: db.execute('UPDATE alerts SET email_next=0')
        with events.connection(self.root) as db: db.execute("UPDATE alerts SET email_status='sending'")
        events.Dispatcher(self.root,lambda service:client)
        self.assertEqual(self.row(identifier)['email_status'],'pending')
    @patch.dict('os.environ',{'GATE_S3_BUCKET':'test'})
    def test_s3_retries_and_failed_recording(self):
        identifier=events.evaluate(self.root,observation(),0)
        path=self.root/'events'/'test.jpg'; path.parent.mkdir(); path.write_bytes(b'jpg')
        events.set_media(self.root,identifier,'partial',path)
        client=Mock(); client.upload_file.side_effect=OSError('offline')
        dispatcher=events.Dispatcher(self.root,lambda service:client)
        for attempt in range(1,6):
            dispatcher.tick()
            self.assertEqual(self.row(identifier)['s3_status'],'failed' if attempt==5 else 'retry')
            with events.connection(self.root) as db: db.execute('UPDATE alerts SET s3_next=0')
        other=events.evaluate(self.root,observation(),60)
        events.set_media(self.root,other,'failed',error='disk full')
        self.assertEqual(self.row(other)['s3_status'],'failed')
    def test_real_clip_pre_post_and_partial(self):
        frame=np.zeros((100,160,3),dtype=np.uint8)
        recorder=EvidenceRecorder(self.root)
        for i in range(21): recorder.feed(frame,i/5)
        identifier=events.evaluate(self.root,observation(),4)
        recorder.trigger(identifier,4,frame)
        for i in range(21,46): recorder.feed(frame,i/5)
        row=self.row(identifier)
        self.assertEqual(row['media_status'],'ready')
        self.assertAlmostEqual(row['media_start'],1); self.assertAlmostEqual(row['media_end'],9)
        video=cv2.VideoCapture(row['media_path'])
        self.assertTrue(video.read()[0]); self.assertEqual(int(video.get(cv2.CAP_PROP_FRAME_COUNT)),41); video.release()
        other=events.evaluate(self.root,observation(run_id='other'),9)
        recorder.trigger(other,9,frame); recorder.close()
        self.assertEqual(self.row(other)['media_status'],'partial')
    def test_still_and_recording_limit(self):
        frame=np.zeros((100,160,3),dtype=np.uint8)
        identifier=events.evaluate(self.root,observation(),0)
        recorder=EvidenceRecorder(self.root,still=True); recorder.trigger(identifier,0,frame); recorder.close()
        self.assertIsNotNone(cv2.imread(self.row(identifier)['media_path']))
        recorder=EvidenceRecorder(self.root); recorder.pending={str(i):None for i in range(16)}
        other=events.evaluate(self.root,observation(),60); recorder.trigger(other,60,frame)
        self.assertEqual(self.row(other)['media_status'],'failed')
    def test_web_registration_alert_ack_media_auth(self):
        manager=JobManager(self.root); app=create_app(manager=manager,password='secret'); app.testing=True
        client=app.test_client(); auth=('admin','secret')
        self.assertEqual(client.get('/healthz').status_code,200)
        self.assertEqual(client.get('/api/alerts').status_code,401)
        client.get('/',auth=auth)
        with client.session_transaction() as session: headers={'X-CSRF-Token':session['csrf']}
        data=dict(FIELDS,vehicle_type='car',watch=True)
        self.assertEqual(client.post('/api/vehicles',json=data,auth=auth).status_code,403)
        response=client.post('/api/vehicles',json=data,auth=auth,headers=headers)
        self.assertEqual(response.status_code,201)
        self.assertEqual(client.post('/api/vehicles',json=data,auth=auth,headers=headers).status_code,400)
        self.assertEqual(client.post('/api/vehicles',json=[1],auth=auth,headers=headers).status_code,400)
        identifier=events.evaluate(self.root,observation(),0)
        alerts=client.get('/api/alerts',auth=auth).json
        self.assertEqual(alerts['unread'],1); self.assertNotIn('media_path',alerts['items'][0])
        client.post('/api/alerts/'+identifier+'/ack',headers=headers,auth=auth)
        self.assertEqual(client.get('/api/alerts',auth=auth).json['unread'],0)
        path=self.root/'events'/'a.mp4'; path.parent.mkdir(); path.write_bytes(b'0123456789')
        events.set_media(self.root,identifier,'ready',path)
        with client.get('/api/alerts/'+identifier+'/media',auth=auth,headers={'Range':'bytes=0-3'}) as result:
            self.assertEqual(result.status_code,206); self.assertEqual(result.data,b'0123')
        events.set_media(self.root,identifier,'ready',self.root/'gate.db')
        self.assertEqual(client.get('/api/alerts/'+identifier+'/media',auth=auth).status_code,404)
        with patch.dict('os.environ',{'GATE_EMAIL_FROM':'a','GATE_EMAIL_TO':'b'}):
            self.assertEqual(client.post('/api/alerts/'+identifier+'/retry-email',auth=auth,headers=headers).status_code,200)
            self.assertEqual(client.post('/api/alerts/'+identifier+'/retry-email',auth=auth,headers=headers).status_code,409)
        manager.shutdown()
