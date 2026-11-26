"""Import the per-device settings from the local Duck Base configuration volume."""
import base64
import grp
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import subprocess
import tempfile


def sync_parent(path):
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def store(path, data, mode, owner=None, gid=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        tmp = Path(f.name)
        try:
            f.write(data)
            if mode is not None:
                os.fchmod(f.fileno(), mode)
            if owner is not None:
                os.fchown(f.fileno(), owner.pw_uid, owner.pw_gid)
            elif gid is not None:
                os.fchown(f.fileno(), -1, gid)
            f.flush()
            os.fsync(f.fileno())
            os.replace(tmp, path)
            sync_parent(path)
        finally:
            tmp.unlink(missing_ok=True)


def matching(path, expected):
    if path.exists() and path.read_bytes() != expected:
        raise ValueError(f'与已有的 {path.name} 不一致，保留现有配置')


def motion_import(settings, config, destination, runtime_marker):
    from check_calibration import inspect_bytes, MOTION_MARKER

    if ((config/'DUCKMOT.ID').read_bytes() != MOTION_MARKER
            or runtime_marker.read_bytes() != MOTION_MARKER):
        raise ValueError('镜像没有兼容的运动运行环境')
    if not isinstance(settings, dict) or not isinstance(settings.get('key'), str):
        raise ValueError('无效的遥控配置')
    key_hex = settings['key']
    if not re.fullmatch(r'[0-9a-f]{64}', key_hex):
        raise ValueError('无效的遥控密钥格式')
    raw = base64.b64decode(settings['calibration_b64'], validate=True)
    if hashlib.sha256(raw).hexdigest() != settings['calibration_sha256']:
        raise ValueError('标定文件摘要不匹配')
    issues = inspect_bytes(raw)
    if issues:
        raise ValueError('标定未通过检查：' + '；'.join(issues))
    key_id = hashlib.sha256(bytes.fromhex(key_hex)).hexdigest()[:16]
    if settings.get('key_id') != key_id:
        raise ValueError('遥控密钥指纹不匹配')
    fingerprint = (key_id+'\n').encode()
    key = (key_hex+'\n').encode()
    matching(destination/'home.key', key)
    matching(destination/'calibration.toml', raw)
    matching(config/'DUCKOWN.ID', fingerprint)
    return raw, key, fingerprint


def configure_network(setup, root=Path('/'), run=subprocess.run):
    ssid, psk, pub = setup['ssid'], setup['psk'], setup['public_key']
    if not 1 <= len(ssid.encode()) <= 32 or not re.fullmatch('[a-f0-9]{64}',psk):
        raise ValueError('invalid SSID or PSK')
    if not re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/=]+',pub):
        raise ValueError('invalid SSH public key')
    password = setup['admin_password']
    if not re.fullmatch(r'[a-zA-Z0-9_-]{32,80}', password):
        raise ValueError('invalid per-device password')
    # The NM keyfile format accepts arbitrary SSID bytes as a semicolon-delimited list.
    encoded = ''.join(f'{n};' for n in ssid.encode())
    profile = ('[connection]\nid=duck-home\ntype=wifi\nautoconnect=true\n[wifi]\nmode=infrastructure\n'
               f'ssid={encoded}\n[wifi-security]\nkey-mgmt=wpa-psk\npsk={psk}\n'
               '[ipv4]\nmethod=auto\n[ipv6]\nmethod=auto\n')
    store(root/'etc/NetworkManager/system-connections/duck-home.nmconnection',profile.encode(),0o600)
    store(root/'etc/ssh/duck-keys/radxa', (pub+'\n').encode(), 0o644)
    os.chmod(root/'etc/ssh/duck-keys', 0o755)
    run(['systemctl','start','NetworkManager'],check=True)
    run(['nmcli','connection','reload'],check=True)
    run(['nmcli','--wait','0','connection','up','duck-home'],check=False)
    run(['chpasswd'],input='radxa:'+password+'\n',text=True,check=True)


def import_card(config, destination, runtime_marker, gid, network=configure_network):
    path = config/'duck-setup.json'
    legacy = config/'duck-calibration.toml'
    setup = json.loads(path.read_bytes()) if path.exists() else None
    motion = None
    if setup is not None and 'motion' in setup:
        if legacy.exists():
            raise ValueError('存在两份待导入标定，请核对新卡')
        motion = motion_import(setup['motion'], config, destination, runtime_marker)
    if legacy.exists() and (destination/'home.key').exists():
        if not (destination/'calibration.toml').is_file():
            raise ValueError('已有遥控密钥，但本机标定缺失')
        matching(destination/'calibration.toml', legacy.read_bytes())
    if setup is not None:
        network(setup)
    if motion is not None:
        calibration, key, fingerprint = motion
        store(destination/'calibration.toml', calibration, 0o644)
        os.chmod(destination, 0o755)
        # Publishing the key is the last operation that can enable control on this boot.
        store(destination/'home.key', key, 0o640, gid=gid)
        # FAT derives permissions from mount options.
        store(config/'DUCKOWN.ID', fingerprint, None)
    elif legacy.exists():
        store(destination/'calibration.toml', legacy.read_bytes(), 0o644)
        os.chmod(destination, 0o755)
        legacy.unlink()
        sync_parent(legacy)
    if setup is not None:
        path.unlink()
        sync_parent(path)


def main():
    os.umask(0o077)
    subprocess.run(['systemd-sysusers','/usr/lib/sysusers.d/duck-base.conf'],check=True)
    try:
        pwd.getpwnam('radxa')
    except KeyError:
        subprocess.run(['useradd','-m','-s','/bin/bash','-G','sudo','radxa'],check=True)
        subprocess.run(['chpasswd'],input='radxa:'+secrets.token_urlsafe(48)+'\n',text=True,check=True)
    import_card(Path('/config'), Path('/etc/duck-base'), Path('/opt/duck-base/DUCKMOT.ID'),
                grp.getgrnam('duckbase').gr_gid)
    subprocess.run(['rfkill','unblock','bluetooth'],check=False)
    try:
        subprocess.run(['bluetoothctl','power','on'],timeout=10,check=False)
    except subprocess.TimeoutExpired:
        pass


if __name__ == '__main__':
    main()
