import io
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
        self.processes[0].done.set(); self.idle()
        self.assertEqual(JobManager(self.tmp.name).list_jobs()[0]['status'],'completed')
    def test_camera_stop_and_busy(self):
        r=self.camera(); self.assertEqual(r.status_code,201)
        self.assertEqual(self.upload().status_code,409)
        self.assertEqual(self.client.post('/api/jobs/'+r.json['id']+'/stop',headers=self.headers).status_code,200)
        self.idle(); self.assertEqual(self.manager.list_jobs()[0]['status'],'stopped')
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
            for i in range(31):
                save_observation(db,dict(id=str(i),processed_at='2026-09-18T00:00:00+00:00',run_id=job,
                    frame_index=i,media_ms=0,vehicle_type='car',confidence=.8,image_path=str(image),plate_candidates=[]))
        data=self.client.get('/api/observations?job='+job+'&vehicle=car&page=2').json
        self.assertEqual(data['total'],31); self.assertEqual(len(data['items']),1)
        self.assertNotIn('image_path',data['items'][0])
        with self.client.get('/api/observations/0/image') as response:
            self.assertEqual(response.data,b'crop')
        self.assertEqual(self.client.get('/api/observations?vehicle=truck').json['total'],0)
        self.assertEqual(self.client.get('/api/jobs/bad/preview').status_code,404)
    def test_failure(self):
        self.upload(); self.processes[0].code=1; self.processes[0].done.set(); self.idle()
        self.assertEqual(self.manager.list_jobs()[0]['status'],'failed')
    def test_restart_marks_interrupted(self):
        with self.manager.connect() as db:
            db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?)',('a'*32,'camera','USB 0','running','2026-09-18',None,None,1,.4))
        self.assertEqual(JobManager(self.tmp.name).list_jobs()[0]['status'],'interrupted')
