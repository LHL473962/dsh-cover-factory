// tests/e2e.mjs —— 用替身引擎把六个工具跑一遍（不需要 GPU、不需要真引擎）
//
// 跑法：node tests/e2e.mjs
// 前提：本机有 Python（设置里的 python → 内置运行时 → PATH 的 python）。
// 覆盖：ENGINE_NOT_READY / cover_setup / cover_run 非阻塞 / JOB_RUNNING / cover_status
//       轮询到 done / cover_export 幂等 / cover_list / cover_cancel / 引擎失效后回退读 job.json。
//
// 替身引擎只实现 init|run|status|cancel 四条 CLI（tests/standin-engine/run.py），
// 用来钉住「插件对引擎的假设」，真引擎在 engine/ 下，与本文件无关。

import assert from 'node:assert/strict'
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const ROOT = mkdtempSync(join(tmpdir(), 'cover-factory-e2e-'))
const HOME = join(ROOT, 'home')
const JOBS = join(ROOT, 'jobs')
const OUT = join(ROOT, 'out')
const MAT = join(ROOT, 'material')
const ENGINE = join(ROOT, 'standin-engine')
for (const d of [HOME, MAT, ENGINE]) mkdirSync(d, { recursive: true })
process.env.DSH_COVER_FACTORY_HOME = HOME

const here = (rel) => fileURLToPath(new URL('../' + rel, import.meta.url))
copyFileSync(here('tests/standin-engine/run.py'), join(ENGINE, 'run.py'))

writeFileSync(join(MAT, 'good1.wav'), Buffer.concat([Buffer.from('RIFF'), Buffer.alloc(4096)]))
writeFileSync(join(MAT, 'good2.wav'), Buffer.concat([Buffer.from('RIFF'), Buffer.alloc(4096)]))
writeFileSync(join(MAT, 'notes.txt'), 'not audio')
const TARGET = join(MAT, 'target.wav')
writeFileSync(TARGET, Buffer.concat([Buffer.from('RIFF'), Buffer.alloc(8192)]))

const settingsPath = join(HOME, 'cover-factory-settings.json')
function writeSettings(patch) {
  const base = {
    engineDir: ENGINE,
    python: '',
    outputDir: OUT,
    defaultSteps: 9000,
    allowDuet: false,
    autoDownload: false,
    workRoot: JOBS,
    ffmpeg: 'ffmpeg',
    ...patch,
  }
  writeFileSync(settingsPath, JSON.stringify(base, null, 2), 'utf8')
}
writeSettings({})

const utilsJobFile = (id) => join(JOBS, id, 'job.json')

const registered = []
const routes = []
const ctx = {
  tools: { register(d) { registered.push(d); return d } },
  effect(fn) { return fn() },
  webServer: { register(r) { routes.push(r); return { dispose() {} } } },
}

const mod = await import(pathToFileURL(here('index.js')).href)
mod.apply(ctx)
const tools = Object.fromEntries(registered.map((d) => [d.name, d]))
const call = (n, a) => tools[n].execute(a || {})

// 没装 Python 的机器上明确跳过，而不是假装通过
const sm = await import(pathToFileURL(here('lib/settings.js')).href)
const py = sm.resolvePython({ python: '' })
if (py.source === 'PATH') {
  console.log('SKIP: 本机没找到 Python（设置 python / 内置运行时 / PATH 都没有）')
  try { rmSync(ROOT, { recursive: true, force: true }) } catch {}
  process.exit(0)
}

let pass = 0
const failed = []
async function step(name, fn) {
  try { await fn(); pass += 1; console.log('  ok   ' + name) }
  catch (e) { failed.push(name + ': ' + (e && e.message)); console.log('  FAIL ' + name + ' -> ' + (e && e.message)) }
}

let jobId = ''

await step('缺 run.py 时 cover_setup 返回 ENGINE_NOT_READY，且 message 不带绝对路径', async () => {
  writeSettings({ engineDir: join(ROOT, 'no-engine') })
  const r = await call('cover_setup', { singer: '测试歌手', folder: MAT, target: TARGET })
  assert.equal(r.ok, false)
  assert.equal(r.error, 'ENGINE_NOT_READY')
  assert.ok(!/[A-Za-z]:[\\/]/.test(r.message), 'message 泄漏路径：' + r.message)
  writeSettings({})
})

