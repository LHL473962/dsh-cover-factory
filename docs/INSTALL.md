# 翻唱工厂 · 依赖安装与体检

插件只是壳：真正干活的是 `engine/`（Python）和它调用的几套外部环境。
**依赖不进插件仓库**（原因见最后一节），所以安装分三条路径，装完统一用 `cover_doctor` 验证。

> **两个 Python 环境，别混用**：
> - **RVC / 换声 = Python 3.10**（`paths.rvc_python`，本机 rvc-env）；
> - **Applio / 训练 = Python 3.11+**（`paths.applio_python`，本机 D:/Apps/applio-env = 3.11.9）。
> `setup --install-applio` 会自动按 “--python → paths.applio_python → py -3.11/-3.12 → python” 选 3.11+；只有 3.10 时直接失败并提示下载 https://www.python.org/downloads/release/python-3119/ 。

## 0. 先体检，再安装

对话里：

    cover_doctor({})

或 设置 → 翻唱工厂 → 顶部「依赖体检：…」点开 → 「重新体检」。

返回：

| 字段 | 含义 |
|---|---|
| `ok` | 体检**本身**跑成了没有（false = 引擎没起、没 doctor 子命令、输出不可解析；不代表依赖缺） |
| `health` | `green` 全齐 / `yellow` 只缺可选 / `red` 缺必需 |
| `ready` | `health !== 'red'` |
| `checks[]` | `{id, label, status: ok\|missing\|warn, required, detail, path, hint}` |
| `missing` / `missingRequired` / `missingOptional` | 缺的组件 id |
| `hints[]` | 怎么装（命令/链接） |

- **green**：直接建 job。
- **yellow**：必需项齐，只缺可选组件。目前只有 **yt-dlp**（仅抓取模式用）算可选，import 模式完全不受影响。
- **red**：缺必需组件。设置页「新建任务」会被禁用，服务端也会拒绝（`DEPS_MISSING`），不会等跑起来才报错。

### 引擎 doctor 的实际输出（已验证）

`python engine/run.py doctor` 打印一个 JSON，**缺项时退出码 1**（插件看 payload 判定，不看退出码）：

    { "ok": false,
      "checks": [
        { "name": "python",     "ok": true,  "found": "3.14.5", "path": "<解释器>", "hint": "" },
        { "name": "torch_cuda", "ok": true,  "found": "torch 2.8.0+cu128 / CUDA True / RTX 5060 Laptop", "path": "<python>", "hint": "" },
        { "name": "ffmpeg",     "ok": true,  "found": "ffmpeg 7.1", "path": "<ffmpeg.exe>", "hint": "" },
        { "name": "msst",       "ok": true,  "found": "MSST + kim 权重", "path": "<MSST 目录>", "hint": "" },
        { "name": "applio",     "ok": true,  "found": "Applio + dsh_rvc2.py", "path": "<Applio 目录>", "hint": "" },
        { "name": "old_rvc",    "ok": true,  "found": "旧版 RVC 2.2.231006 + hubert/rmvpe", "path": "<RVC 目录>", "hint": "" },
        { "name": "ytdlp",      "ok": false, "found": "找不到 python: python", "path": "python", "hint": "pip install -U yt-dlp" }
      ],
      "suggestedTorch": { "gpu": "RTX 5060 Laptop", "capability": "sm_120", "cuda": "cu128",
                          "torch": "2.8.x", "indexUrl": "https://download.pytorch.org/whl/cu128",
                          "python": "<paths.python>", "reason": "Blackwell 只有 cu128+ 有 sm_120 内核" },
      "torchInstallCommand": "<paths.python> -m pip install torch --index-url https://download.pytorch.org/whl/cu128" }

插件把它规整成上面 `checks[]` 形状（`name→id`、`ok→status`、`found→detail`），
并把 **ytdlp 标为可选**（其余为必需）。引擎若以后加 `required`/`label` 字段，插件照样认。

引擎没有 `doctor` 子命令时返回 `DOCTOR_UNAVAILABLE`，按本文档手动核对即可。

## 路径 A：半自动 bootstrap（新机器先跑这个）

    python engine/run.py setup --install --with-models --yes
    # 等价：python engine/setup/bootstrap.py --install --with-models --yes
    # 只打印不下载：去掉 --yes；只装插件不装依赖：install.ps1 -SkipDeps

