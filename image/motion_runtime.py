"""Import only the runtime used by Duck Base from the operator's local V260 console."""
import hashlib
import json
from pathlib import Path
import re
import tarfile
import tomllib
from check_calibration import MOTION_MARKER

ARCHIVE = 'updates/Microduck-Robotd-FT6-R17.tar.gz'
ARCHIVE_SHA = 'd301e5e942eae36cb38e76729cdc97c1834ddacab23b10afea6d761b530ef20d'
LEASE_SHA = 'e2632ee7ec70598d3a90bead0ed744674e2603218955cb29c9dfe105662d4735'
BUILD = '1fa843-feetech-ft6-control.102'
MARKER = MOTION_MARKER
BASE = '/opt/duck-base/'
V260 = BASE + 'v260/'
DROPIN = '/etc/systemd/system/duck-home.service.d/'
VOICE_UNIT = '''[Unit]
Requires=duck-provision.service

[Service]
StateDirectory=duck-voice
StateDirectoryMode=0700
ExecStartPre=/usr/bin/python3 /opt/duck-base/prepare_voice.py
'''


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_runtime(console, board):
    manifest = json.loads((console/'BUILD_MANIFEST.json').read_text())
    if (manifest.get('console_version'), manifest.get('service_build')) != ('1.0.260', BUILD):
        raise ValueError('需要 Microduck Console V260 / control.102')
    archive = console/ARCHIVE
    lease = (console/'ai_input_lease.py').read_bytes()
    with archive.open('rb') as f:
        if hashlib.file_digest(f, 'sha256').hexdigest() != ARCHIVE_SHA or digest(lease) != LEASE_SHA:
            raise ValueError('V260 固件包或控制租约文件的 SHA-256 不符')
        f.seek(0)
        files = {}
        profile = tomllib.loads((board/'remote.toml').read_text())
        if (profile['bus'] != {'port':'/dev/ttyS2','protocol':'feetech','feetech_calibration':'/etc/duck-base/calibration.toml'}
                or profile['control']['hz'] != 50 or not profile['safety']['limp_fall']
                or profile['audio']['bank'] != V260 + 'voice-bank'):
            raise ValueError('运动配置与 ZERO 3W 飞特总线或音库不符')
        policy = profile['policy']
        paths = {policy[key] for key in ('walk','sitstand','ground_pick','kick_left','kick_right','roulade')}
        paths.update(skill['path'] for skill in policy['skill'] if 'path' in skill)
        model_dirs = set()
        for path in paths:
            if not re.fullmatch(re.escape(V260) + r'(models/[0-9a-f]{32}|roller/standup)/policy\.onnx', path):
                raise ValueError('unexpected policy path: ' + path)
            model_dirs.add(path.removeprefix(V260).rsplit('/',1)[0])
        with tarfile.open(fileobj=f, mode='r:gz') as bundle:
            prefix = 'Microduck-Robotd-FT6-R17/'
            for line in bundle.extractfile(prefix+'SHA256SUMS').read().decode().splitlines():
                expected, name = line.split('  ',1)
                root, _, _ = name.partition('/')
                parent = name.rsplit('/',1)[0]
                if not (name in {'robotd','sounds','LICENSE'} or root in {'emotion-sounds','onnxruntime'}
                        or (parent in model_dirs and Path(name).name in {
                            'policy.onnx','manifest.json','deployment-contract.json','deployment-runtime.json',
                            'deployment-execution.json','deployment-profile.toml','model-info.json'})):
                    continue
                member = bundle.getmember(prefix+name)
                if not member.isreg() or not re.fullmatch(r'[A-Za-z0-9._/-]+', name) or '..' in Path(name).parts:
                    raise ValueError('unexpected runtime member: ' + name)
                data = bundle.extractfile(member).read()
                if digest(data) != expected:
                    raise ValueError('runtime checksum: ' + name)
                destination = BASE+'robotd' if name=='robotd' else V260+name
                files[destination] = (data, 0o755 if name in ('robotd','sounds') else 0o644)

    for directory in model_dirs:
        model = json.loads(files[V260+directory+'/manifest.json'][0])
        data = files[V260+directory+'/policy.onnx'][0]
        if V260+directory+'/policy.onnx' == policy['sitstand']:
            contract = json.loads(files[V260+directory+'/deployment-contract.json'][0])
            if (model.get('task'), model.get('onnx_sha256'), contract['input_dim'], contract['output_dim']) != (
                    'Mjlab-SitStand-Flat-MicroDuck', digest(data), 61, 14):
                raise ValueError('incompatible SitStand export')
        elif (model['robot']['servos'], model['obs_len'], model['action_len'], model['robot']['control_hz']) != ('hd1910',61,14,50):
            raise ValueError('incompatible model: '+directory)
        if directory.startswith('models/') and not digest(data).startswith(Path(directory).name):
            raise ValueError('model id mismatch: '+directory)
    for path in (BASE+'robotd',V260+'sounds',V260+'onnxruntime/libonnxruntime.so.1.28.0',
                 V260+'onnxruntime/libonnxruntime_providers_shared.so'):
        header = files[path][0][:20]
        if header[:6] != b'\x7fELF\x02\x01' or int.from_bytes(header[18:20], 'little') != 183:
            raise ValueError('expected 64-bit ARM Linux binary: ' + path)
    files.update({
        BASE+'ai_input_lease.py': (lease,0o644),
        BASE+'prepare_voice.py': (Path(__file__).with_name('prepare_voice.py').read_bytes(),0o644),
        BASE+'DUCKMOT.ID': (MARKER,0o644),
        DROPIN+'full-control.conf': ((board/'full-control.conf').read_bytes(),0o644),
        DROPIN+'first-boot.conf': (VOICE_UNIT.encode(),0o644),
    })
    for name, checksum in {
        'THIRD_PARTY_NOTICES.md':'8945cfec5bd7897a3c08484505f1ae363e6e0b8b92178276b52c11ec1f10d576',
        'licenses/MODEL-NOTICE.md':'dc84832b859e6d71e33bc5f1e268825c9daf2963146997d9ada0dee03f38a8cd',
    }.items():
        data = (console/name).read_bytes()
        if digest(data) != checksum:
            raise ValueError('V260 notices checksum: '+name)
        files[V260+name] = (data,0o644)
    return files
