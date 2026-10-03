import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from model_package import package_from_state, verify_package
from model_publish import GitHubModels
from web import JobManager, create_app


class ModelPackageTests(unittest.TestCase):
    def setup_model(self,root):
        area=root/'ocr-learning'/'paddle';dataset=area/'auto-fixture';weights=dataset/'weights'
        (weights/'paddle-inference').mkdir(parents=True)
        # Fake binary payloads test the package contract, not model validity or accuracy.
        (weights/'plate.pt').write_bytes(b'detector')
        (weights/'paddle-inference'/'inference.pdiparams').write_bytes(b'weights')
        (weights/'paddle-inference'/'inference.json').write_text('{}')
        (area/'auto-state.json').write_text(json.dumps(dict(state='completed',dataset=str(dataset),fingerprint='safe-fingerprint')))
        report=dict(evaluated=3,skipped=0,evaluation_partition='test',ground_truth='lipla_pseudo',
                    paddle=dict(exact_plate_accuracy=.66),diagnostics=[dict(sample='secret-number',image='private.jpg')])
        (area/'comparison-state.json').write_text(json.dumps(dict(state='completed',dataset=str(dataset),report=report)))
        return area,dataset

    def test_export_has_adapter_graph_checksums_and_no_private_training_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);self.setup_model(root)
            with patch.dict(os.environ,{'GATE_MODEL_REPO_TOKEN':'secret-token'},clear=True):
                path,manifest=package_from_state(root)
            self.assertEqual(verify_package(path)['version'],manifest['version'])
            with zipfile.ZipFile(path) as archive:
                self.assertIn('recognize.py',archive.namelist())
                self.assertIn('models/paddle-inference/inference.json',archive.namelist())
                self.assertNotIn('diagnostics',json.loads(archive.read('evaluation.json')))
                for name in archive.namelist():
                    self.assertNotIn(b'secret-token',archive.read(name));self.assertNotIn(b'secret-number',archive.read(name))
            spec=importlib.util.spec_from_file_location('package_validator',Path('training/model-repository-template/scripts/validate_model_package.py'))
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            self.assertTrue(module.validate(path)['valid'])

    def test_changed_binary_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);self.setup_model(root);path,_=package_from_state(root)
            with zipfile.ZipFile(path) as original:data={n:original.read(n) for n in original.namelist()}
            data['models/plate.pt']=b'tampered'
            with zipfile.ZipFile(path,'w') as archive:
                for name,value in data.items():archive.writestr(name,value)
            with self.assertRaisesRegex(ValueError,'チェックサム'):
                verify_package(path)

    def test_validation_only_and_other_dataset_cannot_be_exported(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);area,dataset=self.setup_model(root)
            state=json.loads((area/'comparison-state.json').read_text());state['report']['evaluation_partition']='val'
            (area/'comparison-state.json').write_text(json.dumps(state))
            with self.assertRaisesRegex(ValueError,'テスト'):
                package_from_state(root)
            state['state']='running';(area/'comparison-state.json').write_text(json.dumps(state))
            with self.assertRaisesRegex(ValueError,'比較'):
                package_from_state(root)

    def test_git_publish_preserves_existing_tree_and_never_forces(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);self.setup_model(root)
            with patch.dict(os.environ,{'GATE_MODEL_EXPORT_LICENSE':'Internal reuse; source conditions apply'}):path,manifest=package_from_state(root)
            api=GitHubModels('UeEmon/AI-Gate-JP-Models','test-token');calls=[]
            def request(method,path,payload=None):
                calls.append((method,path,payload))
                if path=='':return dict(default_branch='main')
                if path=='git/ref/heads/main':return dict(object=dict(sha='parent'))
                if path=='git/commits/parent':return dict(tree=dict(sha='base'))
                if path.startswith('git/trees/base'):return dict(tree=[])
                return dict(sha='created')
            api.request=request;result=api.publish(path)
            self.assertEqual(result['state'],'published')
            tree=next(x[2] for x in calls if x[1]=='git/trees')
            self.assertEqual(tree['base_tree'],'base')
            self.assertEqual(calls[-1][2],dict(sha='created',force=False))
            self.assertTrue(all(x['path'].startswith('models/'+manifest['version']+'/') for x in tree['tree']))

    def test_package_download_is_authenticated_and_requires_csrf(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);manager=JobManager(root);app=create_app(manager=manager,password='');client=app.test_client();client.get('/')
            self.setup_model(root)
            with client.session_transaction() as session:headers={'X-CSRF-Token':session['csrf']}
            self.assertEqual(client.post('/api/paddle-training/package').status_code,403)
            response=client.post('/api/paddle-training/package',headers=headers)
            self.assertEqual(response.status_code,200);self.assertEqual(response.mimetype,'application/zip');response.close()
            with patch.dict(os.environ,{'GATE_MODEL_REPO_TOKEN':''}):
                self.assertEqual(client.post('/api/paddle-training/publish',headers=headers).status_code,400)
            manager.shutdown()