它**实际会做的**（按当前引擎实现）：

| 步骤 | 自动？ | 说明 |
|---|---|---|
| yt-dlp | ✅ | `pip install -U yt-dlp`（抓取模式才用） |
| ffmpeg | ✅ | `pip install imageio-ffmpeg`，并把路径写回配置 |
| 旧版 RVC | ✅ | `git clone --depth 1 --branch <tag>` → 自动应用 `engine/vendor/rvc_patches`（torch>=2.6/PyAV/index 空兼容，备份 .orig）→ 复制本机权重 |
| RVC 运行依赖 | ✅（`--install-rvc-deps`） | 装到 paths.rvc_python（跳过 torch/torchaudio）：numpy/librosa/faiss/pyworld/parselmouth 等，版本按本机 rvc-env 锁定 |
| MSST 代码 + kim 权重 | ✅（--with-models） | clone 到 paths.msst_root；已有跳过；kim 约 871MB，断点续传/失败不中断 |
| contentvec embedder | ✅（--with-models） | 378,342,945 B / sha256 d8dd400e...；落点 `<applio_root>/rvc/models/embedders/contentvec/pytorch_model.bin`；官方 HF 失败回退 hf-mirror；doctor 实际 torch.load |
| Applio | ✅（--install-applio 或 --install 链） | 固定 tag 3.7.1；git clone 失败回退 codeload zip；venv + torch + requirements；**需要 Python 3.11+**；实测 8.1G/676 秒，预留 10G |
| fairseq 0.12.2 | ✅（--install-fairseq 或 --install 链） | 预编译 zip + sha256；装到 paths.rvc_python 的 site-packages |
| RVC hubert_base.pt + rmvpe.pt | ✅（--with-models） | 约 181MB + 173MB；已有跳过；URL 在 paths.model_sources，可配镜像 |
| PyTorch + CUDA 轮子 | ✅（--install 链 / `--install-torch`） | 按显卡 capability 自动选 cu128/cu121/cu118；`--yes` 真装 |
| `room_ir.wav` | ❌ | 自备 IR |

所以「全自动」覆盖：torch（按显卡选 cu 版本）/ Applio / 旧版 RVC / RVC 运行依赖 / fairseq / MSST 代码 / MelBand kim / hubert / rmvpe / yt-dlp / ffmpeg；
只需要自备 Python 3.11（Applio）与 `room_ir.wav`（可选混响 IR）。
**需要**：≥15G 空闲磁盘、能访问 PyPI / GitHub / HuggingFace（或配镜像）。
AI 无脑执行首选仓库根目录的 scripts/install.ps1（见 docs/INSTALL_AI.md），它会自动找 profile、幂等注册插件并跑上面这条。

### 已知问题（2026-10-31 实测）

- **Applio 3.7.1 需要 Python 3.11+**：用 3.10 显式跑安装器会在 **0 秒**直接 failed（不 clone、不建 venv、不跑 requirements），hint 给 https://www.python.org/downloads/release/python-3119/ 。本机 3.11 实测成功：临时目录、base=D:/Apps/applio-env 的 3.11.9，**676 秒 / 8.1G**，torch 2.11.0+cu128、transformers 5.13.1、faiss 1.14.3、`import core` 全过；体积比预估大，预留 10G。
- **github.com:443 间歇不可达**：Applio 安装会自动回退 `codeload.github.com` 的固定 tag zip；两者都不通时只能配代理/镜像重试。
- **人声/伴奏比例是硬约束**：实测踩坑——两版人声都 −6dB，源比例 −4.96 被改成 −10.96，听感“人声变小、伴奏变吵”。现在 mix 会算 `ratio_src` / `ratio_mix`，偏差 >0.5dB 直接 failed，并在 report.json 写 `vocalBackingRatio`；qa 同样标红。不要给单版人声私自加/减 dB。

### 只装/重装 torch（先看命令，再加 --yes 真装）

    python engine/run.py setup --install-torch --dry-run   # 只打印将要执行的 pip 命令，不动环境（默认就是 dry-run）
    python engine/run.py setup --install-torch --yes       # 真装（约 2.5–3G，按显卡自动选 cu 版本）
    python engine/run.py setup --install-torch --python D:/Apps/rvc-env/python.exe --yes   # 指定装进哪个环境

