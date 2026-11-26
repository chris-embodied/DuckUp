import base64
import json
import unittest
from unittest.mock import Mock, patch

from test_base import Monitor, FrameTickets, default_observation_key
from control import tag
from remote import BaseProfiles, BaseRemote, SHOW_ORIGINAL, SHOW_TRIALS


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.home = Mock(key=b'k'*32, feedback_ids=(0, ()))
        self.home.robot.status.return_value = dict(skills=['roulade'], expressions={},
                                                  fresh=True, health={'healthy': True})
        self.home.status.return_value = dict(read_only=True, fresh=True, health={})
        self.monitor = Monitor(default_observation_key(), self.home, FrameTickets(), self.home)
        self.remote = BaseRemote(self.monitor, Mock())
        self.service = BaseProfiles(self.monitor, self.remote)

    def hello(self, owner=False):
        hello = {'hello': 1}
        if owner:
            hello['profile'] = 'control'
        return json.loads(self.service.receive('phone', json.dumps(hello).encode()))['nonce']

    def request(self, key, nonce, command, seq=1):
        body = json.dumps({'command': command}).encode()
        packet = dict(body=base64.b64encode(body).decode(), seq=seq,
                      mac=tag(key, b'request', nonce, seq, body))
        reply = self.service.receive('phone', json.dumps(packet).encode())
        if reply:
            wrapped = json.loads(reply)
            payload = base64.b64decode(wrapped['body'])
            self.assertEqual(wrapped['mac'], tag(key, b'response', nonce, seq, payload))
            return json.loads(payload)

    @patch('remote.time.sleep')
    def test_trial_requires_recent_stationary_home_samples(self, sleep):
        report = dict(homed=True, all_joints_fresh=True, fresh_sample=True,
                      policy_enabled=True, native={'torque_state_confirmed': True})
        self.remote.robot.rpc.return_value = report
        frame = dict(policy='walk', safety=dict(fallen=False, limp=False, gravity=[0,0,-1]),
                     imu={'gyro': [0,0,0]}, move={'requested':[0,0,0], 'applied':[0,0,0]})
        self.remote.robot.fresh_frame.side_effect = [dict(frame,t_ns=i) for i in range(4)]
        self.remote._check_trial_start()
        for changes in [dict(t_ns=3), dict(t_ns=4,policy='sit'),
                        dict(t_ns=4,move={'requested':[.1,0,0], 'applied':[0,0,0]}),
                        dict(t_ns=4,imu={'gyro':[0,.5,0]})]:
            self.remote.robot.fresh_frame.side_effect = [dict(frame,t_ns=3), dict(frame,**changes)]
            with self.assertRaises(ValueError):
                self.remote._check_trial_start()
        report['native']['torque_state_confirmed'] = False
        with self.assertRaises(ValueError):
            self.remote._check_trial_start()

    def test_trial_directory_and_commands(self):
        phone = 'phone'
        trial = 'SuperMimic 左脚踢球'
        names = ['kick_left', trial, 'SuperMimic 前行试走']
        robot = self.remote.robot
        robot.status.side_effect = lambda: dict(skills=names.copy(), expressions={'smile':'笑'}, health={})
        self.remote.sessions[phone] = ('nonce', 2)
        self.remote._check_trial_start = Mock()
        self.remote._ready = Mock(return_value={'policy':'walk'})
        policies = {'skills':names}
        robot.rpc.side_effect = lambda action, *args: policies if action=='robot.policies' else {'accepted':True}
        robot.actions.return_value = (names, {})
        def command(kind, **kw):
            return self.remote._command(phone, dict(command=kind, **kw))
        with self.assertRaises(ValueError):
            command('skill', name=SHOW_TRIALS)
        self.remote.owner, self.remote.lease, self.remote.enabled = phone, Mock(), True
        command('skill', name=SHOW_TRIALS)
        s = command('status')
        self.assertEqual(s['skills'], [SHOW_ORIGINAL, trial, 'SuperMimic 前行试走'])
        self.assertEqual(s['expressions'], {})
        command('skill', name='kick_left')
        robot.rpc.assert_called_with('robot.do', {'skill': trial})
        with self.assertRaises(ValueError):
            command('move', vx=0, vy=0, vyaw=.3)
        with self.assertRaises(ValueError):
            command('skill',name='sit_toggle')
        command('skill', name=SHOW_ORIGINAL)
        command('skill', name='kick_left')
        robot.rpc.assert_called_with('robot.do', {'skill':'kick_left'})
        self.assertIn(SHOW_TRIALS, command('status')['skills'])

    def test_private_challenge_and_downgrade(self):
        public = default_observation_key()
        nonce = self.hello()
        self.assertEqual(self.request(public, nonce, 'status')['result']['base_remote_version'], 1)
        self.assertIn('error', self.request(public, nonce, 'arm', 2))
        nonce = self.hello(owner=True)
        self.assertIsNone(self.request(public, nonce, 'arm'))
        self.assertTrue(self.request(self.home.key, nonce, 'status')['result']['duck_base'])
        self.assertIsNone(self.request(self.home.key, nonce, 'status'))
        self.hello()
        self.assertNotIn('phone', self.remote.sessions)
        self.assertIsNone(self.request(self.home.key, nonce, 'arm', 2))
        self.home.robot.rpc.assert_not_called()

    def test_owner_observation_uses_camera_ticket(self):
        self.monitor._command = Mock(return_value={'frame_ticket': 'test'})
        nonce = self.hello(owner=True)
        self.assertEqual(self.request(self.home.key, nonce, 'observe')['result']['frame_ticket'], 'test')
