# 优化发现与运维（2026-11-03）

> 来自 LOA / 明日世界 / 暮れ色 / 猫日 的实测，不是推测。

## 1. 和声会让 RVC 认错主唱

现象：目标曲有和声时，换声在“主唱/和声”之间来回切换。

数据（明日世界高音帧 f0>600Hz，与源主唱匹配率）：

| 分离方式 | 匹配主唱 | 掉低八度 |
|---|---:|---:|
| bs317 | 39.8% | 31.8% |
| kim | 41.1% | 31.0% |
| karaoke（主唱优先） | **61.4%** | **10.6%** |

做法：目标曲用 karaoke 模型分主唱；伴奏仍用 bs317 的完整伴奏。

## 2. 机械音的两个有效手段（可叠加）

- **protect 0.5**：保护清音/气声，不让模型硬编
- **混合输入**：主唱 75% + 原 bs 人声 25%，补回自然谐波

两首歌实测（粗糙度越小越不机械）：

| 版本 | 猫日 | 暮れ色 |
|---|---:|---:|
| 基线 | 0.159 | 0.175 |
| 融合（混合输入 + protect 0.5） | **0.129** | **0.144** |
| 再加 index 0（关检索） | 0.138 | 0.166（变差） |

**结论：融合“混合输入 + protect 0.5”有效；关索引（index 0）有害。**

## 3. f0 注入（外部音高曲线）

- 老 RVC 的 `pipeline.py` 有 `f0_file` 参数但只读不用；已在 `infer_cli.py` 暴露 `--f0_file`
- 直接把 karaoke 主唱的 f0 曲线喂给 bs317 内容：实测更差（暮れ色 >2 半音 17.3%→30.4%）
- 原因：内容来自 bs（含和声）与音高来自主唱不同源；需“内容与音高同源”或做融合
- 详见 `D:/VoiceData/exp/lead_guard/`（见微的报告）

## 4. 已排除的路线

- **Seed-VC**：实测不如 RVC，不作为上限对照
- **单纯提高采样率到 48k**：输出 20–24k 仅 −77 dB（源 −52 dB），解码器造不出高频
- **关掉检索索引**：两首歌都变差

## 5. 孤儿进程与 GPU 占用

典型现象：MSST 分离/换声进程异常退出后，子进程仍占 GPU。

清理脚本：`scripts/cleanup_gpu_orphans.ps1`

检测命令：

```powershell
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*inference.py*' -or $_.CommandLine -like '*dsh_rvc2*' }
```

注意：训练进程名是 `python.exe`，命令行里带 `dsh_rvc2.py train`；MSST 是 `inference.py`。

## 6. 已接入插件（0.2.1）

- `options.vocalStyle = "clean"`：separate 阶段对目标曲跑 karaoke 主唱分离，按 `separate.lead_ratio`（默认 0.75）与普通分离人声混合；convert 阶段自动 `protect=0.5`
- 备份：目标人声原版保留为 `vocals_bs317.wav`
- 工具参数：`cover_setup({ vocalStyle: "clean" })`；设置页有「人声风格」选项
- 端到端测试：`D:/VoiceData/exp/job_clean_test`（暮れ色，kim 素材 + clean）跑通

## 7. 待办

- 把“主唱优先 + 混合输入 + protect 0.5”做成插件可选参数
- 参数消融：filter_radius / is_half / 模型合并 / 训练侧 f0
- 见微的主唱引导需要用户给出“能听出切换”的具体时间点