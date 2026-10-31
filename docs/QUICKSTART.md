# 翻唱工厂 · 快速上手（v0.1）

> 一句话：给歌手名、好球曲、目标曲，本地训练 + 换声 + 混音 + 质检，成品 mp3 和报告落到桌面。v0.1 不承诺完美，只保证流程能跑、能续、指标可查。

## 最短路径

1. **装插件**：把整个插件目录放到你自己环境的 `<DSH profile>/node_modules/dsh-cover-factory`（本机示例：`C:/Users/<你>/.dsh/profiles/desktop/node_modules/dsh-cover-factory`），重启 DSH 或在插件页启用。
2. **准备素材**：新建一个文件夹，好球曲和目标曲都放进去，例如 `<你的素材目录>`。
3. **建 job**：

~~~text
cover_setup({ singer: "张三", folder: "<你的素材目录>", target: "<你的素材目录>/目标曲.wav", steps: 9000 })
~~~

4. **开跑**：`cover_run({ jobId: "<上一步返回的 jobId>" })`，之后隔一阵 `cover_status({ jobId: "..." })` 看进度。
5. **收片**：`cover_export({ jobId: "..." })` → 桌面/翻唱工厂/张三-目标曲.mp3 和 张三-目标曲-report.md。

## 1. 装插件（只做一次）

- 插件目录放到 DSH profile 的 node_modules，路径以你机器的 profile 为准；放对后重启 DSH，插件页里启用 cover-factory。
- 工具列表里出现 **cover_setup / cover_run / cover_status / cover_cancel / cover_list / cover_export**，就算装好。
- 引擎 `engine/run.py` 和 Python 环境必须在位。缺了会返回 **ENGINE_NOT_READY**，这是引擎没就位，不是插件坏了。
- 设置文件：`~/.dsh/cover-factory-settings.json`。键：engineDir / python / outputDir / defaultSteps / allowDuet / autoDownload / workRoot / ffmpeg。改完**下次工具调用生效**，不用重启 DSH。

## 2. 准备素材

| 要给的东西 | 说明 |
| --- | --- |
| 歌手名 | 必填。决定声库名和成品文件名，例：张三。**支持别名与错拼**：suis / 夜鹿 / ヨルシカ / Yorushika / すい 都归一到 `suis`（返回 `resolvedSinger`，见 FAQ Q28） |
| 素材来源（多选） | 至少选 1 个，可同时用：**本地目录**（要给 folder，只读）/ **本机客户端**（扫描已下载文件）/ **B站搜索**（要 `copyrightAck`） |
| 好球曲（refs） | **选填**但强烈建议，2–8 首同歌手、同年代、干净独唱。每行可填**歌名**或**完整路径**（可混填）；歌名先在你给的素材目录里找，开了「联网搜索」并确认版权才去 B 站搜，仍找不到就忽略（不中断） |
| 目标曲 | 要翻唱的那首，给完整文件路径。没给只能跑到训练，出不了成品 |
| 本机客户端选项 | 选了「本机客户端」才用：`clients`（netease / qqmusic / kugou / kuwo，默认全部，`"qq"` 也认）、`keyword`（默认=归一后的歌手名） |
| 目标歌手声部 | `auto`（默认，按目标曲人声判断）/ `female` / `male`。**男歌手请选 male**，或先用 auto 并确认判断正确 |
| 分离方案 | `kim`（默认，稳）/ `bs317`（BS-RoFormer ep317，串音更低）。**bs317 目前实验结论：无可测的听感提升，只有串音指标更低**，日常保持 kim |
| 步数 | 默认 9000；见 §3 |
**素材来源现在可以组合**（新参数 `sources`，至少 1 个）：

```
sources: ["folder"]                 # 只用本地目录（= 旧 mode:"import"）
sources: ["local"]                  # 只用本机客户端（= 旧 mode:"local"）
sources: ["folder", "local"]        # 目录 + 本机客户端，一起当候选池
sources: ["folder", "auto"]         # 目录 + B站搜索（要 copyrightAck）
```

- `mode` 旧参数仍然兼容（等价于单一来源），但**优先用 `sources`**；一个都没选返回 `BAD_SOURCES`。
- 选了 **B站搜索** 必须带 `copyrightAck: true`，否则只返回 `needsConfirm`（不建 job、不联网）。
- 落盘形状：单一来源沿用旧扁平形状；多来源是**扁平组合** `{folder, local_clients: true, clients, keyword, search, limit, author}` + `kinds` 标记（引擎按 folder → local_clients → search 顺序合并去重，见 `engine/README.md`「多来源组合」）。

