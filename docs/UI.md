# 翻唱工厂 · 图形设置页（v0.2）

入口：DSH **设置 → 翻唱工厂**（由 `client.js` 通过 `slots.inject('settings.section')` 注册，section id `cover-factory-panel`，order 42）。

页面全部数据来自插件进程内的同源路由 `/api/cover-factory/*`；跨站 Origin 一律 403，请求体上限 64KB。

**输入框一律不设 placeholder**：示例与「留空等于什么」这类说明都放在输入框下方的小字（hint）里，标签（label）保留。


## 顶部状态条

| 元素 | 含义 |
|---|---|
| 绿点「依赖齐全」/ 黄点「缺可选依赖」/ 红点「缺必需依赖」/ 灰点「体检没跑成」 | 来自 `POST /doctor`（引擎 `run.py doctor`）。**点一下展开**：逐项 `✓/!/✗` 列出 7 项检查（Python / PyTorch+CUDA / ffmpeg / MSST / Applio / 旧版 RVC / yt-dlp）、缺失项和安装命令，并指向 `docs/INSTALL.md`。若引擎给了 `suggestedTorch`，这里还会显示 **「你的显卡：RTX 4060 Laptop（sm_89）→ 推荐 torch cu121（>=2.1,<2.6）」**、一行可复制的 `torchInstallCommand`（「复制安装命令」按钮，走剪贴板 API，失败提示手动选中），以及「约 2.5–3G；装错版本会跑不了」。引擎没给推荐时显示「引擎没给 torch 推荐（版本较旧）：按 INSTALL.md 的显卡矩阵自己选 cu 版本」 |
| 「重新体检」按钮 | 重新跑一次 `POST /doctor` 并刷新缓存（保存设置后也会自动重跑） |
| 黄点「N 个任务在跑」/ 灰点「当前没有任务在跑」 | 由 `plugin-run.json` + pid 存活判断，来自 `GET /jobs` 的 `running` |

## 卡片 1 · 设置

| 字段 | 控件 | 说明 |
|---|---|---|
| `engineDir` | 输入框 | 引擎目录；留空 = 插件自带 `engine/` |
| `python` | 输入框 | 解释器路径；留空 = 环境变量 `DSH_PYTHON` → PATH 的 `python` |
| `outputDir` | 输入框 | 成品输出目录，默认 桌面/翻唱工厂 |
| `defaultSteps` | 下拉 5000 / 9000 / 18000 | 默认训练步数，选项后面标了预计耗时（约 30 分钟 / 55 分钟 / 1 小时 50 分，RTX 5060 Laptop 实测）；下方提示「没有可用 GPU 会走 CPU，慢约 10 倍」 |
| `allowDuet` | 开关 | 允许对唱/合唱素材过预筛（引擎 `--allow-duet`）；默认关，双声部混杂（低/高区各 ≥20% 且中位比 >1.5）会被排除 |
| `autoDownload` | 开关 | 抓取模式的旧开关，v0.2 起**仅作记录**：真正放行抓取的是每次调用带 `copyrightAck` |

按钮：**保存设置** → `POST /api/cover-factory/settings`（只认白名单键，未知键回报 `ignored`）。
结果区显示「设置已保存」或具体失败原因（写盘被占用等）。

## 卡片 2 · 任务

5 秒轮询 `GET /api/cover-factory/jobs`（组件卸载时 `clearInterval`）。列：

| 列 | 内容 |
|---|---|
| 歌手 | `singer` |
| 目标曲 | 目标曲文件名（过长截断，悬停看全） |
| 状态 | 运行中 / 完成 / 失败 / 已取消 / 待续跑（颜色区分） |
| 进度 | `done·total`（阶段数） |
| 当前阶段 | 引擎正在跑的阶段，如 `train` |
| mp3 | 有 / 无（`export/*.mp3` 或引擎 `job.result.mp3` 存在） |
| 操作 | 见下 |

每行按钮：

