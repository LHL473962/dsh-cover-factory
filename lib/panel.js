// HTTP 面板层：/api/cover-factory/*（设置页 client.js 的数据源 + 按钮后端）
//
// 路由（只服务本机同源页面）：
//   GET  /api/cover-factory/settings    -> { ok, settings, engine, running }
//   POST /api/cover-factory/settings    -> { ok, settings, ignored }       白名单键
//   GET  /api/cover-factory/jobs        -> { ok, total, jobs[], engine, running }
//   POST /api/cover-factory/jobs/create { singer, folder, target, steps, mode, copyrightAck }
//   POST /api/cover-factory/jobs/run    { jobId, from?, to?, resume? }
//   POST /api/cover-factory/jobs/cancel { jobId }
//   POST /api/cover-factory/jobs/export { jobId, overwrite? }
//
// 原则（学自 dsh-meme-lookup / dsh-image-gen）：
// - 同源检查（Origin 与 Host 不符直接 403），不让跨站页面改设置/起任务；
// - 请求体 64KB 上限，防止被打爆内存；
// - **不绕过校验**：create/run/cancel/export 直接走 lib/tools.js 里那套导出函数，
//   和对话里的 cover_* 完全同一条代码路径（含 guard、脱敏、错误码）；
// - 设置页是可选件：webServer 不可用就静默跳过，不影响六个工具。

import { engineReady, listJobs } from './engineClient.js'
import { loadSettings, saveSettings } from './settings.js'
import { cancelJobRun, createJob, deleteJob, exportJob, runJob } from './tools.js'
import { redactPaths } from './util.js'

export const ROUTE = '/api/cover-factory'

const MAX_BODY = 64 * 1024

export function registerPanel(ctx) {
  try {
    if (!ctx || typeof ctx.effect !== 'function') return
    const ws = ctx.webServer
    if (!ws || typeof ws.register !== 'function') return
    ctx.effect(() => ws.register({ kind: 'prefix', path: ROUTE, handler }))
  } catch {
    // 设置页挂不上不算故障：工具照常用
  }
}

// 顶部状态条要的两件事：引擎就绪吗、现在有几个任务在跑
function engineInfo() {
  const st = loadSettings()
  const ready = engineReady(st)
  return {
    ready: !!ready.ok,
    engineDir: st.engineDir || ready.engineDir || '',
    script: ready.ok ? ready.script : null,
    pythonWarning: ready.ok ? ready.pythonWarning || null : null,
    message: ready.ok ? null : redactPaths(ready.message),
  }
}

function runningCount(workRoot) {
  const r = listJobs(workRoot, { limit: 100 })
  return (r.jobs || []).filter((j) => j.running).length
}

function jobRow(j) {
  return {
    jobId: j.jobId,
    engineId: j.engineId || null,
    singer: j.singer || '',
    mode: j.mode || '',
    target: j.target || '',
    status: j.status || '',
    progress: j.progress || '',
    done: j.done,
    total: j.total,
    currentStage: j.currentStage || null,
    running: !!j.running,
    pid: j.pid || 0,
    hasMp3: !!j.hasMp3,
    hasMix: !!j.hasMix,
    updatedAt: j.updatedAt || null,
    createdAt: j.createdAt || null,
  }
}

async function readBody(req, maxBytes = MAX_BODY) {
  const chunks = []
  let size = 0
  try {
    for await (const chunk of req) {
      const buf = Buffer.isBuffer(chunk) ? chunk : Buffer.from(String(chunk))
      size += buf.length
      if (size > maxBytes) return { error: 'BODY_TOO_LARGE', message: '请求体超过 64KB' }
      chunks.push(buf)
    }
  } catch (e) {
    return { error: 'BODY_READ_FAILED', message: redactPaths(e && e.message) }
  }
  const text = Buffer.concat(chunks).toString('utf8').trim()
  if (!text) return { value: {} }
  try {
    const v = JSON.parse(text)
    if (!v || typeof v !== 'object' || Array.isArray(v)) return { error: 'BAD_BODY', message: '请求体必须是 JSON 对象' }
    return { value: v }
  } catch {
    return { error: 'BAD_JSON', message: '请求体不是合法 JSON' }
  }
}

