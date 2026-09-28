import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aigate.stream_receiver import StreamReceiver
from onprem.setup_stream_receiver import setup


class StreamReceiverTests(unittest.TestCase):
    def test_provision_toggle_and_reprovision_preserves_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / '.env').write_text('GATE_ADMIN_PASSWORD=test\n')
            path = setup(directory)
            key = path.removeprefix('gate-')
            receiver = StreamReceiver(directory / 'stream-config/mediamtx.yml', key)
            self.assertTrue(receiver.settings()['publish_enabled'])
            receiver.update(False, False)
            self.assertFalse(receiver.settings()['publish_enabled'])
            self.assertFalse(receiver.settings()['rtmps_enabled'])
            self.assertEqual(path, setup(directory))
            self.assertFalse(receiver.settings()['publish_enabled'])
            self.assertFalse(receiver.settings()['rtmps_enabled'])
            receiver.update(True, True)
            self.assertTrue(receiver.settings()['publish_enabled'])
            self.assertTrue(receiver.settings()['rtmps_enabled'])

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


if __name__ == '__main__':
    unittest.main()
