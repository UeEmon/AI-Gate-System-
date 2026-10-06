"""Manage the private MediaMTX ingest service from the authenticated web app."""
import base64
import os
from pathlib import Path
import re
import tempfile
import threading
from urllib.request import Request, urlopen


class StreamReceiver:
    def __init__(self, config=None, key=None, api_url=None):
        self.config = Path(config or os.getenv('GATE_RTMP_CONFIG', '/stream-config/mediamtx.yml'))
        self.key = key if key is not None else os.getenv('GATE_RTMP_STREAM_KEY', '')
        self.api_url = (api_url or os.getenv('GATE_RTMP_API_URL', 'http://rtmp-ingest:9997')).rstrip('/')
        self.lock = threading.Lock()

    @property
    def available(self):
        return bool(self.key and re.fullmatch(r'[a-f0-9]{32}', self.key) and self.config.is_file())

    def settings(self):
        if not self.available:
            return {'available': False}
        content = self.config.read_text(encoding='utf-8')
        return {'available': True,
                'rtmps_enabled': bool(re.search(
                    r'''^rtmpEncryption: ["']?optional["']?\s*$''', content, re.M)),
                'publish_enabled': '  - action: publish\n' in content,
                'path': f'gate-{self.key}',
                'public_host': os.getenv('GATE_RTMP_PUBLIC_HOST', ''),
                'rtmp_port': int(os.getenv('GATE_RTMP_PORT', '1935')),
                'rtmps_port': int(os.getenv('GATE_RTMPS_PORT', '1936'))}

    def update(self, publish_enabled, rtmps_enabled):
        if not self.available:
            raise ValueError('受信サーバーの設定がありません。')
        if type(publish_enabled) is not bool or type(rtmps_enabled) is not bool:
            raise ValueError('設定値は真偽値で指定してください。')
        with self.lock:
            original = self.config.read_text(encoding='utf-8')
            path = f'gate-{self.key}'
            if f'    path: {path}\n' not in original or f'  {path}:\n' not in original:
                raise ValueError('配信パスの設定が一致しません。')
            content, count = re.subn(r'''^rtmpEncryption: ["']?(?:optional|no)["']?$''',
                                     'rtmpEncryption: "optional"' if rtmps_enabled else 'rtmpEncryption: "no"',
                                     original, count=1, flags=re.M)
            if count != 1:
                raise ValueError('RTMPS設定を読み取れません。')
            permission = f'  - action: publish\n    path: {path}\n'
            if publish_enabled and permission not in content:
                content = content.replace('authInternalUsers:\n- user: any\n  permissions:\n',
                                          'authInternalUsers:\n- user: any\n  permissions:\n' + permission, 1)
            elif not publish_enabled:
                content = content.replace(permission, '', 1)
            if content != original:
                fd, tmp = tempfile.mkstemp(prefix='.mediamtx-', dir=self.config.parent)
                try:
                    with os.fdopen(fd, 'w', encoding='utf-8') as output:
                        output.write(content)
                    os.chmod(tmp, 0o600)
                    os.replace(tmp, self.config)
                finally:
                    if os.path.exists(tmp):
                        os.unlink(tmp)
        return self.settings()

    def _get(self, endpoint):
        token = base64.b64encode(f'gate-admin:{self.key}'.encode()).decode()
        request = Request(self.api_url + endpoint, headers={'Authorization': 'Basic ' + token})
        with urlopen(request, timeout=2) as response:
            import json
            return json.load(response)

    def status(self):
        config = self.settings()
        if not config['available']:
            return {'online': False, 'settings': config, 'publisher': None, 'readers': 0}
        try:
            path = next((item for item in self._get('/v3/paths/list').get('items', [])
                         if item.get('name') == config['path']), {})
            connections = []
            for protocol in (('rtmp', 'rtmps') if config['rtmps_enabled'] else ('rtmp',)):
                connections.extend({**item, 'protocol': protocol}
                                   for item in self._get(f'/v3/{protocol}/conns/list').get('items', []))
        except Exception:
            return {'online': False, 'settings': config, 'publisher': None, 'readers': 0}
        publisher = next((item for item in connections if item.get('state') == 'publish'
                          and item.get('path') == config['path']), None)
        return {'online': True, 'settings': config,
                'publisher': {'protocol': publisher['protocol'],
                              'bytes_received': publisher.get('bytesReceived', 0)} if publisher else None,
                'readers': len([item for item in connections if item.get('state') == 'read'
                                and item.get('path') == config['path']]),
                'ready': bool(path.get('ready', False)),
                'tracks': [track if isinstance(track, str) else track.get('codec', '')
                           for track in (path.get('tracks') or [])
                           if isinstance(track, (str, dict))]}
