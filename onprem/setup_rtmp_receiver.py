"""Provision a private LAN RTMP path for the GoPro receiver."""
from pathlib import Path
import re
import secrets


def setup(directory: Path):
    env_file = directory / '.env'
    if not env_file.is_file():
        raise ValueError('onprem/.env がありません。env.example から作成してください。')
    lines = env_file.read_text(encoding='utf-8').splitlines()
    keys = [line.split('=', 1)[1].strip() for line in lines
            if re.match(r'^GATE_RTMP_STREAM_KEY\s*=', line)]
    if len(keys) > 1:
        raise ValueError('GATE_RTMP_STREAM_KEY の設定が重複しています。')
    key = keys[0] if keys else secrets.token_hex(16)
    if not re.fullmatch(r'[a-f0-9]{32}', key):
        raise ValueError('GATE_RTMP_STREAM_KEY は32文字の16進数で指定してください。')
    if not keys:
        with env_file.open('a', encoding='utf-8') as output:
            output.write(f'\nGATE_RTMP_STREAM_KEY={key}\n')
    env_file.chmod(0o600)
    path = f'gopro-{key}'
    config = ("rtsp: false\n"
              "hls: false\n"
              "webrtc: false\n"
              "srt: false\n"
              "rtmp: true\n"
              "rtmpAddress: :1935\n"
              "api: false\n"
              "metrics: false\n"
              "authInternalUsers:\n"
              "- user: any\n"
              "  permissions:\n"
              f"  - action: publish\n    path: {path}\n"
              f"  - action: read\n    path: {path}\n"
              "paths:\n"
              f"  {path}:\n    source: publisher\n")
    config_path = directory / 'mediamtx.gopro.yml'
    config_path.write_text(config, encoding='utf-8')
    config_path.chmod(0o600)
    return path


if __name__ == '__main__':
    path = setup(Path(__file__).resolve().parent)
    print(f'GoPro等の送信先: rtmp://<MacのLAN IP>:1935/{path}')
    print(f'Mac直接起動の読取元: rtmp://127.0.0.1:1935/{path}')
