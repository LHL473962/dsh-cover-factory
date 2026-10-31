# 翻唱工厂 · 本地引擎 engine/

插件只通过 CLI 调用本目录。import 模式不联网；fetch search 模式会联网下载（必须显式 `--copyright-ack`，版权风险自负）。

## 目录

    engine/
      run.py                 CLI（init / run / status / cancel / delete / doctor / setup）
      config_default.json    默认参数（模型路径、混音参数、QA 阈值）
      setup/                 依赖体检/安装
        checks.py            doctor 的 10 项检查（含显卡厂商/架构匹配、Applio/fairseq）
        bootstrap.py         --check / --install，日志写 setup/setup.log
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
    python engine/run.py doctor [--config 覆盖.json]
    python engine/run.py setup --check|--install [--config 覆盖.json]

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

refs：**选填**，支持路径与歌名混填（插件传字符串数组即可）。看起来像路径（含 / \ 或盘符）按路径；
否则当歌名：先在 sources.folder（import）或已下载的 work/raw（auto）里按文件名/标题做不区分大小写的包含匹配，
没匹配上且勾了版权确认（且 mode=auto 或 options.refSearch=true）时会**联网搜这首歌**并下载到 work/raw/refs/。
fetch 后 job.refs 归一成 [{input, kind, matched:'local'|'search'|false, matchedPath, bvid?}]；没命中的写 warning 并忽略，
全部没命中等于没填 refs（按默认权重）。dataset 只训练命中的好球曲（有命中时），权重 ×6 不变。
select/dataset/train 的素材列表通过 stages/base.songs() 读（job.songs，兼容老 job 的 job.refs）。

## 智选：性别 / 对唱判断（options.voice）

- `options.voice`：`auto`（默认）/ `female` / `male`；init 会校验，非法值返回 `BAD_VOICE`。
- 逐帧 F0 分**低区 60–165Hz / 高区 165–500Hz**；两区帧占比都 ≥20% 且高区中位 / 低区中位 > 1.5 → 对唱/合唱（`--allow-duet` 可放行）。
- 单一声部：某一区占比 ≥80% → 不会被误判成对唱。
- `expected`：voice 非 auto 时直接用；auto 时取目标曲人声中位（<200Hz→male，否则 female），目标曲不可用取素材多数。
- 排除规则 = 与 expected 不符：expected=male 时，中位>200Hz 且低区<20%（高区主导）的歌排除；expected=female 反之。**不再无条件排除男声**。
- select 报告每首含 `medianF0 / lowShare / highShare / bimodal / voiceGuess(male|female|mixed) / used / reason`；顶层含 `expectedVoice/expectedSource/bands`。
  `job.json` 记录 `options.voice`、`select_report` 与 `expectedVoice`。
- qa 的音域覆盖按 expected 宽区间算：male 80–400Hz、female 150–600Hz（`qa.voice_ranges` 可改）。

## stage 状态

| stage | 实现 | 真实可跑 | 说明 |
| --- | --- | --- | --- |
| fetch | done | ✅ import + search | import 只复制/转码用户文件夹；search 走 B 站搜索 + yt-dlp（需 --copyright-ack） |
| separate | done | ✅ | MSST kim / bs317 可选 + stem 自检（HPSS） |
| select | done | ✅ | 双峰对唱判断、按 expected 声部排除、现场降权、音色聚类加权 |
| clean | done | ✅ | 高通 45Hz + 掐头去尾静音 |
| dataset | done | ✅ | 训练集目录 + manifest（音域 p5/p95） |
| train | done | ✅ | Applio dsh_rvc2.py prep/train，断点续训 |
| convert | done | ✅ | 旧版 RVC infer_cli（rmvpe） |
| mix | done | ✅ | 移植 mix_unity.sh 的滤波链 + 两遍等响/limiter |
| qa | done | ✅ | preflight / 响度 / 断音率 / 音域覆盖 + EVAL_LEAK（目标曲是否混进训练集） |
| export | done | ✅ | libmp3lame 320k + report.md，复制到桌面 |

fetch 的 search 模式已实现（v0.2，见下方“抓取模式”）；import 模式行为不变。

## 分离方案与 stem 自检

`options.separateModel`：

