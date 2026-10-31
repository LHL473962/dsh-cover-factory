# 翻唱工厂 · FAQ（v0.1）

> 按真实提问整理。耗时按 **RTX 5060 Laptop 实测（约 0.37 秒/步）**，其余数字以引擎默认配置为准。工具名就是对话里能直接调的那七个：`cover_setup / cover_run / cover_status / cover_cancel / cover_list / cover_export / cover_delete`。

## Q1. 训练到底要多久？会不会卡住？

只算 **train 阶段**：5000 步约 30 分钟、9000 步约 55 分钟、18000 步约 1 小时 50 分（RTX 5060 Laptop 实测，约 0.37 秒/步，显存占用 7.5G）。实测：rime7 145e ≈17800 步 111 分钟、suis5 60e ≈16000 步 95 分钟。3000 步约 18 分钟；其他步数按 0.37 秒/步线性估。9000 步约 120 epoch（引擎按 75 步/epoch 估）。

这**不含**分离、特征预处理、换声、混音、质检；素材越多，前后处理越久。中途看 `cover_status` 的 `currentStage` / `percent`，日志在 `<工作目录>/logs/train.log`。几小时的任务别连续轮询，隔一阵看一次就行。

## Q2. 没有显卡行不行？

能跑，但很慢。**训练阶段不再因为硬件 exit 2**：`cover_run` 启动时探测不到可用 GPU 只打警告，然后走 CPU（约 **10 倍慢**：9000 步从约 55 分钟变成约 9 小时），分离也会更慢。建议 8G 显存起步，用 GPU 跑。

唯一的硬件硬闸见 Q3：**空闲显存 < 5G 且没放行**（`LOW_VRAM`）。

## Q3. 显存不足怎么办？

**唯一的硬件硬闸在 `cover_run` 启动时**（引擎 run 入口，不是 train 阶段）：**空闲显存 < 5G** 且没放行 → 退出码 2，`decision.code = 'LOW_VRAM'`，job 原样保留、之后能续跑。二选一：

1. **减步数**：`cover_setup` 新建一个 steps 更小的 job；
2. **显式放行**：引擎命令行加 `--allow-lowmem`，或 job 配置 `options.allowLowmem=true`（v0.2 插件还没把这个开关摆到 UI 上）。

其余情况都只警告、继续跑：

- 空闲显存 **5–8G**：警告「可能 OOM」，继续跑；
- **没有可用 GPU**：警告后走 CPU，约 10 倍慢。

注意：**降步数主要省时间，不省显存**——5000 / 9000 / 18000 都是 7.5G。跑之前关掉别的吃显存程序（游戏、浏览器硬件加速、别的训练）。

## Q4. 素材要什么格式、多少首、多长？

格式：wav / flac / mp3 / m4a / aac / ogg / opus / wma / aif / aiff / ape / wv。素材目录支持子目录递归，插件只读，不删不改。

- 每首 **≥60 秒**，低于 60 秒在智选阶段直接排除。
- 目标曲 **≥30 秒**，这是质检 preflight 的硬门槛。
- 好球曲（refs）**选填**，建议 2–8 首同歌手、同年代、干净独唱。每项可填**歌名**或**完整路径**（可混填）；歌名在素材目录里自动匹配，找不到只警告，路径写错才报 REF_NOT_FOUND。最多 20 条（超过报 REF_TOO_MANY）。
- 对唱、合唱、现场大混响、带主持人口白的版本尽量别放；目标曲优先录音室版 / 官方伴奏版。

## Q5. 为什么某首歌被排除了？

`select` 阶段的规则（**不再无条件排除男声**）：

