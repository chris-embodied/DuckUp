#!/usr/bin/python3
"""Read-only Duck RC profile for Radxa OS. No robotd, policy or torque writer."""
import collections
import hashlib
import json
import logging
import math
from http.server import BaseHTTPRequestHandler, HTTPServer
import secrets
import socket
import threading
import time
from pathlib import Path

from control import Controller
from sensors import CameraRange, RUN, Sensors, worker
from bus import IDS


def default_observation_key():
    """Public pairing code for the read-only Duck Base profile."""
    return hashlib.sha256(b"duck-base/observe-pin/v1:000000").digest()


class FrameTickets:
    """One JPEG per random ticket. Only authenticated BLE observation requests mint tickets."""
    def __init__(self):
        self.tickets = collections.deque(maxlen=40)
        self.lock = threading.Lock()

    def issue(self):
        token = secrets.token_hex(24)
        with self.lock:
            self.tickets.append((token, time.monotonic() + 4))
        return token

    def take(self, token):
        with self.lock:
            for entry in tuple(self.tickets):
                if entry[0] == token:
                    self.tickets.remove(entry)
                    return time.monotonic() < entry[1]
        return False


def serve_frames(tickets):
    class Frame(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != '/frame.jpg' or not tickets.take(self.headers.get('Authorization', '').removeprefix('Bearer ')):
                self.send_error(403)
                return
            try:
                with (RUN/'camera.jpg').open('rb') as f:
                    import os
                    if time.time() - os.fstat(f.fileno()).st_mtime > 2:
                        raise OSError('stale')
                    data = f.read()
            except OSError:
                self.send_error(503)
                return
            self.send_response(200)
            self.send_header('Content-Type', 'image/jpeg')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    class Server(HTTPServer):
        def get_request(self):
            client, address = super().get_request()
            client.settimeout(1)
            return client, address

    with Server(('', 8421), Frame) as server:
        server.serve_forever()


class Monitor(Controller):
    def __init__(self, key, sensors, tickets, home=None):
        super().__init__(key, sensors, None)
        self.observation = CameraRange()
        self.tickets = tickets
        self.home = home
        self.remote = None

    def _command(self, peer, body):
        if self.home and body.get('command') in ('base_home', 'base_relax'):
            if self.remote and self.remote.owner is not None:
                raise ValueError('遥控已被占用，请在遥控端操作')
            nonce, seq = self.sessions[peer]
            return self.home.command(body['command'], body.get('proof'), nonce, seq)
        if body.get('command') == 'status':
            status = self.robot.status()
            if self.remote:
                status['base_remote_version'] = 1
            return status
        if body.get('command') == 'observe':
            result = self.observation.snapshot(self.robot.frame(), body)
            if self.observation.camera_state == 'live':
                result['frame_ticket'] = self.tickets.issue()
            return result
        raise ValueError('只读开发基座仅提供状态与画面')


def calibration(path):
    import tomllib
    if not path.is_file():
        return {}
    config = tomllib.loads(path.read_text())
    rows = {r['id']: r for r in config['joints'] if r['calibrated']}
    mount = config['imu_mount_quat'] if config.get('imu_mount_verified') else None
    if set(rows) != set(IDS) or any(type(r['zero_raw']) is not int or r['direction'] not in (-1, 1) for r in rows.values()):
        raise ValueError('关节标定不完整')
    if mount and (len(mount) != 4 or not math.isfinite(math.hypot(*mount)) or abs(math.hypot(*mount) - 1) > .01):
        raise ValueError('IMU 安装参数异常')
    return {'joints': [(rows[i]['zero_raw'], rows[i]['direction']) for i in IDS], 'mount': mount}


def main():
    import gi
    gi.require_version('Gst', '1.0')
    from gi.repository import Gst
    from ble import serve
    logging.basicConfig(level=logging.INFO)
    Gst.init(None)
    try:
        cal = calibration(Path('/etc/duck-base/calibration.toml'))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logging.warning('只展示原始反馈，标定不可用: %s', exc)
        cal = {}
    home = None
    home_key = Path('/etc/duck-base/home.key')
    if home_key.exists():
        from home import HomeDriver
        key = bytes.fromhex(home_key.read_text().strip())
        if len(key) != 32 or not cal.get('mount'):
            raise ValueError('HOME requires a private device key and verified calibration')
        home = HomeDriver(key)
    sensors = home or Sensors(cal)
    tickets = FrameTickets()
    monitor = Monitor(default_observation_key(), sensors, tickets, home)
    controller = monitor
    if home and Path('/etc/systemd/system/duck-home.service.d/full-control.conf').is_file():
        from remote import profiles
        controller = profiles(monitor)
    worker(sensors.poll)
    worker(lambda: serve_frames(tickets))
    def local_check():
        while True:
            flag = RUN/'check-until'
            if flag.exists() and time.time() - flag.stat().st_mtime < 20:
                monitor.observation.snapshot(sensors.frame(), {})
            time.sleep(.5)
    worker(local_check)
    while True:
        try:
            serve(controller, 'Duck Base')
            return
        except RuntimeError as exc:
            logging.warning('Bluetooth: %s', exc)
            time.sleep(5)


if __name__ == '__main__':
    main()
