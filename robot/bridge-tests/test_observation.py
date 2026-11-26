import base64
from pathlib import Path
import struct
import sys
import time
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
from observation import Observation, fresh, grid, pose, thumbnail


class ObservationChecks(unittest.TestCase):
    def test_range_validity(self):
        frame = {"t_ns": 11, "rows": 8, "cols": 8, "distance_mm": [210]*64, "status": [5,9,6,0]*16}
        self.assertEqual(grid(frame)["mm"][:4], [210,210,0,0])
        self.assertIsNone(grid(dict(frame, rows=4)))
        self.assertFalse(fresh(12, 11, 300))

    def test_only_measured_pose(self):
        f = {"t_ns": 7, "targets": [1]*15, "safety": {"gravity": [0,0,-1]}}
        self.assertIsNone(pose(f))
        self.assertEqual(pose(dict(f,joints=[.2]*15))["j"], [.2]*15)
        self.assertIsNone(pose(dict(f,joints=[float("nan")]*15)))

    def test_image_and_ranges_pair_across_blocks(self):
        o = Observation()
        o.workers = {k: Mock(is_alive=lambda: True) for k in ("camera","tof")}
        now = time.monotonic_ns()
        o.ranges.extend([{"t": now-40_000_000,"mm":[10]*64},{"t":now,"mm":[20]*64}])
        t = now-37_000_000
        png = bytes(range(256))*11
        o.pictures.append((t,png))
        r = o.snapshot(None,{})
        self.assertEqual(r["tof"]["mm"][0],20)
        data = bytearray()
        while len(data)<len(png):
            image = r["image"]
            self.assertEqual(image["at"],len(data))
            data.extend(base64.b64decode(image["data"]))
            if len(data)<len(png):
                o.pictures.append((now,b"later"))
                r = o.snapshot(None,{"image_t":t,"offset":len(data)})
        self.assertEqual(data,png)
        self.assertEqual(image["tof"]["mm"][0],10)
        o.pictures.clear()
        o.pictures.append((now-500_000_000,b"old"))
        self.assertNotIn("tof",o.snapshot(None,{})["image"])
        o.pictures.clear()
        o.pictures.append((now-5_000_000_000,png))
        self.assertNotIn("image",o.snapshot(None,{}))

    def test_small_png(self):
        data = thumbnail({"width":160,"height":120}, bytes([128,128])*160*120)
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(struct.unpack(">II", data[16:24]),(96,72))
