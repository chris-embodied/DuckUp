"""Read the calibration selected by a running Duck Base or kissqy HD1910 driver.

The Mac sends this short program over SSH stdin. It only reads the running
process, its configuration and the associated calibration; stdout is the TOML.
"""
from pathlib import Path
import subprocess
import sys
import tomllib


CALIBRATIONS = {
    "duck-home.service": "/etc/duck-base/calibration.toml",
    "robotd.service": "/etc/robot/feetech-ft5/hd1910-calibration.toml",
}


def selected_file(argv, unit, root):
    if not argv or Path(argv[0]).name != "robotd":
        raise ValueError("未识别到正在运行的 HD1910 驱动。")
    for index, arg in enumerate(argv):
        if arg == "--params" and index + 1 < len(argv):
            params = argv[index + 1]
            break
        if arg.startswith("--params="):
            params = arg.split("=", 1)[1]
            break
    else:
        raise ValueError("驱动没有报告当前配置文件。")
    if not Path(params).is_absolute():
        raise ValueError("无法确认驱动使用的配置路径。")
    with (root/params[1:]).open("rb") as f:
        configured = tomllib.load(f).get("bus", {}).get("feetech_calibration")
    if configured != CALIBRATIONS[unit]:
        raise ValueError("这台设备使用自定义标定位置，请从已验证的旧系统导出标定文件。")
    return root/configured[1:]


def read_active():
    drivers = []
    for unit in CALIBRATIONS:
        pid = subprocess.run(
            ["systemctl", "show", "--property=MainPID", "--value", unit],
            capture_output=True, text=True, check=False
        ).stdout.strip()
        if pid.isdecimal() and int(pid) > 0:
            drivers.append((unit, pid))
    if len(drivers) != 1:
        raise ValueError("无法确定哪一个驱动管理这只鸭。请使用已导出的实机标定。")
    unit, pid = drivers[0]
    argv = Path(f"/proc/{pid}/cmdline").read_bytes().decode().rstrip("\0").split("\0")
    return selected_file(argv, unit, Path("/")).read_bytes()


if __name__ == "__main__":
    try:
        sys.stdout.buffer.write(read_active())
    except (OSError, ValueError) as exc:
        sys.exit(str(exc))
