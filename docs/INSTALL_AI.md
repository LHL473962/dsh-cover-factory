# 翻唱工厂 · AI 安装协议（docs/INSTALL_AI.md）

> **给人类的一句话**：把**仓库地址**和**这份文档**一起丢给 AI 就行——前提是这个 AI 能执行 shell 命令、能访问网络（git / PyPI / HuggingFace）。**纯聊天型、没有 shell 或网络权限的 AI 做不到**，请让它转人工，或你自己按 [`docs/INSTALL.md`](INSTALL.md) 装。
>
> 想装**任意** DSH 插件（不只翻唱工厂）？用通用协议 [`INSTALL_ANY_PLUGIN.md`](INSTALL_ANY_PLUGIN.md) + [`scripts/install-plugin.ps1`](../scripts/install-plugin.ps1)。
>
> 本文是给 AI 执行的**协议**：每一步都有命令、**验证门**（不过不许往下走）和失败处理。所有路径/URL 都是变量，先在第 0 步确定再执行。

## 0. 变量与前置检查

先确定这些变量（下文用 `<>` 表示）：

| 变量 | 含义 | 示例（Windows） |
| --- | --- | --- |
| `REPO_URL` | 本仓库地址 | `https://github.com/<org>/dsh-cover-factory.git` |
| `DSH_HOME` | DSH 家目录 | `$env:USERPROFILE\.dsh` |
| `PROFILE` | 目标 profile 目录 | `$env:USERPROFILE\.dsh\profiles\desktop` |
| `PLUGIN_DIR` | 插件安装目录（必须在 profile 的 node_modules 下） | `<PROFILE>\node_modules\dsh-cover-factory` |
| `PY` | 跑引擎的 Python（3.10–3.12） | 兼容 Python 均可，先 `python --version` 确认 |

**逐项检查（每项都要过；失败就按错误码停，不要猜）**：

```powershell
# 0.1 OS：Windows 10/11
[System.Environment]::OSVersion.Version.Build   # 期望 >= 19041
# 0.2 PowerShell：5.1+ 或 pwsh 7+
$PSVersionTable.PSVersion
# 0.3 git
git --version
# 0.4 网络（三个域，至少 git 与 pytorch 必须通；huggingface 不通只影响下模型）
foreach ($u in 'https://github.com','https://download.pytorch.org/whl/','https://huggingface.co') {
  try { $r = Invoke-WebRequest -Uri $u -Method Head -TimeoutSec 15 -UseBasicParsing; "OK  $u $($r.StatusCode)" }
  catch { "FAIL $u" }
}
# 0.5 磁盘：profile 所在盘剩余 >= 15GB
Get-PSDrive -PSProvider FileSystem | Select-Object Name,@{n='FreeGB';e={[math]::Round($_.Free/1GB,1)}}
# 0.6 NVIDIA GPU / 空闲显存（没有独显不算失败，走 CPU，但训练慢约 10 倍）
nvidia-smi --query-gpu=name,memory.total,memory.free --format=csv,noheader
```

| 检查 | 通过条件 | 失败 |
| --- | --- | --- |
| OS | Windows，Build ≥ 19041 | `E_OS` |
| PowerShell | 5.1+ 可执行 | `E_NO_PWSH` |
| git | `git --version` 退出码 0 | `E_NO_GIT` |
| 网络 github | HEAD 2xx/3xx | `E_NO_NET_GITHUB` |
| 网络 pytorch | HEAD 2xx/3xx | `E_NO_NET_PYTORCH` |
| 网络 HuggingFace | 2xx/3xx | `E_NO_NET_HF`（**警告**：模型下载会失败，可继续） |
| 磁盘 | 剩余 ≥ 15GB | `E_LOW_DISK` |
| GPU | `nvidia-smi` 成功 | `E_NO_NVIDIA`（**警告**：CPU 训练慢约 10 倍；空闲显存 <5G 时任务会停在 `LOW_VRAM`） |

## 1. 获取仓库 → 放进 profile 的 node_modules

