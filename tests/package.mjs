// 打包自检：确保别的用户 clone 下来不缺文件、不踩本机路径
// 用法：node tests/package.mjs
import fs from 'node:fs'
import path from 'node:path'

const root = process.cwd()
const errors = []
const warns = []
const exists = (rel) => fs.existsSync(path.join(root, rel))
const read = (rel) => fs.readFileSync(path.join(root, rel), 'utf8')
const listFiles = (d, ext) => fs.readdirSync(path.join(root, d)).filter((f) => f.endsWith(ext)).map((f) => d + '/' + f)

const REQUIRED = [
  'package.json', 'index.js', 'client.js', 'cordis.patch.yml',
  'lib/tools.js', 'lib/panel.js', 'lib/engineClient.js', 'lib/settings.js', 'lib/doctor.js', 'lib/aliases.js',
  'engine/run.py', 'engine/config_default.json',
  'engine/stages/fetch.py', 'engine/stages/separate.py', 'engine/stages/select.py', 'engine/stages/clean.py',
  'engine/stages/dataset.py', 'engine/stages/train.py', 'engine/stages/convert.py', 'engine/stages/mix.py',
  'engine/stages/qa.py', 'engine/stages/export.py',
  'engine/setup/checks.py', 'engine/setup/bootstrap.py',
  'engine/vendor/applio/dsh_rvc2.py',
  'docs/QUICKSTART.md', 'docs/INSTALL.md', 'docs/INSTALL_AI.md', 'docs/INSTALL_ANY_PLUGIN.md',
  'docs/FAQ.md', 'docs/UI.md', 'preset/prompts.md',
  'scripts/install.ps1', 'scripts/install-plugin.ps1',
  'tests/smoke.mjs', 'tests/e2e.mjs'
]
for (const f of REQUIRED) if (!exists(f)) errors.push('缺少文件: ' + f)

try {
  const pkg = JSON.parse(read('package.json'))
  if (!pkg.name) errors.push('package.json 缺 name')
  if (!pkg.dsh || !pkg.dsh.bundle || !pkg.dsh.bundle.patch) errors.push('package.json 缺 dsh.bundle.patch')
  if (!pkg.dsh || !pkg.dsh.client) errors.push('package.json 缺 dsh.client')
} catch (e) { errors.push('package.json 解析失败: ' + e.message) }

const SELF = ['a' + '1599', 'D:' + '/VoiceData', 'C:/Users/' + 'a' + '1599']
const SCAN = ['index.js', 'client.js'].concat(
  listFiles('lib', '.js'),
  ['engine/run.py', 'engine/config_default.json', 'engine/vendor/applio/dsh_rvc2.py', 'scripts/install.ps1', 'scripts/install-plugin.ps1'],
  listFiles('engine/stages', '.py'),
  listFiles('engine/setup', '.py')
)
for (const f of SCAN) {
  if (!exists(f)) continue
  const txt = read(f)
  for (const s of SELF) {
    if (!txt.includes(s)) continue
    const msg = '本机路径 ' + s + ' 出现在 ' + f
    if (f.indexOf('config_default') >= 0) warns.push(msg)
    else errors.push(msg)
  }
}

const DOCS = ['README.md', 'docs/QUICKSTART.md', 'docs/INSTALL.md', 'docs/INSTALL_AI.md', 'docs/INSTALL_ANY_PLUGIN.md', 'docs/FAQ.md', 'docs/UI.md', 'preset/prompts.md']
const refRe = /(?:`|\s)((?:docs|scripts|engine|lib|tests)\/[A-Za-z0-9_./-]+\.(?:md|py|ps1|js|mjs|json|yml))(?:`|\s|$)/g
for (const f of DOCS) {
  if (!exists(f)) continue
  const txt = read(f)
  let m
  while ((m = refRe.exec(txt))) {
    const rel = m[1]
    if (rel.indexOf('*') >= 0) continue
    if (rel.indexOf('config.local') >= 0) continue
    if (!exists(rel)) warns.push('文档引用不存在: ' + f + ' -> ' + rel)
  }
}

console.log(JSON.stringify({ ok: errors.length === 0, errors, warns, checked: REQUIRED.length }, null, 2))
process.exit(errors.length ? 1 : 0)
