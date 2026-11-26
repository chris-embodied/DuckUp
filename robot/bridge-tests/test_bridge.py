import base64
import hashlib
import hmac
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
from control import Controller, ZERO, action_catalog


class RobotStub:
    def __init__(self):
        self.calls = []
        self.frame = {"safety": {"fallen": False, "limp": False}, "policy": "walk"}
        self.health = {"healthy": True, "bus": {"consecutive_errors": 0},
                       "imu": {"ready": True, "consecutive_stale_blocks": 0}}
        self.policies = {"homed": True, "sitting": False, "skills": ["kick_right"]}
        self.builtins = []
        self.emotion_phase = "idle"
        self.bus_report = {
            "cycle": 10, "mode": "motion", "phase": "control", "fresh_sample": True,
            "policy_enabled": True, "torque_off_pending": False, "native": {"torque_state_confirmed": True},
        }

    def actions(self, policies):
        return action_catalog(policies, {"built_in": self.builtins, "expressions": [{"name":"curious", "label":"好奇", "skill":"emotion_curious"}]})

    def fresh_frame(self):
        return self.frame

    def status(self):
        return {"fresh": self.frame is not None, "health": self.health}

    def rpc(self, method, params=None):
        self.calls.append((method, params))
        if method == "robot.do" and params == {"skill": "emotion_status"}:
            return {"phase": self.emotion_phase}
        return {"robot.health": self.health, "robot.policies": self.policies,
                "robot.busStatus": self.bus_report}.get(method, {"accepted": True})