| 值 | 模型 | 说明 |
| --- | --- | --- |
| `kim`（默认） | mel_band_roformer + MelBandRoformer_kim.ckpt | 人声自然、稳；部分曲目鼓/打击可能少量串到人声 |
| `bs317` | bs_roformer + model_bs_roformer_ep_317_sdr_12.9755 | 鼓/贝斯分离更干净，人声较少混入伴奏；算力略高 |

路径：`paths.msst_config/msst_checkpoint`（kim）、`paths.bs317_config/bs317_checkpoint`（bs317）。
job.json 的 `separate_model` 记录实际模型；换模型会因 separate 参数变化自动重跑。

分离完成后对每条 stem 做 HPSS 自检（60s 中段，librosa 功率谱打击/谐波比 + RMS）：

- `vocals.perc < 0.28` 且 `instrumental.perc > vocals.perc + 0.08` → ok；
- `vocals.perc > 0.30` → warning `VOCAL_STEM_HAS_DRUMS`（人声里有鼓，换声会把乐器唱成人声）；
- `instrumental.perc + 0.03 < vocals.perc`（留 0.03 抖动余量）→ `STEMS_SWAPPED`，**separate 直接 failed**，不再训练；
- vocals RMS 比 instrumental 还响 → warning `VOCAL_STEM_LOUD`（通常人声低 5–15dB）。

结果写 `work/sep/stem_check.json`，job.json 记 `stem_check`（report/verdict/warnings/songs），成品 report.md 显示；
阈值在 `separate.stem_check` 可调。

### 混音极性

`alimiter` 的 attack 是 lookahead：默认 5ms 会让输出整体延迟约 219 samples，导致“成品 vs 伴奏”0 延迟相关假性为负。
引擎默认 `mix.limiter_attack_ms=0.1`（延迟约 3 samples），并在 mix 末尾按 ±`polarity_max_lag` 最佳对齐判断极性；
只有对齐后的相关仍为负才整体乘 -1，metrics/report 记 `polarityFixed`、`polarityCorr0`、`polarityCorr`、`polarityLag`。

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

- **本机绝对路径不要写进 `config_default.json`**：放到 `engine/config.local.json`（已加入 `.gitignore`）。
  加载顺序：`config_default.json` → `config.local.json`（可选，深度合并）→ `--config` / `job.config`（覆盖前者）。
  本机的 yt-dlp / ncmdump / 其它解释器路径都写在 config.local.json，公开默认值保持干净（不含用户名路径）。
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

## 本机音乐客户端（sources.local_clients）

    {"sources": {"local_clients": true, "clients": ["netease", "qqmusic", "kugou", "kuwo"], "keyword": "suis", "days": 0}}

- 扫描（目录存在才扫；递归深度≤3，单目录≤2000 文件，跳过 >200MB）：
  - 网易云：`%USERPROFILE%/Music/CloudMusic`、各盘符 `/CloudMusic`、`/CloudMusic/VipSongsDownload`
  - QQ音乐：`%USERPROFILE%/Music/QQMusic`、各盘符 `/QQMusic`
  - 酷狗：`%USERPROFILE%/Music/KuGou`、各盘符 `/KuGou`
  - 酷我：`%USERPROFILE%/Music/Kuwo`
  - 其它软件：`fetch.local_client_dirs` 里加 `[{"path": "...", "client": "other"}]`
- 格式：`ncm` 自动用 ncmdump 解密（`paths.ncmdump_python` → fallbacks 探测，本机 `D:/Apps/Python311` 可用）；
  `mp3/flac/wav/m4a` 直接转 44.1k stereo；`qmc*/kgm*/mflac/mgg/kwm` **不解密**，只记 detected + warning，不中断。
- 过滤：有 `keyword` 或 `singer` 就只取文件名包含关键词的（不区分大小写）；否则取全部（`limit` 默认 20）。
  `days>0` 只看最近 N 天（0 = 不按时间过滤）。
- 报告：`work/raw/local_client_report.json`（file / client / format / used / reason）。
  `job.songs` 每个文件带 `client`（netease-client / qqmusic-client / …）、`sourcePath`、`format`；`job.local_client_report` 记报告路径。
