// 依赖体检：调用引擎 `run.py doctor`，把结果规范化成插件/UI 都能用的形状。
//
// 期望的引擎输出（契约写在 docs/INSTALL.md；引擎侧实现时按这个来，插件对形状做了兼容）：
//   { "ok": true, "health": "green|yellow|red",
//     "checks": [ {"id":"python","label":"Python","status":"ok|missing|warn","required":true,"detail":"3.12.7","hint":"怎么装"} ],
//     "missing": ["applio"], "hints": ["…"] }
//
// 兼容：checks/items/dependencies(数组或对象)、status/ok、required/optional、hint/install。
// 铁律：**解析不出来就 ok:false**，绝不把「体检没跑成」当「依赖齐全」。

import { engineReady, parseJsonFrom, runCli } from './engineClient.js'
import { loadSettings } from './settings.js'
import { clip, fail, redactPaths } from './util.js'

export const INSTALL_DOC = 'docs/INSTALL.md'

// 引擎 setup/checks.py 的检查项 id；yt-dlp 只有抓取模式要用，缺了算「可选缺失」（黄），不该拦建 job
const OPTIONAL_IDS = new Set(['ytdlp', 'yt_dlp', 'yt-dlp'])
const LABELS = {
  python: 'Python 3.10+',
  torch_cuda: 'PyTorch + CUDA',
  ffmpeg: 'ffmpeg',
  msst: 'MSST 人声分离',
  applio: 'Applio 训练',
  old_rvc: '旧版 RVC 换声',
  ytdlp: 'yt-dlp（仅抓取模式，可选）',
}

const OK_STATUS = new Set(['ok', 'pass', 'passed', 'ready', 'installed', 'done', 'true', 'yes', 'green'])
const BAD_STATUS = new Set(['missing', 'absent', 'fail', 'failed', 'error', 'no', 'false', 'not_found', 'notfound', 'broken', 'red'])
const WARN_STATUS = new Set(['warn', 'warning', 'degraded', 'partial', 'optional_missing', 'yellow'])

function mapHealth(v) {
  const s = String(v == null ? '' : v).trim().toLowerCase()
  if (['green', 'ok', 'ready', 'pass', 'passed', 'healthy'].includes(s)) return 'green'
  if (['yellow', 'warn', 'warning', 'degraded', 'partial'].includes(s)) return 'yellow'
  if (['red', 'error', 'fail', 'failed', 'missing', 'broken'].includes(s)) return 'red'
  return ''
}

function normStatus(item) {
  if (typeof item.ok === 'boolean') return item.ok ? 'ok' : 'missing'
  const s = String(item.status || item.state || '').trim().toLowerCase()
  if (OK_STATUS.has(s)) return 'ok'
  if (BAD_STATUS.has(s)) return 'missing'
  if (WARN_STATUS.has(s)) return 'warn'
  return 'unknown'
}

const SEVERITY = { green: 0, yellow: 1, red: 2 }

// 把任意 doctor 输出规整成 { health, checks, missing, missingRequired, missingOptional, hints }
export function normalizeDoctor(raw) {
  const src = raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : {}
  let list = []
  if (Array.isArray(src.checks)) list = src.checks
  else if (Array.isArray(src.items)) list = src.items
  else if (Array.isArray(src.dependencies)) list = src.dependencies
  else if (src.dependencies && typeof src.dependencies === 'object') {
    list = Object.entries(src.dependencies).map(([k, v]) =>
      v && typeof v === 'object' ? { id: k, ...v } : { id: k, ok: !!v })
  }

  const checks = list.map((entry, i) => {
    const item = entry && typeof entry === 'object' ? entry : { ok: !!entry }
    const id = String(item.id || item.key || item.name || item.module || 'check' + (i + 1))
    const required = item.required === false || item.optional === true ? false : !OPTIONAL_IDS.has(id)
    const detail = item.detail != null
      ? String(item.detail)
      : (item.found != null ? String(item.found) : (item.version != null ? 'v' + String(item.version) : ''))
    return {
      id,
      label: String(item.label || item.title || LABELS[id] || item.name || id),
      status: normStatus(item),
      required,
      detail: detail ? clip(detail, 200) : null,
      path: item.path ? clip(String(item.path), 200) : null,
      hint: item.hint || item.install ? clip(String(item.hint || item.install), 400) : null,
    }
  })

  const missing = checks
    .filter((c) => c.status === 'missing' || c.status === 'unknown')
    .map((c) => c.id)
  if (Array.isArray(src.missing)) {
    for (const m of src.missing) {
      const id = typeof m === 'string' ? m : String((m && (m.id || m.name)) || '')
      if (id && !missing.includes(id)) missing.push(id)
    }
  }

  const missingRequired = checks.filter((c) => c.required && (c.status === 'missing' || c.status === 'unknown')).map((c) => c.id)
  const missingOptional = checks.filter((c) => !c.required && (c.status === 'missing' || c.status === 'warn' || c.status === 'unknown')).map((c) => c.id)

  const hints = []
  if (Array.isArray(src.hints)) {
    for (const h of src.hints) {
      const text = typeof h === 'string' ? h : (h && (h.hint || h.message || h.text))
      const t = text ? clip(String(text), 400) : ''
      if (t && !hints.includes(t)) hints.push(t)
    }
  }
  for (const c of checks) if (c.hint && !hints.includes(c.hint)) hints.push(c.hint)

  // 自己算一遍，再和引擎给的取「更严重」的：引擎说 green 但确实缺必需项时，不能报绿
  let derived = 'green'
  if (missingRequired.length) derived = 'red'
  else if (missingOptional.length || checks.some((c) => c.status === 'warn')) derived = 'yellow'
  if (!checks.length) derived = ''
  const declared = mapHealth(src.health || src.status)
  let health = derived || declared || 'unknown'
  if (derived && declared && SEVERITY[declared] > SEVERITY[derived]) health = declared

  return { health, checks, missing, missingRequired, missingOptional, hints: hints.slice(0, 12) }
}

