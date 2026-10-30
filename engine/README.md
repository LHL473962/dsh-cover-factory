# 翻唱工厂 · 本地引擎 engine/

插件只通过 CLI 调用本目录。import 模式不联网；fetch search 模式会联网下载（必须显式 `--copyright-ack`，版权风险自负）。

## 目录

    engine/
      run.py                 CLI（init / run / status / cancel / delete）
      config_default.json    默认参数（模型路径、混音参数、QA 阈值）
      stages/                10 个 stage，每个暴露 run(job, cfg) -> dict
        base.py              日志 / subprocess / 幂等签名 / 路径约定
        fetch.py separate.py select.py clean.py dataset.py
        train.py convert.py mix.py qa.py export.py
      assets/                从现有脚本移植的命令与 DSP
        commands.py          MSST / Applio / old RVC / ffmpeg 混音命令
        audio.py analysis.py ffmpeg 探测、F0、断音率、立体声相关
        mix_unity.sh         混音链 shell 版（手工复现/对照）
        README.md            每条命令的移植来源
      examples/              示例 job 配置与 init 产物

## CLI

    python engine/run.py init   --config job_config.json --workdir <dir> [--force|--reset]
    python engine/run.py run    --job <job.json> [--from stage] [--to stage] [--resume] [--allow-duet] [--dry-run] [--force] [--config 引擎配置覆盖.json]
    python engine/run.py status --job <job.json> [--text]
    python engine/run.py cancel --job <job.json>
    python engine/run.py delete --job <job.json> [--purge]

退出码：0 成功；2 需要用户决策（硬件/版权确认，决策写在 job.json 的 decision 字段）；1 失败。
status 默认输出 job JSON（插件直接解析），--text 给人看。

## 目录约定（workdir = job.json 所在目录）

    <workdir>/job.json                唯一状态源，tmp + os.replace 原子写
    <workdir>/logs/<stage>.log        每个 stage 一份日志（外部命令完整输出）
    <workdir>/work/raw/*.wav          fetch 产物
    <workdir>/work/stems/<歌名>/      separate 产物：vocals.wav / instrumental.wav
    <workdir>/work/select/report.json 智选报告
    <workdir>/work/clean/*.wav        clean 产物
    <workdir>/work/dataset/<jobId>/   dataset 产物 + manifest.json（含训练音域）
    <workdir>/work/model/<modelId>/   train 产物：model.pth / model.index / model.json
    <workdir>/work/convert/vocal.wav  convert 产物
    <workdir>/work/mix/out.wav        mix 产物（等响 -14 LUFS、limiter -1.5dB level=false）
                                      编码后 mp3 峰值 > -1.0 dBFS 会回退 1dB 重编（最多 2 次）
    <workdir>/work/qa/report.json     qa 报告（+ report.md）
    <workdir>/work/export/*.mp3       export 产物（同时复制到桌面/翻唱工厂）

## job.json

version / id / createdAt / singer / mode / sources / refs / target / target_meta /
options / stages / result / cancelled（决策时另有 decision）。

每个 stage 记录：status（pending|running|done|failed|skipped|cancelled）、startedAt、
finishedAt、artifacts、metrics、error、reason、log、inputHash。

refs：兼容两种写法 —— 插件传的**字符串路径数组**（如 ["D:/x/a.wav", ...]），以及 fetch 跑完后归一化的
对象数组（{id,name,role,kind,path,source}）。select/dataset/train 都通过 stages/base.songs() 读，两种都认。

## stage 状态

| stage | 实现 | 真实可跑 | 说明 |
| --- | --- | --- | --- |
| fetch | done | ✅ import + search | import 只复制/转码用户文件夹；search 走 B 站搜索 + yt-dlp（需 --copyright-ack） |
| separate | done | ✅ | MSST mel_band_roformer + kim 权重 |
| select | done | ✅ | 对唱排除、现场降权、音色聚类加权 |
| clean | done | ✅ | 高通 45Hz + 掐头去尾静音 |
| dataset | done | ✅ | 训练集目录 + manifest（音域 p5/p95） |
| train | done | ✅ | Applio dsh_rvc2.py prep/train，断点续训 |
| convert | done | ✅ | 旧版 RVC infer_cli（rmvpe） |
| mix | done | ✅ | 移植 mix_unity.sh 的滤波链 + 两遍等响/limiter |
| qa | done | ✅ | preflight / 响度 / 断音率 / 音域覆盖 |
| export | done | ✅ | libmp3lame 320k + report.md，复制到桌面 |

fetch 的 search 模式已实现（v0.2，见下方“抓取模式”）；import 模式行为不变。

## 幂等与断点

- 每个 stage 计算输入签名（文件 size+mtime_ns + 参数 JSON）；产物齐全、状态 done/skipped、
  签名一致 -> 返回 skipped，不重做。
- train 的 Applio 断点（G_*/D_*.pth）放在 `work/model/applio_logs/<modelId>/`，重跑会自动续训；
  已导出权重（`<model>_<epoch>e_<step>s.pth` 或 `*_Ne_*.pth`）epoch 达标时直接复用。