- 打印的就是 `cover_doctor` 的 `torchInstallCommand`（按你这张卡的 capability 算出来的），核对后再 `--yes`；
- 装错版本的症状：50 系报 `no kernel image is available` 或 `torch.cuda.is_available() == False`；
- 装完跑 `python engine/run.py doctor`（或 `cover_doctor`）确认 `torch_cuda` 与 `gpu_torch_match` 都变 ok。

装完整套：

    python engine/run.py doctor     # 或 cover_doctor({})

## 路径 B：全手动（自己装，doctor 验证）

| 依赖 | doctor id | 作用 | 体积（量级） | 许可（以上游 LICENSE 为准） | 能自动装 |
|---|---|---|---|---|---|
| Python 3.10–3.12 | `python` | 跑 `engine/run.py` | ~30M | PSF | 需自备 |
| PyTorch 2.x + CUDA 轮子 | `torch_cuda` | 训练 / 推理 | 2.5–3.5G | BSD-3 | ❌ 随显卡选 cuXXX，需自装（见下面「显卡与 torch 版本怎么选」） |
| Applio | `applio` | RVC 预处理 + 训练 | 实测 8.1G（代码 + torch + CUDA libs） | MIT | ✅ `setup --install-applio --yes`（需 Python 3.11+） |
| fairseq 0.12.2 | （old_rvc 检查） | 旧版 RVC 换声特征提取 | ~12M | MIT | ✅ `setup --install-fairseq --yes`（预编译包） |
| MSST（MelBand-RoFormer） | `msst` | 人声/伴奏分离 | 代码 ~100M + kim 权重 ~1G | 代码 MIT；权重按模型页条款 | ⚠️ 代码可 clone，**权重自取** |
| 旧版 RVC（`infer_cli`） | `old_rvc` | 换声推理（rmvpe/pitch） | ~200M | MIT | ✅ bootstrap 会 clone 并自动打兼容补丁 |
| RVC 运行依赖 | `old_rvc` 运行时检查 | numpy/librosa/faiss/pyworld/parselmouth 等 | ~250M | 各上游 | ✅ `setup --install-rvc-deps --yes` |
| ffmpeg | `ffmpeg` | 混音、转 mp3、探测时长/响度 | 80–150M（imageio 自带 ~30M） | LGPL/GPL（构建而定） | ✅ bootstrap 会装 |
| rmvpe / hubert-base / f0G40k 底模 | （`old_rvc` 检查里） | 音高、内容特征、声码器 | 60M + 360M + 200M | MIT / 上游条款 | 随 RVC 权重复制 |
| `room_ir.wav` | — | 卷积混响 IR（混音链） | <1M | 自备/免费 IR | ❌ 自备 |
| yt-dlp（**仅抓取模式**） | `ytdlp`（可选） | 联网抓素材 | ~10M | Unlicense | ✅ bootstrap 会装 |

> 体积只是量级参考，随版本和权重变化；许可以各仓库 `LICENSE` 与模型页为准——这直接决定「能不能随包分发」。

装完把路径写进 **`engine/config_default.json` 的 `paths`**（引擎侧文件，插件不改它）：

    paths.python / paths.ffmpeg / paths.ffmpeg_fallbacks
    paths.msst_root / paths.msst_python / paths.msst_config / paths.msst_checkpoint
    paths.applio_root / paths.applio_python
    paths.rvc_root / paths.rvc_python
    paths.room_ir / paths.ytdlp_python

再 `cover_doctor`；red 就按 `missing` + `hints` 逐项补。

## 路径 C：已有环境，直接指过去

机器上已经装过 Applio / MSST / RVC（比如之前跑过 RVC 或 MSST-GUI）时：

1. **插件设置**（设置 → 翻唱工厂）只改三项：
   - `engineDir`：引擎目录（留空 = 插件自带 `engine/`）
   - `python`：起引擎用的解释器（留空 = 环境变量 `DSH_PYTHON` → PATH 的 `python`）
   - `outputDir`：成品输出目录（默认 桌面/翻唱工厂）
2. **引擎内部**三套环境的路径仍写在 `engine/config_default.json` 的 `paths`；也支持逐 job 覆盖（job 配置里的 `config.paths`，引擎会合并）。
3. 跑 `cover_doctor`：它会逐个点名（找不到哪个 python、哪个权重、ffmpeg 能不能执行）。

## 显卡要求：只支持 NVIDIA（CUDA）

