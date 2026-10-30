// 引擎客户端：唯一与本地引擎打交道的地方。
//
// 原则
// - 只通过 CLI（python engine/run.py ...）调用；engine/ 目录一行都不写。
// - 长任务（run）用 child_process.spawn 后台跑：立刻返回 jobId + pid，stdout 落日志，不阻塞对话。
// - 短查询（init/status/cancel/select dry-run）走 runCli，带超时，超时就杀。
// - 全部返回结构化结果，绝不 throw；失败是 { ok:false, error, message }。
//
// 进程登记
// - 内存 Map jobId -> { child, pid, ... }（本次 DSH 进程内）
// - 落盘 <workdir>/plugin-run.json（DSH 重启后仍能 cover_cancel）；job.json 仍由引擎独占写。

import { spawn } from 'node:child_process'
import { appendFileSync, closeSync, existsSync, mkdirSync, openSync, readdirSync, readSync, statSync } from 'node:fs'
import { basename, dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { fail, isAlive, readJsonSafe, redactPaths, writeFileAtomic, writeJsonAtomic } from './util.js'
import { loadSettings, resolveEngineDir, resolvePython } from './settings.js'

export const PLUGIN_DIR = resolve(dirname(fileURLToPath(import.meta.url)), '..')

// DESIGN.md 的 10 个阶段（0–9）
export const STAGES = ['fetch', 'separate', 'select', 'clean', 'dataset', 'train', 'convert', 'mix', 'qa', 'export']

export const RUN_STATE_FILE = 'plugin-run.json'

const MAX_OUT = 200 * 1024
const JOBS = new Map()

export function enginePaths(settings) {
  const st = settings || loadSettings()
  const dir = resolveEngineDir(st, PLUGIN_DIR)
  const py = resolvePython(st)
  return {
    engineDir: dir,
    script: join(dir, 'run.py'),
    python: py.exe,
    pythonSource: py.source,
    pythonWarning: py.warning,
  }
}

// 引擎就绪检查：缺 run.py 就明确说缺哪一块，别让用户猜
export function engineReady(settings) {
  const p = enginePaths(settings)
  if (!existsSync(p.script)) {
    return fail('ENGINE_NOT_READY', '本地引擎还没就绪：找不到 engine/run.py。插件侧已就位，引擎文件到位后不需要改插件。', {
      hint: '引擎正在另一处开发中；可以用 cover_list 看已有 job，但创建/运行要等引擎到位。',
      engineDir: p.engineDir,
      python: p.pythonSource === 'PATH' ? 'python' : '<configured>',
    })
  }
  return { ok: true, ...p }
}

// 短 CLI 调用：带超时、输出有上限、永不 reject
export function runCli(args, opts = {}) {
  const settings = opts.settings || loadSettings()
  const p = enginePaths(settings)
  const timeoutMs = Number.isFinite(opts.timeoutMs) ? opts.timeoutMs : 30000
  const cwd = opts.cwd || p.engineDir
  return new Promise((done) => {
    let child
    try {
      child = spawn(p.python, [p.script, ...args], {
        cwd,
        windowsHide: true,
        stdio: ['ignore', 'pipe', 'pipe'],
      })
    } catch (e) {
      done(fail('SPAWN_FAILED', '起不来引擎进程：' + (e && e.message), { args, code: -1 }))
      return
    }
    let out = ''
    let err = ''
    let settled = false
    let timedOut = false
    const timer = setTimeout(() => {
      timedOut = true
      try { child.kill('SIGKILL') } catch {}
    }, timeoutMs)
    const finish = (code, extra) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      const base = {
        ok: code === 0 && !timedOut,
        code,
        timedOut,
        stdout: out,
        stderr: err,
        args,
      }
      done(extra ? { ...base, ...extra } : base)
    }
    try {
      child.stdout.on('data', (d) => { if (out.length < MAX_OUT) out += d.toString() })
      child.stderr.on('data', (d) => { if (err.length < MAX_OUT) err += d.toString() })
    } catch {}
    child.on('error', (e) => finish(-1, { error: 'SPAWN_FAILED', stderr: String((e && e.message) || e) }))
    child.on('close', (code) => finish(typeof code === 'number' ? code : -1))
  })
}

