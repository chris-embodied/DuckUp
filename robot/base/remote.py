"""Route observation and owner-authenticated motion over the same BLE service."""
import json
import logging
import math
import time

from bus import IDS

from control import Controller, LIMITS, MAX_FRAME
from sensors import RUN


TRIAL_PREFIX = 'SuperMimic '
SHOW_TRIALS = '切换到 SuperMimic 动作'
SHOW_ORIGINAL = '切回原版动作'
TRIAL_ALIASES = {
    'ground_pick': 'SuperMimic 低头探地',
    'roulade': 'SuperMimic 前滚翻',
    'kick_left': 'SuperMimic 左脚踢球',
    'kick_right': 'SuperMimic 右脚踢球',
}


class BaseRemote(Controller):
    def __init__(self, monitor, lease_factory):
        self.monitor = monitor
        self.trial_peer = None
        home = monitor.home
        super().__init__(home.key, home.robot, lease_factory)

    def _check_trial_start(self):
        report = self._bus_report()
        native = report.get('native') or {}
        if not (report.get('homed') and report.get('all_joints_fresh')
                and report.get('fresh_sample') and report.get('policy_enabled')
                and native.get('torque_state_confirmed') is True):
            raise ValueError('请先 HOME 站稳，再启用遥控')
        previous = None
        for index in range(4):
            if index:
                time.sleep(.06)
            frame = self.robot.fresh_frame() or {}
            safety = frame.get('safety') or {}
            gravity = safety.get('gravity') or []
            gyro = (frame.get('imu') or {}).get('gyro') or []
            move = frame.get('move') or {}
            stamp = frame.get('t_ns')
            if (frame.get('policy') not in ('walk', 'stand') or safety.get('fallen') is not False
                    or safety.get('limp') is not False or not isinstance(stamp, int)
                    or (previous is not None and stamp <= previous)
                    or len(gravity) != 3 or not .95 <= math.hypot(*gravity) <= 1.05
                    or -gravity[2] / math.hypot(*gravity) < math.cos(math.radians(10))
                    or len(gyro) != 3 or not math.hypot(*gyro) <= .3
                    or any(len(move.get(k) or []) != 3 or
                           not all(abs(x) < .01 for x in move[k]) for k in ('requested','applied'))):
                raise ValueError('请松开摇杆，等双脚站稳后再执行动作')
            previous = stamp

    def _command(self, peer, body):
        command = body.get('command')
        name = body.get('name', '')
        owner_enabled = peer == self.owner and self.enabled and self.lease is not None
        in_trial = owner_enabled and self.trial_peer == peer
        if command == 'skill' and name in (SHOW_TRIALS, SHOW_ORIGINAL):
            if not owner_enabled or self.moving_until is not None or self.held_method:
                raise ValueError('请启用遥控并松开当前控制')
            self.lease.ensure_current()
            self._ready()
            self._check_trial_start()
            available = self.robot.rpc('robot.policies').get('skills', [])
            if name == SHOW_TRIALS and not any(s.startswith(TRIAL_PREFIX) for s in available):
                raise ValueError('主板没有加载 SuperMimic 动作')
            self.trial_peer = peer if name == SHOW_TRIALS else None
            return {'accepted': True}
        if in_trial and command == 'move' and any(body.get(k, 0) != 0 for k in LIMITS):
            raise ValueError('请在全身动作中点选方向；按住摇杆请先切回原版')
        if in_trial and command == 'hold' and name not in ('mouth_open', 'long_call'):
            raise ValueError('当前模型仅支持动作列表里的轨迹')
        if command == 'skill' and owner_enabled:
            if in_trial:
                name = TRIAL_ALIASES.get(name, name)
                if not isinstance(name, str) or not name.startswith(TRIAL_PREFIX):
                    raise ValueError('当前模型没有此独立动作，请从全身动作中选择')
                body = dict(body, name=name)
            if isinstance(name, str) and name.startswith(TRIAL_PREFIX):
                self._check_trial_start()
        if command == 'observe':
            return self.monitor._command(peer, body)
        if command == 'home' and not self.monitor.home.status()['base_home_ready']:
            raise ValueError('请先托稳扶正，确认 IMU、全部舵机和卸力反馈')
        result = super()._command(peer, body)
        if command in ('arm', 'home', 'release', 'relax'):
            self.trial_peer = None
        if command == 'status':
            names = result['skills']
            trials = [s for s in names if s.startswith(TRIAL_PREFIX)]
            if len(trials) > 1:
                if in_trial:
                    result['skills'] = [SHOW_ORIGINAL, *trials]
                    result['expressions'] = {}
                else:
                    result['skills'] = [SHOW_TRIALS, *[s for s in names if s not in trials or s == 'SuperMimic 前行试走']]
            result['duck_base'] = True
            updated, ids = self.monitor.home.feedback_ids
            available = set(ids) if time.monotonic() - updated < 2 else set()
            result['health']['servo_count'] = len(available)
            result['health']['missing_ids'] = [i for i in IDS if i not in available]
            if self.sessions[peer][1] == 1:
                logging.info('Duck Base private remote authenticated; %d skills, %d expressions',
                             len(result['skills']), len(result['expressions']))
        return result


class BaseProfiles:
    def __init__(self, monitor, remote):
        self.monitor = monitor
        self.remote = remote
        monitor.remote = remote

    def receive(self, peer, line):
        if len(line) > MAX_FRAME:
            return None
        try:
            request = json.loads(line)
        except (ValueError, TypeError):
            return None
        if request in ({'hello': 1}, {'hello': 1, 'profile': 'control'}):
            self.disconnect(peer)
            target = self.remote if 'profile' in request else self.monitor
            return target.receive(peer, b'{"hello":1}')
        target = self.remote if peer in self.remote.sessions else self.monitor
        return target.receive(peer, line)

    def disconnect(self, peer=None):
        self.remote.disconnect(peer)
        self.monitor.disconnect(peer)

    def tick(self):
        self.remote.tick()


def profiles(monitor):
    import ai_input_lease
    ai_input_lease.MAX_ACTION_S = float('inf')
    def lease():
        return ai_input_lease.InputLease(lambda: False, directory=RUN/'ai-input')
    return BaseProfiles(monitor, BaseRemote(monitor, lease))
