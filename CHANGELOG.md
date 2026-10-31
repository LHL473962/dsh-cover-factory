# 更新日志

## 0.2.0（2026-11-01）

### 新增
- **两套混音标准**：`mixPreset: old | new`（bank 的分离器自动选择）
  - old：原比例、默认混响链、-14 LUFS 归一化
  - new：活跃帧等响基准 + vocalDb、EQ、饱和、混响 0.10、不归一化、峰值保护
- **哼唱段处理**：`humming: auto | keep | replace | mute | manual`
  - auto：faster-whisper 无词区间保留原声（1.2s 淡化 + 电平匹配 + 质量闸）
  - manual：`humming_editor.html` 标注 → JSON
- **自动增益**：活跃帧中位 / LUFS 两种口径（`auto_gain.py`）
- **强制混音工具**：`manual_mix.py`（stem 检查 + 比例校验 + 两套预设）
- **质量工具**：`metrics.py`、`regression.py`（回归集基线对比）
- **训练前去混响/去噪**：`preclean.py`（A1）
- **内容向量校验**：size + sha256 + hf-mirror 回退 + 断点续传

### 修复
- **export 硬闸**：qa 不合格时拒绝导出（`EXPORT_BLOCKED_QA`），`export.allow_failed` 可放行
- 人声/伴奏比例硬检查（偏差 >0.5 dB 失败）
- 哼唱自动识别误判（无歌词段按人声活动 + 音高门控）

### 变更
- 默认仍是 old 预设；新库（bs317 训练）自动走 new
- doctor 增加 mixPresets / humming_asr 提示

## 0.1.11
- 基线版本：fetch/separate/select/clean/dataset/train/convert/mix/qa/export 全流程