- **只读取，不修改**：不重命名、不删除、不往客户端目录写任何文件；解密产物写在 job 的 `work/raw/_ncm/`。
- ncmdump 安装：`python -m pip install ncmdump`（解释器配 `paths.ncmdump_python`），或提供 `ncmdump.exe`。

## 多来源组合（folder + local_clients + search）

`sources` 里可以同时给多个块，fetch 按 **folder → local_clients → search** 顺序执行并合并：

    {
      "sources": {
        "folder": "D:/素材/新增",
        "local_clients": true,
        "keyword": "suis",
        "search": "suis",
        "limit": 8
      }
    }

- 每个来源独立执行；单个来源失败只写 warning，其它来源继续；**所有来源都没取到素材才 exit 1**。
- 合并去重：
  - 同一路径（sourcePath 归一化）只留一份；
  - 内容相同（同 size 再比文件 sha1）只留一份；
  - 同一首歌（标题去括号/空白/标点后相同，且时长差 <3s）只留质量最好的一份（flac/wav > m4a > mp3）。
- 报告：`work/raw/fetch_report.json`（mode=combined）含
  `sources`（每来源 status/candidates/error）、`counts`（folder/local_clients/search/total/candidates/dropped）、`songs`、`dropped`、`warnings`；
  原单来源报告另存 `work/raw/source_reports/<source>.json`。
- `job.sources.used = ["folder","local_clients","search"]`，`job.sources.counts` 记录每个来源最终采用数；`job.fetchReport` 记报告路径。
- search 关键词取 `sources.search`，缺省 `job.singer`；插件侧把 singer 解析成 canonical 后再传，引擎按该字符串搜索。
- 组合里 search 未勾版权确认（options.copyrightAck）时只跳过 search 并 warning，不阻塞本地来源。

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
- **AMD Radeon / Intel 核显**：doctor 的 `gpu_vendor` 直接 false；run 启动发现「AMD/Intel 且 CUDA 不可用」→ decision.code=`GPU_UNSUPPORTED`，**退出码 2**，不默默退 CPU。
- **完全无独显**：不推荐（CPU 极慢），仍会 `[hw] WARNING` 后 CPU 回退（不是 A 卡/核显误判）。
- **空闲显存 < 8G**：只警告（可能 OOM），继续跑；建议先关其它 GPU 程序。
- **空闲显存 < 5G**：退出码 2（`LOW_VRAM`），message 给两种选择：① 减少 `options.steps`；② 加 `--allow-lowmem` 显式放行。
- **训练开始前**日志会打醒目横幅：显卡将跑满、勿开其它 GPU 程序、插电源/散热、每 5 epoch 存盘、中断可 `run --resume`。
### 显卡 / torch 兼容矩阵

doctor 先跑 `gpu_vendor`（nvidia-smi → PowerShell CIM 兜底）：NVIDIA ok；AMD/Intel false 并给出明确提示。
`gpu_torch_match` 会对比 `torch.cuda.get_device_capability(0)` 与 `torch.cuda.get_arch_list()`：
capability 的 `sm_XX` 不在 arch_list 里 = 这个 torch 轮子没有该卡的 kernel（会报 no kernel image / CUDA error），必须换 cu 版本。

| 显卡 | compute capability | 推荐 torch | 说明 |
| --- | --- | --- | --- |
| RTX 50 系（Blackwell） | sm_120 | **必须 cu128**（torch ≥ 2.7） | 旧 cu 轮子不含 sm_120 |
| RTX 40 系（Ada） | sm_89 | 官方旧版 cu121 / cu118 可用 | 新 cu128 也可以 |
| RTX 30 系（Ampere） | sm_86 | cu118 最稳 | cu121 亦可 |
| RTX 20 系及更老 | sm_75 / 更早 | 可用但慢 | FP16 支持差，建议降步数 |
| **AMD Radeon（A 卡）** | — | **不支持** | 本管线依赖 NVIDIA CUDA（PyTorch cu128、MSST、RVC）；doctor false + run exit 2（GPU_UNSUPPORTED） |
| **Intel 核显** | — | **不支持** | 同上；没有 NVIDIA 独显时 run 直接 exit 2 |
| 无独显 | — | 不推荐（CPU 回退） | 约 10 倍慢；仍会警告后继续（与 A 卡/核显区分） |

### 旧版 RVC 兼容补丁（torch >= 2.6 / 新 PyAV）

