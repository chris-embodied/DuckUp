"""Read a local HD1910 calibration before migrating cards. No device or network access."""
import argparse
import hashlib
import math
from pathlib import Path
try:
    import tomllib
except ModuleNotFoundError:
    # Xcode's macOS Python 3.9 already ships this parser with pip.
    from pip._vendor import tomli as tomllib

EXPECTED = {10, 11, 12, 13, 14, 20, 21, 22, 23, 24, 30, 31, 32, 33, 34}
NAMES = {
    10: "right_hip_yaw", 11: "right_hip_roll", 12: "right_hip_pitch", 13: "right_knee", 14: "right_ankle",
    20: "left_hip_yaw", 21: "left_hip_roll", 22: "left_hip_pitch", 23: "left_knee", 24: "left_ankle",
    30: "neck_pitch", 31: "head_pitch", 32: "head_yaw", 33: "head_roll", 34: "mouth",
}
MOTION_MARKER = b"duck-base-motion-v1\n"
# The V260 FT6 R17 archive includes a calibrated example from someone else's robot.
# Its valid fields alone cannot establish ownership of those physical offsets.
PACKAGED_EXAMPLES = {
    "b736b91117826ac5eda703179baff2b3bf94bb8405c8d211ea1e24924353d191",
    "5f3083e48ffd21787d05d70d34eeeb928c07e30dea477204ce6c67cf9bb2fba6",
    "004a87b1bef3769383df46e70d9d3c2fa879b0f08f429aae62e78b15564b6d99",
}


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def inspect(path):
    return inspect_bytes(path.read_bytes())


def inspect_bytes(raw):
    if hashlib.sha256(raw).hexdigest() in PACKAGED_EXAMPLES:
        return ["这是 V260 随包样本，请从自己的鸭子导出实际生效的标定"]
    data = tomllib.loads(raw.decode("utf-8"))
    rows = data.get("joints")
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        return ["缺少 joints 关节记录"]
    problems = []
    ids = [r.get("id") for r in rows]
    if len(rows) != len(EXPECTED) or any(type(i) is not int for i in ids) or set(ids) != EXPECTED:
        problems.append("请核对 10–14、20–24、30–34 各有且仅有一条记录")
    for row in rows:
        joint_id = row.get("id")
        if type(joint_id) is not int or joint_id not in NAMES or row.get("name") != NAMES[joint_id]:
            problems.append(f"关节 {joint_id} 的名称和 HD1910 编号不匹配")
        lo, hi = row.get("min_rad"), row.get("max_rad")
        zero, sign = row.get("zero_raw"), row.get("direction")
        if not (row.get("calibrated") is True and type(zero) is int and 0 <= zero < 4096
                and type(sign) is int and sign in (-1, 1) and number(lo) and number(hi) and lo < hi):
            problems.append(f"关节 {row.get('id', '?')} 的零位、方向、实测范围或标定标记不完整")
    q = data.get("imu_mount_quat")
    if not (data.get("imu_mount_verified") is True and isinstance(q, list) and len(q) == 4
            and all(number(x) for x in q) and abs(math.hypot(*q) - 1) <= .01):
        problems.append("IMU 安装方向或四元数未确认")
    if data.get("motion_enabled") is not True:
        problems.append("标定文件的 motion_enabled 尚未开启")
    return problems


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("file", type=Path)
    try:
        issues = inspect(cli.parse_args().file)
    except (OSError, ValueError) as exc:
        cli.exit(2, f"无法读取标定文件：{exc}\n")
    if issues:
        cli.exit(1, "\n".join(issues) + "\n")
    print("15 个 ID、关节标定字段和 IMU 安装字段齐全。请确认文件来自同一只实物鸭。")
