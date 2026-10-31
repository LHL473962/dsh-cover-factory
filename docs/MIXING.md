# 调音（混音）经验：为什么这么调

这份文档记录踩过坑之后的定论，代码里的参数都能在这里找到理由。改参数前先读。

## 1. 人声/伴奏比例：old = unity；new = active +3

**old 预设**（默认 / kim 库）：分离出来的两条 stem 相加 ≡ 原曲，两条都保持 0 dB，不做"把人声拉平到伴奏"。
**new 预设**（bs317 库）：按活跃帧把「人声=伴奏」对齐，再固定 +3 dB；这是刻意的新标准，不是把 old 的比例改坏。

- 曾经的做法（把人声 RMS 拉到与伴奏相等）会让比例完全走样：人声被抬高 6–10 dB。
- 判断方法：vocal + instrumental 与原始混音做相关，应接近 1.0000（实测 1.0000）。
- 对比实验例外：A/B 两个声库对比时，要把两边人声 RMS 对齐到同一比例（例如相对伴奏 -7.0 dB），否则听感差异来自音量而不是音色。

## 2. 人声处理链（顺序不能乱）

| 步骤 | 参数 | 理由 |
|---|---|---|
| 高通 | 45 Hz | 去掉换声带来的低频隆隆声，不切到基频 |
| 去浑浊 | 260 Hz, Q=1.4, -1.2 dB | 换声模型常在中低频堆积，鼻音/闷 |
| 空气感 | 9 kHz, Q=1.0, +1.6 dB | RVC 换声高频偏暗，补一点齿音/气息 |
| 压缩 | 阈值 -18 dB，比 2.0:1，attack 10 ms，release 120 ms | 压掉字头抖动，保持动态 |
| 混响 | IR 卷积，湿 30%（干 70%） | 让干声有房间感，不糊 |
| 加宽 | extrastereo m=1.8 | 人声居中偏宽，避免"一条线" |

不要做的：门限（gate）、afftdn 降噪、动态响度归一化——都会吃字头/断音，之前试过全部回退。

## 3. 伴奏处理

| 步骤 | 参数 | 理由 |
|---|---|---|
| 让位 | 2.8 kHz, Q=1.2, -1.0 dB | 给人声让出齿音频段 |
| 加宽 | extrastereo m=1.15 | 轻微展宽，不抢人声 |

## 4. 总线与响度

| 项 | 值 | 说明 |
|---|---|---|
| glue 压缩 | 阈值 -15 dB，比 1.3:1，attack 20 ms，release 250 ms | 把人声与伴奏粘在一起 |
| 目标响度 | -14 LUFS（静态增益，不做 loudnorm） | 流媒体通用值 |
| 限幅 | alimiter limit=-1.5dB:level=false:attack=0.1 | 必须 level=false，否则 ffmpeg 会把信号拉回 0 dBFS |
| 峰值复检 | 编码后 finalPeak <= -1.0 dBFS，超了自动降 1 dB 重编（最多 2 次） | 防削波 |

踩过的坑：alimiter 默认 level=true，限到 -1.5 后又被拉回 0，成品峰值 +0.03 dBFS（削波）。这个坑在引擎和独立脚本里各犯过一次。

## 5. 相位

限幅器有 lookahead（约 4.5 ms / 200 采样），0 延迟算相关会出现假负值。
正确做法：在 ±300 采样内找最佳对齐再判极性；只有最佳相关仍为负才整体反相。
实测：混音 vs 伴奏 0 延迟 -0.47，最佳对齐（lag=200）+0.75 → 极性正常。

## 6. 质检红线（qa 阶段）

| 指标 | 红线 | 备注 |
|---|---|---|
| 立体声 | 必须 stereo | 单声道判失败 |
| 时长 | >= 30 s | |
| 峰值 | <= -0.5 dBFS（成品再压到 -1.0） | |
| 响度 | -13 ± 1.5 LUFS（最终 -14） | |
| 断音率 | < 12 次/分 | 20 ms 能量坑判据 |
| 音域覆盖 | >= 70% | 目标曲人声落在训练音域 ±6 半音 |

