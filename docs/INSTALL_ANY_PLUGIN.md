# 任意 DSH 插件 · AI 安装协议（docs/INSTALL_ANY_PLUGIN.md）

> **给人类**：把「插件来源（git 地址 / 本地目录 / zip）」和这份文档丢给有 shell 权限的 AI 即可；本仓库自带通用安装器 [`scripts/install-plugin.ps1`](../scripts/install-plugin.ps1)，AI 一条命令就能装任意 DSH 插件。

本文覆盖：**怎么识别一个仓库是不是 DSH 插件**、**怎么装**、**怎么注册到 profile**、**怎么验证**。
专用安装流程（翻唱工厂自身依赖）见 [`INSTALL_AI.md`](INSTALL_AI.md)。

## 1. DSH 插件识别规则

一个目录能当 DSH 插件，**至少满足**：

1. 有 `package.json`，且是合法 JSON；
2. `package.json` 里有 **`dsh` 字段**（这是唯一硬条件）：
   - `dsh.bundle.patch`：指向 `cordis.patch.yml`，插件会被注册进 profile 的插件树；
   - `dsh.client`：声明浏览器半端（`{ platform: "web", inject: ["slots"] }`），图标/设置页/面板来自它；
   - 两者至少有一个，否则可能只是 host 副作用插件（安装器会给 warning，不阻断）。
3. 通常还会有 `cordis.patch.yml`（`insert: - id: ... name: <package.json name>`），以及 `index.js`（`export const name / inject / apply`）。

**不是 DSH 插件**的典型：只有 `name/version/main`、没有 `dsh` 字段的普通 npm 包 → 安装器返回 `E_NOT_DSH_PLUGIN` 并列出 `missing: ["dsh"]`。

> `dsh` 之外的字段（`files`、`engines.dsh`、`dsh.compatibility`）只影响打包/兼容提示，不影响识别。

## 2. 安装器用法

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install-plugin.ps1 `
  -Source <git URL | 本地目录 | zip 路径 | npm:包名[@版本]> `
  -ProfilePath <profile 目录> `
  [-Name <安装目录名>] [-Yes] [-SkipDeps]
```

- profile 目录示例：`$env:USERPROFILE\.dsh\profiles\desktop`（Electron 桌面版）、`...\profiles\web`、`...\profiles\headless`。
- `-SkipDeps`：只装插件、不跑依赖脚本（推荐 AI 首次先加，依赖单独验证）。
- **stdout 只有一行 JSON**，日志全在 stderr；退出码 0 成功 / 1 失败。

### 输出契约

```json
{
  "ok": true,
  "name": "dsh-foo",
  "type": "dsh-plugin",
  "action": "link",
  "files": ["<node_modules 目录>", "<profile>/package.json.bak-dsh-foo", "<profile>/package.json"],
  "profile": "C:\\Users\\me\\.dsh\\profiles\\desktop",
  "restartRequired": true,
  "verify": {
    "packageJson": "<profile>/package.json",
    "backup": "<profile>/package.json.bak-dsh-foo",
    "bundleRegistered": true,
    "dependency": "link:<node_modules 目录>",
    "nodeModules": "<node_modules 目录>",
    "hasPatch": true,
    "hasClient": false,
    "deps": "skipped",
    "afterRestart": { "toolsHint": "...", "panelHint": "..." }
  }
}
```

失败时：`{"ok":false,"error":"E_XXX","message":"..."}`（可能带 `missing` / `source` / `profile` / `destination`）。

### 错误码

| 错误码 | 含义 | 处理 |
| --- | --- | --- |
| `E_PROFILE_NOT_FOUND` | profile 目录 / package.json 不存在 | 确认 `-ProfilePath` 指向真实 profile |
| `E_PROFILE_JSON` | profile/package.json 不是合法 JSON（或写回后校验失败，已自动恢复备份） | 用 `.bak-<name>` 恢复后重试 |
| `E_SOURCE_NOT_FOUND` | 来源既不是现存路径也不像 git URL | 检查路径；npm 包写 `npm:<pkg>` |
| `E_CLONE_FAILED` / `E_GIT_FAILED` | git clone / pull 失败 | 查网络、权限、地址 |
| `E_UNZIP_FAILED` | zip 解不开 | 换 `Compress-Archive` 重新打包 |
| `E_NOT_DSH_PLUGIN` | package.json 缺 `dsh`（或没有 package.json） | 这不是 DSH 插件，别硬装 |
| `E_PLACE_FAILED` | 复制/克隆后 node_modules 里没有 package.json | 查磁盘/占用/权限 |
| `E_NPM_FAILED` | `npm:` 源安装失败（dsh/pnpm 不可用） | 改用 git/本地目录，或手动 `dsh plugin --profile <name> add <pkg>` |
| `E_UNEXPECTED` | 未预期异常 | 看 stderr 日志，报给插件作者 |

## 3. 两种安装方式

### A. link 本地目录（git / 本地文件夹 / zip 解压后的目录）

安装器把源码放到 `<profile>\node_modules\<name>`，并在 profile 里写：

```json
{
  "dependencies": { "dsh-foo": "link:C:/path/to/profile/node_modules/dsh-foo" },
  "dsh": { "profile": { "bundles": ["@deepseek-ai/dsh-base", "dsh-foo"] } }
}
```

再在 profile 目录跑一次 `pnpm install`（link 依赖需要让 pnpm 建立链接；安装器不自动跑，除非插件带依赖脚本）。