```powershell
New-Item -ItemType Directory -Force -Path (Split-Path $PROFILE) | Out-Null
if (Test-Path $PLUGIN_DIR\.git) {
  git -C $PLUGIN_DIR pull --ff-only
} else {
  git clone --depth 1 $REPO_URL $PLUGIN_DIR
}
```

**验证门 G1**：`$PLUGIN_DIR\package.json` 存在，且 `name` 为 `dsh-cover-factory`；`$PLUGIN_DIR\index.js`、`$PLUGIN_DIR\lib\tools.js`、`$PLUGIN_DIR\engine\run.py` 都存在。

```powershell
node -e "const p=require(process.argv[1]);if(p.name!=='dsh-cover-factory')process.exit(1);console.log('G1 ok')" "$PLUGIN_DIR\package.json"
```

失败：`E_CLONE_FAILED`（git 报错/目录被占用/没有权限）。重试一次；仍失败就停，不要手工拼目录。

## 2. 注册插件（幂等）

编辑 **`<PROFILE>\package.json`**（先备份成 `package.json.bak-cover-factory`）：

1. `dependencies` 里加（没有才加，已有就更新）：`"dsh-cover-factory": "link:<PLUGIN_DIR>"`（路径用正斜杠或双反斜杠）。
2. `dsh.profile.bundles` 数组里**追加** `"dsh-cover-factory"`（已存在就**不要重复加**）。

结构示例（只列相关字段）：

```json
{
  "name": "dsh-profile-desktop",
  "private": true,
  "dependencies": {
    "dsh-cover-factory": "link:<DATA_DIR>/house/plugins/dsh-cover-factory"
  },
  "dsh": {
    "profile": {
      "bundles": [
        "@deepseek-ai/dsh-base",
        "@deepseek-ai/dsh-web-app",
        "dsh-cover-factory"
      ]
    }
  }
}
```

**验证门 G2**：`package.json` 仍是合法 JSON；上面两处都存在；`<PROFILE>\node_modules\dsh-cover-factory` 可访问（link 生效）。可用官方 CLI 装依赖项：

```powershell
dsh plugin --profile desktop add "link:$PLUGIN_DIR"   # 等价 pnpm add；bundles 仍需手工加一行
node -e "const p=require(process.argv[1]);const b=p.dsh?.profile?.bundles||[];if(!b.includes('dsh-cover-factory'))process.exit(1);console.log('G2 ok')" "<PROFILE>\package.json"
```

失败：`E_PROFILE_NOT_FOUND`（profile 目录不对，先 `dsh --profile desktop --dump-config` 确认）、`E_PROFILE_JSON`（JSON 写坏 → 从 `.bak-cover-factory` 恢复后重来）。

## 3. 安装依赖

**首选**（仓库自带一键脚本）：

    # 在仓库根目录；AI 直接执行这一条
    powershell -ExecutionPolicy Bypass -File scripts/install.ps1 -Yes

    # 已经安装过插件、只想补依赖/模型：
    powershell -ExecutionPolicy Bypass -File scripts/install.ps1 -ProfilePath "<PROFILE>" -Yes

脚本步骤（可重复执行，第二次全部 skipped）：
1. 找 DSH profile（-ProfilePath 优先；否则 DSH_PROFILE_PATH/DSH_PROFILE/desktop/web/headless）；
2. 把仓库 clone/pull 到 <PROFILE>\node_modules\dsh-cover-factory；
3. 幂等追加 dsh.profile.bundles（已有不重复）；
4. 执行 engine/setup/bootstrap.py --install --with-models --yes：依赖 + MSST 代码 + MelBand kim + hubert_base + rmvpe，
   已存在则跳过、断点续传、失败不中断，stdout 只输出 {ok, steps, nextSteps} JSON；
5. 打印 RESTART DSH 与 doctor 验证命令。

