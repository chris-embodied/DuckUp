"""Mac-side protocol checks. Fixtures exercise the serial parser without a physical device."""
import base64
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import struct
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE/'base'), str(HERE/'bridge')]
from base import FrameTickets, Monitor, default_observation_key
from bus import IDS, gravity, read_cycle, read_register
from sensors import Sensors
import provision


class BaseTests(unittest.TestCase):
    def test_signed_status_and_motion_refusal(self):
        key = hashlib.sha256(b"duck-base/observe-pin/v1:000000").digest()
        service = Monitor(default_observation_key(), Sensors({}), FrameTickets())
        nonce = json.loads(service.receive('phone', b'{"hello":1}'))['nonce']
        for seq, cmd in enumerate(['status','arm','move','home','relax','hold','skill'],1):
            data = json.dumps({'command':cmd}).encode()
            def mac(role, body):
                return hmac.new(key, f'{role}:{nonce}:{seq}:'.encode()+body, hashlib.sha256).hexdigest()
            req = {'body':base64.b64encode(data).decode(),'seq':seq,'mac':mac('request',data)}
            reply=json.loads(service.receive('phone',json.dumps(req).encode()))
            body=base64.b64decode(reply['body'])
            self.assertEqual(reply['mac'],mac('response',body))
            result=json.loads(body)
            if cmd == 'status':
                self.assertTrue(result['result']['read_only'])
                self.assertEqual(result['result']['health']['servo_count'],0)
                self.assertCountEqual(result['result']['health']['missing_ids'], IDS)
            else:
                self.assertIn('error',result)
            self.assertIsNone(service.receive('phone',json.dumps(req).encode()))

    def test_uart_echo_bad_checksum_and_split_reply(self):
        body=bytes((14,4,0,0x34,0x12))
        reply=b'\xff\xff'+body+bytes((~sum(body)&255,))
        request=bytes.fromhex('ffff0e04023802b1')
        with patch('bus.termios.tcflush'), patch('bus.os.write',return_value=8) as write, \
             patch('bus.select.select',return_value=([7],[],[])), \
             patch('bus.os.read',side_effect=[request+reply[:-1]+b'\x01'+reply[:4],reply[4:]]):
            self.assertEqual(read_register(7,14,56,2),bytes.fromhex('3412'))
            write.assert_called_once_with(7,request)

    def test_real_zero_quaternion_vector_and_stale_sequence(self):
        imu=struct.pack('<6eBBB',0,0,0,0,0,0,22,0,0)
        raw=struct.pack('<H4xBB',2048,78,29)
        def response(fd,device,address,size):
            return imu if device==200 else raw
        with patch('bus.read_register',side_effect=response):
            sample=read_cycle(7,{'mount':[1,0,0,0],'joints':[(2048,1)]*15},None)
            self.assertEqual(sample['angles'],[0]*15)
            self.assertEqual(sample['g'],[0,0,-1])
            self.assertTrue(sample['imu'])
            self.assertFalse(read_cycle(7,{},22)['imu'])
            self.assertIsNone(read_cycle(7,{},None)['angles'])
        self.assertIsNone(gravity(struct.pack('<6eBBB',0,0,0,math.nan,0,0,22,0,0),[1,0,0,0]))

    def test_frame_ticket_single_use_and_expiry(self):
        pool=FrameTickets()
        with patch('base.time.monotonic',return_value=5):
            a,b=pool.issue(),pool.issue()
            self.assertTrue(pool.take(a))
            self.assertFalse(pool.take(a))
        with patch('base.time.monotonic',return_value=10):
            self.assertFalse(pool.take(b))
        self.assertFalse(pool.take(''))

    def test_old_sensor_frame_ages_out(self):
        sensors=Sensors({})
        sensors.sample={'t':time.monotonic_ns()-2_000_000_000,'servos':{20:[10,7.8,28]}}
        self.assertFalse(sensors.status()['fresh'])
        self.assertEqual(sensors.status()['health']['servo_count'],0)

    def test_private_config_is_replaced_atomically(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'private.conf'
            provision.store(path,b'first',0o600)
            provision.store(path,b'second',0o600)
            self.assertEqual(path.read_bytes(),b'second')
            self.assertEqual(path.stat().st_mode & 0o777,0o600)

if __name__=='__main__':
    unittest.main()