// 从 stdout 里捞 JSON（引擎可能先打几行进度再打 JSON）
export function parseJsonFrom(text, fallback = null) {
  const s = String(text || '').trim()
  if (!s) return fallback
  try { return JSON.parse(s) } catch {}
  const first = s.indexOf('{')
  const last = s.lastIndexOf('}')
  if (first >= 0 && last > first) {
    try { return JSON.parse(s.slice(first, last + 1)) } catch {}
  }
  return fallback
}

// job.json 可能落在 --workdir 根，也可能在 <workdir>/<id>/ 或 <workdir>/work/ 下
export function findJobFile(workdir) {
  if (!workdir) return ''
  const direct = [join(workdir, 'job.json'), join(workdir, 'work', 'job.json')]
  for (const p of direct) {
    if (existsSync(p)) return p
  }
  let entries = []
  try { entries = readdirSync(workdir) } catch { return '' }
  for (const name of entries) {
    const dir = join(workdir, name)
    try { if (!statSync(dir).isDirectory()) continue } catch { continue }
    for (const cand of ['job.json', join('work', 'job.json')]) {
      const q = join(dir, cand)
      if (existsSync(q)) return q
    }
  }
  for (const name of entries) {
    if (!/^job.*\.json$/i.test(name)) continue
    const q = join(workdir, name)
    const j = readJsonSafe(q, null)
    if (j && typeof j === 'object' && (j.stages || j.id)) return q
  }
  return ''
}

// job.json → 统一视图（stages 可能是对象也可能是数组，两种都吃）
export function normalizeJob(raw) {
  const j = raw && typeof raw === 'object' ? raw : {}
  const stages = []
  const push = (name, v) => {
    const s = v && typeof v === 'object' ? v : { status: typeof v === 'string' ? v : 'pending' }
    stages.push({
      stage: String(s.stage || s.name || name),
      status: String(s.status || 'pending'),
      startedAt: s.startedAt || null,
      finishedAt: s.finishedAt || null,
      artifacts: s.artifacts === undefined ? null : s.artifacts,
      error: s.error ? redactPaths(s.error) : null,
      log: s.log || null,
    })
  }
  const src = j.stages
  if (Array.isArray(src)) {
    src.forEach((s, i) => push(STAGES[i] || 'stage' + (i + 1), s))
  } else if (src && typeof src === 'object') {
    for (const name of STAGES) if (src[name] !== undefined) push(name, src[name])
    for (const [k, v] of Object.entries(src)) if (!STAGES.includes(k)) push(k, v)
  }
  const done = stages.filter((s) => s.status === 'done' || s.status === 'skipped').length
  const running = stages.find((s) => s.status === 'running') || null
  const failed = stages.find((s) => s.status === 'failed') || null
  return {
    jobId: String(j.id || ''),
    singer: String(j.singer || ''),
    mode: String(j.mode || ''),
    target: typeof j.target === 'string' ? j.target : (j.target && (j.target.file || j.target.path)) || '',
    createdAt: j.createdAt || null,
    updatedAt: j.updatedAt || null,
    stages,
    done,
    total: stages.length || STAGES.length,
    currentStage: running ? running.stage : (failed ? failed.stage : null),
    failedStage: failed ? failed.stage : null,
    cancelled: !!j.cancelled,
    decision: j.decision && typeof j.decision === 'object' ? j.decision : null,
    result: j.result || null,
    options: j.options || null,
  }
}

export function readJob(jobFile) {
  const raw = readJsonSafe(jobFile, null)
  if (!raw || typeof raw !== 'object') return null
  return normalizeJob(raw)
}

export function stageLogPath(workdir, stage) {
  return join(workdir, 'logs', String(stage) + '.log')
}