参数：-SkipDeps（只装插件，不动依赖/模型）、-RepoPath、-PythonPath。
失败时输出 ERROR [E_*] + 处理建议；错误码：E_PROFILE_NOT_FOUND(10)、E_PROFILE_INVALID(11)、
E_GIT_CLONE_FAILED(12)、E_GIT_PULL_FAILED(13)、E_REPO_NOT_FOUND(14)、E_BOOTSTRAP_FAILED(15)、
E_PYTHON_NOT_FOUND(16)、E_REPO_INCOMPLETE(14)。

**验证门 G3**：python engine/run.py setup --install --with-models --yes 的 JSON 里 ok=true 且步骤无 failed；
然后 python engine/run.py doctor 必须 ok=true（全绿）才算装好。缺模型时 steps[].detail 会带手动 URL。
输出示例（stdout 只有这一段 JSON，进度在 stderr）：

    {
      "ok": true,
      "steps": [
        {"name": "msst_repo", "status": "skipped", "detail": "已存在：...", "path": "..."},
        {"name": "melband_kim", "status": "skipped", "detail": "已存在（870.8 MB）：https://...", "path": "..."}
      ],
      "nextSteps": ["运行 python engine/run.py doctor：全绿（ok=true）才算安装成功"]
    }

status 只会是 done / skipped / failed；某步失败不影响其它步，detail 里带手动下载 URL。

失败与下一步：

| 现象 | 错误码 | 下一步 |
| --- | --- | --- |
| 脚本不存在 | `E_NO_INSTALL_SCRIPT` | 走附录 A，不要停 |
| pip 下载失败 / 超时 | `E_NO_NETWORK` | 换镜像（`PIP_INDEX_URL`）后重试；或只装 CPU 版 torch 先跑通 |
| 磁盘不足 | `E_LOW_DISK` | 清理到 ≥15GB（torch 约 2.5–3G、模型 5–10G） |
| torch 装错 CUDA | `E_TORCH_MISMATCH` | `python engine/run.py setup --install-torch --yes`（按显卡自动选 cu128/cu121/cu118） |
| 没有 NVIDIA 驱动 | `E_NO_NVIDIA` | 先装驱动；或接受 CPU（慢约 10 倍） |

## 4. 验证门 A（重启前，CLI 级，**必须全过**）

### G4.1 依赖体检：引擎 doctor 必须 exit 0 且 JSON `ok:true`

```powershell
& $PY "$PLUGIN_DIR\engine\run.py" doctor; $LASTEXITCODE   # 期望 0
& $PY "$PLUGIN_DIR\engine\run.py" doctor | Out-File -Encoding utf8 "$env:TEMP\cover-doctor.json"
$d = Get-Content "$env:TEMP\cover-doctor.json" -Raw | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $d.ok -ne $true) { throw 'G4.1 FAIL' }
$d.checks | Where-Object { -not $_.ok } | Format-Table name,found,hint
```

- 缺 **yt-dlp / ffmpeg / 旧版 RVC / fairseq / Applio / torch**：优先 `python engine/run.py setup --install --with-models --yes`（会按顺序补）。
- 缺 Applio → `setup --install-applio --python <Python 3.11> --yes`（实测 8.1G / 约 11 分钟，预留 10G；Python 3.10 会被版本门立即拒绝）。
- 缺 fairseq → `setup --install-fairseq --yes`（预编译 MIT 包，装到 paths.rvc_python）。
- 缺 MSST 权重 → `setup --with-models --yes`；仍失败再按 [`docs/INSTALL.md`](INSTALL.md) 路径 B 手动补。
- 还不行就停：`E_DOCTOR_RED` / `E_DOCTOR_EXIT`。**不要改 `engine/` 里的代码绕过体检**。

### G4.2 测试 job：`steps=300`、`import`、无 `target`，跑完 `fetch`