await step('cover_setup：jobId / job.json / 预筛 / 素材扫描 / eta', async () => {
  const r = await call('cover_setup', {
    singer: '测试歌手',
    refs: [join(MAT, 'good1.wav')],
    target: TARGET,
    steps: 9000,
    mode: 'import',
    folder: MAT,
    shift: 0,
  })
  assert.equal(r.ok, true, JSON.stringify(r))
  assert.ok(r.jobId, '没有 jobId')
  assert.ok(existsSync(r.jobFile), 'job.json 不存在：' + r.jobFile)
  assert.equal(r.preview.available, false)
  assert.equal(r.preview.reason, 'stems-pending')
  assert.equal(r.preview.sources.audioCount, 3)
  assert.ok(r.preview.sources.files.includes('good1.wav'))
  assert.ok(r.eta.includes('55 分钟'), 'eta 应为 RTX 5060 实测的 55 分钟：' + r.eta)
  jobId = r.jobId
  const job = JSON.parse(readFileSync(r.jobFile, 'utf8'))
  assert.equal(job.singer, '测试歌手')
  assert.equal(Object.keys(job.stages).length, 10)
  assert.equal(job.options.steps, 9000)

  // job_config.json 必须对齐引擎侧 fixtures/config_import_*.json 的形状
  const cfg = JSON.parse(readFileSync(r.config, 'utf8'))
  assert.equal(cfg.version, 1)
  assert.equal(cfg.singer, '测试歌手')
  assert.equal(cfg.mode, 'import')
  assert.equal(cfg.sources.folder, MAT)
  assert.equal(cfg.target.file, TARGET)
  assert.equal(cfg.target.title, 'target')
  assert.equal(typeof cfg.output.dir, 'string')
  assert.equal(cfg.qa.minGapRate, 12)
  assert.equal(cfg.options.steps, 9000)
  assert.equal(cfg.options.shift, 0, 'shift=0 必须显式写进 job_config')
  assert.deepEqual(cfg.refs, [join(MAT, 'good1.wav')], '好球曲要透传到 job_config.refs')
  assert.equal(cfg.id, undefined, '夹具里没有 id，别自创')

  // 返回里要有 shift 说明（正=升高）
  assert.equal(r.shift, 0)
  assert.ok(String(r.shiftNote).includes('原调'), 'shiftNote=' + r.shiftNote)
})

await step('cover_run 立刻返回 pid（不阻塞对话）', async () => {
  const t0 = Date.now()
  const r = await call('cover_run', { jobId })
  const dt = Date.now() - t0
  assert.equal(r.ok, true, JSON.stringify(r))
  assert.ok(r.pid > 0)
  assert.ok(dt < 8000, 'cover_run 阻塞了 ' + dt + 'ms')
  assert.ok(existsSync(r.log), '日志文件没建：' + r.log)
})

await step('重复 cover_run 返回 JOB_RUNNING（不会起两个引擎）', async () => {
  const r = await call('cover_run', { jobId })
  assert.equal(r.ok, false)
  assert.equal(r.error, 'JOB_RUNNING')
})

await step('cover_status 看到阶段推进并最终 done，日志尾部已脱敏', async () => {
  let last = null
  for (let i = 0; i < 60; i++) {
    last = await call('cover_status', { jobId })
    assert.equal(last.ok, true, JSON.stringify(last))
    if (last.state === 'done') break
    await new Promise((res) => setTimeout(res, 250))
  }
  assert.equal(last.state, 'done', '最终状态=' + last.state + ' progress=' + JSON.stringify(last.progress))
  assert.equal(last.progress.done, 10)
  assert.equal(last.engineCli, 'ok')
  assert.ok(last.logTail.length > 0, '没有日志尾部')
  assert.ok(!last.logTail.some((l) => /[A-Za-z]:[\\/]/.test(l)), '日志尾部泄漏路径')
})