// 建 job 前的闸：只有「体检结论是红」才拦。体检本身失败（ok:false）不拦，别把体检故障当成缺依赖。
export function doctorBlockCreate(report) {
  if (!report || typeof report !== 'object' || report.ok === false) return { block: false, missing: [], hints: [] }
  const block = report.health === 'red' || report.ready === false
  const missing = block
    ? (Array.isArray(report.missingRequired) && report.missingRequired.length
      ? report.missingRequired
      : (Array.isArray(report.missing) ? report.missing : []))
    : []
  return { block, missing, hints: block && Array.isArray(report.hints) ? report.hints.slice(0, 6) : [] }
}

// 真正跑一次体检（spawn 引擎 CLI）。任何失败都降级为 ok:false + 人能照着做的说明。
export async function runDoctorReport(settings) {
  const st = settings || loadSettings()
  const ready = engineReady(st)
  if (!ready.ok) {
    return fail('ENGINE_NOT_READY', ready.message + ' 依赖体检要先让引擎就位；装法见 ' + INSTALL_DOC + '。', {
      engineDir: ready.engineDir,
      install: INSTALL_DOC,
    })
  }

  const res = await runCli(['doctor'], { timeoutMs: 120000, settings: st })
  const parsed = parseJsonFrom(res.stdout, null) || parseJsonFrom(res.stderr, null)
  const checked = parsed && typeof parsed === 'object' ? normalizeDoctor(parsed) : null
  const tail = clip(redactPaths(res.stderr || res.stdout), 300)

  if (!checked) {
    const unsupported = res.code !== 0 || /invalid choice|unknown command|no such command/i.test(String(res.stderr || ''))
    return fail(
      unsupported ? 'DOCTOR_UNAVAILABLE' : 'DOCTOR_BAD_OUTPUT',
      unsupported
        ? '引擎还没有 doctor 子命令（或起不来，exit ' + res.code + '）：' + tail + '。先按 ' + INSTALL_DOC + ' 手动装依赖，别指望体检。'
        : 'doctor 有输出但不是可解析的 JSON，给不出结构化体检结果：' + tail + '。按 ' + INSTALL_DOC + ' 手动核对。',
      { engineDir: ready.engineDir, install: INSTALL_DOC, exitCode: res.code },
    )
  }

  const health = checked.health
  return {
    ok: true,
    health,
    ready: health !== 'red',
    checks: checked.checks,
    missing: checked.missing,
    missingRequired: checked.missingRequired,
    missingOptional: checked.missingOptional,
    hints: checked.hints,
    engine: { engineDir: ready.engineDir, pythonSource: ready.pythonSource || null },
    install: INSTALL_DOC,
    at: new Date().toISOString(),
    message: health === 'green'
      ? '依赖齐全，可以建 job。'
      : health === 'yellow'
        ? '必需依赖齐了，缺可选组件（对应功能会降级）：' + (checked.missingOptional.join('、') || '—')
        : '缺必需依赖（' + (checked.missingRequired.join('、') || checked.missing.join('、') || '未知') + '）：先按 ' + INSTALL_DOC + ' 装，再重新体检。',
  }
}
