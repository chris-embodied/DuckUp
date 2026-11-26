"""Exercise a calibrated owner's first remote session in temporary folders.

    python3 image/test_first_remote.py
    python3 image/test_first_remote.py --image /path/to/duck-base-zero3w-motion-v1.img

Image mode checks the builder's adjacent verification.json and reads both FAT
markers with mtype. It opens only a regular image file. Fixtures replace the
old calibration and the hardware feedback. No Bluetooth, servo, Wi-Fi, user
account, physical card or installed service is accessed.
"""
import argparse
import base64
from functools import partial
import hashlib
import hmac
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock

IMAGE = None
MTYPE = 'mtype'
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT/'robot/base'), str(ROOT/'robot/bridge'),
                str(ROOT/'robot/tests'), str(ROOT/'robot/bridge-tests')]
from base import FrameTickets, Monitor, calibration, default_observation_key
from bus import IDS
from check_calibration import MOTION_MARKER
from home import HomeDriver
from provision import configure_network, import_card
from remote import BaseProfiles, BaseRemote
from test_bridge import LeaseStub, RobotStub
from test_card_migration import prepare_card, synthetic_calibration


def image_markers(image, mtype):
    if not stat.S_ISREG(image.stat().st_mode):
        raise ValueError('只支持普通镜像文件')
    report = json.loads(image.with_name('verification.json').read_text())
    with image.open('rb') as disk:
        if hashlib.file_digest(disk, 'sha256').hexdigest() != report['image_sha256']:
            raise ValueError('镜像与同目录校验报告不匹配')
        disk.seek(512)
        header = disk.read(512)
        if header[:8] != b'EFI PART':
            raise ValueError('GPT header missing')
        disk.seek(struct.unpack_from('<Q',header,72)[0]*512+32)
        fat_start = struct.unpack('<Q',disk.read(8))[0]*512
    paths = {
        **{name:ROOT/'robot/base'/name for name in ('base.py','provision.py','home.py','remote.py','sensors.py','bus.py')},
        **{name:ROOT/'robot/bridge'/name for name in ('control.py','observation.py')},
        'check_calibration.py':HERE/'check_calibration.py',
    }
    verified = report['verified_files']
    for name, local in paths.items():
        if hashlib.sha256(local.read_bytes()).hexdigest() != verified['/opt/duck-base/'+name]:
            raise ValueError('镜像和本地验收所用的源文件不一致：'+name)
    if verified['/opt/duck-base/DUCKMOT.ID'] != hashlib.sha256(MOTION_MARKER).hexdigest():
        raise ValueError('rootfs 缺少运动标记')
    markers = {}
    for name in ('DUCKBASE.ID','DUCKMOT.ID'):
        markers[name] = subprocess.check_output([mtype,'-i',f'{image}@@{fat_start}','::'+name])
    return markers


class SimulatedRobot(RobotStub):
    def status(self):
        return {**super().status(), 'skills':self.policies['skills'], 'expressions':{}}


class Phone:
    """Sign both profiles independently using the same byte format as the iPhone."""
    def __init__(self, server, peer, key, control=False):
        self.server, self.peer, self.key = server, peer, key
        hello = {'hello':1,'profile':'control'} if control else {'hello':1}
        self.nonce = json.loads(server.receive(peer,json.dumps(hello).encode()))['nonce']
        self.seq = 0

    def send(self, command, **args):
        self.seq += 1
        suffix = f':{self.nonce}:{self.seq}:'.encode()
        body = json.dumps({'command': command, **args}, separators=(',',':')).encode()
        packet = dict(seq=self.seq,body=base64.b64encode(body).decode(),
                      mac=hmac.digest(self.key,b'request'+suffix+body,'sha256').hex())
        reply = self.server.receive(self.peer,json.dumps(packet).encode())
        if reply is None:
            return None
        signed = json.loads(reply)
        payload = base64.b64decode(signed['body'],validate=True)
        mac = hmac.digest(self.key,b'response'+suffix+payload,'sha256').hex()
        if signed['seq'] != self.seq or not hmac.compare_digest(signed['mac'],mac):
            raise AssertionError('Invalid board response')
        return json.loads(payload)


