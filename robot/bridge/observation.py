"""Read-only observations. Media IPC and conversion never run on the BLE event loop."""
import base64
import collections
import json
import math
import socket
import struct
import threading
import time
import zlib


def fresh(stamp, now, age):
    return type(stamp) is int and 0 <= now - stamp <= age


def grid(frame):
    values, statuses = frame.get("distance_mm"), frame.get("status")
    if (frame.get("rows"), frame.get("cols")) != (8, 8) or not all(
        isinstance(a, list) and len(a) == 64 for a in (values, statuses)
    ) or type(frame.get("t_ns")) is not int:
        return None
    return {"t": frame["t_ns"], "mm": [
        mm if type(mm) is int and mm > 0 and s in (5, 9) else 0 for mm, s in zip(values, statuses)
    ]}


def pose(frame):
    joints = frame.get("joints")
    if not isinstance(joints, list) or len(joints) != 15:
        return None
    finite = lambda x: type(x) in (float, int) and math.isfinite(x)
    # Slot 9 is the mouth. The upstream visual has no mouth hinge.
    if not all(finite(x) for i, x in enumerate(joints) if i != 9):
        return None
    g = (frame.get("safety") or {}).get("gravity")
    yaw = (frame.get("odom") or {}).get("yaw")
    return {"j": [round(x, 5) if finite(x) else None for x in joints],
            "g": g if isinstance(g, list) and len(g) == 3 and all(map(finite, g)) and math.hypot(*g) >= .1 else None,
            "yaw": yaw if finite(yaw) else None, "t": frame["t_ns"]}


def thumbnail(meta, pixels):
    """Small fixed-palette PNG from the actual UYVY pixels, without external codecs."""
    w, h = meta["width"], meta["height"]
    width = min(96, w)
    height = max(1, min(72, round(h * width / w)))
    width = max(1, min(width, round(w * height / h)))
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        line = (y * h // height) * w
        for x in range(width):
            col = x * w // width
            off = 2 * (line + (col & ~1))
            u, v = pixels[off] - 128, pixels[off+2] - 128
            luma = 298 * (pixels[off + (3 if col & 1 else 1)] - 16)
            r, g, b = (max(0, min(255, (a + 128) >> 8)) for a in
                       (luma + 409*v, luma - 100*u - 208*v, luma + 516*u))
            rows.append((r & 224) | ((g >> 3) & 28) | (b >> 6))
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    palette = bytes(value for i in range(256) for value in
                    (round((i >> 5)*255/7), round(((i >> 2)&7)*255/7), (i&3)*85))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB",width,height,8,3,0,0,0))
            + chunk(b"PLTE",palette) + chunk(b"IDAT",zlib.compress(rows, 3)) + chunk(b"IEND",b""))


class Observation:
    def __init__(self):
        self.until = 0
        self.lock = threading.Lock()
        self.ranges = collections.deque(maxlen=64)
        self.pictures = collections.deque(maxlen=5)
        self.camera_state = "waiting"
        self.workers = {}

    def snapshot(self, frame, params):
        now = time.monotonic_ns()
        with self.lock:
            self.until = time.monotonic() + 4
            for name, target in (("tof", self._tof), ("camera", self._camera)):
                if name not in self.workers or not self.workers[name].is_alive():
                    self.workers[name] = threading.Thread(target=target, daemon=True, name=name)
                    self.workers[name].start()
            ranges = list(self.ranges)
            pictures = list(self.pictures)
            state = self.camera_state
        result = {"now": now, "pose": pose(frame) if frame else None, "camera_state": state}
        if ranges and fresh(ranges[-1]["t"], now, 1_000_000_000):
            result["tof"] = ranges[-1]
        if not pictures:
            return result
        # Pull bounded blocks from one immutable frame; never interleave two images.
        requested, offset = params.get("image_t"), params.get("offset", 0)
        item = next((p for p in pictures if p[0] == requested), pictures[-1])
        t, png = item
        if not fresh(t, now, 4_000_000_000) or params.get("seen") == t:
            return result
        if requested != t or type(offset) is not int or not 0 <= offset < len(png):
            offset = 0
        data = png[offset:offset+1200]
        camera = {"t": t, "n": len(png), "at": offset, "data": base64.b64encode(data).decode()}
        if offset + len(data) == len(png) and ranges:
            match = min(ranges, key=lambda r: abs(t - r["t"]))
            if abs(match["t"] - t) <= 250_000_000:
                camera["tof"] = match
        result["image"] = camera
        return result

    def _tof(self):
        # A single quiet subscription survives page switches. It only caches while watched.
        while self.until > time.monotonic():
            try:
                with socket.socket(socket.AF_UNIX) as conn:
                    conn.connect("/run/tofd/tof.sock")
                    conn.sendall(b'{"jsonrpc":"2.0","id":1,"method":"tof.stream","params":{}}\n')
                    with conn.makefile("rb") as stream:
                        while True:
                            line = stream.readline(32769)
                            if not line or len(line) > 32768:
                                raise ValueError("tof stream ended")
                            msg = json.loads(line)
                            if msg.get("error"):
                                raise ValueError("tof unavailable")
                            if self.until < time.monotonic():
                                return
                            if msg.get("method") == "tof.frame":
                                item = grid(msg["params"])
                                if item:
                                    with self.lock:
                                        self.ranges.append(item)
            except (OSError, ValueError, KeyError, TypeError):
                time.sleep(.75)

    def _camera(self):
        from contextlib import ExitStack
        stop = threading.Event()
        while self.until > time.monotonic():
            try:
                from ai_vision import CameraLease, raw_frame
                with ExitStack() as resources:
                    resources.enter_context(CameraLease(stop))
                    while self.until > time.monotonic():
                        meta, pixels = raw_frame(stop)
                        # mediad stamps Unix microseconds; tofd and robotd use monotonic ns.
                        stamp = time.monotonic_ns() + meta["captured_at_unix_us"]*1000 - time.time_ns()
                        if not fresh(stamp, time.monotonic_ns(), 1_000_000_000):
                            raise ValueError("stale camera")
                        data = thumbnail(meta, pixels)
                        with self.lock:
                            self.pictures.append((stamp, data))
                            self.camera_state = "live"
                        time.sleep(.85)
            except Exception as exc:
                with self.lock:
                    self.camera_state = "unavailable"
                time.sleep(.9)