1. **对唱/合唱检测**：逐帧 F0，同一首里低区（60–165Hz）与高区（165–500Hz）占比都 ≥20% 且两区中位比 >1.5（双峰）→ 判对唱/合唱，排除（`allowDuet=true` 可放开）。
2. **与期望声部比对**：按 `voice`（`auto` / `female` / `male`，写在 `job_config.options.voice`）确定期望声部；`auto` 时用**目标曲人声**的 F0 判断。单一男声的素材在期望男声时**正常保留**，只有声部不符或混杂才排除。
3. 时长 **<60 秒**排除；立体声相关 **<0.3** 判现场/伴唱，降权；参考曲按音色聚类（中位音高 + 频谱质心）加权 **×6**。

看 `<jobId 工作目录>/work/select/report.json` 里每首的 `reason` 和 `voiceGuess`（`cover_setup` 的预筛预览也会带 `voiceGuess`，能直接看出「这首被判成什么声部、为什么排除」）。想放行对唱：把 `~/.dsh/cover-factory-settings.json` 的 `allowDuet` 设为 `true`，再 `cover_run({ jobId, from: "select", resume: true })`。代价是模型会学到两个人的混合音色。排除只影响训练集，**不会删除你的源文件**。

## Q6. 抓取模式合法吗？会不会自动去下载？

默认关闭。`autoDownload` 默认 `false`；直接 `cover_setup({ mode: "auto" })` 会返回 `DOWNLOAD_NOT_ENABLED`。插件**不内置、不分发任何音源**。

就算你手动把 `autoDownload` 改成 `true`，v0.1 引擎的 fetch 对抓取模式也只写 `work/raw/FETCH_TODO.md` 然后 `skipped`——引擎不联网、不下载、不调付费接口。所以实际能用的还是 **import 模式**：自己用合法渠道准备文件，放进文件夹。第三方音源的版权风险、平台风控风险由使用者自负；失败也别反复重试。

## Q7. 成品在哪？报告在哪？