- `run --resume`：清除 cancelled，允许重试 failed 阶段；已 done 的照常按幂等跳过。
- `--from/--to` 只限定本次跑哪些阶段，前面的阶段状态保留；`--force` 忽略签名强制重跑选中阶段。
- 成片按 mp3 实测峰值验收：mix 先编一条校验 mp3，export 再对交付 mp3 复核；
  峰值 > -1.0 dBFS 就整体回退 1dB 重编（最多 2 次），`job.json` 的 `result.finalPeak` 记录最终值。

## 默认外部路径（config_default.json 可改）

- MSST：`D:/Apps/MSST/01_人声分离_MSST-GUI`（env/python.exe + kim ckpt）
- Applio：`D:/Apps/_dl/applio/Applio-main`（applio-env/Scripts/python.exe）
- 旧版 RVC：`D:/Apps/_dl/old_rvc/rvc`（rvc-env/python.exe；模型目录走 weight_root 环境变量）
- ffmpeg：imageio-ffmpeg 自带（另有 fallback 列表）
- IR：`<DATA_DIR>/out/tuning/room_ir.wav`

## 抓取模式（fetch search，v0.2）

    python engine/run.py run --job <job.json> --to fetch --copyright-ack

job 配置（init 的 sources 字段）：

    {"sources": {"search": "歌手名", "limit": 8, "author": "仅该作者"}}

- 搜索走 B 站公开搜索 API（匿名，带 UA / Referer + 首页 cookie），超时 20s、重试 2 次。
- 过滤：时长 60–480s；标题含 伴奏/off vocal/inst/カラオケ 等排除；给了 author 时仅保留作者含该串的结果（默认取 singer）。
- 下载用 yt-dlp（`paths.ytdlp_python` 可配）`-f ba`，单条失败跳过并记录，全部失败则 stage failed。
- 产物：`work/raw/ref_XX_<bvid>_<标题>.wav` + `work/raw/fetch_report.json`（bvid/标题/作者/时长/是否采用/原因/本地路径）。
- **版权**：抓取会联网下载第三方音频，版权风险由使用者承担；未加 `--copyright-ack`（或 `options.copyrightAck!=true`）时引擎直接退出码 2。
- 网络异常不中断：搜索失败写报告 + failed 原因；下载失败逐条记录后继续。

## 声库缓存 / 复用

- 训练前算 fingerprint = sha1(素材文件 size+mtime + steps + 参考曲名集合)，存在 `work/model/<modelId>/model.json`。
- 训练完成后自动登记到 `paths.bankDir`（默认 `<DATA_DIR>/house/banks/`）：`<singer>/<fingerprint>.pth` + `.index` + `.meta.json`。
- 命中顺序：`options.reuseModel` / `config.reuseModel`（显式）→ job 内 fingerprint 匹配 → 声库 fingerprint 匹配 → 才真正训练。
- 显式复用：`run --reuse-bank <歌手名>` 取该歌手声库里最新的模型（也可在 job/config 里写 `reuseModel`/`reuseIndex`）。
- 复用时不复制大文件：`model.json` 里写 `modelPth`/`indexPath` 指针，convert 直接按绝对路径加载（weight_root=模型所在目录）。
- 想强制重训：删掉 `work/model/<modelId>/model.json` 或换 steps/素材（fingerprint 会变）。

