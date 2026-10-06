import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import subprocess
import yaml

from aigate.stream_receiver import StreamReceiver
from onprem.setup_stream_receiver import setup, enable_lan


class StreamReceiverTests(unittest.TestCase):
    def test_enable_lan_preserves_ports_credentials_and_web_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            env = directory / '.env'
            env.write_text('GATE_RTMP_BIND_ADDRESS="127.0.0.1"\n'
                           'GATE_BIND_ADDRESS=127.0.0.1\nGATE_RTMP_PORT=21935\n'
                           'GATE_ADMIN_PASSWORD=secret\n')
            enable_lan(directory)
            once = env.read_text()
            enable_lan(directory)
            self.assertEqual(once, env.read_text())
            self.assertIn('GATE_RTMP_BIND_ADDRESS=0.0.0.0\n', once)
            self.assertIn('GATE_RTMPS_BIND_ADDRESS=0.0.0.0\n', once)
            self.assertIn('GATE_RTMP_PORT=21935\n', once)
            self.assertIn('GATE_BIND_ADDRESS=127.0.0.1\n', once)
            self.assertIn('GATE_ADMIN_PASSWORD=secret\n', once)

    def test_enable_lan_rejects_duplicates_without_changing_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            env = directory / '.env'
            before = 'GATE_RTMPS_BIND_ADDRESS=127.0.0.1\nGATE_RTMPS_BIND_ADDRESS=::1\n'
            env.write_text(before)
            with self.assertRaisesRegex(ValueError, '重複'):
                enable_lan(directory)
            self.assertEqual(before, env.read_text())

    def test_mac_compatible_certificate_and_partial_key_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / '.env').write_text('GATE_ADMIN_PASSWORD=test\n')
            setup(directory)
            certificate = directory / 'rtmp-certs/server.crt'
            certificate.unlink()
            setup(directory)
            self.assertTrue(certificate.is_file())

    def test_failed_generation_removes_partial_files_and_shows_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / '.env').write_text('GATE_ADMIN_PASSWORD=test\n')
            def fail(command, **kwargs):
                (directory / 'rtmp-certs/server.key').write_text('partial')
                raise subprocess.CalledProcessError(1, command, stderr='unsupported option')
            with patch('onprem.setup_stream_receiver.subprocess.run', side_effect=fail) as run:
                with self.assertRaisesRegex(RuntimeError, 'unsupported option'):
                    setup(directory)
            self.assertIn('-nodes', run.call_args.args[0])
            self.assertFalse((directory / 'rtmp-certs/server.key').exists())

    def test_provision_toggle_and_reprovision_preserves_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / '.env').write_text('GATE_ADMIN_PASSWORD=test\n')
            path = setup(directory)
            key = path.removeprefix('gate-')
            receiver = StreamReceiver(directory / 'stream-config/mediamtx.yml', key)
            self.assertTrue(receiver.settings()['publish_enabled'])
            receiver.update(False, False)
            self.assertEqual(yaml.safe_load(receiver.config.read_text())['rtmpEncryption'], 'no')
            self.assertFalse(receiver.settings()['publish_enabled'])
            self.assertFalse(receiver.settings()['rtmps_enabled'])
            self.assertEqual(path, setup(directory))
            self.assertFalse(receiver.settings()['publish_enabled'])
            self.assertFalse(receiver.settings()['rtmps_enabled'])
            receiver.update(True, True)
            self.assertEqual(yaml.safe_load(receiver.config.read_text())['rtmpEncryption'], 'optional')
            self.assertTrue(receiver.settings()['publish_enabled'])
            self.assertTrue(receiver.settings()['rtmps_enabled'])

    def test_legacy_unquoted_off_is_repaired_and_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / '.env').write_text('GATE_ADMIN_PASSWORD=test\n')
            path = setup(directory)
            receiver = StreamReceiver(directory / 'stream-config/mediamtx.yml', path[5:])
            receiver.config.write_text(receiver.config.read_text().replace(
                'rtmpEncryption: "optional"', 'rtmpEncryption: no'))
            receiver.update(True, False)
            self.assertEqual(yaml.safe_load(receiver.config.read_text())['rtmpEncryption'], 'no')
            setup(directory)
            self.assertEqual(yaml.safe_load(receiver.config.read_text())['rtmpEncryption'], 'no')
            with patch.object(receiver, '_get', return_value={'items': []}) as request:
                self.assertTrue(receiver.status()['online'])
            self.assertNotIn('/v3/rtmps/conns/list', [c.args[0] for c in request.call_args_list])

    def test_status_distinguishes_publisher_and_reader(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / '.env').write_text('GATE_ADMIN_PASSWORD=test\n')
            path = setup(directory)
            receiver = StreamReceiver(directory / 'stream-config/mediamtx.yml', path[5:])
            def response(endpoint):
                if endpoint.endswith('paths/list'):
                    return {'items': [{'name': path, 'ready': True, 'tracks': [{'codec': 'H264'}]}]}
                if 'rtmps' in endpoint:
                    return {'items': [{'path': path, 'state': 'publish', 'bytesReceived': 321}]}
                return {'items': [{'path': path, 'state': 'read'}]}
            with patch.object(receiver, '_get', side_effect=response):
                status = receiver.status()
            self.assertEqual(status['publisher'], {'protocol': 'rtmps', 'bytes_received': 321})
            self.assertEqual(status['readers'], 1)
            self.assertTrue(status['ready'])
            self.assertEqual(status['tracks'], ['H264'])

    def test_status_handles_mediamtx_string_tracks_and_empty_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / '.env').write_text('GATE_ADMIN_PASSWORD=test\n')
            path = setup(directory)
            receiver = StreamReceiver(directory / 'stream-config/mediamtx.yml', path[5:])
            for tracks, expected in [(['H264', 'MPEG4Audio'], ['H264', 'MPEG4Audio']),
                                     ([{'codec': 'H264'}, 'AAC'], ['H264', 'AAC']),
                                     (None, []), ([], [])]:
                with self.subTest(tracks=tracks):
                    def response(endpoint):
                        if endpoint.endswith('paths/list'):
                            return {'items': [{'name': path, 'ready': True, 'tracks': tracks}]}
                        return {'items': []}
                    with patch.object(receiver, '_get', side_effect=response):
                        status = receiver.status()
                    self.assertTrue(status['online'])
                    self.assertEqual(status['tracks'], expected)
            with patch.object(receiver, '_get', return_value={'items': []}):
                status = receiver.status()
            self.assertTrue(status['online'])
            self.assertFalse(status['ready'])
            self.assertEqual(status['tracks'], [])


if __name__ == '__main__':
    unittest.main()
