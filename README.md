# 翻唱工厂 dsh-cover-factory

输入歌手名 / 好球曲 / 目标曲，端到端产出翻唱成品（mp3 + 报告）到桌面。

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
- cover_setup / cover_run / cover_status / cover_cancel / cover_list / cover_export

## 注意
- **抓取模式**默认关闭：开启后下载音频的版权风险由使用者自行承担。
- 训练耗时（RTX 5060 Laptop 实测，约 0.37 秒/步）：5000 步≈30 分钟、9000 步≈55 分钟、18000 步≈1 小时 50 分；没有可用 GPU 会走 CPU，慢约 10 倍；显存 <5G 会先停下来让你选（减步数 / --allow-lowmem）。
- 混音遵循铁律：人声/伴奏都用 stem 原始电平，不拉平；出片前过 preflight。