// 首次使用自检（站在别人角度）：没有 config.local、没有本机依赖时，
// doctor 必须给出可操作提示、不能崩、不能泄漏作者本机路径。
// 用法：node tests/firstrun.mjs
// 注意：只检查“作者特有的安装根目录”是否泄漏；用户自己的 python 路径出现在提示里是正常的。
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { execFileSync } from 'node:child_process'

const root = process.cwd()
const baseTmp = process.env.CF_TMP_DIR || path.join(root, '..', '.cf-firstrun')
fs.mkdirSync(baseTmp, { recursive: true })
const tmp = fs.mkdtempSync(path.join(baseTmp, 'run-'))
fs.cpSync(root, tmp, { recursive: true, filter: (src) => !/[\\/](node_modules|\.git|__pycache__|dist)$/.test(src) })
fs.rmSync(path.join(tmp, 'engine', 'config.local.json'), { force: true })
fs.rmSync(path.join(tmp, 'engine', 'config.local.example.json'), { force: true })

const py = process.env.CF_PYTHON || 'python'
const run = (args) => {
  try {
    const out = execFileSync(py, args, { cwd: tmp, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] })
    return { code: 0, out }
  } catch (e) {
    return { code: e.status ?? 1, out: (e.stdout || '') + (e.stderr || '') }
  }
}

const errors = []
const warns = []
const r = run(['engine/run.py', 'doctor'])
if (r.code === 0) warns.push('干净环境 doctor 居然是绿的（可能本机 PATH 里真有依赖）')
let doc = null
try { doc = JSON.parse(r.out.trim()) } catch (e) { errors.push('doctor 输出不是合法 JSON: ' + r.out.slice(0, 200)) }
if (doc) {
  if (doc.ok === true) errors.push('干净环境 doctor 不应 ok=true')
  const missing = (doc.checks || []).filter((c) => !c.ok)
  if (!missing.length) errors.push('干净环境应至少有一项缺失')
  for (const c of missing) {
    if (!c.hint || !String(c.hint).trim()) errors.push('缺项没有安装提示: ' + c.name)
  }
  const blob = JSON.stringify(doc)
  const leaks = ['D:/VoiceData', 'D:/Apps']
  for (const s of leaks) if (blob.includes(s)) errors.push('输出泄漏作者本机路径: ' + s)
}
const t = run(['engine/run.py', 'setup', '--check'])
if (t.code !== 0 && t.code !== 1) errors.push('setup --check 异常退出码: ' + t.code)
if (!t.out.includes('{')) errors.push('setup --check 没有输出 JSON')

fs.rmSync(tmp, { recursive: true, force: true })
console.log(JSON.stringify({ ok: errors.length === 0, errors, warns, doctorExit: r.code, setupExit: t.code }, null, 2))
process.exit(errors.length ? 1 : 0)