### B. npm 包

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install-plugin.ps1 -Source npm:dsh-foo -ProfilePath <profile>
# 等价手动：dsh plugin --profile <name> add dsh-foo  → 再手工把 "dsh-foo" 加进 dsh.profile.bundles
```

npm 方式下 `action="npm"`，`dependencies` 由 pnpm 写（安装器不写 `link:`），`bundles` 仍由安装器幂等追加。

> **desktop profile 注意**：它由 Electron 应用独占管理，`dsh --profile desktop --dump-config` 会拒绝执行；改完 `package.json` 后**重启 DSH 桌面应用**生效。`headless` / `web` profile 可以直接 `dsh --profile <name>`。

## 4. 注册与幂等

- `dependencies`：link 方式写入 `link:<绝对路径>`（正斜杠），npm 方式跳过；
- `dsh.profile.bundles`：**只在不存在时追加**，重复安装不会出现两条；
- 备份：首次安装写 `<profile>/package.json.bak-<name>`（已存在不覆盖，保留最原始版本）；
- 已有安装目录时：git 源 → `git pull --ff-only`；本地/zip → **覆盖复制但保留** `config.local.*`、`*.local.json`、`.env.local` 等本地文件；
- 依赖脚本：有 `scripts.postinstall` 就跑（pnpm → npm 回退）；否则若有 `scripts/install.ps1` 就以 `-ProfilePath <profile>`（加 `-Yes`）调用；`-SkipDeps` 全跳过。

## 5. 安装后验证（顺序执行，全过才算成功）

1. **文件**：`<profile>\node_modules\<name>\package.json` 存在，`name` 与 bundles 里那条一致。
2. **JSON**：`<profile>\package.json` 可解析，且
   ```powershell
   node -e "const p=require(process.argv[1]);const b=p.dsh.profile.bundles;if(!b.includes('<name>'))process.exit(1);console.log('bundle ok')" "<profile>\package.json"
   ```
3. **重启 DSH**（桌面版：托盘退出再启动）。
4. **工具可见**：在对话里让 AI 列出工具，应出现该插件的工具（例如 `cover_*`、`meme_*`），或 `GET /api/<插件前缀>/settings` 返回 200。
5. **面板路由**：若插件声明了 `dsh.client`，设置页出现对应分组；页面接口应返回 200（不是 404）。

> 机器可验的最小集合：`node -e`（第 2 步）+ 重启后 `Invoke-RestMethod <base>/api/<prefix>/settings`（或对应只读接口）返回 200。

## 6. 三个例子

### 例 1：git 仓库

```powershell
$plugin = "https://github.com/<org>/dsh-foo.git"
$profile = "$env:USERPROFILE\.dsh\profiles\desktop"
powershell -ExecutionPolicy Bypass -File scripts/install-plugin.ps1 -Source $plugin -ProfilePath $profile -SkipDeps
# 期望 stdout: {"ok":true,"name":"dsh-foo","action":"link",...}
```

### 例 2：本地文件夹（开发中插件）

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install-plugin.ps1 -Source "D:\work\dsh-foo" -ProfilePath $profile -SkipDeps
# 重复执行 = 覆盖复制；config.local.* / *.local.json 不会被覆盖
```

### 例 3：zip

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install-plugin.ps1 -Source "D:\downloads\dsh-foo.zip" -ProfilePath $profile -SkipDeps
# zip 里多包一层目录也能识别（自动下沉）
```

## 7. 回滚

```powershell
Remove-Item -Recurse -Force "<profile>\node_modules\<name>"
$p = Get-Content "<profile>\package.json" -Raw | ConvertFrom-Json
$p.dsh.profile.bundles = @($p.dsh.profile.bundles | Where-Object { $_ -ne '<name>' })
$p.dependencies.PSObject.Properties.Remove('<name>')
$p | ConvertTo-Json -Depth 30 | Out-File -Encoding utf8 "<profile>\package.json"
```

回滚只动这一个插件；其他插件、用户素材、模型与环境都不受影响。

## 8. 机器可读步骤（给 AI）

```json
{
  "protocol": "dsh-any-plugin-install",
  "version": 1,
  "platform": "windows",
  "requires": ["shell"],
  "vars": ["SOURCE", "PROFILE", "NAME"],
  "steps": [
    { "id": "preflight", "check": "PROFILE/package.json 存在且可解析", "on_fail": "E_PROFILE_NOT_FOUND|E_PROFILE_JSON" },
    { "id": "install", "cmd": "powershell -ExecutionPolicy Bypass -File scripts/install-plugin.ps1 -Source <SOURCE> -ProfilePath <PROFILE> -SkipDeps", "gate": "stdout JSON ok==true", "on_fail": "E_*" },
    { "id": "pnpm", "cmd": "pnpm install (cwd=<PROFILE>)", "gate": "exit==0", "on_fail": "E_PNPM" },
    { "id": "restart", "cmd": "重启 DSH（桌面版退出应用再启动）", "gate": "设置页出现插件分组 / 工具列表出现插件工具" },
    { "id": "verify", "cmd": "GET <base>/api/<prefix>/settings", "gate": "HTTP 200", "on_fail": "E_VERIFY" },
    { "id": "rollback", "cmd": "删除 node_modules/<name>；从 bundles/dependencies 移除", "note": "不影响其他插件与用户数据" }
  ]
}
```

## 9. 边界

- 安装器**只改指定 profile 的 package.json + 该插件的 node_modules 目录**；不会碰真实 profile 之外的任何东西。
- 不安装系统级依赖（Python/torch/CUDA 之类看插件自己的 `scripts/install.ps1` 或文档）。
- 不替用户确认插件的许可/版权；npm/git 来源的信任问题由用户自己把关。