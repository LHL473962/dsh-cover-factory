# 翻唱工厂（dsh-cover-factory）设计文档 v0.1

## 目标
用户只提供三样东西：**歌手名 / 好球曲（音色参考）/ 目标曲**，插件端到端产出成品 mp3 + 报告，放在桌面。

## 两种素材模式
1. **导入模式**（默认、合规）：用户给文件夹，工程内只读。
2. **抓取模式**（需显式开启 + 风险自负警告）：按歌手名搜索并下载；插件不内置任何音源。

## 阶段（stage）
| 编号 | 名称 | 输入 | 输出 | 可续跑 |
|---|---|---|---|---|
| 0 | fetch | 歌手名/路径 | work/raw/*.wav | 是 |
| 1 | separate | raw | work/stems/<song>/{vocals,instrumental}.wav | 是 |
| 2 | select | stems | work/select/report.json | 是 |
| 3 | clean | 入选 vocals | work/clean/*.wav | 是 |
| 4 | dataset | clean + 参考曲权重 | work/dataset | 是 |
| 5 | train | dataset + steps | work/model/<id>/model.pth + index | 是（断点续训） |
| 6 | convert | model + 目标曲 vocals | work/convert/vocal.wav | 是 |
| 7 | mix | vocal + 目标曲 instrumental | work/mix/out.wav | 是 |
| 8 | qa | mix | work/qa/report.json | 是 |
| 9 | export | mix | 桌面/翻唱工厂/<歌手>-<目标曲>.mp3 + report.md | 是 |

## job.json（唯一状态源，原子写 tmp+rename）
字段：version / id / singer / mode / sources / refs / target / options / stages / result。
阶段状态取值：pending | running | done | failed | skipped | cancelled。
每个阶段完成后立刻写盘，记录 artifacts 与 log 路径。

## 引擎 CLI（插件只通过它调用）
- python engine/run.py init --config job_config.json --workdir <dir>
- python engine/run.py run --job <job.json> [--from stage] [--to stage] [--resume]
- python engine/run.py status --job <job.json>
- python engine/run.py cancel --job <job.json>
约定：退出码 0 成功；2 需要用户决策（硬件/版权确认）；1 失败。日志写 <workdir>/logs/<stage>.log。

## 插件工具（DSH tools，全部不抛异常）
| 工具 | 作用 |
|---|---|
| cover_setup | 创建 job（歌手/好球曲/目标曲/步数/模式），返回 jobId + 素材预筛结果 |
| cover_run | 后台启动 engine |
| cover_status | 各 stage 状态、进度、日志尾部 |
| cover_cancel | 取消 |
| cover_list | 历史 job |
| cover_export | 把成品复制到桌面并返回路径（幂等） |

## 智选素材规则（stage 2）
1. 逐帧 F0，统计中位音高与男声区（60–165Hz）帧占比；
2. 男声帧 > 15% → 判为对唱/合唱，默认排除（--allow-duet 可覆盖）；
3. 立体声相关 < 0.3 → 现场/伴唱，降权；
4. 时长 < 60s → 排除；
5. 参考曲按音色聚类（中位音高 + 频谱质心）加权 ×6，其余 ×1。

## 质检指标（stage 8）
| 指标 | 合格线 |
|---|---|
| preflight（stereo/时长/峰值） | 全过 |
| 响度 | −13 ± 1.5 LUFS |
| 断音率 | < 12 次/分 |
| 音域覆盖 | 目标曲 ≥70% 帧落在训练音域 ±6 半音内 |
不合格 → 报告标红 + 建议（加步数/换素材/整首移调）。

## 变调策略
- 默认 0；可选 shift=N：整首一起移（人声+伴奏同比例），并给出落甜区比例；
- 禁止只动人声（跑调），仅实验模式开放。

## 混音链（固化 2026-10-30 经验）
人声: highpass 45 → 260Hz −1.2dB → 9k +1.6dB（可选）→ 轻压缩 2:1
空间: 卷积混响 IR 1.2s，湿声 30%，返回加宽 1.8×
伴奏: 0dB 原比例 + 2.8k 让位 −1dB + 加宽 1.15×
总线: 1.3:1 glue → 静态等响 −14 LUFS → limiter −1.5dB
铁律：人声/伴奏都用 stem 原始电平（0dB/0dB），不得拉平。

## 硬件与耗时（RTX 5060 Laptop 实测）
| 步数 | 时长 | 显存 |
|---|---|---|
| 5000 | 约 30 分钟 | 7.5G |
| 9000 | 约 55 分钟 | 7.5G |
| 18000 | 约 1 小时 50 分 | 7.5G |
实测依据：rime7 145e≈17800 步 111 分钟、suis5 60e≈16000 步 95 分钟 → 约 0.37 秒/步。
无 8G 显存 → 提示 CPU（约 10× 慢）或减少步数。

## 硬件风险（训练期）
训练时显卡接近 100%、显存 7.5–8G；勿同开游戏/剪辑/渲染；插电源保散热；每 5 epoch 存盘，中断可 resume；磁盘预留 ≥10G。

## 目录
dsh-cover-factory/{package.json,index.js,cordis.patch.yml,lib/{tools.js,panel.js,engineClient.js},engine/{run.py,stages/*.py,assets/*.sh},preset/,DESIGN.md}

## 验收（v0.1）
- [ ] 本地三首歌素材跑通 0→9，产出 mp3 + report
- [ ] 断点续跑：kill 后 run --resume 从上次 stage 继续
- [ ] 智选能正确排除对唱（用已知对唱样本）
- [ ] 插件工具可被 DSH 调用，status 可见进度
- [ ] 成品自动出现在桌面，文件名含歌手/目标曲