class FirstRemoteTest(unittest.TestCase):
    def test_owner_first_remote(self):
        markers = image_markers(IMAGE, MTYPE) if IMAGE is not None else {
            'DUCKBASE.ID':b'duck-base-zero3w-v1\n', 'DUCKMOT.ID':MOTION_MARKER}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mac_card = root/'fresh-config'
            mac_card.mkdir()
            for name, data in markers.items():
                (mac_card/name).write_bytes(data)
            old_cal = root/'existing-calibration.toml'
            old_cal.write_bytes(synthetic_calibration())
            pub = root/'test.pub'
            pub.write_text('ssh-ed25519 ZmFrZQ== synthetic-fixture\n')
            receipt = prepare_card.prepare(mac_card,'lab-network','lab-password',pub,old_cal,root/'receipt')
            config = shutil.copytree(mac_card,root/'booted-config')
            os_root = root/'guest'
            dest = os_root/'etc/duck-base'
            runtime_marker = os_root/'opt/duck-base/DUCKMOT.ID'
            runtime_marker.parent.mkdir(parents=True)
            runtime_marker.write_bytes(MOTION_MARKER)
            import_card(config,dest,runtime_marker,os.getgid(),
                        network=partial(configure_network,root=os_root,run=Mock()))
            self.assertFalse((config/'duck-setup.json').exists())
            self.assertEqual((dest/'calibration.toml').read_bytes(),old_cal.read_bytes())
            self.assertEqual(len(calibration(dest/'calibration.toml')['joints']),15)
            device_key = bytes.fromhex((dest/'home.key').read_text())
            client_key = bytes.fromhex((receipt/'duck-home.key').read_text())
            self.assertTrue(hmac.compare_digest(device_key,client_key))
            self.assertEqual((config/'DUCKOWN.ID').read_text().strip(),hashlib.sha256(client_key).hexdigest()[:16])

            home = HomeDriver(device_key)
            robot = home.robot = SimulatedRobot()
            robot.policies['homed'] = False
            robot.bus_report.update(all_joints_fresh=True,policy_enabled=False)
            native = robot.bus_report['native']
            native.update(imu_ready=True,torque_state_confirmed=False,
                          joints=[{'id':i,'feedback_fresh':True} for i in IDS])
            robot.frame['safety']['gravity'] = [0,0,-1]
            home.feedback_ids = time.monotonic(), IDS
            observer_key = default_observation_key()
            monitor = Monitor(observer_key,home,FrameTickets(),home)
            server = BaseProfiles(monitor,BaseRemote(monitor,LeaseStub))

            visitor = Phone(server,'visitor',observer_key)
            observed = visitor.send('status')['result']
            self.assertTrue(observed['read_only'])
            self.assertEqual(observed['base_home_key'],home.key_id)
            self.assertIn('error',visitor.send('home'))
            self.assertIn('error',visitor.send('move',vx=.08,vy=0,vyaw=0))
            self.assertIsNone(Phone(server,'wrong-key',observer_key,control=True).send('arm'))
            self.assertFalse(any(name in ('robot.init','robot.enable','robot.move') for name,_ in robot.calls))

            owner = Phone(server,'owner',client_key,control=True)
            self.assertEqual(owner.send('status')['result']['health']['servo_count'],15)
            self.assertEqual(owner.send('arm')['result'],{'accepted':True})
            robot.frame['safety']['gravity'] = [1,0,0]
            self.assertIn('error',owner.send('home'))
            robot.frame['safety']['gravity'] = [0,0,-1]
            last = native['joints'].pop()
            self.assertIn('error',owner.send('home'))
            self.assertNotIn(('robot.init',None),robot.calls)
            native['joints'].append(last)
            self.assertEqual(owner.send('home')['result'],{'accepted':True})
            self.assertEqual(robot.calls[-1],('robot.init',None))
            self.assertIn('error',owner.send('enable'))
            robot.bus_report['cycle'] += 1
            native['torque_state_confirmed'] = True
            robot.policies['homed'] = True
            self.assertEqual(owner.send('enable')['result'],{'accepted':True})
            self.assertEqual(robot.calls[-1],('robot.enable',{'on':True}))
            robot.bus_report['policy_enabled'] = True
            velocity = dict(vx=.08,vy=0,vyaw=0)
            self.assertEqual(owner.send('move',**velocity)['result'],{'accepted':True})
            self.assertEqual(robot.calls[-1],('robot.move',velocity))
            self.assertEqual(owner.send('relax')['result'],{'accepted':True})
            self.assertIn(('robot.relax',None),robot.calls)
            self.assertEqual(owner.send('status')['result']['relax_state'],'pending')
            native['torque_state_confirmed'] = False
            robot.bus_report['policy_enabled'] = False
            self.assertEqual(owner.send('status')['result']['relax_state'],'pending')
            robot.bus_report['cycle'] += 1
            self.assertEqual(owner.send('status')['result']['relax_state'],'confirmed')
            self.assertIn('error',owner.send('move',**velocity))


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    cli.add_argument('--image',type=Path,help='builder output IMG beside verification.json')
    cli.add_argument('--mtype',default='mtype',help='path to the mtools mtype executable')
    args = cli.parse_args()
    IMAGE, MTYPE = args.image, args.mtype
    unittest.main(argv=[sys.argv[0]],verbosity=2)