export function engineLogPath(workdir) {
  return join(workdir, 'logs', 'engine.stdout.log')
}

// 日志尾部：只读最后 64KB，逐行脱敏（日志里必然有绝对路径）
export function readLogTail(logPath, maxLines = 40) {
  try {
    const st = statSync(logPath)
    const size = st.size
    const len = Math.min(size, 64 * 1024)
    if (len <= 0) return []
    const start = size - len
    const buf = Buffer.alloc(len)
    const fd = openSync(logPath, 'r')
    try {
      readSync(fd, buf, 0, len, start)
    } finally {
      try { closeSync(fd) } catch {}
    }
    return String(buf.toString('utf8')).split(/\r?\n/)
      .map((l) => redactPaths(l))
      .filter((l) => l.length > 0)
      .slice(-maxLines)
  } catch {
    return []
  }
}

// 后台起 run：立刻返回，stdout/stderr 直接进 <workdir>/logs/engine.stdout.log
export function startJob(opts, settings) {
  const st = settings || loadSettings()
  const { jobId, workdir, jobFile } = opts || {}
  const ready = engineReady(st)
  if (!ready.ok) return ready
  if (!workdir || !existsSync(workdir)) {
    return fail('WORK_DIR_MISSING', 'job 工作目录不存在：先用 cover_setup 建 job。', { jobId })
  }
  if (!jobFile || !existsSync(jobFile)) {
    return fail('JOB_NOT_FOUND', '找不到这个 job 的 job.json：先 cover_setup，或换一个 jobId。', { jobId })
  }
  const logs = join(workdir, 'logs')
  try { mkdirSync(logs, { recursive: true }) } catch {}
  const logPath = engineLogPath(workdir)
  const args = ['run', '--job', jobFile]
  if (opts.from) args.push('--from', String(opts.from))
  if (opts.to) args.push('--to', String(opts.to))
  if (opts.resume) args.push('--resume')
  if (opts.allowDuet) args.push('--allow-duet')

  let fd = -1
  try {
    fd = openSync(logPath, 'a')
  } catch (e) {
    return fail('LOG_OPEN_FAILED', '日志文件打不开（可能被占用）：' + (e && e.message), { jobId })
  }
  let child
  try {
    child = spawn(ready.python, [ready.script, ...args], {
      cwd: workdir,
      detached: true,
      windowsHide: true,
      stdio: ['ignore', fd, fd],
    })
  } catch (e) {
    try { closeSync(fd) } catch {}
    return fail('SPAWN_FAILED', '起不来引擎进程：' + (e && e.message), { jobId })
  }
  try { closeSync(fd) } catch {}

  const startedAt = new Date().toISOString()
  const entry = {
    jobId: String(jobId || ''),
    pid: child.pid,
    log: logPath,
    workdir,
    startedAt,
    args: args.slice(1),
    exitCode: null,
    alive: true,
  }
  // 给 cover_run 的「早退看门狗」用：exit 事件只发一次，这里转成可复用的 promise
  const exited = new Promise((done) => {
    child.on('exit', (code) => done(typeof code === 'number' ? code : -1))
  })
  JOBS.set(String(jobId || ''), { child, ...entry, exited })

  // 落盘：DSH 重启后 cover_cancel 还能找到 pid
  try { writeJsonAtomic(join(workdir, RUN_STATE_FILE), entry) } catch {}
  try { writeFileAtomic(join(logs, 'engine.args.txt'), [ready.script, ...args].join(' ') + '\n') } catch {}
  try { appendFileSync(logPath, '[' + startedAt + '] start pid=' + String(child.pid) + ' ' + args.slice(1).join(' ') + '\n') } catch {}

  child.on('exit', (code, signal) => {
    const cur = JOBS.get(String(jobId || ''))
    if (cur) { cur.exitCode = typeof code === 'number' ? code : -1; cur.alive = false; cur.signal = signal || null }
    try {
      appendFileSync(logPath, '[' + new Date().toISOString() + '] exit code=' + String(code) + '\n')
    } catch {}
    try {
      const prev = readJsonSafe(join(workdir, RUN_STATE_FILE), null)
      if (prev && prev.pid === entry.pid) {
        writeJsonAtomic(join(workdir, RUN_STATE_FILE), {
          ...prev,
          alive: false,
          exitCode: typeof code === 'number' ? code : -1,
          endedAt: new Date().toISOString(),
        })
      }
    } catch {}
  })
  child.on('error', (e) => {
    const cur = JOBS.get(String(jobId || ''))
    if (cur) { cur.alive = false; cur.error = String((e && e.message) || e) }
    try { appendFileSync(logPath, '[' + new Date().toISOString() + '] spawn error: ' + String((e && e.message) || e) + '\n') } catch {}
  })
  try { child.unref() } catch {}

  return { ok: true, jobId: String(jobId || ''), pid: child.pid, log: logPath, startedAt, args: args.slice(1) }
}