| 按钮 | 端点 | 行为 |
|---|---|---|
| 运行 | `POST /jobs/run {jobId}` | 后台起引擎（非阻塞，立刻回 pid）；已在跑时返回 `JOB_RUNNING`，按钮同时置灰 |
| 续跑 | `POST /jobs/run {jobId, resume:true}` | 从最后 done 的阶段继续，不重做 separate/select；崩溃/关机后用这个（等价对话里的 `cover_run({ resume:true })`） |
| 取消 | `POST /jobs/cancel {jobId}` | 先让引擎 `cancel`，不行再结束进程树；没在跑时置灰 |
| 导出 | `POST /jobs/export {jobId}` | 复制成品到 `outputDir` 并返回路径；只有 wav 时用 ffmpeg 转 mp3，转不了就交付 wav |
| 删除 | `POST /jobs/delete {jobId}` | 二次确认后**软删除**：整个工作目录移到 `<workRoot>/_deleted/<jobId>-<ts>/`，可以手动移回找回。运行中的 job 会先被取消再删；已导出到桌面的成品不受影响 |

删除的二次确认文案（`window.confirm`）会说明：默认软删除、移到 `_deleted/`、可找回、桌面成品不删。物理删除（`purge`）不在 UI 上暴露，避免误点；需要时用对话里的 `cover_delete(jobId, mode:"purge")` 或直接调端点。

任何按钮的失败都会把 `{ok:false, error, message}` 原样显示在提示区（message 已脱敏）。

## 卡片 3 · 新建任务

| 字段 | 控件 | 说明 |
|---|---|---|
| 歌手名 | 输入框（必填） | 决定声库名与成品文件名 |
| 模式 | 下拉：导入目录 / 本机客户端已下载 / 联网抓取（需确认） | `import` / `local` / `auto`；**后续会加「链接导入」** |
| 扫描哪些本机客户端 | 勾选框组（**仅 local 显示**，默认全选） | 网易云 / QQ音乐 / 酷狗 / 酷我 → `clients: ["netease","qqmusic","kugou","kuwo"]`（`"qq"` 也认）；非法值由服务端 `BAD_CLIENT` 拦 |
| 关键词 | 输入框（**仅 local 显示**） | 默认=歌手名（留空即用 `singer`），按关键词匹配已下载歌曲名/文件名 → `keyword` |
| 目标歌手声部 | 下拉：自动 / 女声 / 男声（默认自动） | 提交时 `voice: auto\|female\|male` → `job_config.options.voice`。下方小字：「自动按目标曲人声判断；若素材被大量排除，请手动指定」。**男歌手建议直接选男声**，别让 auto 把男声素材误判成对唱 |
| 素材目录 | 输入框（**仅 import 显示**） | 素材文件夹，插件只读不删 |
| 目标曲路径 | 输入框 | 目标曲音频完整路径；不填只能训到 train |
| 训练步数 | 下拉 5000 / 9000 / 18000 | 与设置里的默认值独立；耗时文案同上（30 分钟 / 55 分钟 / 1 小时 50 分，RTX 5060 Laptop 实测），下拉旁固定提示「没有可用 GPU 会走 CPU，慢约 10 倍」 |
| 好球曲（选填） | 多行输入框（`textarea`，一行一个：歌名或路径） | 选填。**好球曲 = 最能代表你想听的那种音色的几首（建议 2–5 首）；留空则用素材目录里自动筛**。每行可填**歌名**（例：言っちゃいけないことばっか浮かぶよな）或**完整路径**（例：D:/.../xxx.wav），可混填。歌名先本地找，找不到走 `refSearch` 流程；路径写错报 `REF_NOT_FOUND`（带「第几行」）；最多 20 条（`REF_TOO_MANY`）；空行忽略。返回 `refs` 分类成 `{paths,names}` |
| 联网搜索好球曲 | 勾选框（好球曲输入框下方，默认不勾） | 「好球曲找不到时联网搜索（需要版权确认）」→ 提交时 `refSearch: true`；本地找不到的歌名会去 B 站搜并下载，仍失败就忽略。**必须同时勾版权确认**（import 模式下会额外提示「需要同时确认抓取风险」），否则返回 `needsConfirm`（reason `REF_SEARCH_NEEDS_ACK`）不建 job |
| 抓取确认 | 勾选框（**仅 auto 显示**） | 文案：会联网搜索并下载音频、可能触发平台风控、**版权风险自负** |
| 训练期间会发生什么（风险提示） | 折叠块（默认收起，标题色 #F5A524） | 展开后 8 条：显卡跑满/7.5–8G 显存/发热降频；别开游戏剪辑渲染（OOM）；插电源保持散热（过热关机风险）；别中途关机（每 5 epoch 存盘，可用「续跑」resume）；磁盘预留 ≥10G；**显存 <5G 会先停下让你选（减步数 / 加 --allow-lowmem），5–8G 只警告**；无可用 GPU 走 CPU 慢约 10 倍；**RTX 50 系必须用 torch cu128；30/40 系用 cu118/cu121，插件会按显卡自动推荐** |

