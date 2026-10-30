# 翻唱工厂 · 手工验收清单（v0.1）

> 验收对象：dsh-cover-factory v0.1。验收人：______ 日期：______
> 素材：优先用 `TESTS.md` 第 1 节的本机回归素材；没有就用你自己的一首独唱（≥60s）+ 一首目标曲（≥30s）+ 一首已知对唱样本。
> 原则：先验管线通不通，再验音质感受。所有 P0 必须通过；P1 允许带已知缺口，但必须登记。

## 0. 环境与设置

- [ ] 插件目录在 DSH profile 的 `node_modules/dsh-cover-factory`，重启后插件页已启用
- [ ] 八个工具可见：`cover_doctor / cover_setup / cover_run / cover_status / cover_cancel / cover_list / cover_export / cover_delete`
- [ ] `settings` 默认值正确：`defaultSteps=9000`、`allowDuet=false`、`autoDownload=false`、`outputDir` 指向桌面/翻唱工厂
- [ ] 引擎就位：`engine/run.py` 存在，Python 可用；GPU 显存 ≥8G（或已知会走 CPU 决策）

## A. 导入模式端到端（P0）

- [ ] `cover_setup({ singer, folder, target, steps: 9000 })` 返回 `ok:true`、`jobId`、`preview`、`eta`
- [ ] `cover_run({ jobId })` 立刻返回 `pid`，不阻塞对话
- [ ] `cover_status` 能看到 0→9 阶段推进，`currentStage` / `percent` 会变
- [ ] `qa` 阶段 done，`work/qa/report.json` 存在；合格/不合格都有明确结论
- [ ] `export` 阶段 done，桌面出现 mp3（时长与目标曲差 <2s，能正常播放）
- [ ] 全流程无未捕获异常；插件日志无 `[cover-factory] register failed`

**证据**：jobId ______；成品路径 ______；总耗时 ______

## B. 智选排除对唱（P0）

- [ ] 已知对唱样本放进素材目录，跑 `select` 后出现在排除清单，原因含"男声帧 >15%"（或等价表述）
- [ ] 同目录的独唱样本被保留，权重符合参考曲 ×6
- [ ] 排除后源文件仍在，mtime / 大小未变（插件只读）
- [ ] 设 `allowDuet=true`，`cover_run({ from: "select", resume: true })` 后对唱进入入选清单，报告能看到 allowDuet 生效
- [ ] 故意放一首 <60s 的曲子，确认被排除且原因可读

**证据**：`work/select/report.json` 截图/片段 ______

## C. 断点续跑（P0）

- [ ] train 跑到一半 `cover_cancel`（或直接 kill 引擎进程），`cover_status` 显示 cancelled（cancel）或进程已不在（kill），job.json 仍可读
- [ ] `cover_run({ jobId, resume: true })` 从 train 附近继续，**不重做 separate**
- [ ] separate / select 的日志 mtime 在续跑后未变（证明确实跳过）
- [ ] 已 done 的阶段没有产生重复产物或重复日志
- [ ] 人为制造一个 failed 阶段（如临时移走 ffmpeg），修好后 resume 能重试该阶段

**证据**：续跑前后 `logs/separate.log` 时间戳 ______

## D. 导出到桌面（P0）

- [ ] `cover_export({ jobId })` 返回 `ok:true` 和桌面路径
- [ ] 文件名符合 `<歌手>-<目标曲>.mp3`；同目录有 `-report.md`
- [ ] 第二次调用不重复转码、不产生 `(1)` 副本（默认覆盖同名文件）
- [ ] 模拟 ffmpeg 不可用：降级交付 wav，返回 `FFMPEG_MISSING` 或等价 warning，不报"成功 mp3"
- [ ] 歌手名/曲名含空格、中文、斜杠时，文件名被安全替换，不写到目录外

**证据**：两次 export 返回的路径/大小 ______

## E. 报告字段齐全（P0）

对照 `preset/report_template.md` 逐项检查：

- [ ] 任务信息：jobId、歌手、模式、目标曲、时长、时间
- [ ] 素材与筛选：目录、数量、refs、入选/排除清单及原因
- [ ] 训练配置与步数：模型 ID、steps、epoch、batch、采样率、GPU/显存、耗时
- [ ] 换声参数：rmvpe、index_rate 0.75、protect 0.33、filter_radius 3、rms 1.0、shift
- [ ] 混音链：0dB/0dB 铁律、滤波、压缩、IR、伴奏让位、glue、等响、limiter
- [ ] 质检指标：preflight（立体声/时长/峰值）、响度、断音率、音域覆盖，均有实测 + 合格线 + 判定
- [ ] 结论与建议：合格/不合格、下一步、限制说明
- [ ] 文件清单：mp3、报告、qa json、mix wav、convert wav、model.pth、job.json、日志
- [ ] 没有残留 `{{占位符}}`
- [ ] 不合格时 problems / suggestions 非空，建议可执行（加步数 / 换素材 / shift）

> 已知情况：v0.1 引擎自带的 `report.md` 只覆盖部分字段。缺项按模板补齐，并在下方"缺口登记"里记一条，**不要静默通过**。

## F. 失败提示可读（P0）

- [ ] 缺歌手 → `ok:false` + `MISSING_SINGER`，message 是中文、可操作
- [ ] 素材目录不存在 → `FOLDER_NOT_FOUND`；不是目录也给同样清晰的提示
- [ ] 目标曲路径错 → `TARGET_NOT_FOUND`，提示"要完整文件路径"
- [ ] steps=999999 → `BAD_STEPS`，提示范围 100–200000、推荐 9000
- [ ] 未开 autoDownload 却 `mode:"auto"` → `DOWNLOAD_NOT_ENABLED`，附带"改用 import"的合规建议
- [ ] 引擎缺失 → `ENGINE_NOT_READY`，说明"插件不用改，装好引擎即可"
- [ ] 所有 message 不含本机绝对路径（应脱敏为 `<path>`）
- [ ] 所有失败都返回 `ok:false`，不会把异常抛到对话/插件外

**证据**：错误返回原文（脱敏后）______

## G. 一致性与边界（P1）

- [ ] `cover_list` 能列出历史 job、进度、当前阶段、是否有成品
- [ ] 运行中重复 `cover_run` → `JOB_RUNNING`，不启动第二个进程
- [ ] `cover_cancel` 后进程树结束，job 标记 cancelled，仍可 resume
- [ ] 设置里 steps 超过 200000 被夹到 200000，不会写坏配置
- [ ] 断网/无代理下 import 模式仍能跑通（引擎不联网）
- [ ] 磁盘空间不足/目录被占用时，返回明确错误码，不产生半截 job

## 验收结论

| 项 | 结果（通过/不通过/带缺口） | 证据 | 备注 |
| --- | --- | --- | --- |
| A 导入模式端到端 |  |  |  |
| B 排除对唱 |  |  |  |
| C 断点续跑 |  |  |  |
| D 导出到桌面 |  |  |  |
| E 报告字段 |  |  |  |
| F 失败提示 |  |  |  |

**总体结论**：______（P0 全过才可发 v0.1）

**缺口登记**：

1. ______
2. ______

**验收人签字**：______  **日期**：______
