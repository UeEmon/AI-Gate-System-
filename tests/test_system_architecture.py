import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from aigate.model_registry import ModelRegistry, ModelRole
from aigate.performance import PerformanceManager
from aigate.reset import reset_application_data
from aigate.settings import SettingsManager
from aigate.evaluation import EvaluationMetrics
from web import JobManager, create_app


class ArchitectureTests(unittest.TestCase):
    def test_accuracy_metrics_include_detection_and_exact_ocr(self):
        metrics = EvaluationMetrics()
        metrics.add_detection(matched=True, predicted=True, expected=True)
        metrics.add_detection(matched=False, predicted=True, expected=False)
        metrics.add_detection(matched=False, predicted=False, expected=True)
        metrics.add_ocr('品川330さ1234', '品川330さ1234')
        metrics.add_ocr('横浜500あ5678', '横浜500あ5670')
        report = metrics.report()
        self.assertEqual(report['detection_precision'], .5)
        self.assertEqual(report['detection_recall'], .5)
        self.assertEqual(report['ocr_exact_match'], .5)
        self.assertGreater(report['ocr_cer'], 0)

    def test_registry_has_all_roles_and_license_metadata(self):
        registry = ModelRegistry('/tmp/models-not-required')
        items = registry.list()
        self.assertTrue({role.value for role in ModelRole}.issubset({item['role'] for item in items}))
        self.assertEqual(len(items), 12)
        self.assertTrue(all(item['license'] and item['source'] for item in items))
        self.assertTrue(any(item['id'] == 'paddle-ppocr-v6' and item['japanese'] for item in items))
        self.assertTrue(any(item['id'] == 'lipla-jp' and item['license'] == 'MIT' for item in items))
        ids = {item['id'] for item in items}
        self.assertNotIn('lprs-jp', ids)
        self.assertNotIn('alpr-jp-openalpr', ids)
        self.assertTrue(all(item['license_scope'] for item in items))
        fast = next(item for item in items if item['id'] == 'fast-alpr')
        self.assertEqual(fast['role'], 'ocr')
        self.assertEqual(fast['license'], 'MIT')
        jp = next(item for item in items if item['id'] == 'fast-plate-ocr-jp')
        self.assertFalse(jp['available'])

    def test_settings_are_atomic_and_bounded(self):
        with tempfile.TemporaryDirectory() as root:
            settings = SettingsManager(root)
            saved = settings.update({'profile': 'speed', 'imgsz': 1, 'frame_stride': 5000})
            self.assertEqual((saved['imgsz'], saved['frame_stride']), (320, 1000))
            self.assertEqual(json.loads((Path(root) / 'system-settings.json').read_text())['profile'], 'speed')

    def test_performance_percentiles(self):
        with tempfile.TemporaryDirectory() as root:
            performance = PerformanceManager(root)
            performance.record(job_id='a', frame_ms=50, detection_ms=20, ocr_ms=20)
            performance.record(job_id='a', frame_ms=100, detection_ms=40, ocr_ms=40)
            report = performance.summary()
            self.assertEqual(report['samples'], 2)
            self.assertGreater(report['fps'], 0)
            self.assertTrue(report['realtime'])

    def test_reset_preserves_external_model_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'data'; models = Path(temp) / 'models'
            root.mkdir(); models.mkdir(); (models / 'weight.pt').write_bytes(b'model')
            with sqlite3.connect(root / 'gate.db') as db:
                db.execute('CREATE TABLE vehicles(id TEXT)')
                db.execute("INSERT INTO vehicles VALUES ('v')")
            (root / 'images').mkdir(); (root / 'images' / 'x.jpg').write_bytes(b'x')
            result = reset_application_data(root)
            self.assertEqual(result['deleted']['vehicles'], 1)
            self.assertTrue((models / 'weight.pt').exists())
            self.assertEqual(list((root / 'images').iterdir()), [])

    def test_model_and_performance_api(self):
        with tempfile.TemporaryDirectory() as root:
            manager = JobManager(root, popen=lambda *args, **kwargs: None)
            app = create_app(manager=manager, password=''); app.testing = True
            client = app.test_client(); client.get('/')
            with client.session_transaction() as session:
                headers = {'X-CSRF-Token': session['csrf']}
            catalog = client.get('/api/models').get_json()
            self.assertEqual(len(catalog['models']), 12)
            performance = client.get('/api/system/performance').get_json()
            self.assertIn('recommendation', performance['startup'])
            response = client.put('/api/settings/models', json={'vehicle_model': 'missing'}, headers=headers)
            self.assertEqual(response.status_code, 400)


if __name__ == '__main__':
    unittest.main()