await step('cover_export 交付 mp3 + 报告，且幂等', async () => {
  const r = await call('cover_export', { jobId })
  assert.equal(r.ok, true, JSON.stringify(r))
  assert.equal(r.format, 'mp3')
  assert.ok(existsSync(r.output), '成品不存在：' + r.output)
  assert.ok(r.output.endsWith('测试歌手-target.mp3'), r.output)
  assert.ok(existsSync(r.report), '报告不存在：' + r.report)
  assert.ok(readFileSync(r.report, 'utf8').includes('质检'), '报告缺质检段')
  const again = await call('cover_export', { jobId })
  assert.equal(again.ok, true)
  assert.equal(again.output, r.output)
  // 引擎在 job.result.mp3 里给过路径：交付时不该再改命名
  const job2 = JSON.parse(readFileSync(utilsJobFile(jobId), 'utf8'))
  assert.ok(job2.result && job2.result.mp3, '替身引擎应写入 result.mp3')
  assert.equal(r.reportSource, 'plugin', '没有引擎 report.md 时应由插件生成')
})

await step('cover_list 列出这个 job 及其进度', async () => {
  const r = await call('cover_list', {})
  assert.equal(r.ok, true)
  assert.equal(r.total, 1)
  assert.equal(r.jobs[0].jobId, jobId)
  assert.equal(r.jobs[0].status, 'done')
  assert.equal(r.jobs[0].progress, '10/10')
})

await step('cover_cancel 能停掉正在跑的 job', async () => {
  const r1 = await call('cover_run', { jobId, resume: true })
  assert.equal(r1.ok, true, JSON.stringify(r1))
  const r2 = await call('cover_cancel', { jobId })
  assert.equal(r2.ok, true, JSON.stringify(r2))
  assert.ok(r2.killed === true || (r2.engineCli && r2.engineCli.ok === true), '既没杀进程也没拿到引擎确认')
})

await step('引擎目录失效后 cover_status 回退读 job.json', async () => {
  writeSettings({ engineDir: join(ROOT, 'no-engine') })
  const r = await call('cover_status', { jobId })
  assert.equal(r.ok, true, JSON.stringify(r))
  assert.equal(r.engineCli, 'skipped')
  writeSettings({})
})

await step('面板 GET /jobs 能列出 job', async () => {
  const req = { method: 'GET', url: '/api/cover-factory/jobs', headers: { host: '127.0.0.1:1' } }
  const res = { statusCode: 0, body: '', writeHead(c) { this.statusCode = c }, end(b) { this.body = String(b) } }
  await routes[0].handler(req, res)
  const json = JSON.parse(res.body)
  assert.equal(res.statusCode, 200)
  assert.equal(json.ok, true)
  assert.equal(json.total, 1)
})

let jobId2 = ''

await step('cover_setup 带 shift=2：写进 job_config 的 options.shift，返回说明是「升高」', async () => {
  const r = await call('cover_setup', {
    singer: '测试歌手',
    target: TARGET,
    steps: 9000,
    mode: 'import',
    folder: MAT,
    shift: 2,
  })
  assert.equal(r.ok, true, JSON.stringify(r))
  assert.equal(r.shift, 2)
  assert.ok(String(r.shiftNote).includes('升高'), 'shiftNote=' + r.shiftNote)
  const cfg = JSON.parse(readFileSync(r.config, 'utf8'))
  assert.equal(cfg.options.shift, 2)
  assert.equal(cfg.options.steps, 9000, '加 shift 不能动 steps')
  jobId2 = r.jobId
})

await step('cover_delete 软删：移到 _deleted/，列表里消失（回收站不算 job）', async () => {
  const r = await call('cover_delete', { jobId: jobId2 })
  assert.equal(r.ok, true, JSON.stringify(r))
  assert.equal(r.mode, 'soft')
  assert.ok(String(r.recycledTo).includes('_deleted'))
  assert.ok(existsSync(join(r.recycledTo, 'job.json')), '回收目录应保留 job.json')
  assert.equal(existsSync(join(JOBS, jobId2)), false)
  const list = await call('cover_list', {})
  assert.equal(list.total, 1, '软删后只剩第一个 job')
  assert.ok(!list.jobs.some((j) => j.jobId === jobId2))
  assert.ok(!list.jobs.some((j) => String(j.jobId).startsWith('_')))
})

try { rmSync(ROOT, { recursive: true, force: true }) } catch {}

console.log('')
console.log('e2e passed: ' + pass + ', failed: ' + failed.length)
if (failed.length) {
  for (const f of failed) console.log(' - ' + f)
  process.exitCode = 1
} else {
  console.log('E2E OK')
}
