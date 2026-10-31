# 翻唱工厂 · 当前状态（2026-10-30 20:05）

## 一句话
本地引擎 0→9 全链路真跑通；DSH 插件 6 个工具全部注册、测试全绿；已装进 desktop profile（重启 DSH 后生效）。

## 已验证（真 GPU，非 mock）
| 项 | 结果 |
|---|---|
| engine 0→9 | fetch→separate→select→clean→dataset→train(100步)→convert→mix→qa→export 全 done |
| 智选排除 | 对唱样本（低/高区双峰）被排除；短素材 <60s 被排除；男声素材在 voice=male 下保留 |
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
| 抓取模式 | ✅ 已实现（B站搜索 + yt-dlp + copyrightAck）；本行是旧记录，保留仅作历史 |
| 声库缓存/复用 | ✅ 已实现（work fingerprint + bankDir 命中，见 engine/stages/train.py find_bank_model） |
| 好球曲自动挑选 | select 已有聚类加权，尚未做成“从歌手全部歌里自动选 3 首” |
| 和声/混响分离 | MSST 有 dereverb/karaoke 模型，未接入 clean |
| 批量 | 一次多目标曲 |
| 报告模板 | 引擎 report 已含关键项，与 preset/report_template.md 的 8 节尚未完全对齐 |
| 设置页 UI | ✅ 已有图形界面（client.js：体检/新建/任务表/删除/续跑）；本行是旧记录 |

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
## 朋友反馈（2026-10-30 21:10，待排期）
1. **UI/操作懒人优化**：有问题=有需要，值得做（具体点待细化为“少点几下、少填几项”）。
2. **音色相似度百分比**：给一个 0–100 的“像不像”分数。可行路线：用换声结果与参考曲做音色嵌入（如 speaker embedding / 频谱质心+F0 分布）算余弦相似度，映射成百分比；注意口径要固定并写进报告，避免误导。
3. **音源新增“链接导入”方式（不是替换）**：现有三条来源都保留——① 本地目录导入 ② 本机客户端已下载扫描 ③ B站关键词自动搜索；**再加一条 ④ 粘贴任意链接（URL）批量下载**。技术上最容易实现，优先做“粘贴 URL → yt-dlp → 进管线”，内嵌浏览窗口可选。
4. **测试后做一条视频**：《一个插件，让你的大肥鱼自动做任意角色 AI 翻唱》——等第一波朋友测试跑通再拍（演示：输入歌手/好球曲/目标曲 → 出片）。

## 2026-10-31 12:45 更新
- **分离方案二选一**：`options.separateModel` = `kim`（默认）/ `bs317`（BS-RoFormer ep317，串音更低）；实测结论：**无可测听感提升**，日常保持 kim。
- **新增 stem 检测**：分离后自动用 HPSS 打击/谐波比 + RMS 检查两条 stem；
  - 人声轨打击比 >0.30 → `VOCAL_STEM_HAS_DRUMS`（人声里混进鼓，换声会把乐器唱成人声）
  - 强证据才判反装：`instrumental.perc+0.03 < vocals.perc` **且** `vocals.perc>0.30` **且** `vocals.rms > instrumental.rms-2`；否则只给 `STEM_CHECK_UNCERTAIN` warning（避免把安静伴奏误判成接反——chinokate 实测 0.165/0.041 属正常安静伴奏，已从 fail 降级为 warn）。
- **混音相位修复**：根因是 `alimiter` 默认 5ms attack 的 lookahead（约 219 采样延迟），不是反相；已把 attack 降到 0.1ms，并加「±300 采样最佳对齐后再判极性」。
- 测试：smoke 110/110、e2e 24/24、py_compile 全过、doctor 8/8。
## 2026-10-31 17:00 版本说明

- package.json version 从 0.1.0 起就没再动过，导致别人从 profile 里看到的版本号一直是 0.1.0；
  实际功能已到 v0.1.11（对应 git tag/提交）。现在已把 version 更新为 0.1.11。
- 已实现但被旧 STATUS 写成“未完成”的：抓取模式、声库缓存、设置页 UI —— 已在上面改正。
