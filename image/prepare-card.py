#!/usr/bin/env python3
"""Personalize the configuration volume of a freshly flashed DuckUp card."""
import argparse
import base64
import fcntl
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile

from check_calibration import inspect_bytes, MOTION_MARKER


def atomic(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        tmp = Path(f.name)
        try:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
            os.replace(tmp, path)
            if path.read_bytes() != data:
                raise OSError(f'写入后校验失败：{path.name}')
        finally:
            tmp.unlink(missing_ok=True)


def receipt_dir(path):
    if path is not None:
        path.mkdir(mode=0o700, parents=True, exist_ok=False)
        return path
    parent = Path.home() / '.duckbase'
    parent.mkdir(mode=0o700, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix='zero3w-', dir=parent))


def public_key(path):
    key = ' '.join(path.read_text().split()[:2])
    if not re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/=]+', key):
        raise ValueError('请选择 Ed25519 SSH 公钥')
    return key


def create_owner_qr(receipt):
    """Render on the Mac; only file paths are passed to the system image APIs."""
    png = receipt/'duck-home-qr.png'
    with tempfile.NamedTemporaryFile(dir=receipt, delete=False) as f:
        tmp = Path(f.name)
    try:
        subprocess.run(['/usr/bin/osascript', '-l', 'JavaScript', Path(__file__).with_name('owner_qr.js'),
                        receipt/'duck-home.key', tmp], check=True, timeout=20, umask=0o077,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with tmp.open('rb') as f:
            if f.read(8) != b'\x89PNG\r\n\x1a\n':
                raise ValueError('没有生成可用的二维码图片')
            os.fchmod(f.fileno(), 0o600)
            os.fsync(f.fileno())
        os.replace(tmp, png)
    finally:
        tmp.unlink(missing_ok=True)
    return png


def prepare(config, ssid, password, public, calibration, receipt):
    config = config.resolve()
    if not 1 <= len(ssid.encode()) <= 32 or not 8 <= len(password.encode()) <= 63:
        raise ValueError('Wi-Fi 名称须为 1–32 字节，WPA 密码须为 8–63 字节')
    if (receipt or Path.home()/'.duckbase').resolve().is_relative_to(config):
        raise ValueError('私人回执目录须位于电脑上，请勿选在待配置的卡中')
    with (config/'DUCKBASE.ID').open('rb') as marker:
        if marker.read().strip() != b'duck-base-zero3w-v1':
            raise ValueError('所选目录不是 DuckUp 配置卷')
        fcntl.flock(marker, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if any((config/name).exists() for name in ('duck-setup.json','duck-calibration.toml','DUCKOWN.ID')):
            raise ValueError('这张卡已有配置。请保留原回执，或使用一张刚刷好的卡')
        motion_marker = config/'DUCKMOT.ID'
        motion = motion_marker.exists()
        if motion and motion_marker.read_bytes() != MOTION_MARKER:
            raise ValueError('无法识别此镜像的遥控版本')
        source = calibration.read_bytes() if calibration is not None else None
        if source is not None and motion:
            issues = inspect_bytes(source)
            if issues:
                raise ValueError('标定未通过检查：' + '；'.join(issues))
        default_pub = Path.home()/'.ssh/id_ed25519.pub'
        if public is None and default_pub.is_file():
            public = default_pub
        key = public_key(public) if public is not None else None
        receipt = receipt_dir(receipt)
        if key is None:
            private = receipt/'id_ed25519'
            subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-C','duck-base','-f',str(private)],check=True)
            key = public_key(private.with_suffix('.pub'))
        admin = secrets.token_urlsafe(32)
        psk = hashlib.pbkdf2_hmac('sha1', password.encode(), ssid.encode(), 4096, 32).hex()
        setup = dict(ssid=ssid, psk=psk, public_key=key, admin_password=admin)
        atomic(receipt/'radxa-password', (admin+'\n').encode())
        if source is not None and motion:
            owner_key = secrets.token_hex(32)
            atomic(receipt/'duck-home.key', (owner_key+'\n').encode())
            owner_id = hashlib.sha256(bytes.fromhex(owner_key)).hexdigest()[:16]
            setup['motion'] = dict(key=owner_key, key_id=owner_id, calibration_b64=base64.b64encode(source).decode(),
                                   calibration_sha256=hashlib.sha256(source).hexdigest())
        elif source is not None:
            # Legacy observation images consume the calibration from a separate file.
            atomic(config/'duck-calibration.toml', source)
        atomic(config/'duck-setup.json', json.dumps(setup).encode())
    return receipt


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('config',type=Path,help='刷好镜像后的 DuckUp 配置卷')
    cli.add_argument('--ssid',help='家庭 Wi-Fi 名称（省略时询问）')
    cli.add_argument('--public-key',type=Path,help='SSH 公钥，默认使用现有 Ed25519 公钥，否则自动生成')
    cli.add_argument('--calibration',type=Path,help='这只鸭子原来实际使用的 HD1910 标定文件')
    cli.add_argument('--receipt',type=Path,help='新的私人目录，默认在 ~/.duckbase 下创建')
    cli.add_argument('--reveal',action='store_true',help='完成后在 Mac 显示供 iPhone 扫描的遥控授权码')
    args = cli.parse_args()
    try:
        ssid = input('家庭 Wi-Fi 名称：') if args.ssid is None else args.ssid
        receipt = prepare(args.config, ssid, getpass.getpass('Wi-Fi 密码（仅本机输入）：'),
                          args.public_key, args.calibration, args.receipt)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        cli.exit(1, f'配置未完成：{exc}\n')
    print(f'首次启动配置已写入。请保留电脑上的私人目录：{receipt.resolve()}')
    owner_file = receipt/'duck-home.key'
    if owner_file.is_file():
        if args.reveal and sys.platform == 'darwin':
            try:
                png = create_owner_qr(receipt)
                subprocess.run(['/usr/bin/open', '-b', 'com.apple.Preview', png.resolve()], check=True, timeout=10)
                print('在 iPhone 鸭控台连接蓝牙设备 Duck Base，再扫描 Mac 显示的遥控授权码。')
                return
            except (OSError, ValueError, subprocess.SubprocessError):
                print('卡片已配置成功，遥控授权码未能显示。', file=sys.stderr)
        print('将私人目录中的 duck-home.key 传到自己的 iPhone，在鸭控台用「从文件导入」授权遥控。')
    else:
        print('本次配置为观察模式。')
        if args.reveal and sys.platform == 'darwin':
            try:
                subprocess.run(['/usr/bin/open', '-R', str(receipt.resolve())], check=True, timeout=10)
            except (OSError, subprocess.SubprocessError):
                print('访达未能打开，可按上方路径找到私人目录。', file=sys.stderr)


if __name__=='__main__':
    main()