// 等「刚启动就退出」：超时返回 { exited:false }，调用方据此继续走非阻塞路径
export function waitForEarlyExit(jobId, timeoutMs = 2500) {
  const e = JOBS.get(String(jobId || ''))
  if (!e || !e.exited) return Promise.resolve({ exited: false, code: null })
  const limit = Math.max(0, Number(timeoutMs) || 0)
  let timer = null
  return Promise.race([
    e.exited.then((code) => ({ exited: true, code: code === null ? -1 : code })),
    new Promise((done) => { timer = setTimeout(() => done({ exited: false, code: null }), limit) }),
  ]).then((res) => {
    if (timer) clearTimeout(timer)
    return res
  })
}

// 内存里的运行登记（本进程内）
export function runningEntry(jobId) {
  const e = JOBS.get(String(jobId || ''))
  if (!e) return null
  if (e.child && e.child.exitCode !== null && e.exitCode === null) e.exitCode = e.child.exitCode
  return e
}

// 落盘的运行登记（跨 DSH 重启）
export function persistedRun(workdir) {
  const st = readJsonSafe(join(workdir, RUN_STATE_FILE), null)
  if (!st || typeof st !== 'object') return null
  return { ...st, alive: st.alive !== false && isAlive(st.pid) }
}

export function isJobRunning(jobId, workdir) {
  const mem = runningEntry(jobId)
  if (mem && mem.alive && isAlive(mem.pid)) return { running: true, pid: mem.pid, via: 'memory' }
  const disk = persistedRun(workdir)
  if (disk && disk.alive) return { running: true, pid: disk.pid, via: 'disk' }
  return { running: false, pid: 0, via: '' }
}

// 先礼后兵：引擎 CLI cancel（让引擎自己收尾）→ 不在了就 taskkill 整棵树
export async function cancelJob(opts, settings) {
  const st = settings || loadSettings()
  const { jobId, workdir, jobFile } = opts || {}
  const mem = runningEntry(jobId)
  const disk = workdir ? persistedRun(workdir) : null
  const running = isJobRunning(jobId, workdir)
  if (!running.running) {
    return fail('JOB_NOT_RUNNING', '这个 job 现在没有在跑（可能已经结束，用 cover_status 看一眼）。', { jobId })
  }
  let cli = null
  if (jobFile && existsSync(jobFile)) {
    cli = await runCli(['cancel', '--job', jobFile], { timeoutMs: 25000, cwd: workdir || undefined, settings: st })
  }
  const pid = running.pid || (disk && disk.pid) || (mem && mem.pid) || 0
  let killed = false
  if (pid && isAlive(pid)) {
    const k = await killTree(pid)
    killed = !!k.killed
  }
  return {
    ok: true,
    jobId: String(jobId || ''),
    pid,
    killed,
    cli: cli ? { ok: !!cli.ok, code: cli.code, timedOut: !!cli.timedOut } : null,
    note: cli && cli.ok ? '引擎已接受取消' : '引擎 CLI 没确认，已直接结束进程树',
  }
}

