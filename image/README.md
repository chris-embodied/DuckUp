# DuckUp · Radxa ZERO 3W

通用镜像默认提供只读观察。底层采用 Radxa Debian 12 的 ZERO 3 镜像，板端服务由本目录构建。目标硬件为 ZERO 3W、机械鸭 HAT、HD1910、总线 IMU、VL53L5CX 和 IMX219。遥控版将 V260 的运动驱动和动作文件随镜像安装。已标定的设备可在首次启动时导入自己的标定与机主钥匙。[用户安装指南](../docs/start.md)

## 构建

在仓库根目录使用 Python 3.11 或更高版本。Mac 的额外依赖可用 `brew install e2fsprogs mtools dtc xz` 安装。Linux 需要 e2fsprogs、mtools、device-tree-compiler、xz-utils 和 curl。建议预留 30 GB。构建全程操作普通文件，无需管理员权限。

```sh
python3 image/build.py
(cd output/zero3w-base && shasum -a 256 -c SHA256SUMS)
```

输出为 `output/zero3w-base/duck-base-zero3w-v1.img`。脚本固定下载来源及 SHA-256，保留官方引导程序、分区布局、内核和两种无线芯片的设备树。可重建相同配置，每次构建的文件系统时间戳会影响最终镜像哈希。与镜像一同保存当次生成的 `verification.json` 和 `SHA256SUMS`。镜像使用无桌面启动目标，官方图形软件包仍保留。

DuckUp 作为蓝牙外设提供状态，关闭对手机的反向服务查询。鸭控台记住配对后，在前台遇到意外断链会尝试重连并重新认证。点「断开连接」会停止自动重连，之后可在「设备」中手动选择蓝牙设备 **Duck Base**。

## 构建遥控版

在上一步相同的环境中，指定开发者已有的、已解压的 Microduck Console Studio V260 根目录。构建器会核验内含的 FT6 R17 归档、运行库、策略和控制租约，再生成独立的遥控镜像。它在 FAT 和系统分区分别写入遥控版标记。个人标定、Wi-Fi 与机主钥匙只在刷卡后为设备配置时写入。

```sh
python3 image/build.py --v260-console '/path/to/Microduck-Console-Studio-V260' --out output/zero3w-motion
(cd output/zero3w-motion && shasum -a 256 -c SHA256SUMS)
python3 image/test_first_remote.py --image output/zero3w-motion/duck-base-zero3w-motion-v1.img
```

最后一条读取普通镜像文件，在临时目录走一遍首次配置、虚拟舵机及手机控制协议。对外分发镜像前需核对[上游资产的分发范围](../THIRD_PARTY.md)。携带原有步态配置的开发卡可继续使用自己的模型。此候选遥控镜像的默认走路和坐站模型均取自 V260。

## 写卡、配网和配对

原卡重刷和备用卡安装共用一套镜像。刷写前先按[安装指南](../docs/start.md)将实机标定备份到 Mac。刷完后只需选原标定、填写 Wi-Fi、用手机扫一次机主二维码。准备卡片后，私有回执默认存放在 Mac 的 `~/.duckbase/` 下。连接 SSH 时可使用已有的本机密钥。如果助手新生成了密钥，请以 `ssh -i /path/to/receipt/id_ed25519 radxa@duckbase.local` 登录。SSH 仅用于维护。

手工配置入口如下，密码在终端私密输入。省略 `--calibration` 则进入观察模式。原有观察镜像也可用这个脚本配网。

```sh
python3 image/prepare-card.py '/Volumes/刷好镜像的配置卷' --calibration '/path/to/your/calibration.toml'
```

启动时由 `duck-provision` 核验标定和机主密钥。如果文件检查失败，先保留错误信息和个人回执。运动配置就绪后由 `duck-home` 独占 UART2，观察与控制共用驱动反馈。用户在 App 主动发出 HOME、启用遥控或卸力请求。卸力是否生效以新一帧舵机反馈为准。

## 板上验收

- 执行 `sudo duck-check`。它等待最多 30 秒，报告本次启动编号、实际应答的舵机 ID、IMU、8×8 测距、有效区域数、新鲜 JPEG 和 ALSA 声卡。裸板可先验启动、联网和服务；装上外设再验完整清单。舵机及 HUB 要使用适合它们的供电。
- 在鸭控台「设备」中连接蓝牙设备 **Duck Base**，进入「观察」。核对 15 颗舵机、主板温度和姿态，再让镜头前的物体移动、改变 ToF 前方目标的距离，确认画面和测距随之变化。Wi-Fi 接收 1280 × 720 JPEG，蓝牙提供低分辨率预览。高清路径在受信局域网用 HTTP 传输，每帧使用经蓝牙认证签发的短时一次性票据。
- 执行 `systemctl status duck-provision duck-base`，使用 `journalctl -b -u duck-provision -u duck-base` 诊断启动。串口设备为 UART2 `/dev/ttyS2`，波特率 1 Mbps。默认观察模式只发读取寄存器指令。启用 HOME 后由 duck-home 独占该串口。原始位置、舵机端电压、温度位于 `/run/duck-base/bus.json`。观察模式的电压取当前应答舵机中的最低读数；HOME 模式显示驱动报告的电池电压。音频的自动检查验证 HAT 的 aic3104 声卡枚举。首次启动时，duck-audio 使用镜像内的 Radxa 驱动源码和配套内核头文件离线编译，后续启动直接加载。板载麦克风接右侧 MIC3R，已有 ALSA 音量配置优先。可用 `arecord -l` 和 `aplay -l` 核对录放设备，喇叭声音以实听为准。
- 回退前正常关机断电。保留了原卡可直接换回；若重刷了唯一的卡，要原样恢复旧环境，请写回刷前保存的整卡镜像。