bootstrap 的 `install_old_rvc` clone 官方 tag `2.2.231006` 后，会**自动运行**
`engine/vendor/rvc_patches/apply.py` 覆盖 6 个补丁文件（首次覆盖前保留 `.orig`，幂等）。

为什么需要：

1. **新 PyAV**：`infer/lib/audio.py` 里 `av.open(path, 'rb')` 已失效；改为调用 ffmpeg 解码（`RVC_FFMPEG` 由引擎注入，缺省用 PATH）。
2. **torch >= 2.6**：`torch.load` 默认 `weights_only=True`，RVC 的 rmvpe/模型权重加载失败；`rmvpe.py`、`jit/get_rmvpe.py`、`jit/get_synthesizer.py` 加 `weights_only=False`。
3. **index 为空**：`index_root` 未设置时 `os.walk(None)` 崩溃；`infer/modules/vc/utils.py` 返回空串，`tools/infer_cli.py` 把 `None` index 归一成空串，允许空 index 换声。
4. 同文件的 `av.open` 参数写法也同步到新 PyAV API。

手动应用（或重装后补救）：

    python engine/vendor/rvc_patches/apply.py <rvc_root>

补丁文件与上游 RVC 2.2.231006 同源，按 MIT 分发；`engine/vendor/rvc_patches/LICENSE` 保留上游许可证。

### 自动识别 + 一键装对应 torch

    python engine/run.py setup --install-torch                     # 默认 dry-run：只打印命令，不下载
    python engine/run.py setup --install-torch --yes               # 真装（约 2.5–3G）
    python engine/run.py setup --install-torch --python D:/Apps/rvc-env/python.exe --yes
    python engine/setup/bootstrap.py --install-torch --python <exe> --yes

doctor 的 `gpu_torch_match` 会带 `suggestedTorch`（按 capability 自动选）：

| capability | index | torch 版本 |
| --- | --- | --- |
| sm_120（50 系） | https://download.pytorch.org/whl/cu128 | torch>=2.7 |
| sm_89（40 系） | https://download.pytorch.org/whl/cu121（备选 cu118） | torch 2.1–2.5 |
| sm_86（30 系） | https://download.pytorch.org/whl/cu118 | torch 2.0–2.1 |
| sm_75 及更老 | https://download.pytorch.org/whl/cu118 | torch 2.0–2.1 |
| 无 CUDA | （无） | CPU 版，训练约慢 10 倍 |

默认只打印命令；必须 `--yes`（或 `--install`）才真正执行 `<python> -m pip install torch --index-url <index>`。
**装错 cu 版本会报 no kernel image，GPU 训练直接跑不了**；doctor 也支持 `--python <exe>` 指定检查环境。

只算 train 的耗时（0.37 秒/步，RTX 5060 Laptop 8G，train 显存占用约 7.5G）：

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

## 安装依赖

先体检（输出 `{ok, checks:[{name,ok,found,path,hint}]}`，缺项退出码 1）：

    python engine/run.py doctor

检查 + 尝试自动安装（进度走 stderr，最后 stdout 输出汇总 JSON；每步日志写 `engine/setup/setup.log`）：

    python engine/setup/bootstrap.py --check
    python engine/setup/bootstrap.py --install --with-models --yes

单装某一项：

    python engine/run.py setup --install-applio --python <Python 3.11> --yes   # Applio 训练器（实测 8.1G / 约 11 分钟）
    python engine/run.py setup --install-fairseq --yes                       # 旧版 RVC 换声要的 fairseq 0.12.2
    python engine/run.py setup --install-torch --yes                         # 按显卡选 cu 版本

Applio 安装会先按显卡装 torch/torchaudio，再把 requirements 里的 torch 行过滤后 pip install -r（其余依赖保持上游版本）。