- 本管线依赖 **NVIDIA CUDA**：MSST 分离、Applio 训练、换声都需要 CUDA 内核。
- **AMD（Radeon）显卡与 Intel 核显不支持**：run 启动检测到「AMD/Intel 且 CUDA 不可用」会写 decision.code=`GPU_UNSUPPORTED` 并**退出码 2**，不会默默退 CPU 跑 10 小时。
- 完全无独显才走 CPU 回退（极慢，不推荐）；和 A 卡/核显的硬拒绝区分。
- 插件会读体检里的 `gpu_vendor`：amd/intel 时禁用「新建任务」，服务端返回 `UNSUPPORTED_GPU`。
- 装好 NVIDIA 驱动后重新体检，`gpu_vendor=nvidia` 再建 job；50 系注意 torch 必须是 cu128。

## 显卡与 torch 版本怎么选

旧版 RVC（2023 代码）本身**不挑卡**，挑的是 **torch 的 CUDA 版本**：卡再新，这版 torch 没编进对应算力（sm_XX）内核，照样跑不了。

| 显卡 | 架构 / 算力 | torch 要求 | doctor 会推荐 | 说明 |
|---|---|---|---|---|
| RTX 50 系（5060 / 5070 / 5080 / 5090） | Blackwell / **sm_120** | **必须 cu128+** | `cu128`（torch 2.8.x） | 官方旧 torch 没有 sm_120 内核，**根本跑不了**，不是「慢一点」 |
| RTX 40 系（4060–4090） | Ada / sm_89 | 官方 cu118 / cu121 即可 | `cu121`（旧 torch 也行，`cu118` 可替代） | CUDA 11.8 起支持 Ada，**无需打补丁** |
| RTX 30 系（3060–3090） | Ampere / sm_86 | cu118 最稳 | `cu118` | 兼容性最好的一档 |
| RTX 20 系及更老（2080 / 1080 …） | Turing / Pascal，sm_75 / 70 / 61 | cu118 | `cu118` | 能跑，速度慢 |
| **AMD Radeon（A 卡）** | — | **不支持** | — | 本管线依赖 NVIDIA CUDA；doctor `gpu_vendor` false + run `GPU_UNSUPPORTED` exit 2 |
| **Intel 核显** | — | **不支持** | — | 同上；没有 NVIDIA 独显时 run 直接 exit 2 |
| 无独显 | — | CPU 版（不推荐） | `--index-url .../whl/cpu` | 纯 CPU 训练，约慢 **10 倍** |

`cover_doctor` 会按**你这张卡**算出推荐，并在 `suggestedTorch` / `torchInstallCommand` 里返回（设置页体检展开区可以直接复制命令）：

    "suggestedTorch": { "gpu":"RTX 4060 Laptop", "capability":"sm_89", "cuda":"cu121",
                        "torch":">=2.1,<2.6", "indexUrl":"https://download.pytorch.org/whl/cu121",
                        "python":"<paths.python>", "reason":"Ada 架构，官方 cu121 轮子直接支持" },
    "torchInstallCommand": "<paths.python> -m pip install torch --index-url https://download.pytorch.org/whl/cu121"

引擎没给这两项时（旧版本），插件返回 `null` 并提示按上表自己选，不会瞎推荐。

### 为什么本仓库要打补丁

**torch >= 2.6（50 系必须）** 下旧版 RVC 会炸，需要这几处兼容：

- `av`（PyAV）API 变更（`av.open(path,'rb')` 失效）→ 改 ffmpeg 解码；
- `torch.load` 默认 `weights_only=True` → 显式 `weights_only=False`；
- `index_root` 为空 / index_path=None → 返回空字符串，允许空 index。

补丁已随仓库分发：`engine/vendor/rvc_patches/`（6 个文件 + `apply.py`，MIT）。bootstrap clone 后自动应用，首次覆盖保留 `.orig`，幂等；30/40 用旧 torch 也可以应用（`weights_only` 参数兼容），所以同一套引擎代码 30/40/50 通吃。手动补救：

    python engine/vendor/rvc_patches/apply.py <rvc_root>

### 一条命令自查

**用装了 torch 的那个解释器**跑（就是 `paths.python` 指的那个，本机是 `D:/Apps/rvc-env/python.exe`），不是系统默认的 `python`：

    D:/Apps/rvc-env/python.exe -c "import torch;print(torch.__version__, torch.version.cuda, torch.cuda.get_device_capability(0), torch.cuda.get_arch_list())"