class LeaseStub:
    valid = True

    def acquire(self):
        self.ensure_current()

    def ensure_current(self):
        if not self.valid:
            raise ValueError("controller took over")

    def close(self):
        self.valid = False


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.key = bytes(range(32))
        self.robot = RobotStub()
        self.lease = LeaseStub()
        self.now = 10.0
        self.bridge = Controller(self.key, self.robot, lambda: self.lease, lambda: self.now)
        self.peers = {}
        self.connect("phone")

    def connect(self, peer):
        nonce = json.loads(self.bridge.receive(peer, b'{"hello":1}'))["nonce"]
        self.peers[peer] = nonce, 0

    def signature(self, direction, nonce, number, data):
        header = f"{direction}:{nonce}:{number}:".encode()
        return hmac.new(self.key, header + data, hashlib.sha256).hexdigest()

    def request(self, command, peer="phone", **params):
        nonce, number = self.peers[peer]
        number += 1
        self.peers[peer] = nonce, number
        data = json.dumps({"command": command, **params}).encode()
        wire = json.dumps({"seq": number, "body": base64.b64encode(data).decode(),
                           "mac": self.signature("request", nonce, number, data)}).encode()
        return nonce, number, wire

    def send(self, command, peer="phone", **params):
        nonce, number, wire = self.request(command, peer, **params)
        reply = json.loads(self.bridge.receive(peer, wire))
        payload = base64.b64decode(reply["body"])
        self.assertEqual(reply["seq"], number)
        self.assertEqual(reply["mac"], self.signature("response", nonce, number, payload))
        return json.loads(payload)

    def ready(self):
        self.assertEqual(self.send("arm"), {"result": {"accepted": True}})
        self.assertEqual(self.send("enable"), {"result": {"accepted": True}})
        self.assertEqual(self.send("move", vx=0.11, vy=0, vyaw=0), {"result": {"accepted": True}})
        self.assertEqual(self.robot.calls[-1], ("robot.move", {"vx": 0.11, "vy": 0, "vyaw": 0}))

    def test_authentication_replay_and_reconnect(self):
        _, _, wire = self.request("arm")
        tampered = json.loads(wire)
        tampered["mac"] = "0" * 64
        self.assertIsNone(self.bridge.receive("phone", json.dumps(tampered).encode()))
        self.assertFalse(self.robot.calls)
        self.assertIsNotNone(self.bridge.receive("phone", wire))
        self.assertIsNone(self.bridge.receive("phone", wire))
        self.bridge.disconnect("phone")
        self.connect("phone")
        self.assertIsNone(self.bridge.receive("phone", wire))

    def test_release_zero_and_disarm(self):
        self.ready()
        self.assertEqual(self.send("release"), {"result": {"accepted": True}})
        self.assertEqual(self.robot.calls[-1], ("robot.move", ZERO))
        self.assertIn("error", self.send("move", vx=0.01, vy=0, vyaw=0))

    def test_watchdog_and_disconnect(self):
        self.ready()
        self.now += 0.6
        self.bridge.tick()
        self.assertEqual(self.robot.calls[-1], ("robot.move", ZERO))
        self.assertIsNone(self.bridge.owner)

    def test_owner_stays_armed_while_authenticated_status_arrives(self):
        self.send("arm")
        self.send("enable")
        for _ in range(30):
            self.now += 1
            self.bridge.tick()
            status = self.send("status")["result"]
            self.assertTrue(status["armed"])
            self.assertTrue(status["continuous_control"])
        self.now += 4.1
        self.bridge.tick()
        self.assertEqual(self.robot.calls[-1], ("robot.move", ZERO))
        self.assertIsNone(self.bridge.owner)

    def test_second_phone_does_not_extend_owner_timeout(self):
        self.send("arm")
        self.connect("other")
        self.now += 3
        self.send("status", "other")
        self.now += 1.1
        self.bridge.tick()
        self.assertIsNone(self.bridge.owner)

    def test_lateral_commands_preserve_axis_and_sign(self):
        self.send("arm")
        self.send("enable")
        for vy in (0.08, -0.08):
            velocity = {"vx": 0, "vy": vy, "vyaw": 0}
            self.assertEqual(self.send("move", **velocity), {"result": {"accepted": True}})
            self.assertEqual(self.robot.calls[-1], ("robot.move", velocity))
        self.send("stop")
        self.assertEqual(self.robot.calls[-1], ("robot.move", ZERO))

    def test_action_catalog_and_single_press(self):
        self.send("arm")
        self.send("enable")
        self.robot.policies["slots"] = [{"slot":"sitstand","path":"policy.onnx"}]
        self.robot.builtins = ["sit_toggle", "ground_pick"]
        self.assertEqual(self.robot.actions(self.robot.policies)[0], ["kick_right", "sit_toggle", "ground_pick"])
        for name in ("kick_right", "emotion_curious", "ground_pick"):
            self.assertIn("result", self.send("skill", name=name))
            self.assertEqual(self.robot.calls[-1], ("robot.do", {"skill": name}))
        self.robot.frame["policy"] = "sitstand"
        self.robot.policies["sitting"] = True
        self.assertIn("result", self.send("skill", name="sit_toggle"))
        for name in ("kick_right", "absent"):
            self.assertIn("error", self.send("skill", name=name))

    def test_reconnect_after_devastated_can_stand_without_enabling_torque(self):
        self.robot.builtins = ["sit_toggle"]
        self.robot.frame["policy"] = "sit"
        self.robot.policies.update(sitting=True, enabled=True, mode="walk")
        self.send("arm")
        self.robot.emotion_phase = "playing"
        self.assertIn("error", self.send("stand"))
        self.robot.emotion_phase = "completed"
        self.assertIn("result", self.send("stand"))
        self.assertIn("error", self.send("stand"))
        self.robot.frame["policy"] = "walk"
        self.robot.policies["sitting"] = False
        self.now += 2
        self.assertIn("error", self.send("stand"))
        self.assertEqual(self.robot.calls.count(("robot.do", {"skill":"sit_toggle"})), 1)
        self.assertFalse(any(m in ("robot.init", "robot.enable") for m, _ in self.robot.calls))

    def test_seated_recovery_needs_live_supported_driving_robot(self):
        self.send("arm")
        self.robot.frame["policy"] = "sit"
        self.robot.policies.update(sitting=True, enabled=True, mode="walk")
        self.assertIn("error", self.send("stand"))
        self.robot.builtins = ["sit_toggle"]
        self.robot.policies["enabled"] = False
        self.assertIn("error", self.send("stand"))
        self.robot.policies["enabled"] = True
        self.robot.frame["safety"]["fallen"] = True
        self.assertIn("error", self.send("stand"))
        self.assertNotIn(("robot.do", {"skill":"sit_toggle"}), self.robot.calls)

    def test_held_controls_and_release(self):
        self.send("arm")
        self.send("enable")
        for name, method, axis, expected, neutral in [
                ("look_left", "robot.head", "head_yaw", .52, 0),
                ("look_down", "robot.head", "head_pitch", .45, 0),
                ("tilt_left", "robot.head", "head_roll", -.28, 0),
                ("neck_back", "robot.head", "neck_pitch", -.4, 0),
                ("mouth_open", "robot.mouth", "open", 1, 0),
                ("long_call", "robot.sound", "hold", True, False),
                ("lower", "robot.pose", "active", True, False)]:
            self.assertIn("result", self.send("hold", name=name))
            rpc, args = self.robot.calls[-1]
            self.assertEqual((rpc, args[axis]), (method, expected))
            self.assertIn("error", self.send("idle"))
            self.send("stop")
            rpc, args = self.robot.calls[-1]
            self.assertEqual((rpc, args[axis]), (method, neutral))
        self.assertIn("result", self.send("chirp"))
        self.assertEqual(self.robot.calls[-1], ("robot.sound", {"tag":"chirp"}))
        self.assertIn("result", self.send("idle"))
        self.assertEqual(self.robot.calls[-1], ("robot.move", ZERO))

    def test_lost_hold_neutralized_and_skill_cannot_bypass_fall(self):
        self.send("arm")
        self.send("enable")
        self.send("hold", name="mouth_open")
        self.assertIn("error", self.send("skill", name="kick_right"))
        self.robot.frame["safety"]["fallen"] = True
        self.bridge.tick()
        self.assertEqual(self.robot.calls[-1], ("robot.mouth", {"open":0}))
        self.assertIsNone(self.bridge.owner)
        self.assertIn("error", self.send("skill", name="kick_right"))

    def test_stop_feedback_uses_new_applied_frame(self):
        self.ready()
        self.send("stop")
        self.robot.frame.update(t_ns=self.bridge.last_stop_ns, move={"requested":[0]*3, "applied":[0]*3})
        self.assertNotIn("zero_applied", self.send("status")["result"])
        self.robot.frame["t_ns"] += 1
        self.assertTrue(self.send("status")["result"]["zero_applied"])
        self.robot.frame["move"]["applied"][1] = .1
        self.assertFalse(self.send("status")["result"]["zero_applied"])

    def test_gatt_disconnect_stops(self):
        self.ready()
        self.bridge.disconnect("phone")
        self.assertEqual(self.robot.calls[-1], ("robot.move", ZERO))
        self.assertFalse(self.lease.valid)

    def test_manual_takeover_does_not_write_late_zero(self):
        self.ready()
        before = list(self.robot.calls)
        self.lease.valid = False
        self.bridge.tick()
        self.assertEqual(self.robot.calls, before)
        self.assertIsNone(self.bridge.owner)

    def test_second_phone_cannot_control(self):
        self.ready()
        self.connect("other")
        self.assertIn("error", self.send("arm", "other"))
        self.assertIn("error", self.send("relax", "other"))
        self.assertNotIn(("robot.relax", None), self.robot.calls)

    def test_stale_frame_and_fall_revoke_motion(self):
        for state in (None, {"safety": {"fallen": True}}):
            with self.subTest(state=state):
                self.setUp()
                self.ready()
                self.robot.frame = state
                self.bridge.tick()
                self.assertEqual(self.robot.calls[-1], ("robot.move", ZERO))
                self.assertIn("error", self.send("arm"))

    def test_requires_homed_before_enable(self):
        self.robot.policies["homed"] = False
        self.send("arm")
        self.assertEqual(self.send("home"), {"result": {"accepted": True}})
        self.assertIn("error", self.send("enable"))
        self.assertNotIn(("robot.enable", {"on": True}), self.robot.calls)

    def test_refuses_arbitrary_rpc_and_out_of_range_motion(self):
        self.ready()
        before = len(self.robot.calls)
        self.assertIn("error", self.send("robot.shutdown"))
        self.assertEqual(len(self.robot.calls), before)
        self.assertIn("error", self.send("move", vx=0.301, vy=0, vyaw=0))
        self.assertEqual(self.robot.calls[-1], ("robot.move", ZERO))

    def test_web_controller_bounds_are_reported_and_forwarded_unchanged(self):
        self.send("arm")
        self.send("enable")
        limits = {"vx": 0.30, "vy": 0.30, "vyaw": 1.50}
        self.assertEqual(self.send("status")["result"]["move_limits"], limits)
        for axis, maximum in limits.items():
            for sign in (1, -1):
                command = dict(ZERO, **{axis: sign * maximum})
                self.assertEqual(self.send("move", **command), {"result": {"accepted": True}})
                self.assertEqual(self.robot.calls[-1], ("robot.move", command))
        self.assertIn("error", self.send("move", vx=0, vy=0, vyaw=1.501))
        self.assertEqual(self.robot.calls[-1], ("robot.move", ZERO))

    def test_stop_and_unload_different(self):
        self.ready()
        self.send("stop")
        self.assertEqual(self.robot.calls[-1], ("robot.move", ZERO))
        self.assertNotIn(("robot.relax", None), self.robot.calls)
        self.send("relax")
        self.assertIn(("robot.relax", None), self.robot.calls)
        self.assertIsNone(self.bridge.owner)

    def test_fall_releases_motion_but_allows_authenticated_unload(self):
        self.ready()
        self.robot.frame["safety"]["fallen"] = True
        self.bridge.tick()
        self.assertIsNone(self.bridge.owner)
        self.assertIn("error", self.send("arm"))
        self.assertEqual(self.send("relax"), {"result": {"accepted": True}})
        self.assertEqual(self.robot.calls[-1], ("robot.relax", None))
        self.assertIsNone(self.bridge.owner)
        self.assertEqual(self.send("status")["result"]["relax_state"], "pending")
        self.robot.bus_report.update(policy_enabled=False, native={"torque_state_confirmed": False})
        self.assertEqual(self.send("status")["result"]["relax_state"], "pending")
        self.robot.bus_report["cycle"] += 1
        self.assertEqual(self.send("status")["result"]["relax_state"], "confirmed")

    def test_unload_without_fresh_readback_stays_unconfirmed(self):
        self.send("relax")
        self.now += 3.1
        self.robot.bus_report.update(cycle=11, fresh_sample=False, policy_enabled=False,
                                     native={"torque_state_confirmed": False})
        self.assertEqual(self.send("status")["result"]["relax_state"], "unconfirmed")


if __name__ == "__main__":
    unittest.main()
