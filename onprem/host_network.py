"""Discover the physical macOS host address before launching Docker services."""
import ipaddress
from pathlib import Path
import platform
import re
import subprocess


def _command(args):
    try:
        return subprocess.run(args, check=True, capture_output=True, text=True,
                              timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ''


def detect_lan_ip():
    # Running this inside Docker would return the Linux VM/container address.
    if platform.system() != 'Darwin':
        return ''
    route = _command(['route', '-n', 'get', 'default'])
    match = re.search(r'^\s*interface:\s*(\S+)', route, re.M)
    interfaces = ([match[1]] if match and re.fullmatch(r'en\d+', match[1]) else [])
    interfaces += [name for name in _command(['ifconfig', '-l']).split()
                   if re.fullmatch(r'en\d+', name) and name not in interfaces]
    for interface in interfaces:
        value = _command(['ipconfig', 'getifaddr', interface])
        try:
            address = ipaddress.IPv4Address(value)
        except ipaddress.AddressValueError:
            continue
        if not (address.is_loopback or address.is_link_local or address.is_unspecified
                or address.is_multicast):
            return str(address)
    return ''


def refresh_lan_ip(directory: Path):
    env_file = directory / '.env'
    lines = env_file.read_text(encoding='utf-8').splitlines()
    pattern = re.compile(r'^\s*GATE_RTMP_PUBLIC_HOST\s*=')
    positions = [i for i, line in enumerate(lines) if pattern.match(line)]
    if len(positions) > 1:
        raise ValueError('GATE_RTMP_PUBLIC_HOST が重複しています。')
    address = detect_lan_ip()
    if platform.system() != 'Darwin':
        return ''  # Preserve explicitly configured hosts on other platforms.
    value = 'GATE_RTMP_PUBLIC_HOST=' + address
    if positions:
        lines[positions[0]] = value
    else:
        lines.append(value)
    env_file.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    env_file.chmod(0o600)
    return address