本机（RTX 5060 Laptop）实际输出：

    2.8.0+cu128 12.8 (12, 0) ['sm_61', 'sm_70', 'sm_75', 'sm_80', 'sm_86', 'sm_90', 'sm_100', 'sm_120']

怎么看输出：

- 第 1 项：torch 版本（`2.8.0+cu128` = 50 系要的那个）；
- 第 2 项：CUDA 版本（`12.8`）；
- 第 3 项 `(major, minor)`：这张卡的算力（50 系 = `(12, 0)`；40 系 = `(8, 9)`；30 系 = `(8, 6)`）；
- 第 4 项 `arch_list`：这版 torch **编进去**的内核，上面例子里含 `sm_120`，所以 50 系能跑；
- **`(major, minor)` 不在 `arch_list` 里 → 当前 torch 不支持这张卡**，换对应 cu 版本重装（50 系 → cu128+），装完再跑一次 `cover_doctor`；
- 如果直接报 `torch.cuda.is_available() == False`，是驱动/CUDA 没对上，先修 NVIDIA 驱动；
- 如果报 `ModuleNotFoundError: No module named 'torch'`，是解释器用错了——换 `paths.python` 那个再跑。

`cover_doctor` 里的 `torch_cuda` 项就是按这条思路判的：它会把 torch 版本、CUDA 是否可用、卡名一起报出来。

## 分离方案（separateModel）

`cover_setup` 的 `separateModel` 二选一，写进 `job_config.options.separateModel`：

- `kim`（默认）：MelBand-RoFormer kim，稳定、听感好；体检（doctor）检查的就是它那套 MSST config/权重。
- `bs317`：BS-RoFormer ep317，串音指标更低；**目前实验结论是无可测的听感提升**，只建议对比时试。

选 `bs317` 需要另外的 BS-RoFormer ep317 权重与对应 msst config（不是 kim 那套）；缺权重时 `separate` 阶段会失败，按日志/体检提示补。日常保持 `kim` 即可。

## 本机已下载（mode=local）

不想手动整理目录、也不想联网？用本机音乐客户端里**已下载**的文件：

    cover_setup({ singer: "周杰伦", sources: ["local"], clients: ["netease", "qq"], keyword: "稻香", target: "D:/目标曲.wav" })

- 扫描网易云 / QQ音乐 / 酷狗 / 酷我的常见下载目录；`clients`（`netease` / `qqmusic` / `kugou` / `kuwo`，`"qq"` 也认）默认全部，`keyword` 默认 = 歌手名。
- **不需要账号、不需要登录态、不联网**（除非你再开 `mode: "auto"` 抓取或 `refSearch`）；**只读，不修改原文件**。
- 格式：常见音频都认；**ncm 自动解密**；`qmc` / `kgm` 等加密格式需要先手动转成 wav/flac/mp3，否则会被跳过。
- 写进 job 配置：`mode: "import"` + `sources: { local_clients: true, clients: [...], keyword: "..." }`（引擎目前只认 mode=import/auto，靠 `sources.local_clients` 识别本机客户端）；非法客户端名返回 `BAD_CLIENT`。
- 预筛预览里的 `localFetch` 会给扫描到的文件数与格式分布（`fileCount` / `formats`）；fetch 跑之前是 `fetch-pending`。
- 可以和其他来源组合：`sources: ["folder","local"]`（目录 + 本机客户端）或 `["local","auto"]`（本机 + B站搜索，需 `copyrightAck`）；每个来源各贡献几首会出现在 `preview.contributions`。
- 素材来源三种、可多选组合：① 目录导入（`sources: ["folder"]` + folder）② 本机客户端（`sources: ["local"]`）③ B 站搜索（`sources: ["auto"]`，要 copyrightAck）；链接导入规划中。

## 素材怎么给（好球曲）