- 支持格式：wav / flac / mp3 / m4a / aac / ogg / opus / wma / aif / aiff / ape / wv；文件夹可以含子目录，会递归找。
- 单曲 **≥60 秒**（不足会在智选阶段被排除）；目标曲 **≥30 秒**（质检硬门槛）。
- 目标曲的分离质量几乎决定成品：优先录音室版 / 官方伴奏版，别用现场大混响、对唱、带主持人口白的版本。

## 3. 步数怎么选

| 步数 | 训练耗时（RTX 5060 Laptop 实测，约 0.37 秒/步） | 显存 | 适合 |
| --- | --- | --- | --- |
| 5000 | 约 30 分钟 | 7.5G | 先验证素材和音色方向 |
| 9000 | 约 55 分钟 | 7.5G | 默认，正式出片 |
| 18000 | 约 1 小时 50 分 | 7.5G | 愿意等，补细节 |

- 上表是**墙钟实测**：rime7 145e（约 17800 步）111 分钟、suis5 60e（约 16000 步）95 分钟 → 约 0.37 秒/步。
- 步数区间 100–200000；其他值按 0.37 秒/步线性估（3000 步约 18 分钟）。
- 步数不等于"更像"：音色主要看素材。硬件硬闸只有一条：**空闲显存 <5G 且没放行**（`LOW_VRAM`，退出码 2）——二选一：减步数，或引擎加 `--allow-lowmem`；5–8G 只警告，没有可用 GPU 走 CPU（约 10 倍慢，9000 步约 9 小时）。
- 上面的时间**只算训练**；分离、特征预处理、换声、混音、质检还要额外时间，素材越多越久。

## 4. 硬件与风险（训练期间必看）

- 训练时**显卡会跑满（接近 100%）**，显存占用约 **7.5–8G**；笔记本会明显发热、风扇全速，可能降频。
- 期间**不要开游戏 / 剪辑 / 渲染**等吃显卡的程序，容易 OOM 或崩溃。
- 请**插着电源**、保持散热；长时间满载对老笔记本有风险（可能过热关机）。
- **不要中途关机**：引擎每 5 个 epoch 存一次盘，中断后用 `cover_run({ jobId, resume: true })` 续训（同一个 job 重新运行选「续跑」）。
- 磁盘需要预留 **≥ 10G**（素材 + 切片 + 特征 + 模型）。
- 显存 **<5G** 会先停下来让你选：**减步数** 或 加 `--allow-lowmem`（唯一的硬件硬闸）；**5–8G 只警告**，继续跑。
- **没有可用 GPU** 会走 CPU，**慢约 10 倍**（9000 步约 9 小时）。
- **RTX 50 系必须用 torch cu128；30/40 系用 cu118/cu121**；插件会按你的显卡自动推荐（设置 → 翻唱工厂 → 依赖体检展开，可一键复制安装命令）。对照表见 [INSTALL.md](INSTALL.md#显卡与-torch-版本怎么选)。

界面上的「训练期间会发生什么（风险提示）」折叠块写的是同一份内容（设置 → 翻唱工厂 → 新建任务）。

## 5. 常见报错

| 返回 | 什么意思 | 怎么办 |
| --- | --- | --- |
| MISSING_SINGER | 没给歌手名 | 补 singer |
| MISSING_FOLDER / FOLDER_NOT_FOUND | import 模式没给目录，或路径不是文件夹 | 给一个存在的素材文件夹 |
| TARGET_NOT_FOUND | 目标曲路径不对 | 给音频文件的完整路径 |
| BAD_STEPS | 步数不在 100–200000 | 用 5000 / 9000 / 18000 |
| ENGINE_NOT_READY | 找不到 engine/run.py 或 Python 不可用 | 把引擎装好；插件不用改 |
| DOWNLOAD_NOT_ENABLED | 抓取模式没显式开启 | 改用 import；要开见 FAQ Q6，风险自负 |
| JOB_RUNNING | 这个 job 已经在跑 | 别重复 cover_run，先 cover_status |
| NO_ARTIFACT | mix/export 没产出 | cover_status 看卡在哪个阶段 |
| FFMPEG_MISSING | 没找到 ffmpeg | 先交付 wav；装 ffmpeg 或设 settings.ffmpeg 后重导 |

## 6. 出片后先看三行

报告 `-report.md` 里的硬指标：

- **响度**：−13 ± 1.5 LUFS
- **断音率**：< 12 次/分
- **音域覆盖**：≥ 70%（目标曲帧落在训练音域 ±6 半音内）

不合格就按报告建议处理：加步数、换更干净的素材、整首移调（人声+伴奏一起）。**不要只移人声**，会跑调。