## 来源与离线验证

- 官方系统及发布记录见 [Radxa ZERO 3 rsdk-b1](https://github.com/radxa-build/radxa-zero3/releases/tag/rsdk-b1)。串口、I²C 和 CSI 摄像头使用镜像随附的硬件覆盖文件。HAT 音频描述来自 [kissqy 对应版本源码](https://github.com/kissqy/Microduck-kissqy/blob/6e1f336083450cd9161193560f75c88dc0c7ff02/versions/v260/firmware/deploy/audio/aic3104-i2c3.dts)。测距使用 [VL53L5CX](https://pypi.org/project/VL53L5CX/1.0.1/) 和 [smbus2](https://pypi.org/project/smbus2/0.5.0/)。下载版本与哈希位于 `build.py`，Python 依赖以保留上游元数据的原始 wheel 随镜像安装。
- 构建时分别合并 AP6212 和 AIC8800 两种设备树，检查设备节点、串口控制台配置和服务链接，逐文件读回、检查 ext4，并对写入成品的两个修改分区重新计算哈希。
- Mac 可用 `python3 -m unittest discover -s robot/tests` 和 `python3 -m unittest discover -s robot/bridge-tests` 复核协议及兼容性。镜像校验和离线测试单独记录。2026-11-21 已在首张新卡的 ZERO 3W 上完成启动、在线更新、蓝牙读数、高清取帧和 HAT 声卡验收。后续重建产物的首次启动须按上述步骤单独验证。

### 已标定设备的完整遥控

开发实机和遥控镜像均使用各自导入的 15 颗舵机及 IMU 标定。完整遥控复用 V260 的 FT6 R17 驱动、HD1910 动作模型、声音素材与输入租约。只有启用完整遥控的设备使用 `remote.toml` 和 `full-control.conf`。通用镜像不含用户的运动密钥和实机标定。

鸭控台先用观察配对码连接，再用这部 iPhone 钥匙串内的设备密钥建立独立遥控会话。启动后保持卸力，点「遥控 → 准备站姿」，等待 HOME 反馈，再点「开始遥控」。模型配置中的 enabled 表示加载模型，实际运动开关以总线报告中的 policy_enabled 为准。「动作」按主控实际返回的清单展示。

### 对比 VelStand s54 与 V260

以下快捷方式适用于已经另外部署候选模型和切换工具的开发实机。基础遥控镜像只带 V260 模型。以下 `models/profiles/` 路径均相对于[模型仓](https://github.com/chris-embodied/duckup-models)。Mac 在同一 Wi-Fi 下，先在手机托稳卸力，再打开 `models/profiles/试用 VelStand s54.command` 或 `models/profiles/切回 V260.command`。切换工具从运行中的主控核对模型和舵机卸力反馈，短暂重连后以手机「遥控」显示的步态名称为准。每次切换都需重新准备站姿。

s54 对应 yaw_turn_only-s54 第 400 次更新，采用归一化已内嵌的冻结 ONNX。R17 适配文件用本次模型及训练配置生成。s54 使用训练中的 50 Hz、14 关节、动作倍率 1 和无额外滤波；V260 保留自己的执行配置。两版共享本机零位、方向、IMU、保护设置及其他动作模型。

互动试用档使用 s54 权重，头部和身体姿态的指令范围逐项取自 V260 的 deployment-runtime.json。颈、俯仰、左右转头、歪头分别为 ±1.1、±1.1、±1.4、±0.31 rad，身体位移三轴各 ±0.005 m、转角三轴各 ±0.05 rad。这些是输入范围，实际关节角度由 s54 输出。卸力后打开 `models/profiles/试用 s54 歪头.command` 启用，打开 `models/profiles/试用 VelStand s54.command` 恢复 s54 原始指令范围。

### 单独切换坐站模型

这台鸭子另装有飞特版 SitStand r3-s111。先托住机身、确认卸力，再从 Mac 打开 `models/profiles/试用 SitStand s111.command` 或 `models/profiles/切回 V260 坐站.command`。快捷方式只切坐站；原有的步态快捷方式也只切步态。App 中的「准备站姿」仍为 HOME，实际调用坐站模型请用「坐下」和「站起来」。冻结候选此前在静态仿真通过 18/18，长时 8/12，坐姿短停留并动头仍有失稳记录。