- **选填**。每条可以是**歌名**（例：言っちゃいけないことばっか浮かぶよな），也可以是**完整路径**（例：D:/素材/xxx.wav），还能混着填。
- 填歌名的解析顺序：① 在你给的素材目录里按文件名找 → ② 允许联网（`refSearch: true` + `copyrightAck: true`）时去 B 站搜并下载 → ③ 仍失败就**忽略这一条并提示**，不中断 job。
- 填路径（含 / 或反斜杠、或盘符）才校验存在性：写错报 `REF_NOT_FOUND`，message 标出「第几行」。
- 最多 20 条，超出报 `REF_TOO_MANY`；留空就让引擎从素材目录自动筛。
- **男歌手**：建 job 时把 `voice` 设成 `male`（界面上「目标歌手声部 → 男声」）。智选不再无条件排除男声：先做对唱/合唱检测，再和期望声部比对；`auto` 会按目标曲人声判断，判断不对就会把男声素材当对唱排掉。
- 联网搜好球曲属于联网行为，和抓取模式一样要版权确认：`cover_setup` 未确认时返回 `needsConfirm`（reason `REF_SEARCH_NEEDS_ACK`），不建 job。

## 为什么不把依赖打包进仓库

1. **torch/CUDA 跟着显卡和驱动走**：cu121 / cu124 / cu128 的轮子互不通用，打进来 2.5–3.5G，装错就是 3G 垃圾；换机器要整个重下。
2. **模型许可各不相同**：RVC 底模、MSST 的 kim 权重、hubert/rmvpe 的条款不完全一样，有的允许再分发、有的只许自用——插件仓库不能替用户做这个决定。
3. **体积**：全量 5–10G，仓库会从几 MB 变成几 GB，clone / 更新 / 回滚都变慢。
4. **可能重复**：很多用户已经有 Applio/RVC（路径 C），硬打包等于装两套、还占盘。

所以仓库里只放**代码 + 体检逻辑 + 安装指引**；实际依赖由 `setup --install` 或用户自己落到**本机**。

## 常见问题

| 症状 | 原因 / 处理 |
|---|---|
| `DOCTOR_UNAVAILABLE` | 引擎版本还没有 `doctor`（或起不来）。升级引擎，或按本文档手动核对。 |
| `ENGINE_NOT_READY` | `engine/run.py` 不在 `engineDir`，或 Python 不可用。先修好这两个再说体检。 |
| red，缺 `python` | `paths.python` 的解释器不存在；改路径或重装 Python 3.10+。 |
| red，缺 `torch_cuda` | 该 Python 里 torch 装错 CUDA 版本；按显卡重装（`--index-url .../cu128`）。 |
| 50 系报「no kernel image is available」/ CUDA 不可用 | torch 不是 cu128+，没有 sm_120 内核；按上表重装 `torch==2.8.0+cu128` 后重体检。 |
| 40/30 系装 cu128 能跑吗 | 能，但没必要：这两代用 cu118/cu121 更稳、也更省驱动折腾。 |
| red，缺 `ffmpeg` | `pip install imageio-ffmpeg`，或 `paths.ffmpeg` 指到现成 exe。 |
| red，缺 `msst` / `applio` / `old_rvc` | 目录/权重/关键文件不在；按 `hints` 补，再改 `paths`。 |
| yellow，缺 `ytdlp` | 只有抓取模式受影响；import 模式不用它，可忽略。 |
| 建 job 被拒 `DEPS_MISSING` | 体检 red 且没过期。装完点「重新体检」；缓存 10 分钟自动过期。 |
| 装完仍 red | 检查是否装进了**另一个** Python 环境；`paths.python` 和 `paths.*_python` 可能不是同一个。 |


## 训练链（Applio）：驱动已随仓库分发

训练/换声依赖 **Applio**，但训练驱动（`dsh_rvc2.py`）**已经在仓库里**：

```
engine/vendor/applio/dsh_rvc2.py
```

引擎解析顺序：`paths.applio_driver` → `<applio_root>/dsh_rvc2.py` → 仓库自带 vendor 副本。
所以不需要作者额外发文件；把 Applio 装好、把 `paths.applio_root` 指对即可。

### fairseq：只有“旧版 RVC 换声”需要

**Applio 训练不需要 fairseq**（它用 transformers 加载 hubert/contentvec）。只有旧版 RVC（convert 阶段）要 `import fairseq`。

**首选（自动）**：

    python engine/run.py setup --install-rvc-deps --yes   # numpy/librosa/faiss/pyworld/parselmouth 等（不装 torch）
    python engine/run.py setup --install-fairseq --yes

