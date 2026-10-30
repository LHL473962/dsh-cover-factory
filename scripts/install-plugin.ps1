<#
.SYNOPSIS
  通用 DSH 插件安装器：把任意 DSH 插件（git 仓库 / 本地目录 / zip / npm 包）装进指定 profile。

.DESCRIPTION
  - 自动识别：读 package.json，有 dsh 字段才当 DSH 插件（无则 E_NOT_DSH_PLUGIN）。
  - 注册：profile/package.json 的 dependencies 写 link:<path>，dsh.profile.bundles 幂等追加。
  - 幂等：已存在则 git pull 或覆盖复制（保留 config.local 等本地文件），bundles 不重复。
  - 可选跑依赖脚本（package.json scripts.postinstall 或 scripts/install.ps1），-SkipDeps 跳过。
  - **stdout 只输出一行 JSON**；日志一律走 stderr。退出码 0 成功 / 1 失败。

.PARAMETER Source
  git URL（含 file:// 本地仓库）、本地目录、.zip 路径，或 npm:<包名[@版本]>。
.PARAMETER ProfilePath
  目标 profile 目录，例如 $env:USERPROFILE\.dsh\profiles\desktop
.PARAMETER Name
  可选：安装目录名覆盖（默认用 package.json.name）。bundles 注册始终用 package.json.name。
.PARAMETER Yes
  自动确认（传给插件自带的 scripts/install.ps1）。
.PARAMETER SkipDeps
  跳过依赖安装（不跑 postinstall / install.ps1）。

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts/install-plugin.ps1 -Source https://github.com/o/r.git -ProfilePath C:\Users\me\.dsh\profiles\desktop
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts/install-plugin.ps1 -Source D:\plugins\dsh-foo -ProfilePath C:\Users\me\.dsh\profiles\desktop -SkipDeps
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$Source,
  [Parameter(Mandatory = $true)][string]$ProfilePath,
  [string]$Name = "",
  [switch]$Yes,
  [switch]$SkipDeps
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
# stdout 必须是 UTF-8：Windows PowerShell 5.1 默认用控制台代码页（如 GBK），会把中文 JSON 写坏
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch {}

function Log([string]$m) { [Console]::Error.WriteLine("[install-plugin] " + $m) }

function Emit-Json($obj, [int]$code) {
  $json = $obj | ConvertTo-Json -Depth 20 -Compress
  [Console]::Out.WriteLine($json)
  exit $code
}

function Fail([string]$errorCode, [string]$message, $extra) {
  Log("FAIL " + $errorCode + ": " + $message)
  $o = [ordered]@{ ok = $false; error = $errorCode; message = $message; restartRequired = $false }
  if ($extra -and $extra -is [System.Collections.IDictionary]) { foreach ($k in $extra.Keys) { $o[$k] = $extra[$k] } }
  Emit-Json $o 1
}

function Run-Captured([string]$exe, [string[]]$argv, [string]$cwd) {
  # 原生命令的输出必须全部拦下来：stdout 只能留给最终 JSON。
  # 注意：PS 5.1 里 `$ErrorActionPreference=Stop` + `2>&1` 会把 git 的 stderr 当终止错误，
  # 所以这里临时切回 Continue，只按 exit code 判成败。
  $old = $null; if ($cwd) { $old = Get-Location; Set-Location -LiteralPath $cwd }
  $oldEA = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    $out = & $exe @argv 2>&1
    $code = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $oldEA
    if ($old) { Set-Location -LiteralPath $old }
  }
  foreach ($line in @($out)) { if ($null -ne $line) { Log([string]$line) } }
  return $code
}

