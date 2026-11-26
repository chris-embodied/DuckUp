"""Authenticated BLE command handler for the kissqy robotd Unix socket."""
import base64
import hashlib
import hmac
import json
import logging
import math
import secrets
import socket
import threading
import time

from observation import Observation

ZERO = {"vx": 0, "vy": 0, "vyaw": 0}
# V260 web controller full-stick targets, in m/s and rad/s.
LIMITS = {"vx": 0.30, "vy": 0.30, "vyaw": 1.50}
MAX_FRAME = 4096
MOVE_TIMEOUT = 0.55
OWNER_TIMEOUT = 4.0
RELAX_CONFIRM_TIMEOUT = 3.0
HEAD = dict.fromkeys(("neck_pitch", "head_pitch", "head_yaw", "head_roll"), 0.0)
# Manual controls are held like a webpad axis. Releasing restores neutral.
HELD = {
    "look_left": ("robot.head", {**HEAD, "head_yaw": .52}),
    "look_right": ("robot.head", {**HEAD, "head_yaw": -.52}),
    "look_up": ("robot.head", {**HEAD, "head_pitch": -.45}),
    "look_down": ("robot.head", {**HEAD, "head_pitch": .45}),
    "tilt_left": ("robot.head", {**HEAD, "head_roll": -.28}),
    "tilt_right": ("robot.head", {**HEAD, "head_roll": .28}),
    "neck_forward": ("robot.head", {**HEAD, "neck_pitch": .4}),
    "neck_back": ("robot.head", {**HEAD, "neck_pitch": -.4}),
    "mouth_open": ("robot.mouth", {"open": 1}),
    "long_call": ("robot.sound", {"tag": "wheee", "hold": True}),
}
for name, axis, value in (("raise", "z", .010), ("lower", "z", -.025),
                          ("lean_forward", "pitch", .2618), ("lean_back", "pitch", -.2618),
                          ("lean_left", "roll", -.2618), ("lean_right", "roll", .2618)):
    HELD[name] = ("robot.pose", {"z": 0, "roll": 0, "pitch": 0, axis: value, "active": True})
NEUTRAL = {"robot.head": HEAD, "robot.pose": {"active": False},
           "robot.mouth": {"open": 0}, "robot.sound": {"tag": "wheee", "hold": False}}


def action_catalog(policies, report):
    skills = [s for s in policies.get("skills", []) if isinstance(s, str)]
    for action in report.get("built_in", []):
        if action in ("sit_toggle", "ground_pick") and action not in skills:
            skills.append(action)
    expressions = {}
    for row in report.get("expressions", []):
        name, skill, label = row.get("name"), row.get("skill"), row.get("label")
        if isinstance(name, str) and skill in ("emotion_" + name, "emotion_silent_" + name) and isinstance(label, str):
            expressions[skill] = label
    return skills, expressions


LOG = logging.getLogger(__name__)