默认在 **桌面/翻唱工厂/**：

- `<歌手>-<目标曲>.mp3`（320k）
- `<歌手>-<目标曲>-report.md`

ffmpeg 不可用时会先交 `.wav` 并给 `FFMPEG_MISSING` 提示；装好 ffmpeg 或设 `settings.ffmpeg` 后可重导。中间产物在工作目录 `~/.dsh/cover-factory/jobs/<jobId>/`：`work/mix/out.wav`、`work/convert/vocal.wav`、`work/qa/report.json`。

`cover_export` 重复调用不会产生 `(1)` 副本；默认会覆盖同名文件（`overwrite=true`），设 `false` 且大小相同则跳过复制。同名文件被播放器占用会返回 `COPY_FAILED`。

## Q8. 跑到一半失败/被杀，怎么续跑？

1. `cover_status({ jobId })` 看 `state`、`failedStage`、`logTail`；完整日志在 `<工作目录>/logs/<stage>.log`。
2. 修掉原因（缺模型、路径变了、ffmpeg 没了、素材被移走）。
3. `cover_run({ jobId, resume: true })`。已 done / skipped 的阶段按输入签名跳过，**不会重做 separate**。
4. 只想补某几段：`cover_run({ jobId, from: "mix", to: "export" })`。
5. 退出码 2 是在等你决策，看 `job.json` 的 `decision.code`：抓取是 `COPYRIGHT_ACK_REQUIRED`，硬件**只有** `LOW_VRAM`（空闲显存 <5G 且没放行，见 Q3）——**训练阶段不再有硬件退出码**。

取消用 `cover_cancel({ jobId })`；已完成的阶段不丢，之后还能 resume。

## Q9. 怎么重训？能直接加步数吗？

v0.1 建 job 时步数就写进 `job_config.json` 了，之后**没有改步数的工具入口**；`resume` 只是接着跑，不会加步数。

要更多步数：`cover_setup` 新建一个 job，`steps: 18000`（约 1 小时 50 分）。新 job 会重新分离/训练。同一 job 内训练断点（`G_*/D_*.pth`）在 `work/model/applio_logs/<modelId>/`，重跑会自动续训。**不要手改 job.json**——引擎是唯一写方。想只重跑训练段可以 `cover_run({ from: "train", to: "export" })`，但步数不变。

## Q10. 怎么换声库 / 换歌手？能复用以前训好的 .pth 吗？

一个 job 绑一个声库：`work/model/<modelId>/model.pth` 加 `model.index`。v0.1 没有"导入旧 .pth"或"热切换声库"的参数，`cover_setup` 也不收模型路径。**换歌手/换音色 = 新建 job**。

TESTS.md 里记录的 rime7、suis5 这类权重，目前不能直接喂给 `cover_setup`；硬拷到新 job 的 model 目录也不保证被认（引擎按 job 的 modelId 和输入签名找）。等后续版本开放。

## Q11. 音色不像怎么办？

先查素材，再谈步数：

- refs 收敛到 **2–8 首**同歌手、同年代、干净独唱；别混现场、对唱、变声、翻唱别人的版本。refs 选填，歌名/路径都行。
- 步数 9000 起步，愿意等上 18000；但**步数不是万能**，素材不对，加步数只会更像"平均后的那个人"。
- 看 qa 的**音域覆盖**：<70% 说明目标曲超出训练音域，按报告建议整首移调，或换更接近的素材。
- 换声默认 rmvpe + `index_rate=0.75` + `protect=0.33`，这组是稳，不是"更像"的开关。

v0.1 不承诺像本人，只保证流程跑通和指标可查。

## Q12. 断音怎么办？

qa 用 `gap_rate` 统计，合格线 **<12 次/分**。常见原因：目标曲人声 stem 不干净（伴奏串进来）、素材太少/太杂、步数低、整首移调跨度过大、rmvpe 在气声/转音处跟丢。

处理顺序：换更干净的目标曲 → refs 收敛到同一音色 → 加步数 → 避免大跨度移调 → 听 `work/convert/vocal.wav` 判断问题出在换声还是混音。报告的 `suggestions` 会直接写"加训练步数 / 换更干净的素材"。

## Q13. 和声/对唱怎么办？能自动加和声吗？

v0.1 **不做和声生成，也不做声部分离**。

- 对唱/合唱由「低/高区双峰（各 ≥20% 且中位比 >1.5）」检测后排除（settings 的 `allowDuet` 可放开，但模型会学混合音色）；单一声部不会被误杀。
- 女声和声、叠唱检测不出来，会留在训练集里。
- 目标曲自带和声/伴唱，会留在 vocals 或 instrumental stem 里，跟着一起换声。

要干净：换独唱版目标曲；对唱曲别硬开 allowDuet，宁可不训那首。

## Q14. 能批量出很多首吗？

不能真批量。一次一个 job、一个目标曲；多首 = 多个 job，GPU 串行，而且**每个 job 会重新训练**（v0.1 没有复用声库换目标曲的入口）。没有任务队列工具，得自己等前一个跑完再 `cover_run` 下一个；`cover_list` 看历史。目标多的话，建议先等"声库复用"能力。

## Q15. 如何整首移调？

引擎支持 `options.shift` 取 **−12..+12** 半音，在 mix 阶段把人声和伴奏**同比例一起移**（禁止只动人声，会跑调，只动人声属实验模式）。qa 音域覆盖 <70% 时，会算出一个建议的 `shift=N` 写进报告。

但插件 v0.1 的 `cover_setup` **没有暴露 shift 参数**，`job_config.json` 里固定 0；目前要移调只能等版本或让引擎侧改配置跑，别手改 `job.json`。报告模板里保留了 shift 字段，将来直接填。

## Q16. 需要配代理吗？

不用，也不该配。引擎声明不联网：`fetch` 的 import 只做本地复制/转码；auto 模式直接 `skipped`；训练、换声、混音全在本地。代理、镜像、API key 跟它无关。

看到"抓取模式"也别去配代理——v0.1 不会下载。真要下载，用浏览器/下载工具自己拿，再走 import。

## Q17. 任务文件、日志都在哪？

工作目录：`~/.dsh/cover-factory/jobs/<jobId>/`

- `job.json`：唯一状态源（引擎独占写，原子写）
- `job_config.json`：建 job 时的输入快照
- `plugin-run.json`：后台进程 pid / 存活状态
- `logs/<stage>.log`：每个阶段一份日志
- `work/raw`、`work/stems`、`work/select/report.json`、`work/clean`、`work/dataset`、`work/model`、`work/convert`、`work/mix`、`work/qa`、`work/export`

成品另有一份在桌面。清空间删整个 job 目录即可（先确认不需要续跑）。

## Q18. 设置在哪改？改完要重启吗？

`~/.dsh/cover-factory-settings.json`。改完**下次工具调用生效**，不用重启 DSH。

| 键 | 默认 | 作用 |
| --- | --- | --- |
| engineDir | 插件目录/engine | 引擎位置 |
| python | 空（回退 DSH_PYTHON，再回退 PATH） | Python 解释器 |
| outputDir | 桌面/翻唱工厂 | 成品目录 |
| defaultSteps | 9000 | 默认步数 |
| allowDuet | false | 放行对唱/合唱 |
| autoDownload | false | 抓取模式总开关 |
| workRoot | ~/.dsh/cover-factory/jobs | 工作目录根 |
| ffmpeg | ffmpeg | 导出转 mp3 用 |

设置页 UI 排到 v0.2，现在只有 `/api/cover-factory/settings` 接口。坏值会被夹回范围（例如 steps 超过 200000 会被夹到 200000）。

## Q19. 装完没反应 / 看不到工具？

按顺序查：

1. 插件目录是否在 profile 的 `node_modules` 下，名字是不是 `dsh-cover-factory`。
2. 是否重启 DSH、插件页是否启用。
3. 工具列表有没有 `cover_setup`；调 `cover_list` 能返回就说明插件活着。
4. 返回 `ENGINE_NOT_READY` 是缺 `engine/run.py` 或 Python 不可用，**不是插件 bug**。
5. 设置里 `python` 为空时，先用环境变量 `DSH_PYTHON`，再回退 PATH 的 python。

所有工具都 `ok:false` 也不抛异常，所以"没反应"多半是没装上或没启用；先跑 `cover_doctor` 看依赖齐不齐。

## Q20. 能商用吗？版权怎么算？

素材和成品的权利归你或原权利人，插件不做授权判断。import 模式只读你给的文件；抓取模式默认关闭，开了也不内置音源，风险自负。翻唱成品是**演绎作品**，商用/发布前自行确认词曲版权、录音版权和目标平台规则。软件按现状提供，不承诺音质完美或商用合规。

## Q21. 训练到一半崩了 / 关机了怎么办？

短答：`cover_run({ jobId, resume: true })` 再来一次。

- 引擎每 **5 个 epoch** 存一次盘（`save_every_epoch=5`），最多丢最后几十分钟。
- resume 只跑还没 done 的阶段：**不会重做 separate / select**；已经训出来的模型缓存（`work/model/applio_logs/...`）会被复用，不会从 0 开始。
- 先 `cover_status` 看 `failedStage` 和 `logTail`，把原因修掉（显存被别的程序占了、路径变了、断电后目录只读）。
- 断电 / 过热关机之后，先等机器凉下来、确认电源和散热，再 resume。
- 界面上对同一个 job 点「续跑」等价于 `resume: true`。

## Q22. 依赖没装齐怎么办？体检红/黄是什么意思？

先跑 `cover_doctor({})`（或设置页顶部「重新体检」）：

- **green**：全齐，直接建 job。
- **yellow**：必需项齐，只缺可选（目前只有 yt-dlp，只影响抓取模式）；import 模式照常用。
- **red**：缺必需项。新建任务会被禁用、服务端也会拒（`DEPS_MISSING`）。
  把 `missing` + `hints` 照着做，或看 `docs/INSTALL.md` 的三条路径：
  A `python engine/run.py setup --install`（能自动装的部分）/ B 全手动 + doctor 验证 / C 已有环境把 `paths` 指过去。
- `ok:false`（`ENGINE_NOT_READY` / `DOCTOR_UNAVAILABLE`）：体检本身没跑成，**不等于依赖齐全**，按 INSTALL.md 手动核对。

装完点「重新体检」（缓存 10 分钟自动过期）。

## Q23. 显卡和 torch 版本怎么配？50 系为什么必须 cu128？

旧版 RVC（2023 代码）本身**不挑卡**，挑的是 **torch 的 CUDA 版本**——torch 没编进这张卡的算力内核，就跑不起来。

| 显卡 | 算力 | torch 要求 |
|---|---|---|
| RTX 50 系（5060–5090） | Blackwell / sm_120 | **必须 cu128+**（本机这套 `2.8.0+cu128`），官方旧 torch 跑不了 |
| RTX 40 系（4060–4090） | Ada / sm_89 | 官方 cu118 / cu121 即可（CUDA 11.8 起支持 Ada），**不用打补丁** |
| RTX 30 系（3060–3090） | Ampere / sm_86 | cu118 最稳 |
| RTX 20 系及更老 | sm_75 / 70 / 61 | cu118，能跑但慢 |
| 无独显 | — | CPU，约慢 10 倍 |

**为什么要打补丁**：只有在 **torch >= 2.6 上跑旧版 RVC**（50 系唯一可行路径）才需要兼容 `av` API 变更、`torch.load` 的 `weights_only`、以及 `index_root` 为空的情况；30/40 系用旧 torch 不需要这些补丁。

**不想自己查？** `cover_doctor` 会按你的显卡算出推荐（`suggestedTorch`）并给一条可直接复制的 `torchInstallCommand`（设置页体检展开区有「复制安装命令」）。

**一条命令自查**（要用装了 torch 的那个解释器，即 `paths.python`；本机是 `D:/Apps/rvc-env/python.exe`）：

    D:/Apps/rvc-env/python.exe -c "import torch;print(torch.__version__, torch.version.cuda, torch.cuda.get_device_capability(0), torch.cuda.get_arch_list())"

本机输出：`2.8.0+cu128 12.8 (12, 0) ['sm_61','sm_70','sm_75','sm_80','sm_86','sm_90','sm_100','sm_120']` → `(12, 0)` 对应列表里的 `sm_120`，所以 50 系能跑。

看 `get_device_capability(0)` 的 `(major, minor)` 在不在 `get_arch_list()` 里：**不在 = 这版 torch 不支持这张卡**，换对应的 cu 版本重装（50 系 → cu128+）。完整对照表和说明见 `docs/INSTALL.md` 的「显卡与 torch 版本怎么选」。

## Q24. 好球曲能只填歌名吗？会联网搜吗？

可以，而且**选填**。`refs` 的每一项可以是歌名也可以是完整路径，混着填也行。流程：

1. **先在你给的素材目录里找**（按文件名匹配歌名）——大多数情况到这一步就够。
2. 找不到、且开了 **`refSearch: true`**（界面上是「好球曲找不到时联网搜索（需要版权确认）」）→ 去 B 站搜并下载。
   这是联网行为：**必须带 `copyrightAck: true`**（或 `mode: "auto"`），否则 `cover_setup` 返回 `needsConfirm: true`（reason `REF_SEARCH_NEEDS_ACK`），**不会建 job**。
3. 仍然失败 → **忽略这一条并提示**，不会中断整个 job。

补充：

- 填了**路径**（含 `/` 或反斜杠、或盘符）才校验存在性；写错直接 `REF_NOT_FOUND`（message 带「第几行」）。
- 最多 20 条；超过报 `REF_TOO_MANY`。
- 返回里 `refs` 会分类成 `{paths:[...], names:[...]}`，方便确认哪些走本地、哪些可能联网。
- 界面输入框不设 placeholder，示例和说明都在输入框下方的小字里。

## Q25. 我是男歌手 / 素材是男声，会被当成对唱排掉吗？

不会（v0.3 起规则改过）。智选**不再无条件排除男声**：

1. 先做**对唱/合唱检测**：同一首里低区与高区占比都 ≥20% 且两区中位比 >1.5（双峰）才算对唱，排除。
2. 再和**期望声部**比对：`voice` 写 `male` 时，单一声部的男声素材正常保留；写 `female` 时女声保留、男声或混杂才排。
3. `voice` 默认 `auto`：用**目标曲人声**的 F0 自动判断期望声部。判断错了（例如目标曲是男声清唱却被判成女声）就显式指定 `cover_setup({ voice: "male" })`。

怎么核对：`cover_setup` 返回的 `preview.voiceGuess`、或 `select/report.json` 里每首的 `voice` + `reason`。界面上新建任务卡片的「目标歌手声部」下拉就是传这个参数；非法值返回 `BAD_VOICE`（只认 `auto` / `female` / `male`）。

## Q26. 能直接用网易云 / QQ音乐里下载好的歌吗？

能。建 job 时把本机客户端放进来源（`sources: ["local"]`，界面勾选「本机客户端」；旧写法 `mode: "local"` 仍兼容）：

- **只读本地已下载的文件**：插件/引擎扫描客户端常见下载目录，不联网、**不需要账号、不需要登录态**，也不修改原文件。
- 可选 `clients`（`netease` / `qqmusic` / `kugou` / `kuwo`，默认全部；`"qq"` 也认，会归一成 `qqmusic`）和 `keyword`（默认 = 歌手名）。
- 常见格式都认；**ncm 会自动解密**；`qmc` / `kgm` 等加密格式需要你先手动转成 wav/flac/mp3。
- 写进 job 配置的是 `mode: "import"` + `sources: { local_clients: true, clients: [...], keyword: "..." }`（引擎靠 `sources.local_clients` 识别，`mode` 只认 import/auto）；非法客户端名返回 `BAD_CLIENT`（会列出可选项）。
- 跑起来后，`cover_setup` 的预筛预览里 `localFetch` 会给出**扫描到的文件数**与**格式分布**（`fileCount` / `formats`）；fetch 还没跑时是 `fetch-pending`。

想更可控就同时给 `refs`（歌名/路径都行）；只想用 QQ音乐 就传 `clients: ["qqmusic"]`。

## Q27. 能不能同时用几个素材来源？

能，**多选、至少 1 个**（新参数 `sources`）：

```
cover_setup({ singer: "suis", sources: ["folder", "local"], folder: "D:/素材/suis", clients: ["netease","qqmusic"] })
cover_setup({ singer: "suis", sources: ["folder", "auto"], folder: "D:/素材/suis", copyrightAck: true })
```

- 一个都没选 → `BAD_SOURCES`（会带 `allowed: ["folder","local","auto"]`）。
- 选了 `folder` 就必须给 `folder`（否则 `MISSING_FOLDER`）；选了 `auto` 就必须 `copyrightAck: true`（否则只回 `needsConfirm`，不建 job、不联网）。
- 落盘：单一来源沿用旧扁平形状；多来源是**扁平组合** `sources: { folder, local_clients: true, clients, keyword, search, limit, author, kinds:[...] }`，引擎按 folder → local_clients → search 合并去重。
- 预筛预览里 `preview.contributions` 会给出**每个来源各贡献几首**，`preview.totalCandidates` 是总数；fetch 没跑的来源显示 `available:false` / 「等 fetch」。
- 旧参数 `mode` 仍可用（等价单一来源），但**优先用 `sources`**。

## Q28. 歌手名写成别名 / 写错字会怎样？

会**自动归一**到规范名（canonical），归一结果写进声库名、成品文件名，以及抓取/本机扫描的关键词：

| 你写 | 归一为 | matchedBy |
| --- | --- | --- |
| `suis` | `suis` | `canonical`（置信度 1） |
| `夜鹿` / `ヨルシカ` / `Yorushika` / `すい` / `スイ` | `suis` | `alias`（0.95） |
| `suis 夜鹿`（带后缀） | `suis` | `contains`（0.8） |
| `sius` / `yorushka`（错 1–2 个字符） | `suis` | `fuzzy`（0.5–0.85，距离越近越高） |

- 返回值里看 `resolvedSinger: { input, canonical, matchedBy, confidence, candidates }`；界面会在结果区显示「歌手名已归一：夜鹿 → suis（alias，置信度 0.95）」。
- **没命中**时：`canonical` = 你输入的原名、`confidence` = 0、`matchedBy` = `none`，并给出 `candidates`（编辑距离最近的几个，含 `distance`）供确认；按原名继续建 job，不会失败。
- 别名表在 `lib/aliases.js`（`ALIAS_TABLE`），扩展就加一条；归一化会处理大小写、全半角、空格/连字符、片假名→平假名→罗马字。

## Q29. 分离方案 kim / bs317 怎么选？

建 job 时 `separateModel` 二选一（界面：新建任务卡片「分离方案」下拉）：

| 值 | 说明 | 建议 |
| --- | --- | --- |
| `kim`（默认） | MelBand-RoFormer kim，稳定、听感好 | **日常用这个** |
| `bs317` | BS-RoFormer ep317，串音（bleed）指标更低 | 想对比时可以试 |

**bs317 目前的实验结论：无可测的听感提升，只有串音指标更低。** 别把它当升级版；默认 kim。写进 `job_config.options.separateModel`，非法值返回 `BAD_SEPARATE_MODEL`（只认 `kim` / `bs317`，`BS-317`、`ep317` 这些别名也认）。

换方案要重建 job（或在 `job_config.json` 里改 `options.separateModel` 后从 `separate` 续跑）；已经分离过的阶段不会自动重跑。

## Q30. cover_status 里的 stemCheck / polarityFixed 是什么？

引擎在 separate 之后会对两条轨做体检（人声/伴奏是否正常、是否反相），插件只是**原样透出**，不自己算：

- `stemCheck`：`{ ok, vocal, instrumental, inverted, warnings[] }`（字段以引擎返回为准）；`ok:false` 时看 `warnings`，多半是素材太差/串音严重/一条轨几乎为空。
- `polarityFixed`：`true` 说明引擎检测到反相并**自动修好了**，不需要你再操作；`false`/`null` 表示没检测到或还没跑。
- 还没跑 `separate` 时两者都是 `null`，别据此判断「素材坏了」。

## 附录：硬件与风险（训练期间必看）

- 训练时**显卡会跑满（接近 100%）**，显存占用约 **7.5–8G**；笔记本会明显发热、风扇全速，可能降频。
- 期间**不要开游戏 / 剪辑 / 渲染**等吃显卡的程序，容易 OOM 或崩溃。
- 请**插着电源**、保持散热；长时间满载对老笔记本有风险（可能过热关机）。
- **不要中途关机**：引擎每 5 个 epoch 存一次盘，中断后用 `cover_run({ jobId, resume: true })` 续训（同一个 job 重新运行选「续跑」）。
- 磁盘需要预留 **≥ 10G**（素材 + 切片 + 特征 + 模型）。
- 显存 **<5G** 会先停下来让你选：**减步数** 或 加 `--allow-lowmem`（这是唯一的硬件硬闸）；**5–8G 只警告**，继续跑。
- **没有可用 GPU** 会走 CPU，**慢约 10 倍**（9000 步约 9 小时）。
- **RTX 50 系必须用 torch cu128；30/40 系用 cu118/cu121**；插件会按你的显卡自动推荐（体检展开里能直接复制安装命令）。

界面上的「训练期间会发生什么（风险提示）」折叠块（设置 → 翻唱工厂 → 新建任务）写的是同一份内容。
