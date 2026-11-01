# 翻唱工厂 dsh-cover-factory

输入歌手名 / 好球曲 / 目标曲，端到端产出翻唱成品（mp3 + 报告）到桌面。

> **让 AI 全自动安装**：把**仓库地址 + [docs/INSTALL_AI.md](docs/INSTALL_AI.md)** 一起交给有 shell 与网络权限的 AI 即可（纯聊天 AI 无法执行）。
> 人类手动安装见 [docs/INSTALL.md](docs/INSTALL.md)；快速上手见 [docs/QUICKSTART.md](docs/QUICKSTART.md)。

## AI 一键安装（推荐）

在仓库根目录执行（Windows，会自动找 DSH profile）：

    powershell -ExecutionPolicy Bypass -File scripts/install.ps1 -Yes

可选参数：-ProfilePath <profile目录>、-RepoPath <仓库目录>、-SkipDeps（只装插件不装依赖/模型）、-PythonPath <python.exe>。
脚本会 clone/pull 到 <profile>/node_modules/dsh-cover-factory、幂等地把 dsh-cover-factory 加进
dsh.profile.bundles，再调用 engine/setup/bootstrap.py --install --with-models --yes（依赖 + MSST/RVC 权重）。
装完会打印“重启 DSH”。

**验证门**：python engine/run.py doctor 输出 ok=true（全绿）才算成功。详见 [docs/INSTALL_AI.md](docs/INSTALL_AI.md)。

## 安装（本地试用）
1. 把本目录复制/链接到 DSH profile 的 node_modules：
   <你的 DSH profile>/node_modules/dsh-cover-factory
2. 重启 DSH 或在插件页启用。
3. 在对话里说：用 cover_setup 建一个任务：歌手=xxx，目标曲=路径，步数=9000。

## 前置依赖（本机已具备）
- Python 3.10 + torch 2.8 cu128（Applio env / rvc-env）
- MSST 人声分离（MelBand-RoFormer kim）
- 旧版 RVC（2023 代码，D:/Apps/_dl/old_rvc/rvc）
- ffmpeg（<DATA_DIR>/tools/bin 或 imageio 自带）

## 工具
- cover_doctor（依赖体检：green/yellow/red）
- cover_setup / cover_run / cover_status / cover_cancel / cover_list / cover_export / cover_delete

## 依赖
不打包进仓库（torch/CUDA 随显卡变、模型许可各异、全量 5–10G）。安装与体检见 [docs/INSTALL.md](docs/INSTALL.md)：
`cover_doctor({})` → 缺什么按提示装 → `python engine/run.py setup --install` 可自动装一部分。

## 注意
- **抓取模式**默认关闭：开启后下载音频的版权风险由使用者自行承担。
- 训练耗时（RTX 5060 Laptop 实测，约 0.37 秒/步）：5000 步≈30 分钟、9000 步≈55 分钟、18000 步≈1 小时 50 分；没有可用 GPU 会走 CPU，慢约 10 倍；显存 <5G 会先停下来让你选（减步数 / --allow-lowmem）。
- 混音遵循铁律：人声/伴奏都用 stem 原始电平，不拉平；出片前过 preflight。

## 效果可能不佳的情况

见 docs/LIMITATIONS.md（术曲、男女对唱、合唱、混响不一致、哼唱多、和声重、音域宽）。


## 对话成本

插件本机执行不耗 token；对话轮次才消耗。估算见 docs/TOKEN_COST.md（安装 5–15 万，跑一首 2–5 万）。


## 本地音库导入

支持导入 RVC .pth（不需要训练）；Synthesizer V 官方音库不可导入。见 docs/VOICE_BANK_IMPORT.md。