local 模式下卡片会多一行小字：**「会自动扫描常见下载目录；ncm 自动解密；qmc / kgm 等加密格式需手动转换；只读取不修改原文件，不需要账号、不需要登录态。」**

按钮 **新建任务** → `POST /api/cover-factory/jobs/create`。

创建成功后，若预筛预览里有 `voiceGuess`（引擎逐首声部判断），结果区会直接列出来：`期望声部：female；目标曲判断：female` + 每首 `名字：声部（保留 / 排除：原因）`，方便一眼看出「这首为什么被排除」。素材还没分离完时预览只显示 `stems-pending`，等 `select` 跑完看 `select/report.json` 的 `voiceGuess` 即可。

`mode=local` 时预览还会带 `localFetch`：`{available, fileCount, formats:[{ext,count}], clients, keyword}`——扫描到多少个文件、各是什么格式一目了然；fetch 还没跑时为 `{available:false, reason:"fetch-pending"}`。

**依赖体检为红时，这个按钮会禁用**（标题提示「依赖体检是红的：先装依赖」），卡片里直接列出缺的必需项和安装命令（与状态条展开内容一致）；装完点顶部「重新体检」变绿即可建。服务端 `POST /jobs/create` 用同一条规则再拦一次（`DEPS_MISSING`），刷新页面也不会绕过去。

- import：直接建 job（校验同对话侧 `cover_setup`），返回 `jobId`。
- auto 且未勾选确认：返回 `{ok:true, needsConfirm:true, warnings:[…], next}`，**不建 job、不联网**，页面把三条风险提示和「确认后带 copyrightAck:true 再调用」显示出来。
- auto 且已勾选：等同 `copyrightAck:true`，建 job；引擎仍要求 `options.copyrightAck===true` 才抓。
- 建完**不会自动开跑**，需要去任务表点「运行」。

## 端点一览（全部同源、64KB 上限、错误统一 `{ok:false, error, message}`）

| 方法 | 路径 | 入参 | 复用逻辑 |
|---|---|---|---|
| GET | `/settings` | — | `{settings, engine:{ready,engineDir,message}, running}` |
| POST | `/settings` | 设置白名单键 | `saveSettings()` |
| GET | `/jobs` | — | `listJobs()` + 引擎状态 + 在跑数 |
| POST | `/doctor` | — | `runDoctorReport()` → 同 `cover_doctor`；结果进缓存，供状态条和建任务闸用 |
| POST | `/jobs/create` | `singer, folder, target, steps, mode, copyrightAck, refs[], refSearch` | `createJob()` → 同 `cover_setup`；**体检 red 且未过期时先回 `DEPS_MISSING`** |
| POST | `/jobs/run` | `jobId, from?, to?, resume?` | `runJob()` → 同 `cover_run` |
| POST | `/jobs/cancel` | `jobId` | `cancelJobRun()` → 同 `cover_cancel` |
| POST | `/jobs/export` | `jobId, overwrite?` | `exportJob()` → 同 `cover_export` |
| POST | `/jobs/delete` | `jobId, purge?` | `deleteJob()` → 同 `cover_delete`（`purge:true` ⇒ `mode:"purge"`） |

**不绕过校验**：这条链走的是 `lib/tools.js` 里注册给 DSH 的那同一批 `execute`（`runTool()` → 守卫后的实现），所以错误码、脱敏、参数范围（steps/shift/limit）与对话侧完全一致。

## v0.2 已知边界

- 没有逐阶段时间线/日志查看：需要日志时用对话里的 `cover_status`。
- 任务表只列最近 20 个 job（按目录 mtime），更多用对话里的 `cover_list`。
- 删除只做软删（移到 `_deleted/`）；物理清理由对话侧 `cover_delete(mode:"purge")` 或手动删目录。
- `_deleted/` 里的东西不会出现在任务表（`listJobs` 跳过 `_` 开头的目录），要找回就把它移回 `jobs/`。
- 设置页半端只读 `settings.section`，不提供独立路由页面。