function Copy-Tree([string]$src, [string]$dst) {
  # 覆盖复制：不删除 dst 里已有的额外文件（config.local 之类要保留）
  if (-not (Test-Path -LiteralPath $dst)) { New-Item -ItemType Directory -Force -Path $dst | Out-Null }
  $skip = @("node_modules", ".git", ".venv", "__pycache__")
  # 本地覆盖文件不覆盖：config.local.* / *.local.json / .env.local（用户自己的配置）
  $keepLocal = @("^config\.local\..+$", "^.*\.local\.json$", "^\.env\.local$")
  foreach ($item in @(Get-ChildItem -LiteralPath $src -Force)) {
    if ($skip -contains $item.Name) { continue }
    $isLocal = $false
    foreach ($pat in $keepLocal) { if ($item.Name -match $pat) { $isLocal = $true } }
    if ($isLocal -and (Test-Path -LiteralPath (Join-Path $dst $item.Name))) { Log("保留本地文件：" + $item.Name); continue }
    $target = Join-Path $dst $item.Name
    if ($item.PSIsContainer) {
      Copy-Tree $item.FullName $target
    } else {
      Copy-Item -LiteralPath $item.FullName -Destination $target -Force
    }
  }
}

function Ensure-Object($obj, [string]$prop) {
  $names = @($obj.PSObject.Properties.Name)
  if (-not ($names -contains $prop) -or $null -eq $obj.$prop) {
    Add-Member -InputObject $obj -NotePropertyName $prop -NotePropertyValue ([pscustomobject]@{}) -Force
  }
  return $obj.$prop
}

function Write-JsonNoBom([string]$path, $obj) {
  $text = $obj | ConvertTo-Json -Depth 30
  $utf8 = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($path, $text, $utf8)
}

