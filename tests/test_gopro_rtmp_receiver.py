import re
import tempfile
from pathlib import Path
import unittest

import yaml

from onprem.setup_rtmp_receiver import setup


class GoProReceiverTests(unittest.TestCase):
    def test_setup_generates_stable_private_path_and_restricts_media_server(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            env_file = folder / '.env'
            env_file.write_text('GATE_ADMIN_PASSWORD=example\n')
            path = setup(folder)
            self.assertRegex(path, r'^gopro-[a-f0-9]{32}$')
            self.assertEqual(setup(folder), path)
            self.assertEqual(len(re.findall(r'^GATE_RTMP_STREAM_KEY=',
                                            env_file.read_text(), re.M)), 1)
            self.assertEqual(env_file.stat().st_mode & 0o777, 0o600)
            config_file = folder / 'mediamtx.gopro.yml'
            self.assertEqual(config_file.stat().st_mode & 0o777, 0o600)
            config = yaml.safe_load(config_file.read_text())
            self.assertEqual(list(config['paths']), [path])
            self.assertEqual(config['paths'][path]['source'], 'publisher')
            self.assertTrue(config['rtmp'])
            self.assertFalse(config['rtsp'])
            self.assertFalse(config['hls'])
            self.assertFalse(config['webrtc'])
            self.assertEqual(config['authInternalUsers'][0]['permissions'],
                             [{'action': 'publish', 'path': path},
                              {'action': 'read', 'path': path}])

    def test_bad_stream_key_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            (folder / '.env').write_text('GATE_RTMP_STREAM_KEY=invalid\n')
            with self.assertRaises(ValueError):
                setup(folder)
            self.assertFalse((folder / 'mediamtx.gopro.yml').exists())
