# 翻唱工厂 测试计划（TESTS.md）

## 0. 原则
- 先测管线通不通，再测音质；训练类测试用极小步数（100–300 步）验证管道，不追求好听。
- 每个 stage 单独可测；任何一个 stage 失败不得破坏 job.json 已有状态。
- 真跑测试只用本机已有素材，不触发下载。

## 1. 已有可复用素材（回归基准）
| 用途 | 路径 |
|---|---|
| 目标曲（4:19） | D:/Apps/MSST/input_yours/yours.wav |
| 目标曲分离 stem | <DATA_DIR>/sep_yours/yours/{Vocals.wav,instrumental.wav} |
| 目标曲 2（3:50） | <DATA_DIR>/raw/nekonichi/neko |
| 已训声库 rime7 | D:/Apps/_dl/applio/Applio-main/logs/rime7/rime7_145e_18125s.pth |
| 已训声库 suis5 | D:/Apps/_dl/applio/Applio-main/logs/suis5/suis5_60e_16200s.pth |
| 旧版 RVC | D:/Apps/_dl/old_rvc/rvc |
| MSST | D:/Apps/MSST/01_人声分离_MSST-GUI |
| 混音脚本 | <DATA_DIR>/out/tuning/mix_unity.sh |
| 质检 | <DATA_DIR>/out/tuning/preflight.py / artifacts.py |

## 2. 契约测试（不需要 GPU）
| 编号 | 用例 | 判定 |
|---|---|---|
| C1 | run.py init 生成 job.json | 文件存在、字段齐全、原子写 |
| C2 | run.py status 读取 | 返回各 stage 状态 |
| C3 | 重复 init 同 id | 覆盖或拒绝，不产生半截文件 |
| C4 | 非法 config（缺 target） | 退出码 1，错误信息明确 |

## 3. 阶段测试（GPU）
| 编号 | 阶段 | 输入 | 判定 |
|---|---|---|---|
| S1 | separate | yours.wav | 两 stem、时长一致、corr(v+i, src) > 0.99 |
| S2 | select | 已知对唱样本 + 独唱样本 | 对唱被排除、独唱保留 |
| S3 | dataset | 2 首短素材 | 切片数 > 0、含 -2 增强 |
| S4 | train | steps=100 | 产出 .pth，可就绪推断 |
| S5 | convert | rime7 + yours vocals | 输出时长一致、有声音（RMS > −30dB） |
| S6 | mix | S5 输出 + yours inst | preflight 全过、响度 −13±1.5 |
| S7 | qa | S6 输出 | 报告含 preflight/断音率/音域覆盖 |
| S8 | export | S7 | 桌面出现 mp3 + report.md |

## 4. 断点测试
| 编号 | 用例 | 判定 |
|---|---|---|
| R1 | 在 train 中 kill 引擎，再 run --resume | 从 train 续跑，不重做 separate |
| R2 | 修改 job.json options.steps | 仅影响 train 及之后 stage |

## 5. 插件测试（DSH 内）
| 编号 | 用例 | 判定 |
|---|---|---|
| P1 | cover_setup 缺参数 | ok:false + 明确 message |
| P2 | cover_setup 正常 | 返回 jobId + 预筛预览 |
| P3 | cover_status 轮询 | 状态变化可见 |
| P4 | cover_cancel | 进程被终止，job 标记 cancelled |

## 6. 端到端（最终验收）
一条命令：import 模式 + 已有素材 + steps=300 → 桌面出成品；报告里三个指标齐全。