```powershell
$T = Join-Path $env:TEMP ("cover-probe-" + [guid]::NewGuid().ToString('N').Substring(0,8))
New-Item -ItemType Directory -Force -Path "$T\src" | Out-Null
# 生成 65 秒测试音频（fetch 要求单曲 ≥60s）。用 Python 标准库，不依赖 ffmpeg 在 PATH 里
@'
import sys, wave, math, array
p = sys.argv[1]
w = wave.open(p, 'wb'); w.setnchannels(1); w.setsampwidth(2); w.setframerate(44100)
n = 44100 * 65
buf = array.array('h', (int(12000 * math.sin(2 * math.pi * 220 * i / 44100)) for i in range(n)))
w.writeframes(buf.tobytes()); w.close()
print('wrote', p)
'@ | & $PY - "$T\src\probe.wav"

# 建测试 job（等价 cover_setup：import + 无 target + steps=300）
$cfg = @{
  version = 1
  singer = 'install-probe'
  mode = 'import'
  sources = @{ folder = "$T\src" }
  refs = @()
  target = $null
  options = @{ steps = 300; shift = 0; voice = 'auto' }
  output = @{ dir = "$T\out" }
  qa = @{ minGapRate = 12; minLoudness = -14.5; maxLoudness = -11.5 }
}
$cfg | ConvertTo-Json -Depth 6 | Out-File -Encoding utf8 "$T\job_config.json"
& $PY "$PLUGIN_DIR\engine\run.py" init --config "$T\job_config.json" --workdir "$T\job"
& $PY "$PLUGIN_DIR\engine\run.py" run --job "$T\job\job.json" --from fetch --to fetch
$LASTEXITCODE   # 期望 0

# 断言 fetch=done
$j = Get-Content "$T\job\job.json" -Raw | ConvertFrom-Json
if ($j.stages.fetch.status -ne 'done') { throw "G4.2 FAIL: fetch=$($j.stages.fetch.status)" }
```

**G4.2 通过条件**：`run` 退出码 0 且 `job.json` 的 `stages.fetch.status == 'done'`（本仓库作者在 RTX 5060 Laptop 上按这段命令实测通过：init 0 / run 0 / fetch=done）。
失败：`E_TEST_JOB_FAILED`（看 `$T\job\logs\*.log`；fetch 失败常见原因：文件夹里没有 ≥60s 音频、ffmpeg 不可用、路径含中文引号没转义）。
测完可删 `$T`（这是临时目录，不碰用户素材）。

## 5. 重启 DSH 并确认设置页

1. **完全退出** DSH（桌面版关窗口 + 托盘退出），再启动；或命令行 `dsh --profile desktop` 重新起。
2. 打开 GUI → **设置 → 翻唱工厂** 应该出现。

**验证门 G5（机器可验）**：DSH 起来后，面板接口能通、且体检绿。

```powershell
# 把 <DSH_BASE_URL> 换成本次 DSH 的地址（如 http://127.0.0.1:60984）
$base = '<DSH_BASE_URL>'
$s = Invoke-RestMethod -Uri "$base/api/cover-factory/settings" -TimeoutSec 20
if (-not $s.ok) { throw 'G5 settings FAIL' }
$doc = Invoke-RestMethod -Uri "$base/api/cover-factory/doctor" -Method Post -Body '{}' -ContentType 'application/json' -TimeoutSec 180
if ($doc.health -ne 'green') { throw "G5 doctor health=$($doc.health) missing=$($doc.missingRequired -join ',')" }
```

**G5 通过条件**：`/settings` 返回 `ok:true`；`POST /doctor` 返回 `health=green`。设置页里看到「翻唱工厂」即为人工确认（可选）。
失败：`E_RESTART_TIMEOUT`（DSH 没起来/端口不对）、`E_DOCTOR_RED`（回到第 4 步）。

## 6. 失败回滚

```powershell
# 1) 移除插件目录（只删这一份，不动其他插件）
Remove-Item -Recurse -Force "$PLUGIN_DIR"
# 2) 从 profile 的 bundles / dependencies 移除 dsh-cover-factory
$p = Get-Content "<PROFILE>\package.json" -Raw | ConvertFrom-Json
$p.dsh.profile.bundles = @($p.dsh.profile.bundles | Where-Object { $_ -ne 'dsh-cover-factory' })
$p.dependencies.PSObject.Properties.Remove('dsh-cover-factory')
$p | ConvertTo-Json -Depth 20 | Out-File -Encoding utf8 "<PROFILE>\package.json"
```

