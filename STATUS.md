# 翻唱工厂 · 当前状态（2026-10-30 20:05）

## 一句话
本地引擎 0→9 全链路真跑通；DSH 插件 6 个工具全部注册、测试全绿；已装进 desktop profile（重启 DSH 后生效）。

## 已验证（真 GPU，非 mock）
| 项 | 结果 |
|---|---|
| engine 0→9 | fetch→separate→select→clean→dataset→train(100步)→convert→mix→qa→export 全 done |
| 智选排除 | 对唱样本 male 24% > 15% 被排除；短素材 <60s 被排除 |
| 训练 | Applio 真训练 100 步，产出 model.pth + index，幂等复跑 skipped |
| 换声 | 旧版 RVC + rmvpe，65s 素材 15s 完成 |
| 混音 | 0dB/0dB 原比例 + IR 混响 + 加宽 + glue + limiter |
| 质检 | lufs −14.0 / peak −1.4 / 断音 3.2 次/分 / 音域覆盖 93.4% → qaOk=true |
| 导出 | 桌面目录出 mp3（320k）+ report.md，报告含总耗时与四项关键指标 |
| 插件测试 | smoke 39/39、e2e 11/11 |

## 关键修复（本轮）
1. `alimiter` 默认 `level=true` 会把输出拉回 0dBFS，导致“限幅 −1.5 却峰值 0.1”——已关 auto-level。
2. 增加 mp3 编码后实测与回退重编（最多 2 次），最终 peak ≤ −1.0 dBFS。
3. 报告阶段表 export 显示 pending → 写报告前先置 done；补总耗时。
4. 插件 cover_setup 新增 shift（−5..+5，整首移调）；jobId 用目录名，避免中文名被引擎清洗后找不到。

## 安装状态
- profile package.json 已加 `dsh-cover-factory: link:...`，已 `pnpm install`（node_modules 软链存在）。
- **需要重启 DSH** 才能加载插件并出现 cover_* 工具。

## 尚未完成（v0.2 计划）
| 项 | 说明 |
|---|---|
| 抓取模式 | 引擎按设计只写 FETCH_TODO，不联网；需接 B站/网易云下载器 + copyrightAck 流程 |
| 声库缓存/复用 | 同一歌手第二次不必重训；目前每次都训 |
| 好球曲自动挑选 | select 已有聚类加权，尚未做成“从歌手全部歌里自动选 3 首” |
| 和声/混响分离 | MSST 有 dereverb/karaoke 模型，未接入 clean |
| 批量 | 一次多目标曲 |
| 报告模板 | 引擎 report 已含关键项，与 preset/report_template.md 的 8 节尚未完全对齐 |
| 设置页 UI | 只有 JSON API，无图形界面 |

## 复现命令
- 引擎单测：`D:/Apps/rvc-env/python.exe engine/run.py init --config engine/examples/job_config.sample.json --workdir engine/examples/sample-work`
- 全链路：`python engine/run.py run --job <job.json> --force`
- 插件测试：`node tests/smoke.mjs && node tests/e2e.mjs`
- 本轮证据：`<DATA_DIR>/out/covertest/job_full/`（含 qa/report.json 与成品 mp3）
## 2026-10-30 20:30 更新
- 新增 `cover_delete`（软删到 `_deleted/`，purge 物理删；运行中先取消）+ UI 删除按钮。
- 新建任务卡片新增「好球曲」多行输入（refs ≤20，路径不存在报 REF_NOT_FOUND，提示不泄漏绝对路径）。
- 训练耗时口径修正：**0.37 秒/步**（实测 rime7 17800 步 111 分钟、suis5 16000 步 95 分钟）→ 5000≈30min / 9000≈55min / 18000≈1h50m。
- 新增硬件体检：`logs/hw.log` + `job.hw`；无 GPU 警告 CPU 回退；空闲 <8G 警告；**<5G 唯一硬闸**（exit 2，需减步数或 `--allow-lowmem`）。
- 训练日志加醒目警告横幅；UI 加「训练期间会发生什么（风险提示）」折叠块；任务行新增「续跑」按钮。
- 测试：smoke 73/73、e2e 12/12。
## 2026-10-30 21:00 更新
- 新增 `cover_doctor`（8 项体检：python/torch_cuda/gpu_torch_match/ffmpeg/msst/applio/old_rvc/ytdlp），本机 8/8 全绿。
- 新增 `engine/setup/bootstrap.py`（`run.py setup --install`）：yt-dlp/ffmpeg/旧版 RVC 可自动，MSST 权重与 torch 必须人工。
- 新增 docs/INSTALL.md（三条路径 + 显卡/torch 矩阵 + 为什么不打包）。
- **显卡矩阵**：50系必须 cu128（旧 torch 无 sm_120）；40系 cu121/cu118 可用；30系 cu118 最稳；20系及更老可用但慢；无独显 CPU 慢 10 倍。
- doctor 的 `gpu_torch_match` 会比对 capability 与 torch arch_list，不匹配直接红并给 cu 版本建议。
- 测试：smoke 83/83、e2e 16/16、doctor 8/8。