// 只把白名单字段交给工具，页面传别的也不会影响行为
const pick = (v, keys) => {
  const out = {}
  for (const k of keys) if (v[k] !== undefined) out[k] = v[k]
  return out
}

export async function handler(req, res) {
  const send = (code, body) => {
    try {
      res.writeHead(code, { 'content-type': 'application/json; charset=utf-8' })
      res.end(JSON.stringify(body))
    } catch {}
  }

  const postJson = async (keys, fn) => {
    const body = await readBody(req)
    if (body.error) return send(400, { ok: false, error: body.error, message: body.message })
    const value = body.value || {}
    let result
    try {
      result = await fn(pick(value, keys))
    } catch (e) {
      return send(500, { ok: false, error: 'PANEL_ERROR', message: redactPaths(e && e.message) })
    }
    if (!result || typeof result !== 'object') {
      return send(500, { ok: false, error: 'EMPTY_RESULT', message: '工具没有返回结果' })
    }
    // 业务失败也用 200 回：页面只看 body.ok / body.error，和对话侧返回形状一致
    return send(200, result)
  }

  try {
    // 同源检查：跨站页面不许读写设置或起任务
    const origin = String((req.headers && req.headers.origin) || '')
    if (origin) {
      let same = false
      try { same = new URL(origin).host === String((req.headers && req.headers.host) || '') } catch { same = false }
      if (!same) return send(403, { ok: false, error: 'ORIGIN_REJECTED', message: '只接受同源请求' })
    }

    const url = new URL(String(req.url || '/'), 'http://127.0.0.1')
    const sub = url.pathname.replace(ROUTE, '') || '/'
    const method = String(req.method || 'GET').toUpperCase()

    if (sub === '/settings' && method === 'GET') {
      const settings = loadSettings()
      return send(200, { ok: true, settings, engine: engineInfo(), running: runningCount(settings.workRoot) })
    }
    if (sub === '/settings' && method === 'POST') {
      const body = await readBody(req)
      if (body.error) return send(400, { ok: false, error: body.error, message: body.message })
      const saved = saveSettings(body.value)
      if (!saved.ok) return send(500, { ok: false, error: saved.error, message: saved.message, settings: saved.settings })
      return send(200, { ok: true, settings: saved.settings, ignored: saved.ignored, engine: engineInfo() })
    }
    if (sub === '/jobs' && method === 'GET') {
      const st = loadSettings()
      const list = listJobs(st.workRoot, { limit: 20 })
      return send(200, {
        ok: true,
        total: list.total,
        jobs: (list.jobs || []).map(jobRow),
        engine: engineInfo(),
        running: (list.jobs || []).filter((j) => j.running).length,
      })
    }

    if (sub === '/jobs/create' && method === 'POST') {
      return postJson(['singer', 'folder', 'target', 'steps', 'mode', 'copyrightAck', 'search', 'limit', 'refs'], (v) => createJob(v))
    }
    if (sub === '/jobs/run' && method === 'POST') {
      return postJson(['jobId', 'from', 'to', 'resume'], (v) => runJob(v))
    }
    if (sub === '/jobs/cancel' && method === 'POST') {
      return postJson(['jobId'], (v) => cancelJobRun(v))
    }
    if (sub === '/jobs/export' && method === 'POST') {
      return postJson(['jobId', 'overwrite'], (v) => exportJob(v))
    }
    if (sub === '/jobs/delete' && method === 'POST') {
      // 页面只给 {jobId, purge}；工具层要的是 mode，这里做一层映射（同一份删除逻辑）
      return postJson(['jobId', 'purge', 'mode'], (v) => deleteJob({
        jobId: v.jobId,
        mode: v.mode || (v.purge ? 'purge' : 'soft'),
      }))
    }

    return send(404, { ok: false, error: 'NOT_FOUND', message: '没有这条路由' })
  } catch (e) {
    return send(500, { ok: false, error: 'PANEL_ERROR', message: redactPaths(e && e.message) })
  }
}
