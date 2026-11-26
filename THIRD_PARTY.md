# 依赖与素材

| 部分 | 来源和此次处理 |
| --- | --- |
| 操作系统、内核、声卡驱动 | 构建时读取固定版本的 Radxa 系统及驱动源码，逐文件核对 SHA-256。依赖列表见 [构建脚本](image/build.py) 和 [Radxa ZERO 3 发布页](https://github.com/radxa-build/radxa-zero3/releases/tag/rsdk-b1)。仓库提供构建脚本。分发已构建的镜像前还需核对镜像内软件的许可和对应源码提供方式。 |
| 飞特 HD1910 适配、声音和运动 | 兼容 kissqy V260 / control.102。镜像脚本从固定提交获取声卡设备树，运动配置按 DuckUp 的目录及独立遥控授权适配。kissqy 按内容分项授权，运行时、模型权重和声音包各自核对。见 [许可索引](https://github.com/kissqy/Microduck-kissqy/blob/6e1f336083450cd9161193560f75c88dc0c7ff02/LICENSE)、[软件与模型说明](https://github.com/kissqy/Microduck-kissqy/blob/6e1f336083450cd9161193560f75c88dc0c7ff02/microduck-hd1910/docs/SOFTWARE-MODEL-RIGHTS.md) 和 licenses 目录内保留的上游文本。 |
| 策略 | s54 和 s111 是开发实机使用的训练产物。[模型仓](https://github.com/chris-embodied/duckup-models)收录部署配置与打包工具，权重及训练数据的发布另行处理。踢球、低头、前滚翻复用上游技能。 |
| 三维模型 | 完整外观基于 Pollen Robotics 的 STL 和运动骨架。上游对指定 STL 标注 CC BY-NC-SA 4.0。草稿提供姿态解码与显示代码，没有附带派生几何文件。见 [模型许可及范围](https://github.com/pollen-robotics/microduck_rl/blob/develop/LICENSE-MODELS.md)。 |
| 测距 | 构建时按哈希下载 VL53L5CX 和 smbus2 的原始 wheel，保留包内元数据。版本见 [构建脚本](image/build.py)。 |

DuckUp 和鸭控台是独立的适配项目。感谢 Radxa、Pollen Robotics、kissqy 及各上游贡献者。

### 演示画面

`docs/media/console.png` 为项目开发时制作的 iPhone 界面预览，观察页中可见基于 Pollen Robotics Microduck 的外观。`docs/media/find-me-sim.gif` 为本项目身后找人物理仿真的节选，机器人外观源自 Microduck。所示外观的上游权利和署名沿用 [Pollen Robotics 的模型许可说明](https://github.com/pollen-robotics/microduck_rl/blob/develop/LICENSE-MODELS.md)。演示图与视频不授予模型的额外权利。二维离线演示以俯视图显示方向与视野。
