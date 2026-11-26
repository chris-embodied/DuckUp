"""Create robotd's sound bank in its service-owned StateDirectory before first use."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def prepare(source, bank, identity):
    seed = int.from_bytes(hashlib.sha256(identity.encode()).digest()[:4], 'big')
    marker = bank/'.duck-base-version'
    version = f'v260-{seed}\n'
    if marker.is_file() and marker.read_text() == version:
        return
    subprocess.run([str(source/'sounds'), 'ensure-bank', '--dir', str(bank), '--seed', str(seed), '--force'],
                   check=True, timeout=60)
    for wav in sorted((source/'emotion-sounds').glob('*/*.wav')):
        dest = bank/wav.relative_to(source/'emotion-sounds')
        dest.parent.mkdir(exist_ok=True)
        shutil.copyfile(wav,dest)
    with tempfile.NamedTemporaryFile(dir=bank, mode='w', delete=False) as tmp:
        tmp.write(version)
        tmp.flush()
        os.fsync(tmp.fileno())
    os.replace(tmp.name, marker)


if __name__ == '__main__':
    serial = Path('/proc/device-tree/serial-number')
    identity = serial.read_text().strip('\0\n ') if serial.exists() else Path('/etc/machine-id').read_text().strip()
    prepare(Path('/opt/duck-base/v260'), Path('/var/lib/duck-voice'), identity)