- 预编译 Windows/py310 包（MIT，11.4MB，配置在 `paths.fairseq_zip_url/fairseq_zip_sha256`）；
- 下载后校验 sha256，解压到 `paths.rvc_python` 的 site-packages，再 pip 装 omegaconf<2.1 / hydra-core<1.1 / sacrebleu / bitarray / regex / tqdm / cffi；
- 目标解释器已能 `import fairseq` 时直接 skip；
- 验证：`<rvc_python> -c "import fairseq; print(fairseq.__version__)"`。

**手动 fallback（三条，按顺序试）**：

1. 下载同一个预编译 zip，手动解压到 `<rvc_python>\Lib\site-packages`：https://github.com/LHL473962/dsh-cover-factory/releases/download/v0.1.11/fairseq-0.12.2-win-py310.zip
2. **用 Applio 官方安装脚本**（`install.bat`），它自带依赖处理；
3. **装 VS Build Tools 后源码编译**：`pip install fairseq==0.12.2 --no-build-isolation`；或从已有可用环境复制 `site-packages/fairseq`（最后手段）。

### 不装训练能不能用

可以先只跑 **分离 + 素材筛选**（`cover_run to=select`）验证环境；但**出成品必须训练**（或复用已有声库缓存）。


## 路径配置（必读）

`engine/config_default.json` 只放**可移植默认值**（空路径 / `python` / `ffmpeg`）。
把你自己机器的路径写进 **`engine/config.local.json`**（已 gitignore，不会被提交）：

```json
{
  "paths": {
    "python": "C:/Python310/python.exe",
    "ffmpeg": "ffmpeg",
    "msst_root": "...", "msst_python": "...", "msst_config": "...", "msst_checkpoint": "...",
    "applio_root": "...", "applio_python": "...",
    "rvc_root": "...", "rvc_python": "...", "rvc_hubert": "...", "rvc_rmvpe": "...",
    "bankDir": "~/.dsh/cover-factory/banks"
  }
}
```

加载顺序：`config_default.json` → `config.local.json` → job 的 `--config`。
写完跑一次 `cover_doctor` / `python engine/run.py doctor`，全绿再建任务。

打包自检：`node tests/package.mjs`（检查关键文件、本机路径泄漏、文档引用）。

## 首次使用检查清单（站在别人机器上）

安装完、跑任务前，按顺序过一遍。任何一条不通过，先别建 job。

1. **干净环境自检**：`node tests/firstrun.mjs`（复制一份仓库、删掉 config.local，验证 doctor 报错可操作、不崩溃、不泄漏作者路径）。
2. **体检全绿**：`cover_doctor` 或 `python engine/run.py doctor`，ok=true。
3. **路径都在自己机器上**：`engine/config.local.json` 里的 msst/applio/rvc 路径逐条确认存在。
4. **Python 版本对**：RVC 环境 3.10；Applio 环境 ≥3.11（doctor 会报）。
5. **fairseq**：`<rvc_python> -c "import fairseq"` 能过（只有换声需要）。
6. **磁盘** ≥15G，**显存** ≥5G 空闲（否则 run 会停下来让你选）。
7. **先跑 300 步**：steps: 300 全链路出片（约 3 分钟），确认训练/换声/混音都通，再上 9000。

常见“别人跑不通”的原因，按概率排序：

| 原因 | 症状 | 解法 |
|---|---|---|
| 路径写成了作者机器 | doctor 全红 | 填 config.local.json |
| Python 版本不对 | Applio venv 建失败 / 版本门 | RVC 3.10、Applio 3.11+ |
| 缺 fairseq | 换声阶段 ImportError | setup --install-fairseq --yes |
| 显存不足 | run 退出码 2 / LOW_VRAM | 关其它 GPU 程序，或减步数 |
| 网络不通 | 下载权重失败 | 开代理，或手动下模型 |

## fairseq 与 Python 版本（重要）

预编译 fairseq zip 是 **Windows / Python 3.10** 的二进制（cp310）。
如果 `rvc_python` 是 3.12，那个包**导入会失败**（ABI 不匹配），不能硬装。

正确做法：**两个环境分开**

| 用途 | Python | 依赖 |
|---|---|---|
| 训练（Applio） | **3.11+** | transformers（不需要 fairseq） |
| 换声（旧版 RVC） | **3.10** | 需要 fairseq → 用 `setup --install-fairseq` |

旧版 RVC 用 3.10 建独立 venv 即可；Applio 用 3.11/3.12。