回滚**不会**动：

- 用户自己的素材目录（插件对素材只读）；
- 已装的模型/环境（Applio / MSST / RVC / torch，都在插件目录之外）；
- 历史任务数据 `~/.dsh/cover-factory/jobs/`（想清就自己删，包含 `_deleted/` 回收站）；
- `~/.dsh/cover-factory-settings.json`（需要时手动删）。

如果 profile 的 `package.json` 被写坏：用第 2 步备份的 `package.json.bak-cover-factory` 覆盖回去。

## 附录 A：没有 `scripts/install.ps1` 时的等价命令

```powershell
# A1 依赖体检（先看缺什么）
& $PY "$PLUGIN_DIR\engine\run.py" doctor
# A2 一键链：torch -> Applio -> 旧版 RVC -> RVC 运行依赖 -> fairseq -> MSST/权重 -> yt-dlp/ffmpeg
& $PY "$PLUGIN_DIR\engine\run.py" setup --install --with-models --yes
# A3 单装 torch（先 dry-run 看命令）
& $PY "$PLUGIN_DIR\engine\run.py" setup --install-torch --dry-run
& $PY "$PLUGIN_DIR\engine\run.py" setup --install-torch --yes
# A4 Applio（需要 Python 3.11+）/ RVC 运行依赖 / fairseq（旧版 RVC）
& $PY "$PLUGIN_DIR\engine\run.py" setup --install-applio --python <Python 3.11> --yes
& $PY "$PLUGIN_DIR\engine\run.py" setup --install-rvc-deps --yes
& $PY "$PLUGIN_DIR\engine\run.py" setup --install-fairseq --yes
# A5 复查
& $PY "$PLUGIN_DIR\engine\run.py" doctor
```

## 附录 B：错误码总表

| 错误码 | 含义 | 能否继续 | 下一步 |
| --- | --- | --- | --- |
| `E_OS` | 不是 Windows 10/11 | 否 | 换机器 |
| `E_NO_PWSH` | 没有 PowerShell | 否 | 装 PowerShell 5.1+/pwsh 7 |
| `E_NO_GIT` | 没有 git | 否 | 装 git 后重跑第 1 步 |
| `E_NO_NET_GITHUB` / `E_NO_NET_PYTORCH` | 访问不了 github / pytorch | 否 | 配代理/镜像后重试 |
| `E_NO_NET_HF` | 访问不了 HuggingFace | 是（警告） | 配镜像；否则模型权重下不来 |
| `E_LOW_DISK` | 磁盘 < 15GB | 否 | 清盘 |
| `E_NO_NVIDIA` | 没有可用 NVIDIA GPU | 是（警告） | CPU 训练（约 10 倍慢）；或装驱动 |
| `E_CLONE_FAILED` | git clone/pull 失败 | 否 | 查权限/占用/地址 |
| `E_PROFILE_NOT_FOUND` | profile 目录不对 | 否 | `dsh --profile desktop --dump-config` 确认 |
| `E_PROFILE_JSON` | profile package.json 坏了 | 否 | 用 `.bak-cover-factory` 恢复 |
| `E_NO_INSTALL_SCRIPT` | 仓库没有 install.ps1 | 是 | 走附录 A |
| `E_NO_NETWORK` | pip/模型下载失败 | 视情况 | 换镜像/重试；先 CPU 版跑通 |
| `E_TORCH_MISMATCH` | torch 与显卡不匹配（`no kernel image`） | 否 | `setup --install-torch --yes` |
| `E_DOCTOR_RED` / `E_DOCTOR_EXIT` | 体检不绿 / doctor 退出码非 0 | 否 | 按 `hints` 装；别改 engine 绕过 |
| `E_TEST_JOB_FAILED` | 测试 job 的 fetch 没 done | 否 | 看日志，修 ffmpeg/素材路径 |
| `E_RESTART_TIMEOUT` | 重启后 DSH 接口不通 | 否 | 确认 DSH 已起、端口正确 |

