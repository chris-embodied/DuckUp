# 一起做下一种玩法

最有用的贡献有三种：在另一只已标定的鸭子上复现原卡重刷或备用卡安装流程，提交一个有成功和退出条件的互动，或分享与冻结模型在相同条件下的对照。

**试装反馈**　告诉我们你的主板、HAT、舵机型号，复用哪套标定，在哪一步第一次看见状态、画面或动作。遇到问题请走「安装与复刻」Issue 模板，贴与该步相关的一小段错误即可。

**互动提案**　先描述人在做什么，以及鸭子什么时候该停止。可以从 [身后找人](examples/find-me/README.md) 这样的离线示例开始。提交 PR 时附短回放或可运行例子，注明桌面原型、物理仿真或实机。演示里出现他人的脸或声音前，请确认可公开。

**模型对照**　请提交到[模型仓](https://github.com/chris-embodied/duckup-models)。带上任务、关节顺序、训练起点的哈希、开发和确认用的种子，并报告至少一个失败案例。保留一个可切回的已知版本。权重与大文件通过单独的发布资产管理。

从仓库根目录检查 Python 部分：

```sh
python3 -B -m unittest discover -s robot/tests
python3 -B -m unittest discover -s robot/bridge-tests
python3 -B -m unittest discover -s examples/find-me
```

iPhone 工程位于 [apps/ios](https://github.com/chris-embodied/duckup-control/blob/main/apps/ios/README.md)。修改遥控协议时请一起运行板端测试，并注明旧 App 或旧板端的行为。不要提交个人标定、访问密钥、网络凭据或真实设备日志全集。
