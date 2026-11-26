"""Bench HOME/relax using the V260 HD1910 driver as the sole serial owner."""
import hashlib
import hmac
import logging
import math
import time

from bus import IDS
from control import Robot
from sensors import RUN, publish
import json


class HomeDriver:
    def __init__(self, key, path='/run/duck-home/robot.sock'):
        self.key = key
        self.key_id = hashlib.sha256(key).hexdigest()[:16]
        self.robot = Robot(path)
        self.last_request = None
        self.before = 0
        self.requested_at = 0
        self.feedback_ids = (0, ())

    def frame(self):
        return self.robot.fresh_frame()

    def poll(self):
        self.robot.start()
        while True:
            try:
                report = self.robot.rpc('robot.busStatus')
                native = report.get('native') or {}
                rows = {r['id']: [r['position_raw'], r['volts'], r['temperature_c']]
                        for r in native.get('joints', []) if r.get('feedback_fresh')}
                self.feedback_ids = (time.monotonic(), tuple(rows) if report.get('fresh_sample') else ())
                fresh = report.get('fresh_sample') and report.get('all_joints_fresh') and self.frame()
                sample = {'t': time.monotonic_ns(), 'servos': rows if fresh else {},
                          'imu': bool(fresh and native.get('imu_ready'))}
                publish(RUN/'bus.json', json.dumps(sample).encode())
            except (OSError, ValueError):
                pass
            time.sleep(.5)

    def status(self):
        frame = self.frame() or {}
        try:
            report = self.robot.rpc('robot.busStatus')
            health = self.robot.rpc('robot.health')
        except (OSError, ValueError):
            report, health = {}, {'healthy': False}
        native = report.get('native') or {}
        fresh = bool(frame and report.get('fresh_sample') and report.get('all_joints_fresh')
                     and not native.get('held_joint_ids') and set(r['id'] for r in native.get('joints', []) if r.get('feedback_fresh')) == set(IDS) and report.get('phase') == 'control')
        torque = native.get('torque_state_confirmed') if fresh else None
        guard = report.get('home_start_guard') or {}
        g = frame.get('safety', {}).get('gravity') or []
        upright = len(g) == 3 and .95 < math.hypot(*g) < 1.05 and -g[2]/math.hypot(*g) >= math.cos(math.radians(25))
        ready = bool(fresh and health.get('healthy') and native.get('imu_ready') and upright
                     and report.get('policy_enabled') is False and torque is False and health.get('bus', {}).get('consecutive_errors') == 0)
        state = '反馈未确认'
        if fresh and torque is False:
            state = '已卸力'
        if fresh and torque is True:
            state = '已上力，等待站姿确认'
            if guard.get('ready') and report.get('homed'):
                state = 'HOME 已站稳'
        relax = None
        if self.last_request == 'relax':
            if fresh and torque is False and report.get('cycle', 0) > self.before and not report.get('torque_off_pending'):
                relax = 'confirmed'
            elif time.monotonic() - self.requested_at < 3:
                relax = 'pending'
            else:
                relax = 'unconfirmed'
        if fresh:
            health['servo_count'] = len(IDS)
            health['missing_ids'] = []
        return {'read_only': True, 'observation_version': 1, 'fresh': fresh, 'health': health,
                'base_home_key': self.key_id, 'base_home_ready': ready, 'base_home_state': state,
                'relax_state': relax, 'armed': False, 'enabled': False, 'skills': []}

    def command(self, cmd, proof, nonce, seq):
        if cmd not in ('base_home', 'base_relax'):
            raise ValueError('不支持此操作')
        msg = f'duck-base/home/v1:{nonce}:{seq}:{cmd}'.encode()
        expected = hmac.digest(self.key, msg, 'sha256').hex()
        if not isinstance(proof, str) or not proof.isascii() or not hmac.compare_digest(proof, expected):
            raise ValueError('这部手机没有本机动作密钥')
        if cmd == 'base_home' and not self.status()['base_home_ready']:
            raise ValueError('请先托稳扶正，确认 IMU、全部舵机和卸力反馈')
        # An unavailable status report must never prevent a release request.
        try:
            before = self.robot.rpc('robot.busStatus').get('cycle', 0)
        except (OSError, ValueError):
            before = 0
        result = self.robot.rpc('robot.init' if cmd == 'base_home' else 'robot.relax')
        if result.get('accepted') is not True:
            raise ValueError('主控未接受请求')
        self.last_request = 'home' if cmd == 'base_home' else 'relax'
        self.before, self.requested_at = before, time.monotonic()
        logging.info('Duck Base %s accepted at bus cycle %s', cmd, before)
        return {'accepted': True}