## 硬件与风险

- **run 启动时体检一次**：nvidia-smi 读 GPU 型号 / 显存总量 / 当前空闲显存（没有 nvidia-smi 时用 torch 兜底），
  结果写 `job.json` 的 `hw` 字段和 `logs/hw.log`；没有 NVIDIA GPU 标 `cpuFallback: true`。
- **无 GPU**：不失败，`[hw] WARNING` 提示 CPU 回退约慢 10 倍，继续跑。
- **空闲显存 < 8G**：只警告（可能 OOM），继续跑；建议先关其它 GPU 程序。
- **空闲显存 < 5G**：退出码 2（`LOW_VRAM`），message 给两种选择：① 减少 `options.steps`；② 加 `--allow-lowmem` 显式放行。
- **训练开始前**日志会打醒目横幅：显卡将跑满、勿开其它 GPU 程序、插电源/散热、每 5 epoch 存盘、中断可 `run --resume`。
- 只算 train 的耗时（0.37 秒/步，RTX 5060 Laptop 8G，train 显存占用约 7.5G）：

| 步数 | 预计耗时（只算 train 阶段） |
| --- | --- |
| 5000 | 约 30 分钟 |
| 9000 | 约 55 分钟 |
| 18000 | 约 1 小时 50 分 |

墙钟实测依据：rime7 145 epoch ≈ 17800 步 = 111 分钟；suis5 60 epoch ≈ 16000 步 = 95 分钟 → 约 **0.37 秒/步**
（旧文档按 1 秒/步 估成“9000 步≈2.7 小时、18000 步≈5 小时”，是错的）。

引擎估算：`train.sec_per_step_est = 0.37`（config_default.json），`ETA = steps × 0.37 秒`；
`status` 输出 `trainEta`，train 日志打印预计耗时，训练完成后 metrics 记 `etaSec` / `secPerStepActual`（实测秒/步）。
只覆盖 train（separate/convert/混音另算）；声库 fingerprint 命中时整段训练被跳过。

引擎不从权重文件名的 trainer 秒数推 ETA（文件名只解析 epoch/step 用于断点复用）。
插件 UI/文档里的旧数字（1.5h / 2.7h / 5h）在 `engine/` 之外，需另行同步。

## 删除 job（delete）

    python engine/run.py delete --job <workdir>/job.json            # 软删：整个 workdir 移到 <workRoot>/_deleted/
    python engine/run.py delete --job <workdir>/job.json --purge    # 硬删：物理删除整个 workdir

- workRoot = workdir 的上一级目录；软删目标为 `<workRoot>/_deleted/<basename>-<YYYYmmdd-HHMMSS>/`（重名自动加 -2/-3）。
- 删除前检查是否有对应 python 进程在跑：有 -> 退出码 1，提示先 `cancel` 或等它结束（run 阶段会写 `<workdir>/engine.pid`，另用进程命令行匹配 job.json 绝对路径）。
- 输出 JSON：`{ok, job, movedTo}` 或 `{ok, job, deleted}`；job.json 不存在 -> 退出码 1。
- **声库不动**：bankDir 里的 `<singer>/<fingerprint>.pth/.index/.meta.json` 不在 workdir 内，删除 job 不会碰模型；下次同素材可直接复用。
- 桌面成品（outputDir 里的 mp3/report）也不在 workdir 内，不会被删。

## 最小验证

    python engine/run.py init --config engine/examples/job_config.sample.json --workdir engine/examples/sample-work
    python engine/run.py status --job engine/examples/sample-work/job.json --text

跑通冒烟（不需要 GPU 训练）：`run --to fetch`，把 separate 的 stem 手动放进 work/stems，
再从 select 开始。完整 0→9 仍需 GPU（separate 与 train）。
