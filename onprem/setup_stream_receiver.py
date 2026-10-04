"""Prepare the production RTMP/RTMPS ingest configuration and TLS material."""
from pathlib import Path
import re
import secrets
import subprocess


def setup(directory: Path):
    env_file = directory / '.env'
    if not env_file.is_file():
        raise ValueError('onprem/.env がありません。env.example から作成してください。')
    lines = env_file.read_text(encoding='utf-8').splitlines()
    values = [line.split('=', 1)[1].strip() for line in lines
              if re.match(r'^GATE_RTMP_STREAM_KEY\s*=', line)]
    if len(values) > 1:
        raise ValueError('GATE_RTMP_STREAM_KEY が重複しています。')
    key = values[0] if values else secrets.token_hex(16)
    if not re.fullmatch(r'[a-f0-9]{32}', key):
        raise ValueError('GATE_RTMP_STREAM_KEY は32文字の16進数で指定してください。')
    if not values:
        with env_file.open('a', encoding='utf-8') as output:
            output.write(f'\nGATE_RTMP_STREAM_KEY={key}\n')
    env_file.chmod(0o600)
    cert_dir = directory / 'rtmp-certs'
    cert_dir.mkdir(mode=0o700, exist_ok=True)
    private_key, certificate = cert_dir / 'server.key', cert_dir / 'server.crt'
    if certificate.exists() and not private_key.is_file():
        raise ValueError('RTMPSの証明書がありますが秘密鍵がありません。両方を配置してください。')
    if not certificate.exists() and private_key.is_file() and private_key.stat().st_size == 0:
        private_key.unlink()
    if not certificate.is_file():
        # macOS ships LibreSSL/OpenSSL versions that do not accept -noenc.
        # -nodes is supported by both and still writes an unencrypted key.
        creating_key = not private_key.is_file()
        command = ['openssl', 'req', '-x509']
        command += (['-newkey', 'rsa:2048', '-nodes', '-keyout', str(private_key)]
                    if creating_key else ['-key', str(private_key)])
        command += ['-out', str(certificate), '-days', '365', '-subj', '/CN=localhost']
        try:
            subprocess.run(command, check=True, capture_output=True, text=True)
        except (subprocess.CalledProcessError, FileNotFoundError) as error:
            certificate.unlink(missing_ok=True)
            if creating_key:
                private_key.unlink(missing_ok=True)
            detail = (error.stderr or str(error)).strip() if isinstance(error, subprocess.CalledProcessError) else str(error)
            raise RuntimeError('RTMPS証明書を生成できません: ' + detail[-600:]) from error
    private_key.chmod(0o600)
    path = f'gate-{key}'
    config_file = directory / 'stream-config' / 'mediamtx.yml'
    old_config = config_file.read_text(encoding='utf-8') if config_file.is_file() else ''
    rtmps_enabled = not re.search(
        r'''^rtmpEncryption: ["']?no["']?\s*$''', old_config, re.M)
    publish_enabled = '  - action: publish\n' in old_config if old_config else True
    config = ("rtsp: false\nhls: false\nwebrtc: false\nsrt: false\n"
              "api: true\napiAddress: :9997\nmetrics: false\nrtmp: true\n"
              f"rtmpEncryption: \"{'optional' if rtmps_enabled else 'no'}\"\nrtmpAddress: :1935\nrtmpsAddress: :1936\n"
              "rtmpServerKey: /rtmp-certs/server.key\n"
              "rtmpServerCert: /rtmp-certs/server.crt\n"
              "authInternalUsers:\n- user: any\n  permissions:\n"
              + (f"  - action: publish\n    path: {path}\n" if publish_enabled else "")
              + f"  - action: read\n    path: {path}\n"
              + f"- user: gate-admin\n  pass: {key}\n  permissions:\n  - action: api\n"
              + f"paths:\n  {path}:\n    source: publisher\n")
    config_file.parent.mkdir(mode=0o700, exist_ok=True)
    config_file.write_text(config, encoding='utf-8')
    config_file.chmod(0o600)
    return path


if __name__ == '__main__':
    path = setup(Path(__file__).resolve().parent)
    print(f'配信先 RTMP: rtmp://<MacのLAN IP>:1935/{path}')
    print(f'配信先 RTMPS: rtmps://<MacのLAN IP>:1936/{path}')
