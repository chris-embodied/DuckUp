"""Independent sensor workers, shared by the local hardware check and BLE viewer."""
import json
import logging
import os
from pathlib import Path
import threading
import time

from bus import IDS, open_bus, read_cycle
from observation import Observation, thumbnail

RUN = Path('/run/duck-base')


def publish(path, data):
    temp = path.with_suffix('.tmp')
    temp.write_bytes(data)
    temp.replace(path)


def worker(target):
    def retry():
        while True:
            try:
                target()
            except Exception as exc:
                logging.warning('%s: %s', target.__name__, exc)
                time.sleep(4)
    threading.Thread(target=retry, daemon=True, name=target.__name__).start()


class Sensors:
    def __init__(self, calibration):
        self.calibration = calibration
        self.sample = {}
        self.errors = 0

    def poll(self):
        fd = open_bus('/dev/ttyS2')
        try:
            previous = None
            while True:
                sample = read_cycle(fd, self.calibration, previous)
                previous = sample['sequence']
                self.errors = 0 if len(sample['servos']) == 15 and sample['imu'] else self.errors + 1
                self.sample = sample
                publish(RUN/'bus.json', json.dumps(sample).encode())
                time.sleep(.15)
        finally:
            os.close(fd)

    def latest(self):
        if time.monotonic_ns() - self.sample.get('t', 0) < 1_500_000_000:
            return self.sample
        return {}

    def frame(self):
        sample = self.latest()
        if sample.get('angles') is not None:
            return {'t_ns': sample['t'], 'joints': sample['angles'], 'safety': {'gravity': sample['g']}}

    def status(self):
        sample = self.latest()
        servos = sample.get('servos', {})
        missed = [i for i in IDS if i not in servos]
        good = not missed and bool(sample.get('imu'))
        health = {'healthy': good, 'imu': {'ready': bool(sample.get('imu'))},
                  'bus': {'consecutive_errors': self.errors if sample else max(1, self.errors)},
                  'servo_count': len(servos), 'missing_ids': missed}
        if not good:
            health['reason'] = '舵机反馈 %d/15，IMU %s' % (len(servos), '就绪' if sample.get('imu') else '等待反馈')
        if servos:
            health['battery'] = {'volts': min(row[1] for row in servos.values())}
            health['motors'] = {'max_c': max(row[2] for row in servos.values())}
        try:
            health['cpu_temp_c'] = int(Path('/sys/class/thermal/thermal_zone0/temp').read_text()) / 1000
        except (OSError, ValueError):
            pass
        return {'fresh': bool(sample), 'health': health, 'read_only': True,
                'observation_version': 1, 'skills': [], 'armed': False, 'enabled': False}


class CameraRange(Observation):
    def _tof(self):
        from vl53l5cx.vl53l5cx import VL53L5CX
        sensor = None
        started = False
        try:
            sensor = VL53L5CX(bus_id=3)
            if not sensor.is_alive():
                raise OSError('VL53L5CX not found at 0x29 on I2C3')
            sensor.init()
            sensor.set_resolution(64)
            sensor.set_ranging_frequency_hz(8)
            sensor.start_ranging()
            started = True
            while time.monotonic() < self.until:
                if sensor.check_data_ready():
                    frame = sensor.get_ranging_data()
                    mm = [max(0, int(v)) if s in (5, 9) else 0
                          for v, s in zip(frame.distance_mm, frame.target_status, strict=True)]
                    if len(mm) != 64:
                        raise ValueError('ToF resolution mismatch')
                    result = {'mm': mm, 't': time.monotonic_ns()}
                    with self.lock:
                        self.ranges.append(result)
                    publish(RUN/'tof.json', json.dumps(result).encode())
                time.sleep(.04)
        except Exception as exc:
            logging.warning('ToF: %s', exc)
            time.sleep(3)
        finally:
            try:
                if started:
                    sensor.stop_ranging()
            finally:
                if sensor is not None:
                    sensor._i2c_bus.close()

    def _camera(self):
        from gi.repository import Gst
        device = next((p.parent.name for p in sorted(Path('/sys/class/video4linux').glob('video*/name'))
                       if p.read_text().strip() == 'rkisp_mainpath'), None)
        if not device:
            self.camera_state = 'unavailable'
            time.sleep(2)
            return
        # The preview worker owns the capture node. Keep only the newest frame on each branch.
        pipeline = Gst.parse_launch(f'v4l2src device=/dev/{device} ! video/x-raw,format=NV12,width=1280,height=720 '
            '! tee name=t t. ! queue leaky=downstream max-size-buffers=1 ! videorate drop-only=true '
            '! video/x-raw,framerate=4/1 ! jpegenc quality=82 ! appsink name=hd drop=true max-buffers=1 sync=false '
            't. ! queue leaky=downstream max-size-buffers=1 ! videoscale ! videoconvert '
            '! video/x-raw,format=UYVY,width=96,height=54 ! appsink name=small drop=true max-buffers=1 sync=false')
        def pull(name):
            item = pipeline.get_by_name(name).emit('try-pull-sample', Gst.SECOND)
            if item is None:
                raise OSError('no camera frame')
            buffer = item.get_buffer()
            return buffer.extract_dup(0, buffer.get_size())
        try:
            pipeline.set_state(Gst.State.PLAYING)
            last_preview = 0
            while time.monotonic() < self.until:
                jpeg = pull('hd')
                raw = pull('small')
                if len(raw) != 96*54*2 or not jpeg.startswith(b'\xff\xd8'):
                    raise ValueError('invalid camera frame')
                stamp = time.monotonic_ns()
                publish(RUN/'camera.jpg', jpeg)
                # BLE reads each PNG in fragments. Retain a frame across several requests.
                if stamp - last_preview >= 850_000_000:
                    png = thumbnail({'width': 96, 'height': 54}, raw)
                    with self.lock:
                        self.pictures.append((stamp, png))
                        self.camera_state = 'live'
                    last_preview = stamp
                time.sleep(.24)
        except Exception as exc:
            self.camera_state = 'unavailable'
            logging.warning('camera: %s', exc)
            time.sleep(2)
        finally:
            pipeline.set_state(Gst.State.NULL)
