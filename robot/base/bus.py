"""ZERO 3W telemetry. The only bus instruction this module can emit is READ."""
import fcntl
import math
import os
import select
import struct
import termios
import time
import tty

IDS = (*range(20, 25), *range(30, 35), *range(10, 15))


def open_bus(path):
    fd = os.open(path, os.O_RDWR | os.O_NONBLOCK | os.O_NOCTTY)
    try:
        fcntl.ioctl(fd, termios.TIOCEXCL)
        tty.setraw(fd)
        attr = termios.tcgetattr(fd)
        attr[2] &= ~(termios.CRTSCTS | termios.CSTOPB | termios.PARENB)
        attr[2] |= termios.CLOCAL | termios.CREAD
        attr[4] = attr[5] = termios.B1000000
        termios.tcsetattr(fd, termios.TCSANOW, attr)
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_register(fd, address, register, size):
    body = bytes([address, 4, 2, register, size])
    packet = b'\xff\xff' + body + bytes([~sum(body) & 255])
    termios.tcflush(fd, termios.TCIFLUSH)
    if os.write(fd, packet) != len(packet):
        raise OSError("short UART write")
    received = bytearray()
    deadline = time.monotonic() + .015
    while (remaining := deadline - time.monotonic()) > 0:
        if not select.select([fd], [], [], remaining)[0]:
            break
        chunk = os.read(fd, 256)
        if not chunk:
            break
        received.extend(chunk)
        # Some adapters echo the request. Match the entire expected reply,
        # including the device, length, error byte and checksum.
        prefix = bytes([255, 255, address, size + 2, 0])
        for start in range(len(received) - size - 5):
            frame = received[start:start + size + 6]
            if frame.startswith(prefix) and sum(frame[2:]) & 255 == 255:
                return bytes(frame[5:-1])
    raise TimeoutError(f"no valid reply from ID {address}")


def position(data):
    value = int.from_bytes(data[:2], 'little')
    return -(value & 32767) if value & 32768 else value


def gravity(data, mount):
    x, y, z = struct.unpack_from('<3e', data, 6)
    norm = x*x + y*y + z*z
    if not 0 <= norm <= 1.02 or data[13] & 7 or data[13] & 0xf0 or data[14]:
        return None
    w = math.sqrt(max(0, 1 - norm))
    a, b, c, d = mount
    # sensor->world composed with inverse(sensor->body)
    q = (w*a+x*b+y*c+z*d, -w*b+x*a-y*d+z*c,
         -w*c+x*d+y*a-z*b, -w*d-x*c+y*b+z*a)
    w, x, y, z = (v / math.hypot(*q) for v in q)
    return [2*(w*y-x*z), -2*(w*x+y*z), 2*(x*x+y*y)-1]


def read_cycle(fd, calibration, previous_sequence):
    servos = {}
    for device in IDS:
        try:
            raw = read_register(fd, device, 56, 8)
        except TimeoutError:
            continue
        servos[device] = [position(raw), raw[6] / 10, raw[7]]
    try:
        imu = read_register(fd, 200, 56, 15)
    except TimeoutError:
        imu = None
    sequence = imu[12] if imu else None
    imu_ok = bool(imu and imu[13] & 0xf7 == 0 and imu[14] == 0
                  and sequence != previous_sequence and math.hypot(*struct.unpack_from('<3e', imu, 6)) <= 1.01)
    g = gravity(imu, calibration['mount']) if imu_ok and calibration.get('mount') else None
    angles = None
    if len(servos) == len(IDS) and calibration.get('joints'):
        angles = [(servos[i][0]-zero)*direction*math.tau/4096
                  for i, (zero, direction) in zip(IDS, calibration['joints'], strict=True)]
    return {'t': time.monotonic_ns(), 'servos': servos, 'imu': imu_ok,
            'sequence': sequence, 'g': g, 'angles': angles}
