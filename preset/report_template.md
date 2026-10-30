# 翻唱工厂 · 成品报告

> **{{singer}} - {{target_title}}**
> 生成时间：{{generated_at}} ｜ jobId：`{{job_id}}` ｜ 模板版本：v0.1

> 使用说明：把 `{{...}}` 全部替换成实际值；没有测到的写"未测"，不要留空，也不要编数。字段与 `docs/ACCEPTANCE.md` 对齐。

## 1. 任务信息

| 字段 | 值 |
| --- | --- |
| 歌手 | {{singer}} |
| 模式 | {{mode}}（import / auto） |
| 目标曲 | {{target_title}} |
| 目标曲文件 | {{target_file}} |
| 目标曲时长 | {{target_duration}} |
| 建 job 时间 | {{created_at}} |
| 完成时间 | {{finished_at}} |
| 工作目录 | {{workdir}} |
| 插件 / 引擎版本 | {{plugin_version}} / {{engine_version}} |

## 2. 素材与筛选

- 素材目录：{{source_folder}}
- 扫描到音频：{{source_count}} 个，总时长 {{source_total_duration}}
- 好球曲：{{refs_count}} 首（{{refs_list}}）
- 目标曲人声 stem：{{target_vocals_stem}}
- 目标曲伴奏 stem：{{target_instrumental_stem}}

| 曲名 | 时长 | 权重 | 中位音高 | 男声帧占比 | 立体声相关 | 结果 |
| --- | --- | --- | --- | --- | --- | --- |
| {{song_1}} | {{dur_1}} | {{weight_1}} | {{median_f0_1}} | {{male_ratio_1}} | {{corr_1}} | {{kept_1}} |
| {{song_2}} | {{dur_2}} | {{weight_2}} | {{median_f0_2}} | {{male_ratio_2}} | {{corr_2}} | {{kept_2}} |

**排除清单**：{{excluded_count}} 首

| 曲名 | 原因 | 依据 |
| --- | --- | --- |
| {{excluded_song_1}} | {{excluded_reason_1}} | {{excluded_evidence_1}} |

> 规则：低区 60–165Hz 与高区 165–500Hz 占比都 ≥20% 且中位比 >1.5（双峰）判对唱/合唱；与期望声部（voice=auto/female/male）不符的排除；时长 <60s 排除；立体声相关 <0.3 降权；参考曲按音色聚类加权 ×6。

## 3. 训练配置与步数

| 配置 | 值 |
| --- | --- |
| 模型 ID | {{model_id}} |
| 训练步数 | {{steps}} |
| 估算 epoch | {{epochs}}（75 步/epoch） |
| batch size | 8 |
| 采样率 | 40kHz |
| 预训练底模 | {{pretrain}} |
| GPU / 显存 | {{gpu}} / {{vram}} |
| 训练耗时 | {{train_elapsed}} |
| 断点续训 | {{resume_count}} 次 |
| 最终权重 | {{model_pth}} |
| 索引文件 | {{model_index}} |

## 4. 换声参数

| 参数 | 值 |
| --- | --- |
| f0 方法 | rmvpe |
| index_rate | 0.75 |
| protect | 0.33 |
| filter_radius | 3 |
| rms_mix_rate | 1.0 |
| resample_sr | 0 |
| 整首移调 shift | {{shift}} |
| 设备 | {{convert_device}} |

> shift 非 0 时，人声与伴奏同比例移动；不做只动人声（会跑调）。

## 5. 混音链

| 环节 | 设置 |
| --- | --- |
| 人声电平 | 0dB（stem 原始电平） |
| 人声滤波 | highpass 45Hz；260Hz −1.2dB；9kHz +1.6dB（{{presence_on}}） |
| 人声压缩 | 2:1，阈值 −18dB，attack 10ms / release 120ms |
| 空间 | 卷积混响 IR {{ir_name}}（1.2s），湿声 30%，返回加宽 1.8× |
| 伴奏电平 | 0dB（stem 原始电平） |
| 伴奏处理 | 2.8kHz 让位 −1dB，加宽 1.15× |
| 总线 | glue 1.3:1 → 静态等响 {{mix_lufs}} LUFS → limiter −1.5dB |

> 铁律：人声 / 伴奏都用 stem 原始电平（0dB/0dB），不得拉平。

## 6. 质检指标

| 指标 | 实测 | 合格线 | 判定 |
| --- | --- | --- | --- |
| preflight · 立体声 | {{channels}} | 立体声 | {{verdict_stereo}} |
| preflight · 时长 | {{duration}} | ≥30s | {{verdict_duration}} |
| preflight · 峰值 | {{peak_db}} | ≤−0.5 dBFS | {{verdict_peak}} |
| 响度 | {{lufs}} LUFS | −13 ± 1.5 | {{verdict_loudness}} |
| 断音率 | {{gap_per_min}} 次/分 | <12 | {{verdict_gap}} |
| 音域覆盖 | {{range_coverage}} | ≥70%（±6 半音） | {{verdict_range}} |
| 综合 | {{qa_ok}} | 全部通过 | {{verdict_all}} |

### 问题

- {{problem_1}}
- {{problem_2}}

### 建议

- {{suggestion_1}}
- {{suggestion_2}}

## 7. 结论与建议

- **结论**：{{conclusion}}（合格可直接用 / 不合格需修 / 需重训）
- **可用性**：{{usability}}
- **下一步**：{{next_action}}（加步数到 {{next_steps}} / 换素材 / 整首移调 shift={{suggested_shift}}）
- **限制说明**：v0.1 不承诺音色完美；不做和声生成与声部分离；未测项必须标"未测"。指标不合格不等于文件不能用，但发布前应知道差在哪。

## 8. 文件清单

| 文件 | 路径 | 说明 |
| --- | --- | --- |
| 成品 mp3 | {{output_mp3}} | 320k，已复制到桌面 |
| 本报告 | {{output_report}} | Markdown |
| 质检 JSON | {{qa_json}} | 机器可读指标 |
| 混音 wav | {{mix_wav}} | work/mix/out.wav |
| 换声人声 | {{convert_wav}} | work/convert/vocal.wav |
| 声库权重 | {{model_pth}} | work/model/<modelId>/model.pth |
| 索引文件 | {{model_index}} | 可选 |
| 任务状态 | {{job_json}} | 唯一状态源 |
| 日志目录 | {{logs_dir}} | 每阶段一份 |
| 训练集清单 | {{dataset_manifest}} | 含训练音域 p5/p95 |

---

本报告由翻唱工厂 v{{plugin_version}} 生成。数字以 job.json / qa/report.json 为准；模板见 `preset/report_template.md`。
