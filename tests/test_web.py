import io
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from app import save_observation
from web import create_app, JobManager

class Process:
    def __init__(self): self.done=threading.Event(); self.code=0
    def wait(self,timeout=None):
        if not self.done.wait(timeout): raise subprocess.TimeoutExpired('test',timeout)
        return self.code
    def terminate(self): self.done.set()
    def kill(self): self.done.set()

class WebTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.commands=[]; self.processes=[]
        def launch(command,**kwargs):
            self.commands.append(command); p=Process(); self.processes.append(p); return p
        self.manager=JobManager(self.tmp.name,popen=launch)
        self.app=create_app(manager=self.manager,password=''); self.app.testing=True
        self.client=self.app.test_client(); self.client.get('/')
        with self.client.session_transaction() as s: self.headers={'X-CSRF-Token':s['csrf']}
    def idle(self):
        deadline=time.monotonic()+2
        while self.manager.active_id and time.monotonic()<deadline: time.sleep(.01)
        self.assertIsNone(self.manager.active_id)
    def tearDown(self):
        self.manager.shutdown(); self.idle(); self.tmp.cleanup()
    def upload(self,name='test.jpg'):
        return self.client.post('/api/jobs',data={'kind':'file','file':(io.BytesIO(b'image'),name)},headers=self.headers)
    def camera(self,source='0',**extra):
        return self.client.post('/api/jobs',data={'kind':'camera','source':source,**extra},headers=self.headers)
    def test_file_completion_persistence(self):
        r=self.upload('../../日本語.jpg'); self.assertEqual(r.status_code,201)
        job=r.json['id']; self.assertTrue((self.manager.folder(job)/'input.jpg').is_file())
        cmd=self.commands[0]; self.assertEqual(cmd[cmd.index('--source-kind')+1],'file')
        self.assertEqual(cmd[cmd.index('--vehicle-threshold')+1],'0.001')
        self.assertEqual(cmd[cmd.index('--ocr-threshold')+1],'0.0')
        self.processes[0].done.set(); self.idle()
        self.assertEqual(JobManager(self.tmp.name).list_jobs()[0]['status'],'completed')
    def test_multiple_cameras_stop_independently_and_file_is_busy(self):
        r=self.camera('0'); self.assertEqual(r.status_code,201)
        second=self.camera('1');self.assertEqual(second.status_code,201)
        jobs=self.client.get('/api/jobs').json
        self.assertEqual(set(jobs['active_ids']),{r.json['id'],second.json['id']})
        self.assertEqual(jobs['max_concurrent'],4)
        self.assertEqual(self.camera('0').status_code,409)
        self.assertEqual(self.upload().status_code,409)
        self.assertEqual(self.client.post('/api/jobs/'+r.json['id']+'/stop',headers=self.headers).status_code,200)
        deadline=time.monotonic()+2
        while r.json['id'] in self.manager.active_ids and time.monotonic()<deadline: time.sleep(.01)
        self.assertNotIn(r.json['id'],self.manager.active_ids)
        self.assertIn(second.json['id'],self.manager.active_ids)
        self.assertEqual(self.client.post('/api/jobs/'+second.json['id']+'/stop',headers=self.headers).status_code,200)
        self.idle()
        self.assertTrue(all(job['status']=='stopped' for job in self.manager.list_jobs()[:2]))

    def test_camera_concurrency_limit(self):
        manager=JobManager(self.tmp.name,popen=lambda *a,**k:Process(),max_cameras=2)
        one=manager.start('camera','0','USB 0',1,.4)
        two=manager.start('camera','1','USB 1',1,.4)
        with self.assertRaisesRegex(Exception,'上限'):
            manager.start('camera','2','USB 2',1,.4)
        self.assertEqual(set(manager.active_ids),{one,two})
        manager.shutdown()

    def test_browser_camera_frame_endpoint(self):
        response=self.client.post('/api/jobs',data={'kind':'browser','every':'1','confidence':'0.4'},headers=self.headers)
        self.assertEqual(response.status_code,201)
        job=response.json['id']
        self.assertEqual(self.client.post('/api/jobs/'+job+'/browser-frame',data=b'x'*100,content_type='image/jpeg',headers=self.headers).status_code,200)
        self.processes[0].done.set(); self.idle()
        self.assertEqual(self.client.post('/api/jobs/'+job+'/browser-frame',data=b'jpeg',content_type='image/jpeg',headers=self.headers).status_code,409)
    def test_rtsp_redaction(self):
        self.assertEqual(self.camera('rtsp://user:secret@192.0.2.1/live').status_code,201)
        text=self.client.get('/api/jobs').get_data(as_text=True)
        self.assertNotIn('secret',text); self.assertNotIn('192.0.2.1',text)
    def test_validation_csrf_size(self):
        self.assertEqual(self.client.post('/api/jobs').status_code,403)
        self.assertEqual(self.upload('evil.html').status_code,400)
        for source in ['/etc/passwd','https://example.com','rtsp://[']:
            self.assertEqual(self.camera(source).status_code,400)
        self.assertEqual(self.camera(every='0').status_code,400)
        self.assertEqual(self.camera(confidence='nan').status_code,400)
        self.app.config['MAX_CONTENT_LENGTH']=10
        self.assertEqual(self.upload().status_code,413)
    def test_auth_and_host(self):
        self.assertEqual(self.client.get('/api/jobs',environ_base={'REMOTE_ADDR':'192.0.2.10'}).status_code,403)
        self.assertEqual(self.client.get('/api/jobs',base_url='http://attacker.example').status_code,403)
        c=create_app(manager=self.manager,password='test-pass').test_client()
        self.assertEqual(c.get('/').status_code,401)
        self.assertEqual(c.get('/',auth=('admin','wrong')).status_code,401)
        self.assertEqual(c.get('/',auth=('admin','test-pass')).status_code,200)
    def test_progress_history_paging_image(self):
        job=self.upload().json['id']; folder=self.manager.folder(job)
        (folder/'progress.json').write_text(json.dumps({'frames_processed':3}))
        (folder/'preview.jpg').write_bytes(b'preview')
        self.assertEqual(self.client.get('/api/jobs').json['jobs'][0]['progress']['frames_processed'],3)
        self.assertEqual(self.client.get('/api/jobs/'+job+'/preview').data,b'preview')
        image=self.manager.root/'images'/'test.jpg'; image.parent.mkdir(); image.write_bytes(b'crop')
        with self.manager.connect() as db:
            fields=dict(region='品川',category='300',kana='あ',serial='1234')
            for i in range(31):
                save_observation(db,dict(id=str(i),processed_at='2026-09-18T00:00:00+00:00',run_id=job,
                    frame_index=i,media_ms=0,vehicle_type='car',confidence=.8,image_path=str(image),
                    result_eligible=True,plate_candidates=[dict(fields=fields,confidence=.7)]))
            save_observation(db,dict(id='hidden',processed_at='2026-09-18T00:00:00+00:00',run_id=job,
                frame_index=32,media_ms=0,vehicle_type='car',confidence=.8,image_path=str(image),
                result_eligible=False,plate_candidates=[dict(fields=fields,confidence=.699)]))
        data=self.client.get('/api/observations?job='+job+'&vehicle=car&page=2').json
        self.assertEqual(data['total'],31); self.assertEqual(len(data['items']),1)
        self.assertNotIn('image_path',data['items'][0])
        with self.client.get('/api/observations/0/image') as response:
            self.assertEqual(response.data,b'crop')
        self.assertEqual(self.client.get('/api/observations?vehicle=truck').json['total'],0)
        self.assertEqual(self.client.get('/api/observations?job='+job).json['total'],31)
        self.assertEqual(self.client.get('/api/jobs/bad/preview').status_code,404)
    def test_failure(self):
        self.upload(); self.processes[0].code=1; self.processes[0].done.set(); self.idle()
        self.assertEqual(self.manager.list_jobs()[0]['status'],'failed')

    def test_bulk_history_deletion_scopes_files_and_retained_data(self):
        import events
        job=self.upload().json['id'];folder=self.manager.folder(job)
        endpoint='/api/history'
        body={'scopes':['processing','recognition'],'confirmation':'DELETE HISTORY'}
        self.assertEqual(self.client.delete(endpoint,json=body).status_code,403)
        self.assertEqual(self.client.delete(endpoint,json=body,headers=self.headers).status_code,409)
        self.processes[0].done.set();self.idle()
        image=self.manager.root/'images'/'history.jpg';image.parent.mkdir();image.write_bytes(b'crop')
        fields=dict(region='品川',category='300',kana='あ',serial='1234')
        record=dict(id='history-source',processed_at='2026-09-19',run_id=job,frame_index=0,
            media_ms=0,vehicle_type='car',confidence=.9,image_path=str(image),result_eligible=True,
            plate_candidates=[dict(fields=fields,confidence=.9)])
        with self.manager.connect() as db:save_observation(db,record)
        events.register_vehicle(self.manager.root,dict(fields,serial='9999',vehicle_type='car'))
        alert=events.evaluate(self.manager.root,record,0)
        sample_image=b'sample'
        with events.connection(self.manager.root) as db:
            db.execute('INSERT INTO ocr_samples VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                ('sample','history-source',0,'品川|300|あ|1234','品川300あ1234','品川300','あ1234',.45,
                 sample_image,hashlib.sha256(sample_image).hexdigest(),events.utc()))
        summary=self.client.get('/api/history/summary').json
        self.assertEqual((summary['processing'],summary['recognition']), (1,1))
        self.assertEqual((summary['vehicles_retained'],summary['alerts_retained'],summary['learning_samples_retained']),(1,1,1))
        self.assertEqual(self.client.delete(endpoint,json={'scopes':['recognition'],'confirmation':'wrong'},headers=self.headers).status_code,400)
        result=self.client.delete(endpoint,json={'scopes':['recognition'],'confirmation':'DELETE HISTORY'},headers=self.headers).json
        self.assertEqual(result['deleted'],{'processing':0,'recognition':1});self.assertEqual(result['file_errors'],[])
        self.assertFalse(image.exists());self.assertTrue(folder.exists())
        self.assertEqual(self.client.get('/api/jobs').json['jobs'][0]['id'],job)
        learning=self.client.get('/api/ocr-learning').json
        self.assertEqual(learning['count'],1);self.assertEqual(learning['samples'][0]['has_observation'],0)
        with events.connection(self.manager.root) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM vehicles').fetchone()[0],1)
            self.assertIsNotNone(db.execute('SELECT id FROM alerts WHERE id=?',(alert,)).fetchone())
        result=self.client.delete(endpoint,json={'scopes':['processing'],'confirmation':'DELETE HISTORY'},headers=self.headers).json
        self.assertEqual(result['deleted'],{'processing':1,'recognition':0})
        self.assertFalse(folder.exists());self.assertEqual(self.client.get('/api/jobs').json['jobs'],[])
        for scopes in ([],['bad'],['processing','processing']):
            self.assertEqual(self.client.delete(endpoint,json={'scopes':scopes,'confirmation':'DELETE HISTORY'},headers=self.headers).status_code,400)

    def test_registration_feed_cursor_and_existing_filter(self):
        import events
        job=self.camera().json['id']
        fields=dict(region='品川',category='300',kana='あ',serial='1234')
        with self.manager.connect() as db:
            for i in range(103):
                save_observation(db,dict(id=str(i),processed_at='2026-09-19',run_id=job,
                    frame_index=i,media_ms=i*1000,vehicle_type='car',confidence=.9,image_path=None,
                    plate_candidates=[] if i==0 else [dict(fields=fields,confidence=.8)]))
        url='/api/registration-feed?job='+job
        data=self.client.get(url).json['items']
        self.assertEqual(len(data),100);self.assertIsNone(data[0]['draft'])
        self.assertEqual(data[1]['draft']['key'],'品川|300|あ|1234')
        rest=self.client.get(url+'&after='+str(data[-1]['cursor'])).json['items']
        self.assertEqual(len(rest),3)
        events.register_vehicle(self.manager.root,dict(fields,vehicle_type='car',enabled=False))
        self.assertIsNone(self.client.get(url+'&after='+str(data[-1]['cursor'])).json['items'][0]['draft'])
        self.assertEqual(self.client.get(url+'&after=-1').status_code,400)
        self.assertEqual(self.client.get('/api/registration-feed?job=unknown').status_code,404)
        secured=create_app(manager=self.manager,password='secret').test_client()
        self.assertEqual(secured.get(url).status_code,401)

    def test_batch_registration_deduplication_and_atomic_rollback(self):
        vehicle=dict(region='品川',category='300',kana='あ',serial='1234',vehicle_type='car',label='元の登録',watch=True)
        first=self.client.post('/api/vehicles',json=vehicle,headers=self.headers).json['id']
        duplicate=dict(vehicle,serial='1234',label='上書きしない',watch=False)
        new=dict(vehicle,serial='5678')
        endpoint='/api/vehicles/batch'
        self.assertEqual(self.client.post(endpoint,json={'items':[new]}).status_code,403)
        response=self.client.post(endpoint,json={'items':[duplicate,new,new]},headers=self.headers)
        self.assertEqual(response.status_code,201)
        self.assertEqual(len(response.json['added']),1);self.assertEqual(len(response.json['skipped']),2)
        vehicles=self.client.get('/api/vehicles').json['items']
        original=next(v for v in vehicles if v['id']==first)
        self.assertEqual(original['label'],'元の登録');self.assertEqual(original['watch'],1)
        response=self.client.post(endpoint,json={'items':[dict(new,serial='9999'),dict(new,kana='invalid')]},headers=self.headers)
        self.assertEqual(response.status_code,400)
        self.assertEqual(len(self.client.get('/api/vehicles').json['items']),2)
        for items in ([],[new]*101,'bad'):
            self.assertEqual(self.client.post(endpoint,json={'items':items},headers=self.headers).status_code,400)

    def test_camera_registration_review_save_and_match(self):
        import events
        fields=dict(region='品川',category='300',kana='あ',serial='1234')
        for kind in ('camera','browser'):
            with self.subTest(kind=kind):
                record=dict(id=kind,processed_at='2026-09-19T00:00:00+00:00',run_id=kind,
                    frame_index=10,media_ms=1000,vehicle_type='car',confidence=.9,
                    image_path=None,plate_candidates=[dict(text='品川 300 あ 12-34',fields=fields,confidence=.85)])
                with self.manager.connect() as db: save_observation(db,record)
                response=self.client.get('/api/observations/'+kind+'/registration')
                self.assertEqual(response.status_code,200)
                draft=response.json
                self.assertNotIn('image_path',draft)
                self.assertEqual(draft['plate_candidates'][0]['fields'],fields)
                self.assertEqual(draft['observation_id'],kind)
        self.assertEqual(self.client.get('/api/vehicles').json['items'],[])
        # User corrects the read before saving. The observation stays unchanged.
        payload=dict(fields,serial='5678',vehicle_type='truck',label='初期登録',watch=True)
        self.assertEqual(self.client.post('/api/vehicles',json=payload).status_code,403)
        self.assertEqual(self.client.post('/api/vehicles',json=payload,headers=self.headers).status_code,201)
        self.assertEqual(self.client.post('/api/vehicles',json=payload,headers=self.headers).status_code,400)
        record.update(vehicle_type='truck',plate_candidates=[dict(fields=payload,confidence=.9)])
        alert=events.evaluate(self.manager.root,record,2)
        with events.connection(self.manager.root) as db:
            self.assertEqual(db.execute('SELECT reason FROM alerts WHERE id=?',(alert,)).fetchone()[0],'watch')
        self.assertEqual(self.client.get('/api/observations/camera/registration').json['plate_candidates'][0]['fields']['serial'],'1234')

    def test_registration_draft_missing_unreadable_and_multiple_candidates(self):
        self.assertEqual(self.client.get('/api/observations/missing/registration').status_code,404)
        record=dict(id='unread',processed_at='2026-09-19T00:00:00+00:00',run_id='camera',
            frame_index=0,media_ms=0,vehicle_type='car',confidence=.8,image_path=None,plate_candidates=[])
        with self.manager.connect() as db: save_observation(db,record)
        self.assertEqual(self.client.get('/api/observations/unread/registration').json['plate_candidates'],[])
        candidates=[dict(text='候補1',fields=None,confidence=.4),dict(text='候補2',fields=None,confidence=.3)]
        record.update(id='multiple',plate_candidates=candidates)
        with self.manager.connect() as db: save_observation(db,record)
        self.assertEqual(self.client.get('/api/observations/multiple/registration').json['plate_candidates'],candidates)
        secured=create_app(manager=self.manager,password='secret').test_client()
        self.assertEqual(secured.get('/api/observations/multiple/registration').status_code,401)
    def test_restart_marks_interrupted(self):
        with self.manager.connect() as db:
            db.execute('''INSERT INTO jobs(id,kind,label,status,created_at,ended_at,error,every,confidence)
                       VALUES (?,?,?,?,?,?,?,?,?)''',('a'*32,'camera','USB 0','running','2026-09-18',None,None,1,.4))
        self.assertEqual(JobManager(self.tmp.name).list_jobs()[0]['status'],'interrupted')