def json_bytes(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


def tag(key, direction, nonce, seq, payload):
    data = b":".join((direction, nonce.encode(), str(seq).encode(), payload))
    return hmac.new(key, data, hashlib.sha256).hexdigest()


def read_line(stream):
    line = stream.readline(262145)
    if len(line) > 262144 or not line.endswith(b"\n"):
        raise ValueError("robotd reply incomplete")
    value = json.loads(line)
    if not isinstance(value, dict):
        raise ValueError("robotd reply invalid")
    return value


class Robot:
    def __init__(self, path):
        self.path = path
        self.frame = None
        self.catalog = {}
        self.catalog_until = 0.0

    def _connect(self, timeout):
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        conn.settimeout(timeout)
        try:
            conn.connect(self.path)
        except OSError:
            conn.close()
            raise
        return conn

    @staticmethod
    def _send(conn, method, params=None):
        request = {"jsonrpc": "2.0", "id": 1, "method": method}
        if params is not None:
            request["params"] = params
        conn.sendall(json_bytes(request) + b"\n")

    def rpc(self, method, params=None):
        with self._connect(0.12) as conn, conn.makefile("rb") as stream:
            self._send(conn, method, params)
            reply = read_line(stream)
        if reply.get("id") != 1 or "result" not in reply:
            raise ValueError("robotd refused " + method)
        return reply["result"]

    def start(self):
        threading.Thread(target=self._observe, daemon=True, name="robot-state").start()

    def _observe(self):
        while True:
            try:
                with self._connect(0.4) as conn, conn.makefile("rb") as stream:
                    self._send(conn, "robot.subscribe", {"hz": 20})
                    if read_line(stream).get("result", {}).get("accepted") is not True:
                        raise ValueError("state subscription refused")
                    while True:
                        message = read_line(stream)
                        if message.get("method") == "robot.state":
                            frame = message["params"]
                            if not isinstance(frame, dict):
                                raise ValueError("state frame invalid")
                            # t_ns is assigned by robotd, on the same Linux monotonic clock.
                            when = frame.get("t_ns", 0)
                            self.frame = when, frame
            except (OSError, ValueError, KeyError, TypeError):
                self.frame = None
                time.sleep(0.2)

    def fresh_frame(self):
        item = self.frame
        if item is None:
            return None
        when, frame = item
        if type(when) is int and 0 <= time.monotonic_ns() - when <= 250_000_000:
            return frame
        return None

    def actions(self, policies):
        if time.monotonic() >= self.catalog_until:
            try:
                self.catalog = self.rpc("robot.skills")
            except (OSError, ValueError):
                self.catalog = {}
            self.catalog_until = time.monotonic() + 10
        return action_catalog(policies, self.catalog)

    def status(self):
        health = self.rpc("robot.health")
        try:
            policies = self.rpc("robot.policies")
        except (OSError, ValueError):
            policies = {}
        frame = self.fresh_frame()
        safety = (frame or {}).get("safety", {})
        skills, expressions = self.actions(policies)
        walk = next((slot.get("path") for slot in policies.get("slots", []) if slot.get("slot") == "walk" and not slot.get("error")), None)
        walk_label = {"/opt/duck-base/models/velstand-s54/policy.onnx": "VelStand s54",
                      "/opt/duck-base/models/velstand-s54-roll/policy.onnx": "VelStand s54",
                      "/opt/duck-base/v260/models/1cbf8730eadd75a5704332c9f3c10f60/policy.onnx": "kissqy V260"}.get(walk)
        return {"walk_model": walk_label, "health": health, "fresh": frame is not None, "fallen": safety.get("fallen"),
                "limp": safety.get("limp"), "policy": (frame or {}).get("policy"),
                "homed": policies.get("homed"), "sitting": policies.get("sitting"),
                "skills": skills, "expressions": expressions}


class Controller:
    def __init__(self, key, robot, lease_factory, clock=time.monotonic):
        if len(key) != 32:
            raise ValueError("32-byte pairing key required")
        self.key, self.robot, self.lease_factory, self.clock = key, robot, lease_factory, clock
        self.observation = Observation()
        self.sessions = {}
        self.owner = None
        self.owner_until = None
        self.lease = None
        self.moving_until = None
        self.held_method = None
        self.last_stop_ns = None
        self.enabled = False
        self.last_stand = -math.inf
        self.relax_check = None
        self.relax_state = None

    def disconnect(self, peer=None):
        if peer is None or peer == self.owner:
            self._release()
        if peer is None:
            self.sessions.clear()
        else:
            self.sessions.pop(peer, None)

    def tick(self):
        if self.lease is None:
            return
        if self.clock() >= self.owner_until:
            self._release()
            return
        try:
            self.lease.ensure_current()
        except (OSError, ValueError):
            self._release()
            return
        if self.moving_until is not None:
            frame = self.robot.fresh_frame()
            if self.clock() >= self.moving_until or frame is None or frame.get("safety", {}).get("fallen") is not False:
                self._release()

    def _intent(self, method, params=None):
        result = self.robot.rpc(method, params)
        if result.get("accepted") is not True:
            raise ValueError(str(result.get("reason") or "robotd refused the request")[:160])
        return {"accepted": True}

    def _neutralize(self):
        if self.held_method:
            self._intent(self.held_method, NEUTRAL[self.held_method])
            self.held_method = None

    def _release(self, send_zero=True):
        lease = self.lease
        self.lease = self.owner = self.owner_until = self.moving_until = None
        self.enabled = False
        if lease is None:
            return
        try:
            # A physical controller that took over must not receive our late zero.
            if send_zero:
                lease.ensure_current()
                self._intent("robot.move", ZERO)
                self._neutralize()
        except (OSError, ValueError):
            pass  # robotd's own deadman remains active if IPC or the input lease is lost.
        finally:
            self.held_method = None
            lease.close()

    def _bus_report(self):
        try:
            return self.robot.rpc("robot.busStatus")
        except (OSError, ValueError):
            return {}

    def _update_relax(self):
        if self.relax_check is None:
            return
        before, until = self.relax_check
        report = self._bus_report()
        cycle = report.get("cycle")
        if (type(before) is int and type(cycle) is int and cycle > before
                and report.get("mode") == "motion" and report.get("phase") == "control"
                and report.get("fresh_sample") is True and report.get("policy_enabled") is False
                and not report.get("torque_off_pending")
                and (report.get("native") or {}).get("torque_state_confirmed") is False):
            self.relax_check = None
            self.relax_state = "confirmed"
            LOG.info("Torque OFF confirmed by fresh bus readback")
        elif self.clock() >= until:
            self.relax_check = None
            self.relax_state = "unconfirmed"
            LOG.warning("Torque OFF could not be confirmed")

    def _command(self, peer, body):
        command = body.get("command")
        if command == "observe":
            return self.observation.snapshot(self.robot.fresh_frame(), body)
        if command == "status":
            self._update_relax()
            status = self.robot.status()
            status["armed"] = self.owner == peer
            status["enabled"] = self.owner == peer and self.enabled
            status["continuous_control"] = True
            status["move_limits"] = LIMITS
            status["observation_version"] = 1
            status["action_controls"] = 1
            status["seated_stand"] = True
            frame = self.robot.fresh_frame() or {}
            if self.last_stop_ns is not None and frame.get("t_ns", 0) > self.last_stop_ns:
                move = frame.get("move") or {}
                status["zero_applied"] = all(move.get(k) == [0, 0, 0] for k in ("applied", "requested"))
            status["relax_without_owner"] = True
            status["relax_state"] = self.relax_state
            return status
        if command == "relax":
            # An authenticated stop must still be available after fall protection
            # has revoked the motion lease. It never grants movement authority.
            if self.owner is not None and self.owner != peer:
                raise ValueError("Another phone has control")
            before = self._bus_report().get("cycle")
            try:
                result = self._intent("robot.relax")
            except (OSError, ValueError):
                self._release()
                self.relax_check = None
                self.relax_state = "unconfirmed"
                raise
            self._release(send_zero=False)
            self.relax_check = before, self.clock() + RELAX_CONFIRM_TIMEOUT
            self.relax_state = "pending"
            LOG.info("Torque OFF requested; waiting for bus readback")
            return result
        if command == "arm":
            if self.owner is not None:
                raise ValueError("Control is already in use")
            self._ready()
            lease = self.lease_factory()
            lease.acquire()
            self.owner, self.lease = peer, lease
            self.owner_until = self.clock() + OWNER_TIMEOUT
            self.relax_check = self.relax_state = None
            return {"accepted": True}
        if self.owner != peer or self.lease is None:
            raise ValueError("Acquire control first")
        self.lease.ensure_current()
        if command == "idle":
            if not self.enabled or self.moving_until is not None or self.held_method is not None:
                raise ValueError("Not in neutral remote mode")
            return self._intent("robot.move", ZERO)
        if command == "stop":
            result = self._intent("robot.move", ZERO)
            self._neutralize()
            self.last_stop_ns = time.monotonic_ns()
            self.moving_until = None
            return result
        if command == "release":
            result = self._intent("robot.move", ZERO)
            self._neutralize()
            self.moving_until = None
            self.enabled = False
            lease, self.lease, self.owner, self.owner_until = self.lease, None, None, None
            lease.close()
            return result
        if command == "chirp":
            return self._intent("robot.sound", {"tag": "chirp"})
        if command not in {"home", "enable", "move", "hold", "skill", "stand"}:
            raise ValueError("Unsupported command")
        frame = self._ready()
        if command == "home":
            if self.robot.rpc("robot.policies").get("sitting") is not False:
                raise ValueError("Stand or exit sitting mode before HOME")
            self._intent("robot.move", ZERO)
            self.moving_until = None
            self.enabled = False
            return self._intent("robot.init")
        if command == "enable":
            policies = self.robot.rpc("robot.policies")
            if policies.get("homed") is not True or policies.get("sitting") is not False:
                raise ValueError("Wait for HOME confirmation")
            result = self._intent("robot.enable", {"on": True})
            self.enabled = True
            return result
        if command == "stand":
            policies = self.robot.rpc("robot.policies")
            if (policies.get("sitting") is not True or frame.get("policy") != "sit"
                    or "sit_toggle" not in self.robot.actions(policies)[0]):
                raise ValueError("请等待主控确认坐稳，再起身")
            if (policies.get("enabled") is not True or policies.get("homed") is not True
                    or policies.get("mode") != "walk" or frame.get("safety", {}).get("limp") is not False
                    or self.moving_until is not None):
                raise ValueError("请先确认主控已启用、舵机有支撑且移动已停止")
            phase = self.robot.rpc("robot.do", {"skill": "emotion_status"}).get("phase")
            if phase not in ("idle", "completed", "cancelled"):
                raise ValueError("请等待表情播放结束，再起身")
            if self.clock() - self.last_stand < 1:
                raise ValueError("已请求起身，请等待姿态回报")
            self.lease.ensure_current()
            result = self._intent("robot.do", {"skill": "sit_toggle"})
            self.last_stand = self.clock()
            self.enabled = True
            return result
        if command == "skill":
            if not self.enabled or self.moving_until is not None:
                raise ValueError("请先停止移动，并启用遥控")
            policies = self.robot.rpc("robot.policies")
            skills, expressions = self.robot.actions(policies)
            name = body.get("name")
            if name not in skills and name not in expressions:
                raise ValueError("此设备未提供该动作")
            if frame.get("policy") not in {"walk", "stand"} and not (name == "sit_toggle" and policies.get("sitting") is True):
                raise ValueError("请等当前动作完成")
            return self._intent("robot.do", {"skill": name})
        if not self.enabled or frame.get("policy") not in {"walk", "stand"}:
            raise ValueError("Walking policy is not running")
        if command == "hold":
            method, params = HELD[body["name"]]
            if self.moving_until is not None and self.held_method != method:
                raise ValueError("先松开当前控制")
            self._intent("robot.move", ZERO)
            self.held_method = method
            self.last_stop_ns = None
            self.moving_until = self.clock() + MOVE_TIMEOUT
            return self._intent(method, params)
        if self.held_method is not None:
            raise ValueError("请先松开姿态控制")
        self.last_stop_ns = None
        velocity = {}
        for axis, limit in LIMITS.items():
            value = body.get(axis)
            if type(value) not in (int, float) or not -limit <= value <= limit or not math.isfinite(value):
                raise ValueError("Velocity outside phone limits")
            velocity[axis] = value
        self.lease.ensure_current()
        result = self._intent("robot.move", velocity)
        self.moving_until = self.clock() + MOVE_TIMEOUT if any(velocity.values()) else None
        return result

    def _ready(self):
        frame = self.robot.fresh_frame()
        health = self.robot.rpc("robot.health")
        if (frame is None or frame.get("safety", {}).get("fallen") is not False
                or health.get("healthy") is not True
                or health.get("bus", {}).get("consecutive_errors") != 0
                or health.get("imu", {}).get("ready") is not True
                or not 0 <= health.get("imu", {}).get("consecutive_stale_blocks", 25) < 25):
            raise ValueError("Waiting for live bus, IMU and upright posture")
        return frame

    def receive(self, peer, line):
        """Return an optional response. Invalid authentication is silent."""
        if len(line) > MAX_FRAME:
            return None
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                return None
            if request == {"hello": 1}:
                # A new handshake cannot revive or retain an old motion lease.
                self.disconnect(peer)
                if len(self.sessions) >= 8:
                    return None
                nonce = secrets.token_hex(16)
                self.sessions[peer] = (nonce, 0)
                return json_bytes({"nonce": nonce}) + b"\n"
            nonce, previous = self.sessions[peer]
            seq, encoded, mac = request["seq"], request["body"], request["mac"]
            if type(seq) is not int or seq != previous + 1 or type(mac) is not str or not mac.isascii():
                return None
            payload = base64.b64decode(encoded, validate=True)
            if not hmac.compare_digest(mac, tag(self.key, b"request", nonce, seq, payload)):
                return None
            body = json.loads(payload)
            if not isinstance(body, dict):
                return None
        except (ValueError, KeyError, TypeError):
            return None
        self.sessions[peer] = (nonce, seq)
        try:
            self.tick()
            if self.owner == peer:
                self.owner_until = self.clock() + OWNER_TIMEOUT
            result = {"result": self._command(peer, body)}
            if seq == 1 and body.get("command") == "status":
                LOG.info("Authenticated status read: fresh=%s fallen=%s healthy=%s",
                         result["result"]["fresh"], result["result"].get("fallen"),
                         result["result"]["health"].get("healthy"))
        except (OSError, ValueError, KeyError, TypeError, OverflowError) as exc:
            if body.get("command") in {"move", "stop", "release", "hold"} and self.owner == peer:
                self._release()
            result = {"error": str(exc)[:180]}
        payload = json_bytes(result)
        return json_bytes({"seq": seq, "body": base64.b64encode(payload).decode(),
                           "mac": tag(self.key, b"response", nonce, seq, payload)}) + b"\n"
