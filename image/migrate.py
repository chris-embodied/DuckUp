"""Interactive Mac assistant for exporting calibration and setting up a freshly flashed card."""
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from check_calibration import MOTION_MARKER, inspect_bytes


HERE = Path(__file__).resolve().parent


def export_calibration(host, directory):
    host = host.strip().removeprefix("radxa@")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}", host):
        raise ValueError("请输入旧鸭子的 IP 或主机名。")
    raw = subprocess.run(
        ["ssh", "-T", "-o", "ConnectTimeout=8", "-o", "ForwardAgent=no", "-o",
         "ClearAllForwardings=yes", "-l", "radxa", "--", host, "python3 -"],
        input=(HERE/"read_active_calibration.py").read_bytes(), stdout=subprocess.PIPE, check=True,
    ).stdout
    issues = inspect_bytes(raw)
    if issues:
        raise ValueError("标定未通过检查：" + "；".join(issues))
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup_dir = Path(tempfile.mkdtemp(prefix="鸭子标定-", dir=directory))
    backup = backup_dir/"calibration.toml"
    with backup.open("xb") as f:
        os.fchmod(f.fileno(), 0o600)
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    if backup.read_bytes() != raw:
        raise OSError("备份读回不一致。")
    return backup


def motion_volume(mounts):
    matches = []
    for volume in mounts:
        base, motion = volume/"DUCKBASE.ID", volume/"DUCKMOT.ID"
        if (base.is_file() and motion.is_file() and base.read_bytes() == b"duck-base-zero3w-v1\n"
                and motion.read_bytes() == MOTION_MARKER):
            matches.append(volume)
    if len(matches) > 1:
        raise ValueError("找到多张 DuckUp 遥控卡。请只保留这次要配置的卡。")
    if not matches:
        raise ValueError("没有找到 DuckUp 遥控版配置卷。请先备份原系统标定，再刷入 motion 镜像并重新插入读卡器。")
    return matches[0]


def choose_calibration():
    location = Path.home()/".duckbase"
    if not location.is_dir():
        location = Path.home()
    selection = subprocess.run(
        ["osascript", "-e", 'on run argv', "-e",
         'return POSIX path of (choose file with prompt "选择这只鸭子的实机标定 TOML" default location (POSIX file (item 1 of argv)))',
         "-e", 'end run', str(location)],
        capture_output=True, text=True,
    )
    if selection.returncode:
        raise ValueError("已取消文件选择。")
    return Path(selection.stdout.rstrip("\n"))


def main():
    print("DuckUp 安装助手（支持原卡重刷或备用卡）\n1  刷卡前，将原系统的实机标定备份到 Mac\n2  刷卡后，用 Mac 上的标定配置这张卡")
    choice = input("选择 1 或 2：").strip()
    if choice == "1":
        host = input("输入原中控显示的鸭子 IP 或主机名：")
        backup = export_calibration(host, Path.home()/".duckbase")
        print(f"15 个关节和 IMU 安装数据已核对，备份保存在 {backup}")
        subprocess.run(["open", "-R", backup], check=False)
        print("重刷原卡会清空卡内数据。请另存要保留的文件，如需完整回退，先备份并校验整卡镜像。也可使用备用卡。用 Etcher 写入并校验后，再运行本助手选 2。")
    elif choice == "2":
        volume = motion_volume(p for p in Path("/Volumes").iterdir() if p.is_mount())
        print(f"已识别带 DuckUp 遥控标记的配置卷：{volume}")
        cal = choose_calibration()
        print(f"标定来源：{cal}\n请确认这份标定来自即将使用 DuckUp 的那只鸭。")
        if input("确认后按回车，输入其他内容取消："):
            return
        subprocess.run([sys.executable, HERE/"prepare-card.py", volume, "--calibration", cal, "--reveal"], check=True)
    else:
        raise ValueError("已退出换卡助手。")


if __name__ == "__main__":
    try:
        main()
    except (EOFError, KeyboardInterrupt):
        sys.exit("已取消。")
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        sys.exit(f"未完成：{exc}")
