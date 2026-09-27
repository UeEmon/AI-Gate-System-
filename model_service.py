"""Container resident inference models shared by short lived processing jobs.

Only local processes with access to the private socket and authentication key may
connect. Requests carry NumPy arrays, so this socket must never be exposed to a LAN.
"""
import argparse
from multiprocessing.connection import Client, Listener
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
from types import SimpleNamespace


class ModelClient:
    def __init__(self, address, key):
        self.address = str(address)
        self.key = key.encode() if isinstance(key, str) else key

    def request(self, operation, **payload):
        with Client(self.address, family='AF_UNIX', authkey=self.key) as connection:
            connection.send((operation, payload))
            ok, value = connection.recv()
        if not ok:
            raise RuntimeError(value)
        return value

    def predict(self, frame, **options):
        import numpy as np
        rows, names = self.request('vehicle', frame=frame, options=options)
        boxes = [SimpleNamespace(cls=np.asarray(row['cls']), conf=np.asarray(row['conf']),
                                 xyxy=[np.asarray(row['xyxy'])]) for row in rows]
        return [SimpleNamespace(boxes=boxes, names=names)]

    def read_plate(self, crop, threshold, diagnostics=None):
        candidates, report = self.request('plate', crop=crop, threshold=threshold)
        if diagnostics is not None:
            diagnostics.update(report)
        return candidates


class ModelServer:
    def __init__(self, root, loader=None):
        self.root = Path(root)
        self.lock = threading.RLock()
        self.loader = loader or self._load
        self.vehicle = self.plate = self.readers = None

    def _load(self, config):
        import easyocr
        from ultralytics import YOLO
        from app import initialize_models
        return initialize_models(config['model'], config.get('plate_model'), self.root,
                                 easyocr, YOLO, os.getenv('GATE_OFFLINE') == '1')

    def reload(self, config):
        # Infer with the previous models until all replacements have loaded.
        with self.lock:
            updates = config.get('environment', {})
            previous = {name: os.environ.get(name) for name in updates}
            try:
                for name, value in updates.items():
                    os.environ[name] = value
                replacement = self.loader(config)
                if config.get('warmup', True):
                    import numpy as np
                    vehicle, _, readers = replacement
                    vehicle.predict(np.zeros((320, 320, 3), dtype=np.uint8),
                                    conf=.25, imgsz=config.get('imgsz', 960),
                                    iou=.55, device='cpu', verbose=False)
                    if len(readers) == 1 and readers[0][0] == 'lipla-jp':
                        readers[0][1].model(np.zeros((160, 320, 3), dtype=np.uint8))
            except Exception:
                for name, value in previous.items():
                    if value is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = value
                raise
            self.vehicle, self.plate, self.readers = replacement

    def handle(self, operation, payload):
        if operation == 'ping':
            return {'ready': self.vehicle is not None}
        with self.lock:
            if operation == 'reload':
                self.reload(payload['config'])
                return {'ready': True}
            if self.vehicle is None:
                raise RuntimeError('モデルの読み込みが完了していません。')
            if operation == 'vehicle':
                result = self.vehicle.predict(payload['frame'], **payload['options'])[0]
                return ([dict(cls=int(box.cls.item()), conf=float(box.conf.item()),
                              xyxy=box.xyxy[0].tolist()) for box in result.boxes], result.names)
            if operation == 'plate':
                import cv2
                from app import read_plate
                report = {}
                candidates = read_plate(payload['crop'], self.readers, cv2,
                                        payload['threshold'], self.plate, diagnostics=report)
                return candidates, report
            raise ValueError('不明なモデル操作です。')


def serve(address, key, config):
    address = Path(address)
    address.parent.mkdir(parents=True, exist_ok=True)
    address.unlink(missing_ok=True)
    server = ModelServer(config['root'])
    try:
        server.reload(config)
        with Listener(str(address), family='AF_UNIX', authkey=key.encode()) as listener:
            address.chmod(0o600)
            while True:
                connection = listener.accept()
                threading.Thread(target=_reply, args=(connection, server), daemon=True).start()
    finally:
        address.unlink(missing_ok=True)


def _reply(connection, server):
    with connection:
        try:
            operation, payload = connection.recv()
            connection.send((True, server.handle(operation, payload)))
        except Exception as error:
            connection.send((False, f'{type(error).__name__}: {error}'))


class ModelServiceProcess:
    def __init__(self, root, config, timeout=900):
        self.address = str(Path(root) / '.model-service.sock')
        self.key = secrets.token_hex(32)
        self.client = ModelClient(self.address, self.key)
        self.process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                                         '--serve', self.address, self.key],
                                        stdin=subprocess.PIPE, cwd=Path(__file__).resolve().parent)
        try:
            import json
            self.process.stdin.write(json.dumps(config).encode() + b'\n')
            self.process.stdin.close()
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise RuntimeError('モデル常駐プロセスの初期化に失敗しました。')
                if Path(self.address).exists():
                    try:
                        if self.client.request('ping')['ready']:
                            return
                    except (OSError, EOFError, ConnectionError):
                        pass
                time.sleep(.2)
            raise TimeoutError('モデルの起動待ちがタイムアウトしました。')
        except BaseException:
            self.close()
            raise

    def reload(self, config):
        return self.client.request('reload', config=config)

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--serve', required=True)
    parser.add_argument('key')
    args = parser.parse_args()
    import json
    serve(args.serve, args.key, json.loads(sys.stdin.readline()))
