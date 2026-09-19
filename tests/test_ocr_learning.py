import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import cv2
import numpy as np
import events
import ocr_learning as learning
from app import save_observation
from web import JobManager, create_app
from ocr_train import metrics, eligible


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.manager = JobManager(self.root)
        self.app = create_app(manager=self.manager, password='')
        self.client = self.app.test_client()
        self.client.get('/')
        with self.client.session_transaction() as s:
            self.headers = {'X-CSRF-Token': s['csrf']}
        (self.root / 'images').mkdir()
        image = self.root / 'images' / 'source.jpg'
        cv2.imwrite(str(image), np.full((80, 140, 3), 180, np.uint8))
        record = dict(id='source', processed_at=events.utc(), run_id='run', frame_index=0,
                      media_ms=None, vehicle_type='car', confidence=.9, image_path=str(image),
                      plate_candidates=[dict(text='品川330さ12-35', bbox_in_vehicle=[10, 10, 130, 70])])
        with self.manager.connect() as db:
            save_observation(db, record)
        self.data = dict(region='品川', category='330', kana='さ', serial='1234', vehicle_type='car',
                         learning=dict(observation_id='source', candidate_index=0, top_text='品川330',
                                       bottom_text='さ12-34', split=.45, confirmed=True))

    def tearDown(self):
        self.manager.shutdown()
        self.tmp.cleanup()

    def post(self, route, data=None):
        return self.client.post(route, json=data or self.data, headers=self.headers)

    def test_registration_and_sample_are_atomic_and_pixels_match(self):
        self.data['learning']['bottom_text'] = 'さ12-35'
        self.assertEqual(self.post('/api/vehicles').status_code, 400)
        self.assertEqual(self.client.get('/api/vehicles').json['items'], [])
        self.data['learning']['bottom_text'] = 'さ12-34'
        self.assertEqual(self.post('/api/vehicles').status_code, 201)
        with events.connection(self.root) as db:
            row = dict(db.execute('SELECT * FROM ocr_samples').fetchone())
        self.assertEqual(row['original_text'], '品川330さ12-35')
        self.assertEqual(row['bottom_text'], 'さ12-34')
        image = cv2.imdecode(np.frombuffer(row['image'], np.uint8), cv2.IMREAD_COLOR)
        self.assertEqual(image.shape, (60, 120, 3))
        self.assertEqual(row['image_sha256'], hashlib.sha256(row['image']).hexdigest())
        self.assertEqual(self.client.get('/api/ocr-learning/preview/source/0').status_code, 200)

    def test_repeat_save_updates_label_and_delete_removes_future_training(self):
        first = self.post('/api/ocr-learning/samples')
        self.data['serial'] = '5678'; self.data['learning']['bottom_text'] = 'さ56-78'
        second = self.post('/api/ocr-learning/samples')
        self.assertEqual(first.json['id'], second.json['id'])
        result = self.client.get('/api/ocr-learning').json
        self.assertEqual(result['count'], 1)
        self.assertEqual(result['samples'][0]['bottom_text'], 'さ56-78')
        self.client.delete('/api/ocr-learning/samples/'+first.json['id'], headers=self.headers)
        self.assertEqual(self.client.get('/api/ocr-learning').json['count'], 0)

    def test_confirmation_candidate_and_csrf_required(self):
        self.data['learning']['confirmed'] = False
        self.assertEqual(self.post('/api/ocr-learning/samples').status_code, 400)
        self.data['learning']['confirmed'] = True
        self.data['learning']['candidate_index'] = -1
        self.assertEqual(self.post('/api/ocr-learning/samples').status_code, 400)
        self.assertEqual(self.client.post('/api/ocr-learning/samples', json=self.data).status_code, 403)
        self.assertEqual(self.post('/api/ocr-learning/train').status_code, 400)

    def test_external_image_path_rejected(self):
        with self.manager.connect() as db:
            record = json.loads(db.execute('SELECT details_json FROM observations').fetchone()[0])
            record['image_path'] = str(self.root / 'secret.jpg')
            db.execute('UPDATE observations SET details_json=?', (json.dumps(record),))
        self.assertEqual(self.post('/api/ocr-learning/samples').status_code, 400)

    def test_activation_requires_evaluation_and_can_restore_standard(self):
        identifier = 'a'*32
        self.assertEqual(self.post('/api/ocr-learning/activate', {'id': identifier}).status_code, 400)
        with events.connection(self.root) as db:
            db.execute('INSERT INTO ocr_training_runs VALUES (?,?,?,?,NULL)',
                       (identifier, 'completed', events.utc(), json.dumps({'eligible': True})))
        folder = learning.model_dir(self.root, identifier); folder.mkdir()
        (folder / 'weights.pth').write_bytes(b'test checkpoint')
        self.assertEqual(self.post('/api/ocr-learning/activate', {'id': identifier}).status_code, 200)
        self.assertEqual(learning.active_model(self.root), identifier)
        self.post('/api/ocr-learning/activate', {'id': None})
        self.assertIsNone(learning.active_model(self.root))
        self.assertEqual(self.post('/api/ocr-learning/activate', {'id': '../bad'}).status_code, 400)

    def test_split_is_stable_by_plate_and_snapshot_immutable(self):
        # Distinct pixels and keys; consecutive observations of the same plate stay together.
        with events.connection(self.root) as db:
            for i in range(1, 61):
                key = '品川|330|さ|' + str(i)
                image = bytes([i])
                db.execute('INSERT INTO ocr_samples VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                           (str(i), str(i), 0, key, '', '品川330', 'さ'+str(i), .45,
                            image, hashlib.sha256(image).hexdigest(), events.utc()))
        samples = learning.dataset_snapshot(self.root)
        train = {s['plate_key'] for s in samples if s['partition'] == 'train'}
        valid = {s['plate_key'] for s in samples if s['partition'] == 'validation'}
        self.assertFalse(train & valid)
        self.assertGreaterEqual(len(valid), 2)
        self.assertEqual(samples, learning.dataset_snapshot(self.root))

    def test_cer_gate_does_not_accept_regression_or_equal_score(self):
        base = metrics(['1234','5678'], ['1235','5678'])
        self.assertEqual(base['cer'], .125)
        self.assertFalse(eligible(base, base))
        self.assertFalse(eligible(base, {'cer': .1, 'line_accuracy': 0}))
        self.assertTrue(eligible(base, metrics(['1234','5678'], ['1234','5678'])))

    def test_start_failure_is_durable_and_does_not_change_active(self):
        sample = dict(id='a', image=b'image')
        with patch.object(learning, 'dataset_snapshot', return_value=[sample]), \
             patch.object(learning.subprocess, 'Popen', side_effect=OSError('cannot spawn')):
            with self.assertRaises(OSError):
                self.app.extensions['ocr_training'].start()
        result = self.client.get('/api/ocr-learning').json
        self.assertEqual(result['runs'][0]['status'], 'failed')
        self.assertIsNone(result['active'])
