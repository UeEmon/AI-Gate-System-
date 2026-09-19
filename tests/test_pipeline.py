"""Real OpenCV I/O; fake inference does not assess recognition accuracy."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import cv2
import numpy as np
import app

class FakeModel:
    last_kwargs=None
    def __init__(self,name): pass
    def predict(self,*args,**kwargs):
        FakeModel.last_kwargs=kwargs
        box=types.SimpleNamespace(cls=np.array(0),conf=np.array(.9),xyxy=np.array([[0,0,160,100]]))
        return [types.SimpleNamespace(names={0:'car'},boxes=[box])]

class PipelineTests(unittest.TestCase):
    def test_image_database_preview_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); source=root/'日本語.jpg'
            cv2.imencode('.jpg',np.zeros((100,160,3),dtype=np.uint8))[1].tofile(source)
            reader=types.SimpleNamespace(readtext=lambda *a,**k:[])
            modules={'ultralytics':types.SimpleNamespace(YOLO=FakeModel),'easyocr':types.SimpleNamespace(Reader=lambda *a,**k:reader)}
            argv=['app.py','--source',str(source),'--source-kind','file','--output',str(root),'--run-id','test-run',
                  '--alerts','--save-images','--preview',str(root/'preview.jpg'),'--progress',str(root/'progress.json')]
            with patch.dict(sys.modules,modules),patch.object(sys,'argv',argv),patch('builtins.print'): app.main()
            progress=json.loads((root/'progress.json').read_text())
            self.assertEqual(progress['frames_processed'],1); self.assertEqual(progress['observations'],0)
            self.assertIsNotNone(cv2.imread(str(root/'preview.jpg')))
            db=sqlite3.connect(root/'gate.db'); record=json.loads(db.execute('SELECT details_json FROM observations').fetchone()[0]); db.close()
            self.assertEqual(record['run_id'],'test-run'); self.assertTrue(Path(record['image_path']).is_file())
            self.assertEqual(record['plate_status'],'unreadable')
            self.assertFalse(record['result_eligible'])
            self.assertEqual(record['result_thresholds'], {'vehicle': .8, 'ocr': .7})
            self.assertEqual(FakeModel.last_kwargs['imgsz'],960)
            self.assertEqual(FakeModel.last_kwargs['iou'],.55)
            import events
            with events.connection(root) as db:
                alert=dict(db.execute('SELECT * FROM alerts').fetchone())
            self.assertEqual(alert['reason'],'unreadable')
            self.assertEqual(alert['media_status'],'ready')
            self.assertIsNotNone(cv2.imread(alert['media_path']))
    def test_video_interval_eof(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'clip.avi'; writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'MJPG'),10,(160,100))
            self.assertTrue(writer.isOpened())
            for i in range(5): writer.write(np.full((100,160,3),i*30,dtype=np.uint8))
            writer.release()
            self.assertEqual([r[0] for r in app.frames(str(path),cv2,2)],[0,2,4])
    def test_latest_camera_frame_and_disconnect(self):
        class Capture:
            def __init__(self): self.i=0; self.released=False
            def isOpened(self): return True
            def read(self):
                self.i+=1
                return (True,np.zeros((2,2,3))) if self.i<=100 else (False,None)
            def release(self): self.released=True
        capture=Capture(); stream=app.live_frames('0',types.SimpleNamespace(VideoCapture=lambda source:capture),1); seen=[]
        with self.assertRaisesRegex(ValueError,'取得が停止'):
            while True: seen.append(next(stream)[0])
        self.assertEqual(seen[-1],99); self.assertTrue(capture.released)

    def test_browser_frames_reads_uploaded_jpeg(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory); image=folder/'latest.jpg'
            cv2.imencode('.jpg',np.full((20,30,3),100,dtype=np.uint8))[1].tofile(image)
            stream=app.browser_frames(folder,cv2,1)
            row=next(stream); stream.close()
            self.assertEqual(row[0],0); self.assertEqual(row[2].shape[:2],(20,30))