try {
  # ---------- 0. 参数与 profile ----------
  $resolvedProfile = $null
  try { $resolvedProfile = (Resolve-Path -LiteralPath $ProfilePath -ErrorAction Stop).Path } catch {}
  if (-not $resolvedProfile -or -not (Test-Path -LiteralPath $resolvedProfile)) {
    Fail "E_PROFILE_NOT_FOUND" "profile 目录不存在：$ProfilePath" @{ profile = $ProfilePath }
  }
  $profileJsonPath = Join-Path $resolvedProfile "package.json"
  if (-not (Test-Path -LiteralPath $profileJsonPath)) {
    Fail "E_PROFILE_NOT_FOUND" "profile 下没有 package.json：$resolvedProfile" @{ profile = $resolvedProfile }
  }
  try {
    $profile = Get-Content -LiteralPath $profileJsonPath -Raw -Encoding UTF8 | ConvertFrom-Json
  } catch {
    Fail "E_PROFILE_JSON" "profile/package.json 不是合法 JSON：$($_.Exception.Message)" @{ profile = $resolvedProfile }
  }

  $action = "link"
  $warnings = New-Object System.Collections.ArrayList
  $filesTouched = New-Object System.Collections.ArrayList
  $tmpRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("dsh-install-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
  New-Item -ItemType Directory -Force -Path $tmpRoot | Out-Null

  # ---------- 1. 取源码 ----------
  $npmSpec = $null
  if ($Source -match "^npm:(.+)$") { $npmSpec = $Matches[1].Trim() }

  $srcDir = $null
  $mode = ""
  if ($npmSpec) {
    # npm 包：交给 dsh plugin（pnpm 转发）。只允许真实 profile 目录，避免误建新 profile。
    $profilesRoot = Join-Path (Split-Path (Split-Path $resolvedProfile -Parent) -Parent) "profiles"
    $leaf = Split-Path $resolvedProfile -Leaf
    if (-not (Get-Command dsh -ErrorAction SilentlyContinue)) {
      Fail "E_NPM_FAILED" "找不到 dsh 命令，无法用 pnpm 安装 npm 包；请改用本地目录/git 源（action=link）或手动 dsh plugin --profile <name> add <pkg>" @{ action = "npm"; source = $Source }
    }
    Log("npm mode: dsh plugin --profile $leaf add $npmSpec")
    $code = Run-Captured "dsh" @("plugin", "--profile", $leaf, "add", $npmSpec) $null
    if ($code -ne 0) { Fail "E_NPM_FAILED" "dsh plugin add 失败（exit $code）：$npmSpec" @{ action = "npm"; source = $Source } }
    $action = "npm"
    $srcDir = $null   # npm 分支不复制，直接从 node_modules 读
  } elseif (Test-Path -LiteralPath $Source) {
    $resolvedSource = (Resolve-Path -LiteralPath $Source -ErrorAction Stop).Path
    if (Test-Path -LiteralPath $resolvedSource -PathType Leaf) {
      if ([System.IO.Path]::GetExtension($resolvedSource) -ne ".zip") {
        Fail "E_SOURCE_NOT_FOUND" "Source 是文件但不是 .zip：$resolvedSource" @{ source = $Source }
      }
      $mode = "zip"
      try { Expand-Archive -LiteralPath $resolvedSource -DestinationPath $tmpRoot -Force -ErrorAction Stop }
      catch { Fail "E_UNZIP_FAILED" "解压失败：$($_.Exception.Message)" @{ source = $Source } }
      $srcDir = $tmpRoot
    } else {
      $mode = "local"
      $srcDir = $resolvedSource
    }
  } elseif ($Source -match "^(https?://|git@|ssh://|file://)" -or $Source -match "\.git$") {
    $mode = "git"
    $srcDir = Join-Path $tmpRoot "repo"
    Log("git clone --depth 1 $Source")
    $code = Run-Captured "git" @("clone", "--depth", "1", $Source, $srcDir) $null
    if ($code -ne 0) { Fail "E_GIT_FAILED" "git clone 失败（exit $code）：$Source" @{ source = $Source } }
  } else {
    Fail "E_SOURCE_NOT_FOUND" "无法识别 Source：既不是已存在的路径，也不像 git URL。npm 包请写 npm:<包名>。" @{ source = $Source }
  }

  # zip 里可能多包一层目录
  if ($mode -eq "zip") {
    $inner = @(Get-ChildItem -LiteralPath $srcDir -Force)
    if ($inner.Count -eq 1 -and $inner[0].PSIsContainer) { $srcDir = $inner[0].FullName }
  }

  # ---------- 2. 读 package.json / 判定插件类型 ----------
  $pkgPath = $null
  if ($srcDir) {
    $pkgPath = Join-Path $srcDir "package.json"
    if (-not (Test-Path -LiteralPath $pkgPath)) {
      Fail "E_NOT_DSH_PLUGIN" "源码里没有 package.json，不是 DSH 插件（缺：package.json）" @{ source = $Source; missing = @("package.json") }
    }
    try { $pkg = Get-Content -LiteralPath $pkgPath -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { Fail "E_NOT_DSH_PLUGIN" "package.json 不是合法 JSON：$($_.Exception.Message)" @{ source = $Source; missing = @("package.json#valid-json") } }
  } else {
    $guess = ($npmSpec -replace "\(.*$", "")
    $pkgPath = Join-Path $resolvedProfile (Join-Path "node_modules" (Join-Path $guess "package.json"))
    if (-not (Test-Path -LiteralPath $pkgPath)) {
      Fail "E_NOT_DSH_PLUGIN" "npm 安装后找不到 $guess 的 package.json" @{ action = "npm"; source = $Source }
    }
    $pkg = Get-Content -LiteralPath $pkgPath -Raw -Encoding UTF8 | ConvertFrom-Json
  }

  $propNames = @($pkg.PSObject.Properties.Name)
  $missing = @()
  if (-not ($propNames -contains "dsh")) { $missing += "dsh" }
  if ($missing.Count -gt 0) {
    Fail "E_NOT_DSH_PLUGIN" ("package.json 缺 " + ($missing -join ", ") + "：这不是 DSH 插件。DSH 插件的 package.json 至少有 dsh 字段（dsh.bundle.patch 或 dsh.client）；也可能需要 cordis.patch.yml。") @{ source = $Source; missing = $missing }
  }
  $dsh = $pkg.dsh
  $hasPatch = ($dsh.PSObject.Properties.Name -contains "bundle") -and $dsh.bundle -and ($dsh.bundle.PSObject.Properties.Name -contains "patch")
  $hasClient = ($dsh.PSObject.Properties.Name -contains "client") -and $dsh.client
  if (-not $hasPatch -and -not $hasClient) {
    [void]$warnings.Add("dsh 字段存在，但既没有 dsh.bundle.patch 也没有 dsh.client：插件可能只做 host 副作用，或清单不完整。")
  }

  # ---------- 3. 名称 ----------
  $pkgName = ""
  if ($pkg.PSObject.Properties.Name -contains "name") { $pkgName = [string]$pkg.name }
  if (-not $pkgName) { $pkgName = Split-Path $srcDir -Leaf }
  if (-not $pkgName) { Fail "E_NOT_DSH_PLUGIN" "无法确定插件名（package.json.name 与目录名都为空）" @{ source = $Source } }
  $bundleName = $pkgName
  $dirName = $bundleName
  if ($Name) { $dirName = $Name }
  if ($bundleName.StartsWith("@")) {
    $parts = $bundleName.Split("/")
    if ($parts.Count -lt 2) { Fail "E_NOT_DSH_PLUGIN" "scoped 包名不合法：$bundleName" @{ source = $Source } }
    $dest = Join-Path $resolvedProfile (Join-Path "node_modules" (Join-Path $parts[0] $parts[1]))
  } else {
    $dest = Join-Path $resolvedProfile (Join-Path "node_modules" $dirName)
  }
  Log("plugin=$bundleName dest=$dest mode=$mode action=$action")

  # ---------- 4. 放置 / 更新 ----------
  $destParent = Split-Path $dest -Parent
  if (-not (Test-Path -LiteralPath $destParent)) { New-Item -ItemType Directory -Force -Path $destParent | Out-Null }

  if ($action -eq "link") {
    $destExists = Test-Path -LiteralPath $dest
    $isGit = $false
    if ($destExists) { $isGit = Test-Path -LiteralPath (Join-Path $dest ".git") }
    if ($mode -eq "git" -and $destExists -and $isGit) {
      Log("update via git pull：$dest")
      $code = Run-Captured "git" @("-C", $dest, "pull", "--ff-only") $null
      if ($code -ne 0) { [void]$warnings.Add("git pull 失败（exit $code），保留现有目录继续。") }
    } elseif ($mode -eq "git" -and -not $destExists) {
      Log("把已克隆的仓库放到目标目录（含 .git，后续可 pull）：$dest")
      New-Item -ItemType Directory -Force -Path $dest | Out-Null
      Copy-Item -LiteralPath (Join-Path $srcDir "*") -Destination $dest -Recurse -Force
      if (-not (Test-Path -LiteralPath (Join-Path $dest "package.json"))) { Fail "E_PLACE_FAILED" "复制仓库到 $dest 失败" @{ source = $Source } }
    } else {
      if ($destExists) { Log("覆盖复制（保留本地文件）：$dest") } else { Log("复制到：$dest") }
      Copy-Tree $srcDir $dest
    }
  }
  if (-not (Test-Path -LiteralPath (Join-Path $dest "package.json"))) {
    Fail "E_PLACE_FAILED" "插件没有落到 node_modules（缺 package.json）：$dest" @{ profile = $resolvedProfile; destination = $dest }
  }
  [void]$filesTouched.Add($dest)

  # ---------- 5. 注册到 profile/package.json ----------
  $backup = Join-Path $resolvedProfile ("package.json.bak-" + ($bundleName -replace "[^A-Za-z0-9._-]", "_"))
  if (-not (Test-Path -LiteralPath $backup)) {
    Copy-Item -LiteralPath $profileJsonPath -Destination $backup -Force
    [void]$filesTouched.Add($backup)
  }

  $depsObj = Ensure-Object $profile "dependencies"
  if ($action -eq "link") {
    Add-Member -InputObject $depsObj -NotePropertyName $bundleName -NotePropertyValue ("link:" + ($dest -replace "\\", "/")) -Force
  }

  $dshObj = Ensure-Object $profile "dsh"
  $profObj = Ensure-Object $dshObj "profile"
  $names = @($profObj.PSObject.Properties.Name)
  if (-not ($names -contains "bundles") -or $null -eq $profObj.bundles) {
    Add-Member -InputObject $profObj -NotePropertyName "bundles" -NotePropertyValue @() -Force
  }
  $bundles = @($profObj.bundles)
  $bundleRegistered = $false
  if (-not ($bundles -contains $bundleName)) {
    $bundles = @($bundles) + $bundleName
  } else {
    $bundleRegistered = $true
  }
  $profObj.bundles = @($bundles)
  Write-JsonNoBom $profileJsonPath $profile
  [void]$filesTouched.Add($profileJsonPath)

  # 回读校验（写坏了就恢复备份）
  try {
    $check = Get-Content -LiteralPath $profileJsonPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $checkBundles = @($check.dsh.profile.bundles)
    if (-not ($checkBundles -contains $bundleName)) { throw "bundles 里没有 $bundleName" }
  } catch {
    Copy-Item -LiteralPath $backup -Destination $profileJsonPath -Force
    Fail "E_PROFILE_JSON" "写回 profile/package.json 后校验失败（已从备份恢复）：$($_.Exception.Message)" @{ profile = $resolvedProfile }
  }

  # ---------- 6. 可选依赖 ----------
  $depsStatus = "skipped"
  if (-not $SkipDeps -and $action -eq "link") {
    $pluginPkgPath = Join-Path $dest "package.json"
    try { $pluginPkg = Get-Content -LiteralPath $pluginPkgPath -Raw -Encoding UTF8 | ConvertFrom-Json } catch { $pluginPkg = $null }
    $postinstall = $null
    if ($pluginPkg -and $pluginPkg.PSObject.Properties.Name -contains "scripts" -and $pluginPkg.scripts) {
      if ($pluginPkg.scripts.PSObject.Properties.Name -contains "postinstall") { $postinstall = [string]$pluginPkg.scripts.postinstall }
    }
    $pluginInstall = Join-Path $dest "scripts\install.ps1"
    if ($postinstall) {
      Log("运行 postinstall：$postinstall")
      $pm = $null
      if (Get-Command pnpm -ErrorAction SilentlyContinue) { $pm = "pnpm" }
      elseif (Get-Command npm -ErrorAction SilentlyContinue) { $pm = "npm" }
      $code = -1
      if ($pm -eq "pnpm") { $code = Run-Captured "pnpm" @("-C", $dest, "run", "postinstall") $null }
      elseif ($pm -eq "npm") { $code = Run-Captured "npm" @("--prefix", $dest, "run", "postinstall") $null }
      if ($code -ne 0 -and $pm -eq "pnpm" -and (Get-Command npm -ErrorAction SilentlyContinue)) { $code = Run-Captured "npm" @("--prefix", $dest, "run", "postinstall") $null }
      if ($code -eq 0) { $depsStatus = "ran-postinstall" } else { $depsStatus = "failed-postinstall"; [void]$warnings.Add("postinstall 失败（exit $code），插件本身已装好。") }
    } elseif (Test-Path -LiteralPath $pluginInstall) {
      $argv = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $pluginInstall, "-ProfilePath", $resolvedProfile)
      if ($Yes) { $argv += "-Yes" }
      Log("运行插件安装脚本：scripts\install.ps1")
      $code = Run-Captured "powershell" $argv $null
      if ($code -eq 0) { $depsStatus = "ran-install-script" } else { $depsStatus = "failed-install-script"; [void]$warnings.Add("scripts/install.ps1 失败（exit $code），插件本身已装好。") }
    } else {
      $depsStatus = "none"
    }
  }

  # ---------- 7. 输出 ----------
  $out = [ordered]@{
    ok = $true
    name = $bundleName
    type = "dsh-plugin"
    action = $action
    files = @($filesTouched)
    profile = $resolvedProfile
    restartRequired = $true
    verify = [ordered]@{
      packageJson = $profileJsonPath
      backup = $backup
      bundleRegistered = $true
      dependency = $(if ($action -eq "link") { "link:" + ($dest -replace "\\", "/") } else { $null })
      nodeModules = $dest
      hasPatch = $hasPatch
      hasClient = $hasClient
      deps = $depsStatus
      afterRestart = [ordered]@{
        toolsHint = "重启后在对话里问「你有哪些 cover_* 工具」或看工具列表里是否出现该插件的 tools"
        panelHint = "若插件声明了 dsh.client/设置页，打开 设置 → 对应分组；页面接口应返回 200"
      }
    }
  }
  if ($warnings.Count -gt 0) { $out.warnings = @($warnings) }
  Log("done: $bundleName ($action)")
  Emit-Json $out 0
} catch {
  Fail "E_UNEXPECTED" ("未预期错误：" + $_.Exception.Message) @{}
} finally {
  if ($tmpRoot -and (Test-Path -LiteralPath $tmpRoot)) {
    try { Remove-Item -LiteralPath $tmpRoot -Recurse -Force -ErrorAction SilentlyContinue } catch {}
  }
}