## 附录 C：机器可读步骤清单

给 AI 直接消费的 JSON（命令是模板，`<...>` 先替换）：

```json
{
  "protocol": "dsh-cover-factory-install",
  "version": 1,
  "platform": "windows",
  "requires": ["shell", "network"],
  "vars": ["REPO_URL", "PROFILE", "PLUGIN_DIR", "PY", "DSH_BASE_URL"],
  "steps": [
    { "id": "preflight", "gate": "os>=19041 && git && github && pytorch && disk>=15GB", "on_fail": "E_OS|E_NO_GIT|E_NO_NET_GITHUB|E_NO_NET_PYTORCH|E_LOW_DISK" },
    { "id": "clone", "cmd": "git clone --depth 1 <REPO_URL> <PLUGIN_DIR>", "gate": "G1", "on_fail": "E_CLONE_FAILED" },
    { "id": "register", "edit": "<PROFILE>/package.json dependencies + dsh.profile.bundles (idempotent)", "gate": "G2", "on_fail": "E_PROFILE_NOT_FOUND|E_PROFILE_JSON" },
    { "id": "deps", "cmd": "pwsh -File <PLUGIN_DIR>/scripts/install.ps1 -ProfilePath <PROFILE> -Yes", "fallback": "docs/INSTALL_AI.md#附录-a", "on_fail": "E_NO_INSTALL_SCRIPT|E_NO_NETWORK|E_LOW_DISK|E_TORCH_MISMATCH" },
    { "id": "doctor", "cmd": "<PY> <PLUGIN_DIR>/engine/run.py doctor", "gate": "exit==0 && json.ok==true", "on_fail": "E_DOCTOR_RED|E_DOCTOR_EXIT" },
    { "id": "probe_job", "cmd": "init + run --from fetch --to fetch (steps=300, import, target=null)", "gate": "stages.fetch.status=='done'", "on_fail": "E_TEST_JOB_FAILED" },
    { "id": "restart", "cmd": "restart DSH (desktop app or dsh --profile desktop)", "gate": "GET /api/cover-factory/settings ok && POST /api/cover-factory/doctor health==green", "on_fail": "E_RESTART_TIMEOUT" },
    { "id": "rollback", "cmd": "remove <PLUGIN_DIR>; drop 'dsh-cover-factory' from dependencies & dsh.profile.bundles", "note": "不删用户素材/模型/job 数据" }
  ]
}
```

## 边界与承诺

- 本协议**只装插件 + 依赖**，不下载任何音源；抓取模式/B 站搜索需要用户自己确认版权（`copyrightAck`）。
- 素材目录只读，回滚也不会动它；模型与环境在插件目录之外，回滚不卸。
- 本协议**不会**绕过 `engine/**` 的检查：doctor 不绿就是装不成，如实停下来。

## 训练链说明（AI 必读）

- **不要去找作者要 `dsh_rvc2.py`**：它在仓库里，路径 `engine/vendor/applio/dsh_rvc2.py`，引擎会自动使用。
- 训练前确认 Applio 环境可导入：`<applio_python> -c "import core; print('ok')"`；Applio 3.7.1 需要 Python 3.11+（numpy 2.4.6 不支持 3.10）。
- 换声前确认旧版 RVC 环境：`<rvc_python> -c "import fairseq; print(fairseq.__version__)"`。
- 若 `import fairseq` 失败：首选 `setup --install-fairseq --yes`（预编译 zip，sha256 校验）。还不行再手动下载同一 zip 解压到 site-packages，或用 Applio 安装脚本/VS Build Tools 编译（最后手段）。
- 训练很慢（9000 步约 55 分钟 @ RTX 5060 Laptop），先用 300 步验证链路：`steps: 300`，出片可听但音色差，属正常。
