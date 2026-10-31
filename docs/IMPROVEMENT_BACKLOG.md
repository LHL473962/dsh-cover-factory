# 改进清单（预期收益 ≥ 中，交给见微实现）

> 背景：插件流水线 fetch → separate → select → clean → dataset → train → convert → mix → qa → export。
> 目标：解决「换声后发闷 / 不像真人 / 唱法不对 / 高音不稳」，并保持两套混音标准（old/new）。
> 判定口径：预期收益 中 = 盲听能稳定听出；高 = 明显改善整体像真度。
> 已被数据否定的方向不要做（见文末）。

## A. 训练/数据层（收益最高）

### A1 训练前「去混响 + 去噪」链路 —— 收益：高
- 现状：separate 出来的 stem 直接进 dataset，残留混响/噪声被 vocoder 放大。
- 改动：
  - 新增可选 stage `clean`（已有空壳）或 separate 后处理：`dereverb_mel_band_roformer_anvuew` + `denoise_mel_band_roformer_aufr33_aggr`。
  - 配置项 `preclean: {dereverb: bool, denoise: bool, strength}`。
  - 只在训练侧做；目标曲不强制。
- 验收：同库同步数，训练素材 SNR 提升；出片谱平坦度上升（更接近原声 0.0196），且无「水声」。
- 涉及：`engine/stages/clean.py`、`engine/assets/commands.py`、`engine/config_default.json`。

### A2 好球曲加权 + 双声库 —— 收益：高（唱法问题）
- 现状：一个库把两种唱法平均；用户明确要求「活泼/高音用中高音唱法」。
- 改动：
  - `refs` 权重从固定 ×6 改为可配 `refWeights`（默认 6，可 0–10）。
  - 支持 `bank.modes = {low:[...], mid_high:[...]}`：训练时按模式分组建库（两个 pth），推理按目标曲中位音高/能量选库；或按句切换+交叉淡化。
  - job option `mode: auto|low|mid_high`。
- 验收：同一目标曲，`mid_high` 库在副歌段的音色更亮、咬字更积极；`auto` 不劣于单库。
- 涉及：`engine/stages/train.py`、`convert.py`、`lib/tools.js`、`client.js`。

### A3 48k 全链路（含采样率参数化） —— 收益：中
- 现状：训练 SR 写死 40k；分离器强制 44.1k。
- 改动：
  - `train.sampleRate: 40000|48000` → `SUIS_SR`；pretrained 自动选 48k。
  - separate 支持 `sample_rate` 透传，遇到 44.1k 模型要显式 WARN「升采样无真实高频」。
  - qa 记录实际 SR 与有效带宽。
- 验收：48k 库出片 8–20k 能量不低于 40k 库；文件/指标写入 report。
- 涉及：`engine/stages/train.py`、`separate.py`、`engine/vendor/applio/dsh_rvc2.py`。

### A4 转写/歌词驱动的哼唱与和声分离（可选替代） —— 收益：中
- 现状：humming auto 已有，但误判。
- 改动：允许传入 `.lrc` 或词表，用行区间替代 ASR；和声检测（左右声道相关性/频谱聚类）→ 单独 stem 或直接静音。
- 验收：有 lrc 的歌，保留比例稳定（5%–20%）；无 lrc 仍走 ASR。

## B. 换声/声码器层

### B1 神经修复后处理（Resemble-Enhance / AudioSR 类） —— 收益：中
- 现状：vocoder 平滑导致谱平坦度 0.003（原声 0.0196）、4k 以上 −3 dB。
- 改动：convert 输出后可选 `enhance: {model, strength}`，只对人声做；qa 对比修复前后 HF 能量与平坦度，防过冲。
- 验收：4–8k 相对能量提升 ≥1.5 dB，平坦度上升且不刺耳；A/B 盲听能听出「更清」。
- 涉及：`engine/stages/convert.py`、新 `engine/tools/enhance.py`。

### B2 换 RefineGAN vocoder（训练+推理） —— 收益：高
- 改动：训练配置加 `vocoder: hifigan|refinegan`；推理端加载对应生成器；bank meta 记 vocoder。
- 验收：同库同数据，RefineGAN 版 8–16k 能量与平坦度优于 HiFi-GAN；无金属感。
- 风险：与旧 .pth 不兼容 → 需版本标记与回退。

### B3 Applio 推理（crepe f0 + formant shift） —— 收益：中
- 改动：convert 支持 `engine: rvc|applio`，Applio 路径用 `run_infer_script`，暴露 `f0_method: crepe|rmvpe`、`formant_shift`。
- 验收：高音段破音/哑音减少；变调歌曲音色自然度提升。

## C. 混音层（低于上面的优先级）

### C1 细节补偿链（激励 + 气息 + 瞬态） —— 收益：中
- 现状：试过 aexciter 单点，无显著提升；需要组合。
- 改动：new 预设加入可选 `detail: {exciter, breath, transient}`，qa 卡削波与齿音。
- 验收：谱平坦度接近原声的 50% 以上，且不刺。

### C2 自动增益改为「感知响度对齐」 —— 收益：中
- 现状：active median 对暗音色偏保守，导致人声偏小。
- 改动：用短时 LUFS（vocal 活跃段）与伴奏对齐，目标差值可配；替代 RMS median。
- 验收：不同歌曲人声「突出度」一致（盲听排序稳定）。

## D. 评估与质量闸

### D1 客观指标入库 —— 收益：中
- 加 `qa.metrics`：谱平坦度、4–8k/8–16k 相对能量、vocal 浊音率、串音相关、SI-SDR（有参考时）。
- 出片 report 带这些数，用于回归对比（今天已能算）。

### D2 回归基线 —— 收益：中
- 固定 3 首歌（カタオモイ / LOA / 太陽）作为回归集；每次改训练或混音跑一遍，指标退化则 fail。

## 已否定，不要做
- **集成分离（多模型加权）**：实测 bs317 / Resurrection / kim 互相关 0.996，串音指标不变，收益 ≈0。
- **RVC 参数微调**（protect/index_rate/filter_radius）：差异 <15 Hz 质心、<0.2 dB 频段，听不出。
- **靠 EQ 提亮解决清晰度**：频谱质心已够，问题在平坦度/气声缺失。

## 建议排期
1. A1（去混响/去噪）→ 2. B1（神经修复）→ 3. A2（双声库）→ 4. B2（RefineGAN）→ 5. A3/A4 → 6. C/D


## 已交付（2026-11-01）
- `engine/tools/metrics.py`：LUFS/峰值/平坦度/频段/浊音/串音/比例
- `engine/tools/regression.py`：回归集运行与基线对比（阈值见脚本）
- `engine/tools/preclean.py`：训练前去混响+去噪（A1）
- `engine/tools/auto_gain.py --metric lufs`：感知响度口径（C2）