// Windows 上 python 会再起子进程（ffmpeg / 训练），必须整棵树杀
export function killTree(pid) {
  return new Promise((done) => {
    const n = Number(pid)
    if (!Number.isFinite(n) || n <= 0) return done({ ok: false, killed: false, reason: 'bad-pid' })
    if (!isAlive(n)) return done({ ok: true, killed: false, reason: 'not-running' })
    if (process.platform === 'win32') {
      let child
      try {
        child = spawn('taskkill', ['/pid', String(n), '/T', '/F'], { windowsHide: true, stdio: 'ignore' })
      } catch {
        try { process.kill(n) } catch {}
        return done({ ok: true, killed: true, via: 'kill' })
      }
      child.on('error', () => {
        try { process.kill(n) } catch {}
        done({ ok: true, killed: true, via: 'kill' })
      })
      child.on('close', () => done({ ok: true, killed: true, via: 'taskkill' }))
      return
    }
    try {
      process.kill(-n, 'SIGTERM')
      done({ ok: true, killed: true, via: 'SIGTERM' })
    } catch {
      try {
        process.kill(n, 'SIGTERM')
        done({ ok: true, killed: true, via: 'SIGTERM' })
      } catch (e) {
        done({ ok: false, killed: false, reason: redactPaths(e && e.message) })
      }
    }
  })
}

// 历史 job：扫 workRoot 下的目录，能读到 job.json 的才列出来
export function listJobs(workRoot, opts = {}) {
  const limit = Math.min(Math.max(Number(opts.limit) || 20, 1), 100)
  const rows = []
  let names = []
  try { names = readdirSync(workRoot) } catch { return { ok: true, jobs: [], total: 0 } }
  for (const name of names) {
    const dir = join(workRoot, name)
    let st
    try { st = statSync(dir) } catch { continue }
    if (!st.isDirectory()) continue
    // _deleted 等内部目录不是 job（软删的 job 就藏在里面）
    if (String(name).startsWith('_')) continue
    const jobFile = findJobFile(dir)
    if (!jobFile) continue
    const job = readJob(jobFile)
    if (!job) continue
    const mixDir = join(dir, 'mix')
    const exportDir = join(dir, 'export')
    let hasMp3 = false
    try {
      hasMp3 = readdirSync(exportDir).some((f) => String(f).toLowerCase().endsWith('.mp3'))
    } catch {}
    if (!hasMp3 && job.result && typeof job.result.mp3 === 'string') {
      try { hasMp3 = existsSync(job.result.mp3) } catch {}
    }
    const live = isJobRunning(name, dir)
    rows.push({
      // ★ 插件侧 jobId 永远是 workdir 目录名：cover_setup 返回的、cover_run/status 能解析的都是它。
      //   引擎会给 job.json 生成自己的 id（sanitize 过、可能和目录名不同），只当附加信息。
      jobId: name,
      engineId: job.jobId || null,
      dirName: name,
      singer: job.singer,
      mode: job.mode,
      target: job.target ? basename(String(job.target)) : '',
      createdAt: job.createdAt,
      updatedAt: job.updatedAt,
      status: job.cancelled ? 'cancelled' : (job.failedStage ? 'failed' : (job.done >= job.total && job.total ? 'done' : 'running-or-idle')),
      progress: String(job.done) + '/' + String(job.total),
      done: job.done,
      total: job.total,
      currentStage: job.currentStage,
      engineId: job.jobId || null,
      running: !!live.running,
      pid: live.pid || 0,
      cancelled: !!job.cancelled,
      hasMix: existsSync(mixDir),
      hasMp3,
      mtime: st.mtimeMs,
    })
  }
  rows.sort((a, b) => (b.mtime || 0) - (a.mtime || 0))
  return { ok: true, jobs: rows.slice(0, limit), total: rows.length }
}
