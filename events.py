"""Vehicle registry, durable alerts and SES/S3 delivery (one dispatcher)."""
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import unicodedata
import uuid

TYPES = {'car', 'motorcycle', 'bus', 'truck'}
REASONS = {'unknown': '未登録ナンバー', 'type_mismatch': '登録車種と不一致',
           'watch': '指定車両を検知', 'unreadable': 'ナンバー要確認'}


def utc():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connection(root):
    db = sqlite3.connect(Path(root) / 'gate.db', timeout=30)
    db.row_factory = sqlite3.Row
    try:
        with db:
            yield db
    finally:
        db.close()


def initialize(root):
    Path(root).mkdir(parents=True, exist_ok=True)
    with connection(root) as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS vehicles (
            id TEXT PRIMARY KEY, plate_key TEXT NOT NULL UNIQUE, plate TEXT NOT NULL,
            vehicle_type TEXT NOT NULL, label TEXT NOT NULL,
            watch INTEGER NOT NULL DEFAULT 0, enabled INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS alerts (
            id TEXT PRIMARY KEY, observation_id TEXT NOT NULL, run_id TEXT NOT NULL,
            created_at TEXT NOT NULL, reason TEXT NOT NULL, plate TEXT,
            vehicle_type TEXT NOT NULL, registry_json TEXT,
            dedupe_key TEXT NOT NULL, source_seconds REAL NOT NULL,
            acknowledged INTEGER NOT NULL DEFAULT 0,
            media_status TEXT NOT NULL DEFAULT 'recording', media_path TEXT,
            media_error TEXT, media_start REAL, media_end REAL,
            email_status TEXT NOT NULL DEFAULT 'disabled', email_attempts INTEGER NOT NULL DEFAULT 0,
            email_next REAL NOT NULL DEFAULT 0, email_error TEXT, email_message_id TEXT,
            s3_status TEXT NOT NULL DEFAULT 'disabled', s3_key TEXT, s3_attempts INTEGER NOT NULL DEFAULT 0,
            s3_next REAL NOT NULL DEFAULT 0, s3_error TEXT);
        CREATE INDEX IF NOT EXISTS alerts_dedupe ON alerts(run_id,dedupe_key,source_seconds);
        CREATE INDEX IF NOT EXISTS alerts_time ON alerts(created_at);
        ''')


def plate_key(fields):
    region=unicodedata.normalize('NFKC',str(fields['region'])).strip()
    category=unicodedata.normalize('NFKC',str(fields['category'])).upper().strip()
    kana=unicodedata.normalize('NFKC',str(fields['kana'])).strip()
    serial=unicodedata.normalize('NFKC',str(fields['serial']))
    serial=re.sub(r'[\s・.\-−ー]', '', serial)
    if not re.fullmatch(r'[一-龥ぁ-んァ-ヶ]{2,8}',region) or not re.fullmatch(r'[0-9][0-9A-Z]{2}',category):
        raise ValueError('地名・分類番号を確認してください。')
    if not re.fullmatch(r'[ぁ-ん]',kana) or not re.fullmatch(r'[0-9]{1,4}',serial):
        raise ValueError('ひらがな・一連指定番号を確認してください。')
    if int(serial)==0: raise ValueError('一連指定番号は1以上にしてください。')
    return '|'.join((region,category,kana,str(int(serial))))


def register_vehicle(root, data, vehicle_id=None, database=None):
    if not isinstance(data,dict): raise ValueError('登録内容はオブジェクトで指定してください。')
    key=plate_key(data)
    kind=data.get('vehicle_type')
    if kind not in TYPES: raise ValueError('車種区分を選択してください。')
    if type(data.get('watch',False)) is not bool or type(data.get('enabled',True)) is not bool:
        raise ValueError('通知指定と有効状態は真偽値で指定してください。')
    label=str(data.get('label','')).strip()[:120]
    plate=' '.join(key.split('|'))
    vehicle_id=vehicle_id or uuid.uuid4().hex
    with (connection(root) if database is None else nullcontext(database)) as db:
        db.execute('''INSERT INTO vehicles VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET plate_key=excluded.plate_key,plate=excluded.plate,
            vehicle_type=excluded.vehicle_type,label=excluded.label,watch=excluded.watch,
            enabled=excluded.enabled,updated_at=excluded.updated_at''',
            (vehicle_id,key,plate,kind,label,int(data.get('watch',False)),int(data.get('enabled',True)),utc()))
    return vehicle_id


def email_configured():
    return bool(os.getenv('GATE_EMAIL_FROM') and os.getenv('GATE_EMAIL_TO'))


def evaluate(root, observation, source_seconds, cooldown=60):
    """One alert for a recognized pair per job/cooldown. Unreadable has its own category."""
    candidate=next((c for c in observation.get('plate_candidates',[]) if c.get('fields')
                    and c.get('confidence',0)>=0.6), None)
    key=None
    if candidate:
        try: key=plate_key(candidate['fields'])
        except (ValueError,KeyError): pass
    kind=observation['vehicle_type']
    with connection(root) as db:
        db.execute('BEGIN IMMEDIATE')
        registered=db.execute('SELECT * FROM vehicles WHERE plate_key=? AND enabled=1',(key,)).fetchone() if key else None
        if key is None: reason='unreadable'
        elif registered is None: reason='unknown'
        elif registered['vehicle_type']!=kind: reason='type_mismatch'
        elif registered['watch']: reason='watch'
        else: return None
        dedupe=reason+'|'+(key or 'unreadable')+'|'+kind
        last=db.execute('SELECT source_seconds FROM alerts WHERE run_id=? AND dedupe_key=? ORDER BY source_seconds DESC LIMIT 1',
                        (observation['run_id'],dedupe)).fetchone()
        if last and source_seconds-last[0]<cooldown: return None
        alert_id=uuid.uuid4().hex
        snapshot=dict(registered) if registered else None
        db.execute('''INSERT INTO alerts(id,observation_id,run_id,created_at,reason,plate,vehicle_type,
            registry_json,dedupe_key,source_seconds,email_status,s3_status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
            (alert_id,observation['id'],observation['run_id'],utc(),reason,' '.join(key.split('|')) if key else None,
             kind,json.dumps(snapshot,ensure_ascii=False) if snapshot else None,dedupe,source_seconds,
             'pending' if email_configured() else 'disabled','waiting' if os.getenv('GATE_S3_BUCKET') else 'disabled'))
    return alert_id


def set_media(root, alert_id, status, path=None, error=None, start=None, end=None):
    with connection(root) as db:
        db.execute('UPDATE alerts SET media_status=?,media_path=?,media_error=?,media_start=?,media_end=? WHERE id=?',
                   (status,str(path) if path else None,error,start,end,alert_id))
        if status=='failed':
            db.execute("UPDATE alerts SET s3_status='failed',s3_error='映像保存に失敗しました。' WHERE id=? AND s3_status='waiting'",(alert_id,))


class Dispatcher:
    """Durable at-least-once delivery; duplicates possible after remote success + local crash."""
    def __init__(self,root,client_factory=None):
        self.root=Path(root)
        self.client_factory=client_factory
        self.stop_event=threading.Event()
        self.thread=None
        initialize(root)
        with connection(root) as db:
            db.execute("UPDATE alerts SET email_status='pending' WHERE email_status='sending'")

    def client(self,service):
        if self.client_factory: return self.client_factory(service)
        import boto3
        from botocore.config import Config
        return boto3.client(service,region_name=os.getenv('AWS_REGION','ap-northeast-1'),
                            config=Config(connect_timeout=5,read_timeout=10,retries={'max_attempts':2}))

    def start(self):
        self.thread=threading.Thread(target=self.run,daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread: self.thread.join(timeout=2)

    def run(self):
        while not self.stop_event.is_set():
            try: self.tick()
            except Exception:
                import logging
                logging.exception('Alert dispatcher failed')
            self.stop_event.wait(2)

    def tick(self):
        with connection(self.root) as db:
            mail=db.execute("SELECT * FROM alerts WHERE email_status IN ('pending','retry') AND email_next<=? ORDER BY created_at LIMIT 5",(time.time(),)).fetchall()
        for row in mail:
            if self.stop_event.is_set(): return
            item=dict(row)
            if not email_configured():
                with connection(self.root) as db: db.execute("UPDATE alerts SET email_status='disabled' WHERE id=?",(item['id'],))
                continue
            attempts=item['email_attempts']+1
            with connection(self.root) as db:
                db.execute("UPDATE alerts SET email_status='sending',email_attempts=? WHERE id=?",(attempts,item['id']))
            try:
                recipients=[v.strip() for v in os.environ['GATE_EMAIL_TO'].split(',') if v.strip()]
                if not 1<=len(recipients)<=50: raise ValueError('Invalid recipient count')
                subject='AI Gate System: '+REASONS[item['reason']]
                body=f"{REASONS[item['reason']]}\nナンバー候補: {item['plate'] or '読取不可'}\n車種区分: {item['vehicle_type']}\n処理日時(UTC): {item['created_at']}\nイベントID: {item['id']}\n認識結果は候補です。映像と照合してください。"
                snapshot=json.loads(item['registry_json']) if item['registry_json'] else None
                if snapshot: body+=f"\n登録名: {snapshot['label']}\n登録車種: {snapshot['vehicle_type']}"
                url=os.getenv('GATE_PUBLIC_URL','').rstrip('/')
                if url: body+='\n管理画面: '+url+'/#alerts'
                result=self.client('ses').send_email(Source=os.environ['GATE_EMAIL_FROM'],Destination={'ToAddresses':recipients},
                    Message={'Subject':{'Data':subject,'Charset':'UTF-8'},'Body':{'Text':{'Data':body,'Charset':'UTF-8'}}})
                with connection(self.root) as db:
                    db.execute("UPDATE alerts SET email_status='sent',email_error=NULL,email_message_id=? WHERE id=?",(result['MessageId'],item['id']))
            except Exception as exc:
                with connection(self.root) as db:
                    db.execute('UPDATE alerts SET email_status=?,email_next=?,email_error=? WHERE id=?',
                        ('failed' if attempts>=5 else 'retry',time.time()+min(900,30*2**(attempts-1)),type(exc).__name__,item['id']))
        bucket=os.getenv('GATE_S3_BUCKET')
        if not bucket: return
        with connection(self.root) as db:
            rows=db.execute("SELECT * FROM alerts WHERE s3_status IN ('waiting','retry') AND media_status IN ('ready','partial') AND s3_next<=? LIMIT 5",(time.time(),)).fetchall()
        for row in rows:
            item=dict(row); attempts=item['s3_attempts']+1
            try:
                path=Path(item['media_path']).resolve()
                if not path.is_relative_to((self.root/'events').resolve()): raise ValueError('Invalid evidence path')
                key='events/'+item['id']+path.suffix
                self.client('s3').upload_file(str(path),bucket,key,ExtraArgs={'ServerSideEncryption':'AES256'})
                with connection(self.root) as db:
                    db.execute("UPDATE alerts SET s3_status='uploaded',s3_key=?,s3_error=NULL,s3_attempts=? WHERE id=?",(key,attempts,item['id']))
            except Exception as exc:
                with connection(self.root) as db:
                    db.execute('UPDATE alerts SET s3_status=?,s3_next=?,s3_error=?,s3_attempts=? WHERE id=?',
                               ('failed' if attempts>=5 else 'retry',time.time()+60*attempts,type(exc).__name__,attempts,item['id']))
