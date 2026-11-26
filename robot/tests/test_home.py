"""Motion requests require the owner's key as well as the BLE session."""
import hashlib
import hmac
from unittest import TestCase
from unittest.mock import Mock
from test_base import default_observation_key
from home import HomeDriver
from bus import IDS


class HomeTests(TestCase):
    def setUp(self):
        self.key = bytes(range(32))
        self.driver = HomeDriver(self.key)
        self.robot = self.driver.robot = Mock()
        self.native = {'imu_ready': True, 'torque_state_confirmed': False,
                       'joints': [{'id': i, 'feedback_fresh': True} for i in IDS]}
        self.bus = {'cycle': 7, 'phase': 'control', 'fresh_sample': True,
                    'all_joints_fresh': True, 'native': self.native, 'policy_enabled': False}
        self.gravity = [0, 0, -1]
        self.robot.fresh_frame.return_value = {'safety': {'gravity': self.gravity}}
        self.robot.rpc.side_effect = {'robot.busStatus': self.bus, 'robot.health': {
            'healthy': True, 'bus': {'consecutive_errors': 0}}, 'robot.init': {'accepted': True},
            'robot.relax': {'accepted': True}}.__getitem__

    def proof(self, command, key=None):
        return hmac.digest(key or self.key, f'duck-base/home/v1:nonce:2:{command}'.encode(), hashlib.sha256).hex()

    def test_no_observer_or_replayed_motion(self):
        for proof, nonce, seq in [(self.proof('base_home', default_observation_key()), 'nonce', 2),
                                  (self.proof('base_relax'), 'nonce', 2),
                                  (self.proof('base_home'), 'nonce', 3),
                                  (self.proof('base_home'), 'old', 2)]:
            with self.assertRaises(ValueError):
                self.driver.command('base_home', proof, nonce, seq)
        self.robot.rpc.assert_not_called()

    def test_requires_upright_complete_feedback(self):
        self.assertTrue(self.driver.status()['base_home_ready'])
        self.gravity[:] = [1, 0, 0]
        with self.assertRaises(ValueError):
            self.driver.command('base_home', self.proof('base_home'), 'nonce', 2)
        self.gravity[:] = [0, 0, -1]
        self.native['joints'].pop()
        self.assertFalse(self.driver.status()['base_home_ready'])

    def test_relax_must_have_later_fresh_hardware_confirmation(self):
        self.native['torque_state_confirmed'] = True
        self.gravity[:] = [1, 0, 0]
        self.driver.command('base_relax', self.proof('base_relax'), 'nonce', 2)
        self.assertEqual(self.driver.status()['relax_state'], 'pending')
        self.bus['cycle'] += 1
        self.native['torque_state_confirmed'] = False
        self.bus['fresh_sample'] = False
        self.assertEqual(self.driver.status()['relax_state'], 'pending')
        self.bus['fresh_sample'] = True
        self.assertEqual(self.driver.status()['relax_state'], 'confirmed')
