"""Bake short traces from the desktop prototype into an offline browser replay."""
from pathlib import Path
import json

from engine import Duck

ROOT = Path(__file__).resolve().parent
SCENARIOS = {"front": 0, "left": 90, "rear": 180, "empty": None}


def trace(bearing):
    duck = Duck()
    duck.wake()
    frames = []
    for step in range(361):
        if step:
            duck.tick(0.1, bearing)
        frames.append([round(duck.body, 2), round(duck.head, 2), duck.state, duck.seen])
        if duck.state in ("dialogue", "idle"):
            return frames
    raise RuntimeError(f"Scenario did not finish: {bearing}")


if __name__ == "__main__":
    data = {key: {"bearing": bearing, "frames": trace(bearing)} for key, bearing in SCENARIOS.items()}
    source = (ROOT / "template.html").read_text()
    assert source.count("/*TRACES*/") == 1
    (ROOT / "index.html").write_text(source.replace("/*TRACES*/", json.dumps(data, separators=(",", ":"))))