| 依赖 | 能不能自动装 | 说明 |
| --- | --- | --- |
| 两个 Python 环境 | 手动安装解释器 | **RVC/换声 = Python 3.10**；**Applio/训练 = Python 3.11+**（实测 3.11.9）。安装器自动按 --python → paths.applio_python → py -3.11/-3.12 → python 选 3.11+；只有 3.10 时立即失败并给下载链接 |
| torch + CUDA | 自动（--install 链 / setup --install-torch） | 按显卡 capability 自动选 cu128/cu121/cu118；--yes 真装，约 2.5–3G |
| ffmpeg | 自动（pip imageio-ffmpeg）或手动 | bootstrap 装完会给出可执行文件路径，把它写进 paths.ffmpeg；失败则从 https://www.gyan.dev/ffmpeg/builds/ 下载 |
| yt-dlp | 自动（pip，在 paths.ytdlp_python 环境里） | 只有 fetch search 抓取模式需要；import 模式不联网 |
| MSST 代码 + MelBand kim 权重 | ✅（--with-models） | clone 到 paths.msst_root；kim 871MB、hubert 181MB、rmvpe 173MB，已存在跳过、断点续传 |
| Applio | ✅ setup --install-applio --yes | clone 固定 tag 3.7.1（git 失败时回退 codeload zip）→ venv → torch（按显卡）→ requirements → import core 验证；需要 Python 3.11+；**实测 8.1G / 676 秒**（网络快），建议预留 10G |
| fairseq 0.12.2（旧版 RVC） | ✅ setup --install-fairseq --yes | 预编译 zip（MIT，11.4MB，sha256 校验）解压到 paths.rvc_python 的 site-packages，再装 omegaconf/hydra/sacrebleu 等 |
| 旧版 RVC 2.2.231006 | 自动 clone + 打补丁 + 拷本机权重 | clone 后自动应用 `engine/vendor/rvc_patches`（torch>=2.6/PyAV/index 空兼容，备份 .orig）；`hubert_base.pt`/`rmvpe.pt` 从 `paths.rvc_models_src` 递归复制 |
| 声库权重 / 分离权重 / 预训练底模 | 一律不自动下载 | 许可各异、体积大 |

**为什么不能打包整个环境**：torch/CUDA 必须按显卡与驱动选版本；MSST/RVC/Applio 的模型许可各不相同；整套依赖 5–10G。
所以引擎做“体检 + pip/git 小件自动装 + 大权重按需下载”，不把环境塞进仓库。

### 已知问题 / 实测失败点

- **两个环境别混用**：RVC/换声固定 Python 3.10（本机 rvc-env）；Applio/训练固定 Python 3.11+（本机 D:/Apps/applio-env = 3.11.9）。安装器会自动选 3.11+；若只有 3.10 会在 clone 前直接失败并提示下载 https://www.python.org/downloads/release/python-3119/ 。
- **github.com:443 间歇被墙**：Applio 的 git clone 失败时会自动回退到 codeload.github.com 的固定 tag zip；如果两者都不通，只能配代理/镜像后重试。
- **实测 3.10（显式 --python rvc-env）**：安装器在 **0 秒 / 0 文件**时直接 failed，不 clone、不建 venv、不跑 requirements；step 带 hint 和下载 https://www.python.org/downloads/release/python-3119/ 。
- **实测 3.11（临时 D:/VoiceData/exp/applio_test311，base=D:/Apps/applio-env 的 3.11.9）**：成功。git clone 被墙 → codeload 回退；torch 2.11.0+cu128 约 5 分钟；requirements 34 条约 5 分钟；`import core` / transformers 5.13.1 / faiss 1.14.3 全过。**总耗时 676 秒，目录 8.1G**（比预估 3–5G 大，建议预留 10G）。

`paths` 覆盖（改 `config_default.json`，或用 `--config 覆盖.json`）：

    python / applio_python / rvc_python / msst_python / ytdlp_python
    ffmpeg / ffmpeg_fallbacks
    msst_root / msst_inference / msst_config / msst_checkpoint
    applio_root
    rvc_root / rvc_hubert / rvc_rmvpe / rvc_models_src
    bankDir / export_dir / room_ir / nvidia_smi

默认值是本机路径（`D:/Apps/...`），换机器只需要改这些；`doctor` 会告诉你哪项没对上。

## 最小验证

    python engine/run.py init --config engine/examples/job_config.sample.json --workdir engine/examples/sample-work
    python engine/run.py status --job engine/examples/sample-work/job.json --text

跑通冒烟（不需要 GPU 训练）：`run --to fetch`，把 separate 的 stem 手动放进 work/stems，
再从 select 开始。完整 0→9 仍需 GPU（separate 与 train）。
