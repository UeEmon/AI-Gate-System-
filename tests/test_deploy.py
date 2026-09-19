import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('dispatch', Path(__file__).resolve().parents[1] / 'deploy/dispatch.py')
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)


class DeploymentTests(unittest.TestCase):
    def test_rejects_shell_injection(self):
        with self.assertRaises(ValueError):
            d.command('bucket;id', 'releases/test', 'a'*64, 'b'*40, 'ap-northeast-1')

    def run_status(self, status):
        s3, ssm = Mock(), Mock()
        ssm.send_command.return_value = {'Command': {'CommandId': 'command-1'}}
        ssm.get_command_invocation.return_value = {'Status': status}
        with tempfile.NamedTemporaryFile() as f:
            f.write(b'test archive'); f.flush()
            env = dict(AWS_REGION='ap-northeast-1', DEPLOY_BUCKET='test-bucket',
                       INSTANCE_ID='i-0123456789abcdef0', GITHUB_SHA='a'*40,
                       GITHUB_RUN_ID='1', GITHUB_RUN_ATTEMPT='1')
            with patch.dict(os.environ, env), patch.object(d.sys, 'argv', ['dispatch', f.name]), \
                 patch.object(d.boto3, 'client', side_effect=[s3, ssm]), patch.object(d.time, 'sleep'):
                d.main()
        return s3, ssm

    def test_success_requires_remote_success(self):
        s3, ssm = self.run_status('Success')
        s3.upload_file.assert_called_once()
        script = ssm.send_command.call_args.kwargs['Parameters']['commands'][0]
        self.assertLess(script.index('sha256sum -c'), script.index('tar -xzf'))

    def test_remote_failure_is_failure(self):
        with self.assertRaises(RuntimeError):
            self.run_status('Failed')

    def test_timeout_is_not_success(self):
        with self.assertRaises(TimeoutError):
            self.run_status('InProgress')