## 7. 整首移调

shift 同时作用于人声与伴奏（伴奏用 asetrate + atempo 补偿），禁止只动人声——那会让人声与伴奏调性错位。

## 8. 换声参数

- 旧版 RVC：f0_method=rmvpe，index_rate 默认，protect 0.33–0.5。
- 换声输入必须是"已去过伴奏残留"的人声 stem；直接拿带乐器的 Vocals 去换声，会把乐器"唱"成人声。

## 9. 文件与复现

- IR：engine/assets/room_ir.wav（约 21 万字节，随仓库分发）
- 参数默认值：engine/config_default.json 的 mix 段
- 独立脚本版：engine/assets/mix_unity.sh（与引擎 filter graph 同源）


## 自动增益（每首歌单独判断）

不同歌的原始人声/伴奏电平差不一样（Yours 比伴奏低 9.3 dB，Spica 低 4.0 dB），所以**不能固定增益**。

做法：取“人声活跃帧中位电平”（50 ms 窗，剔除 -45 dB 以下静音）与“伴奏活跃帧中位电平”，
把两者的差对齐到目标值（默认人声比伴奏低 4 dB）：

```
gain = (instActiveDb + targetDb) - vocalActiveDb
```

- CLI：`python engine/tools/auto_gain.py --vocal X --inst Y --target-db -4`
- 手工混音：`manual_mix.py --auto-gain --target-db -4`
- 网页：每轨“自动增益”按钮 + 总线“人声目标”滑杆

> 引擎 new 预设使用同一套 active 口径，但固定 `targetDb=0`（人声=伴奏）后再 `vocalDb=+3`，即 `ratioActive=+3dB`；
> old 预设不走 auto-gain，保持 unity 原比例。


## 哼唱段保留原声（humming）

默认 `auto`：faster-whisper small 转写原人声 stem（ja 优先，失败 None；CPU int8），词区间 ±0.15s，Hann 1.2s + `scipy.signal.fftconvolve` 平滑；keep 段用 rms 比例对齐；`keptRatio` 3%–35% 为合格。
不合格先走 F0 置信度（<0.12 视为哼唱），仍不合格降级全换声 + WARN，不 fail。

`manual`：`job.options.hummingSegments` JSON `{segments:[{a,b,type:'keep'|'mute'|'conv'}]}`，20ms 斜坡；缺 JSON 退化为全换声并 WARN。

注意：哼唱段用原声是为了避免 RVC 在无歌词段崩/断；不改变混音比例规则（old/new 仍按各自标准）。
## 两套混音标准（旧库 / 新库）

| 项 | old（kim 声库） | new（bs317 声库） |
|---|---|---|
| 人声基准 | 原曲比例（目标 vocals 对齐） | 活跃帧等响，人声 = 伴奏 |
| ratio 目标 | unity（ratio_src） | **ratioActive = options.vocalDb**（默认 0；要 +3 就设 3） |
| 额外人声增益 | 0 dB | **vocalDb** 每首可调（默认 0；targetDb 固定 0） |
| EQ | 260 −1.2 / 9k +1.6 | 120 −2 / 800 −2 / 3k +3 / 9k −2 |
| 饱和 | 无 | tanh 软削波 threshold **0.5** |
| 混响 | IR 湿 0.30 | IR 湿 0.10 / 干 0.90 |
| 加宽 | 人声 1.8 / 伴奏 1.15 | 不加宽 |
| 总线压缩 | glue 1.3:1 | 关闭（ratio 1.0） |
| 响度 | 归一化 −14 LUFS | **不归一化**，只 limiter −1dB 保峰值 |
| qa | −13±1.5 LUFS + 比例 | 不卡响度，只卡峰值 ≤−1 和削波 |
| 适用 | 旧库（kim 素材训练） | 新库（bs317 素材训练） |

**切换方式**：job 里 `options.mixPreset: "old" | "new"`（显式优先）；不写时读 bank meta 的训练分离器，bs317→new，kim→old。
