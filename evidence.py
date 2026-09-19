"""Sampled, bounded pre/post event clips. Audio is not captured."""
from collections import deque
from pathlib import Path
import shutil
import subprocess
import threading
import cv2
from events import set_media


class EvidenceRecorder:
    def __init__(self, root, pre=3, post=5, fps=5, still=False):
        self.root=Path(root)
        (self.root/'events').mkdir(parents=True,exist_ok=True)
        self.pre,self.post,self.fps,self.still=pre,post,fps,still
        self.buffer=deque(maxlen=int((pre+post+15)*fps)+2)
        self.pending={}
        self.lock=threading.RLock()
        self.last=-1e9
        self.closed=False

    def encode(self,frame):
        scale=min(1,960/frame.shape[1])
        frame=cv2.resize(frame,None,fx=scale,fy=scale)
        ok,jpeg=cv2.imencode('.jpg',frame,[cv2.IMWRITE_JPEG_QUALITY,80])
        if not ok: raise OSError('JPEG encoding failed')
        return jpeg

    def feed(self,frame,seconds):
        with self.lock:
            if self.closed or seconds-self.last < 1/self.fps-0.001: return
            self.last=seconds
            jpeg=self.encode(frame)
            self.buffer.append((seconds,jpeg))
            for alert_id,clip in list(self.pending.items()):
                self.write(clip,seconds,jpeg)
                if seconds>=clip['deadline']:
                    self.finish(alert_id,partial=False)

    def trigger(self,alert_id,seconds,frame):
        with self.lock:
            if self.still:
                path=self.root/'events'/(alert_id+'.jpg')
                self.encode(frame).tofile(path)
                set_media(self.root,alert_id,'ready',path,start=seconds,end=seconds)
                return
            if len(self.pending)>=16:
                set_media(self.root,alert_id,'failed',error='同時録画数の上限16件に達しました。')
                return
            samples=[(t,j) for t,j in self.buffer if seconds-self.pre<=t<=seconds+self.post]
            if not samples: samples=[(seconds,self.encode(frame))]
            initial=cv2.imdecode(samples[0][1],cv2.IMREAD_COLOR)
            height,width=initial.shape[:2]
            path=self.root/'events'/(alert_id+'.avi')
            writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'MJPG'),self.fps,(width,height))
            if not writer.isOpened():
                writer.release(); set_media(self.root,alert_id,'failed',error='動画エンコーダを開始できません。'); return
            clip=dict(writer=writer,path=path,start=samples[0][0],end=samples[0][0],deadline=seconds+self.post,
                      count=0,last_frame=initial,size=(width,height))
            self.pending[alert_id]=clip
            for t,jpeg in samples: self.write(clip,t,jpeg)
            if self.last>=clip['deadline']: self.finish(alert_id,partial=False)

    def write(self,clip,seconds,jpeg):
        seconds=min(seconds,clip['deadline'])
        frame=cv2.imdecode(jpeg,cv2.IMREAD_COLOR)
        frame=cv2.resize(frame,clip['size'])
        target=max(0,round((seconds-clip['start'])*self.fps))
        if target < clip['count']:
            clip['last_frame']=frame
            return
        # Hold the prior sample for any skipped source interval.
        while clip['count']<target:
            clip['writer'].write(clip['last_frame']); clip['count']+=1
        clip['writer'].write(frame); clip['count']+=1
        clip['last_frame']=frame; clip['end']=seconds

    def finish(self,alert_id,partial):
        clip=self.pending.pop(alert_id)
        clip['writer'].release()
        path=clip['path']
        error=None
        ffmpeg=shutil.which('ffmpeg')
        if ffmpeg:
            target=path.with_suffix('.mp4')
            try:
                subprocess.run([ffmpeg,'-loglevel','error','-y','-i',str(path),'-an','-c:v','libx264',
                    '-vf','pad=ceil(iw/2)*2:ceil(ih/2)*2','-pix_fmt','yuv420p','-movflags','+faststart',str(target)],
                    check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
                if target.stat().st_size==0: raise OSError('Empty recording')
                path.unlink(); path=target
            except (subprocess.SubprocessError,OSError):
                target.unlink(missing_ok=True)
                error='MP4変換に失敗したためAVI形式で保存しました。'
        else: error='FFmpegがないためAVI形式で保存しました。'
        if not path.exists() or path.stat().st_size==0:
            set_media(self.root,alert_id,'failed',error='動画ファイルを保存できませんでした。')
        else:
            set_media(self.root,alert_id,'partial' if partial else 'ready',path,error,clip['start'],clip['end'])

    def close(self):
        with self.lock:
            self.closed=True
            for alert_id in list(self.pending): self.finish(alert_id,partial=True)
