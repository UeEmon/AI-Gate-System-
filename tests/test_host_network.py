import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from onprem.host_network import detect_lan_ip, refresh_lan_ip


class HostNetworkTests(unittest.TestCase):
    @patch('onprem.host_network.platform.system', return_value='Darwin')
    def test_mac_route_selects_real_host_ip(self, _):
        results = {('route', '-n', 'get', 'default'): 'interface: en0',
                   ('ifconfig', '-l'): 'lo0 en0 en1',
                   ('ipconfig', 'getifaddr', 'en0'): '192.168.3.13'}
        with patch('onprem.host_network._command', side_effect=lambda args: results.get(tuple(args), '')):
            self.assertEqual(detect_lan_ip(), '192.168.3.13')

    @patch('onprem.host_network.platform.system', return_value='Darwin')
    def test_vpn_uses_physical_interface_and_rejects_link_local(self, _):
        results = {('route', '-n', 'get', 'default'): 'interface: utun2',
                   ('ifconfig', '-l'): 'lo0 en0 en1 utun2',
                   ('ipconfig', 'getifaddr', 'en0'): '169.254.1.2',
                   ('ipconfig', 'getifaddr', 'en1'): '192.168.3.13'}
        with patch('onprem.host_network._command', side_effect=lambda args: results.get(tuple(args), '')):
            self.assertEqual(detect_lan_ip(), '192.168.3.13')

    @patch('onprem.host_network.platform.system', return_value='Darwin')
    def test_refresh_updates_address_and_clears_stale_value_on_failure(self, _):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            env = directory / '.env'
            env.write_text('GATE_ADMIN_PASSWORD=secret\nGATE_RTMP_PUBLIC_HOST=192.168.1.10\n')
            with patch('onprem.host_network.detect_lan_ip', return_value='192.168.3.13'):
                self.assertEqual(refresh_lan_ip(directory), '192.168.3.13')
            self.assertIn('GATE_RTMP_PUBLIC_HOST=192.168.3.13\n', env.read_text())
            self.assertIn('GATE_ADMIN_PASSWORD=secret\n', env.read_text())
            with patch('onprem.host_network.detect_lan_ip', return_value=''):
                refresh_lan_ip(directory)
            self.assertIn('GATE_RTMP_PUBLIC_HOST=\n', env.read_text())

    @patch('onprem.host_network.platform.system', return_value='Linux')
    def test_container_ip_is_not_used(self, _):
        with patch('onprem.host_network._command') as command:
            self.assertEqual(detect_lan_ip(), '')
            command.assert_not_called()
