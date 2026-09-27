"""Model residency, reinitialization and client compatibility."""
import tempfile
import os
from multiprocessing.connection import Listener
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

import app
from model_service import ModelClient, ModelServer, _reply
from web import BusyError, JobManager, create_app


class ModelServiceTests(unittest.TestCase):
    def test_models_remain_loaded_across_jobs_and_reload_once_on_change(self):
        initial = Mock()
        replacement = Mock()
        loaders = Mock(side_effect=[(initial, None, []), (replacement, None, [])])
        with tempfile.TemporaryDirectory() as root:
            server = ModelServer(root, loader=loaders)
            server.reload(dict(model='first', warmup=False))
            initial.predict.return_value = [SimpleNamespace(boxes=[], names={})]
            for _ in range(2):
                self.assertEqual(server.handle('vehicle', {'frame': np.zeros((8, 8, 3)),
                                                   'options': {}}), ([], {}))
            self.assertEqual(loaders.call_count, 1)
            server.reload(dict(model='second', warmup=False))
            replacement.predict.return_value = [SimpleNamespace(boxes=[], names={})]
            server.handle('vehicle', {'frame': np.zeros((8, 8, 3)), 'options': {}})
            self.assertEqual(loaders.call_count, 2)
            self.assertEqual(initial.predict.call_count, 2)

    def test_worker_uses_resident_plate_reader(self):
        remote = ModelClient('/not-used', b'test')
        report = {'plate_recognition_ms': 2}
        with patch.object(remote, 'request', return_value=([{'text': '品川'}], report)) as request:
            diagnostics = {}
            self.assertEqual(app.read_plate(np.zeros((10, 20, 3)), remote, None,
                                            diagnostics=diagnostics), [{'text': '品川'}])
        self.assertEqual(diagnostics, report)
        request.assert_called_once()

    def test_remote_vehicle_boxes_match_existing_worker_interface(self):
        remote = ModelClient('/not-used', b'test')
        with patch.object(remote, 'request', return_value=(
                [dict(cls=0, conf=.92, xyxy=[5, 8, 50, 40])], {0: 'car'})):
            result = remote.predict(np.zeros((60, 80, 3)))[0]
        self.assertEqual(result.names[int(result.boxes[0].cls.item())], 'car')
        self.assertEqual(result.boxes[0].xyxy[0].tolist(), [5, 8, 50, 40])

    def test_authenticated_local_socket_serves_multiple_jobs_from_one_model(self):
        with tempfile.TemporaryDirectory() as root:
            address = os.path.join(root, 'model.sock')
            model = Mock()
            model.predict.return_value = [SimpleNamespace(boxes=[], names={})]
            loader = Mock(return_value=(model, None, []))
            server = ModelServer(root, loader=loader)
            server.reload(dict(model='shared', warmup=False))
            try:
                listener = Listener(address, family='AF_UNIX', authkey=b'local')
            except PermissionError:
                self.skipTest('実行環境でUnixソケットの作成が禁止されています。')
            with listener:
                replies = []
                def accept():
                    for _ in range(2):
                        replies.append(threading.Thread(target=_reply,
                            args=(listener.accept(), server), daemon=True))
                        replies[-1].start()
                dispatcher = threading.Thread(target=accept, daemon=True)
                dispatcher.start()
                for _ in range(2):
                    client = ModelClient(address, b'local')
                    self.assertEqual(client.predict(np.zeros((8, 8, 3)))[0].boxes, [])
                dispatcher.join(timeout=2)
                for reply in replies:
                    reply.join(timeout=2)
            self.assertEqual(loader.call_count, 1)
            self.assertEqual(model.predict.call_count, 2)

    def test_failed_reload_preserves_previous_models_and_environment(self):
        old = Mock()
        loader = Mock(side_effect=[(old, None, []), ValueError('missing weights')])
        with tempfile.TemporaryDirectory() as root:
            server = ModelServer(root, loader=loader)
            server.reload(dict(model='first', warmup=False))
            with patch.dict(os.environ, {'GATE_OCR_BACKEND': 'lipla'}):
                with self.assertRaises(ValueError):
                    server.reload(dict(model='bad', environment={'GATE_OCR_BACKEND': 'paddle'},
                                       warmup=False))
                self.assertEqual(os.environ['GATE_OCR_BACKEND'], 'lipla')
            self.assertIs(server.vehicle, old)

    def test_model_change_reloads_service_and_rejects_change_with_active_job(self):
        with tempfile.TemporaryDirectory() as root:
            manager = JobManager(root, popen=Mock())
            create_app(manager=manager, password='')
            manager.model_service = Mock()
            manager.apply_settings({'imgsz': 640})
            manager.model_service.reload.assert_not_called()
            # Use another available built-in vehicle model; no weights are downloaded here.
            current = manager.settings.read()['vehicle_model']
            changed = next(value for value in ('ultralytics-yolo11n', 'ultralytics-yolov8n',
                                               'ultralytics-yolo26s') if value != current)
            manager.apply_settings({'vehicle_model': changed})
            manager.model_service.reload.assert_called_once()
            manager.processes['running'] = (Mock(), 'camera')
            with self.assertRaises(BusyError):
                manager.apply_settings({'vehicle_model': current})
            manager.processes.clear()


if __name__ == '__main__':
    unittest.main()
