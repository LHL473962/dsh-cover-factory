// tests/smoke.mjs —— 不依赖 DSH 的最小单测：mock ctx 验证工具注册 + 参数校验
//
// 跑法：node tests/smoke.mjs
// 只用本机临时目录，不碰 ~/.dsh、不碰 engine/、不启任何引擎进程。

import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

const HOME = mkdtempSync(join(tmpdir(), 'cover-factory-smoke-'))
process.env.DSH_COVER_FACTORY_HOME = HOME

let checks = 0
const failures = []
async function check(name, fn) {
  try {
    await fn()
    checks += 1
    console.log('  ok   ' + name)
  } catch (e) {
    failures.push(name + ' -> ' + (e && e.message))
    console.log('  FAIL ' + name + ' -> ' + (e && e.message))
  }
}

// ── mock ctx（只实现插件真正用到的那几个方法）─────────────────────────────
const registered = []
const routes = []
const ctx = {
  tools: {
    register(def) {
      registered.push(def)
      return def
    },
  },
  effect(fn) {
    return fn()
  },
  webServer: {
    register(route) {
      routes.push(route)
      return { dispose() {} }
    },
  },
}

import { doctorBlockCreate, normalizeDoctor, normalizeTorchSuggestion } from '../lib/doctor.js'
import { buildJobConfig, classifyRefs, etaText, summarizeSelectReport } from '../lib/tools.js'
import { isAlive } from '../lib/util.js'

const pkg = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'))
const mod = await import('../index.js')

const byName = (n) => registered.find((d) => d.name === n)
const call = async (name, args) => byName(name).execute(args || {})
const here = (rel) => fileURLToPath(new URL('../' + rel, import.meta.url))

// ── 1. 包清单与入口契约 ─────────────────────────────────────────────────
await check('package.json: name/version/type', () => {
  assert.equal(pkg.name, 'dsh-cover-factory')
  assert.equal(pkg.version, '0.1.0')
  assert.equal(pkg.type, 'module')
})

await check('package.json: engines.dsh >= 0.2.0-rc.2', () => {
  assert.ok(pkg.engines && pkg.engines.dsh, 'engines.dsh 缺失')
  assert.ok(pkg.engines.dsh.includes('0.2.0-rc.2'))
  assert.ok(pkg.engines.node)
})

await check('package.json: dsh.bundle.patch + dsh.client + files', () => {
  assert.equal(pkg.dsh.bundle.patch, './cordis.patch.yml')
  assert.equal(pkg.dsh.client.platform, 'web')
  assert.ok(Array.isArray(pkg.dsh.client.inject))
  for (const f of ['index.js', 'lib', 'engine', 'preset', 'DESIGN.md']) assert.ok(pkg.files.includes(f), 'files 缺 ' + f)
  for (const k of ['index.js', 'lib/engineClient.js', 'lib/tools.js', 'lib/panel.js', 'lib/settings.js', 'preset/prompts.md', 'tests/smoke.mjs']) {
    assert.ok(existsSync(here(k)), '缺文件 ' + k)
  }
})

await check('index.js: name / inject / apply / VERSION', () => {
  assert.equal(mod.name, 'cover-factory')
  assert.deepEqual(mod.inject, ['tools', 'webServer'])
  assert.equal(typeof mod.apply, 'function')
  assert.equal(mod.VERSION, pkg.version)
})

// ── 2. 工具注册 ─────────────────────────────────────────────────────────
mod.apply(ctx)

await check('注册了且只注册了 8 个 cover_* 工具', () => {
  const names = registered.map((d) => d.name).sort()
  assert.deepEqual(names, ['cover_cancel', 'cover_delete', 'cover_doctor', 'cover_export', 'cover_list', 'cover_run', 'cover_setup', 'cover_status'])
})

await check('每个工具：description/parameters/output/execute 形状合法', () => {
  for (const def of registered) {
    assert.equal(typeof def.description, 'string', def.name + ' description')
    assert.ok(def.description.length > 6, def.name + ' description 太短')
    assert.equal(def.parameters.type, 'object', def.name + ' parameters.type')
    assert.equal(typeof def.parameters.properties, 'object', def.name + ' parameters.properties')
    assert.equal(def.parameters.additionalProperties, false, def.name + ' 应拒绝未知参数')
    assert.equal(typeof def.execute, 'function', def.name + ' execute')
    assert.equal(typeof def.output.render, 'function', def.name + ' output.render')
  }
})

await check('cover_run / cover_status 描述里写明 LOW_VRAM 是唯一硬件决策', () => {
  for (const name of ['cover_run', 'cover_status']) {
    const d = byName(name).description
    assert.ok(d.includes('LOW_VRAM'), name + ' 描述缺 LOW_VRAM：' + d)
    assert.ok(d.includes('硬件'), name + ' 描述缺硬件口径')
  }
  assert.ok(byName('cover_run').description.includes('训练阶段不会因硬件退出'), byName('cover_run').description)
})

await check('必填参数声明正确', () => {
  assert.deepEqual(byName('cover_setup').parameters.required, ['singer'])
  for (const n of ['cover_run', 'cover_status', 'cover_cancel', 'cover_export']) {
    assert.deepEqual(byName(n).parameters.required, ['jobId'], n + ' required')
  }
  assert.equal(byName('cover_list').parameters.required, undefined)
  assert.deepEqual(byName('cover_setup').parameters.properties.mode.enum, ['import', 'local', 'auto'])
  assert.equal(byName('cover_setup').parameters.properties.steps.type, 'number')
  assert.equal(byName('cover_setup').parameters.properties.shift.type, 'number')
  assert.equal(byName('cover_setup').parameters.properties.shift.enum, undefined, 'shift 的范围由校验兜，不靠 enum')
  assert.equal(byName('cover_setup').parameters.properties.refs.type, 'array')
})

// ── 3. 参数校验：全部 ok:false，且绝不抛 ─────────────────────────────────
// engineDir 指到不存在的目录：校验通过后必然停在 ENGINE_NOT_READY，测试不启 Python
writeFileSync(join(HOME, 'cover-factory-settings.json'), JSON.stringify({ engineDir: join(HOME, 'no-engine') }), 'utf8')

const BAD = [
  ['cover_setup', {}, 'MISSING_SINGER'],
  ['cover_setup', { singer: '测试' }, 'MISSING_FOLDER'],
  ['cover_setup', { singer: '测试', mode: 'yolo' }, 'BAD_MODE'],
  // 抓取 limit 越界/非整数（1-50）
  ['cover_setup', { singer: '测试', mode: 'auto', limit: 0 }, 'BAD_LIMIT'],
  ['cover_setup', { singer: '测试', mode: 'auto', limit: 999 }, 'BAD_LIMIT'],
  ['cover_setup', { singer: '测试', mode: 'auto', limit: 'abc' }, 'BAD_LIMIT'],
  ['cover_setup', { singer: '测试', folder: join(HOME, 'no-such-dir') }, 'FOLDER_NOT_FOUND'],
  ['cover_setup', { singer: '测试', steps: 10, folder: HOME }, 'BAD_STEPS'],
  ['cover_setup', { singer: '测试', folder: HOME, target: join(HOME, 'no-such.wav') }, 'TARGET_NOT_FOUND'],
  // shift 越界（-5..+5）
  ['cover_setup', { singer: '测试', folder: HOME, shift: 6 }, 'BAD_SHIFT'],
  ['cover_setup', { singer: '测试', folder: HOME, shift: -6 }, 'BAD_SHIFT'],
  // shift 非整数（不能静默截断成 2）
  ['cover_setup', { singer: '测试', folder: HOME, shift: 2.5 }, 'BAD_SHIFT'],
  ['cover_setup', { singer: '测试', folder: HOME, shift: 'abc' }, 'BAD_SHIFT'],
  ['cover_run', {}, 'MISSING_JOB_ID'],
  ['cover_run', { jobId: 'nope' }, 'JOB_NOT_FOUND'],
  ['cover_run', { jobId: '../escape' }, 'BAD_JOB_ID'],
  ['cover_status', { jobId: 'nope' }, 'JOB_NOT_FOUND'],
  ['cover_cancel', { jobId: 'nope' }, 'JOB_NOT_FOUND'],
  ['cover_export', { jobId: 'nope' }, 'JOB_NOT_FOUND'],
  ['cover_delete', {}, 'MISSING_JOB_ID'],
  ['cover_delete', { jobId: 'nope' }, 'JOB_NOT_FOUND'],
  ['cover_delete', { jobId: '_deleted' }, 'BAD_JOB_ID'],
  ['cover_delete', { jobId: '../escape' }, 'BAD_JOB_ID'],
]

for (const [name, args, code] of BAD) {
  await check(name + ' ' + JSON.stringify(args) + ' -> ' + code, async () => {
    const r = await call(name, args)
    assert.ok(r && typeof r === 'object', '返回值不是对象')
    assert.equal(r.ok, false)
    assert.equal(r.error, code)
    assert.equal(typeof r.message, 'string')
    assert.ok(r.message.length > 0)
    // 路径脱敏：message 里不许出现盘符绝对路径
    assert.ok(!/[A-Za-z]:[\\/]/.test(r.message), 'message 泄漏了绝对路径：' + r.message)
  })
}

await check('合法 shift（-5 / 0 / +5）不被误判为 BAD_SHIFT，能走到引擎检查', async () => {
  for (const shift of [-5, 0, 5, '-3']) {
    const r = await call('cover_setup', { singer: '测试', folder: HOME, shift })
    assert.notEqual(r.error, 'BAD_SHIFT', 'shift=' + shift + ' 被误判：' + r.message)
    assert.equal(r.error, 'ENGINE_NOT_READY', 'shift=' + shift + ' 的流程异常：' + JSON.stringify(r))
  }
})

await check('BAD_SHIFT 的 message 说清范围与整首同比例', async () => {
  const r = await call('cover_setup', { singer: '测试', folder: HOME, shift: 9 })
  assert.equal(r.error, 'BAD_SHIFT')
  assert.ok(r.message.includes('-5'), r.message)
  assert.ok(r.message.includes('+5'), r.message)
  assert.ok(r.message.includes('整数'), r.message)
  assert.ok(r.message.includes('同比例'), r.message)
})

await check('mode=auto 未确认：needsConfirm，不建 job、不联网', async () => {
  const r = await call('cover_setup', { singer: '测试', mode: 'auto' })
  assert.equal(r.ok, true, 'needsConfirm 不是失败')
  assert.equal(r.needsConfirm, true)
  assert.equal(r.jobCreated, false)
  assert.equal(r.jobId, undefined, '未确认不该建 job')
  assert.equal(r.copyrightAck, false)
  assert.ok(Array.isArray(r.warnings) && r.warnings.length >= 2, '要有多条风险提示')
  assert.ok(r.warnings.join(' ').includes('版权'), '提示里要有版权风险')
  assert.ok(String(r.next).includes('copyrightAck:true'), 'next 要教怎么确认')
})

await check('mode=auto 已确认：过了确认闸，停在引擎检查', async () => {
  const r = await call('cover_setup', { singer: '测试', mode: 'auto', copyrightAck: true, limit: 5 })
  assert.equal(r.error, 'ENGINE_NOT_READY', JSON.stringify(r))
})

await check('import 模式忽略 search/limit/copyrightAck（不因 limit=999 报错）', async () => {
  const r = await call('cover_setup', { singer: '测试', folder: HOME, mode: 'import', copyrightAck: true, search: 'X', limit: 999 })
  assert.equal(r.error, 'ENGINE_NOT_READY', 'import 不该被这些参数影响：' + JSON.stringify(r))
})

await check('buildJobConfig：auto 模式 sources/options 正确', () => {
  const c = buildJobConfig({ singer: '测试', mode: 'auto', search: '周杰伦', limit: 5, copyrightAck: true, steps: 9000, shift: 0, outputDir: 'X' })
  assert.deepEqual(c.sources, { search: '周杰伦', limit: 5, author: '测试' })
  assert.equal(c.options.copyrightAck, true)
  assert.equal(c.options.steps, 9000)
  const d = buildJobConfig({ singer: '测试', mode: 'auto', copyrightAck: false })
  assert.equal(d.sources.search, '测试', 'search 默认取 singer')
  assert.equal(d.sources.limit, 8)
  assert.equal(d.options.copyrightAck, false)
})

await check('eta 文案与实测一致：5000≈30 分钟 / 9000≈55 分钟 / 18000≈1 小时 50 分', () => {
  assert.ok(etaText(5000).includes('30 分钟'), etaText(5000))
  assert.ok(etaText(9000).includes('55 分钟'), etaText(9000))
  assert.ok(etaText(18000).includes('1 小时 50 分'), etaText(18000))
  assert.ok(etaText(3000).includes('18 分钟'), etaText(3000))
  assert.ok(etaText(9000).includes('RTX 5060'), '要注明实测机型：' + etaText(9000))
  // 非档位值按 0.37 秒/步线性估：10000 步 ≈ 62 分钟
  assert.ok(etaText(10000).includes('1 小时 2 分'), etaText(10000))
  assert.ok(etaText(10000).includes('0.37 秒/步'), etaText(10000))
})

await check('buildJobConfig：import 模式忽略 search/limit/copyrightAck', () => {
  const c = buildJobConfig({ singer: '测试', mode: 'import', folder: 'F', search: '周杰伦', limit: 3, copyrightAck: true, steps: 9000, shift: 2 })
  assert.deepEqual(c.sources, { folder: 'F' })
  assert.equal(c.options.copyrightAck, undefined, 'import 不该写 copyrightAck')
  assert.equal(c.options.shift, 2)
})

await check('refSearch：默认 false，job 配置里不出现 refSearch', () => {
  const c = buildJobConfig({ singer: '测试', mode: 'import', folder: 'F', refs: ['歌名'], steps: 9000 })
  assert.equal(c.options.refSearch, undefined, '默认不该写 refSearch')
  assert.equal(c.options.copyrightAck, undefined, '默认 import 不该写 copyrightAck')
  assert.equal(byName('cover_setup').parameters.properties.refSearch.type, 'boolean')
  assert.equal(byName('cover_setup').parameters.required.includes('refSearch'), false)
})

await check('refSearch=true 但没版权确认 -> needsConfirm，不建 job', async () => {
  const r = await call('cover_setup', { singer: '测试', folder: HOME, refs: ['某首歌名'], refSearch: true })
  assert.equal(r.ok, true, JSON.stringify(r))
  assert.equal(r.needsConfirm, true)
  assert.equal(r.jobCreated, false)
  assert.equal(r.reason, 'REF_SEARCH_NEEDS_ACK')
  assert.equal(r.jobId, undefined, '未确认不该建 job')
  assert.ok(r.warnings.join(' ').includes('好球曲'), JSON.stringify(r.warnings))
  assert.ok(r.warnings.join(' ').includes('版权'), JSON.stringify(r.warnings))
  assert.ok(String(r.next).includes('copyrightAck:true'), r.next)
})

await check('refSearch=true + copyrightAck=true -> 过闸（引擎缺失时停在 ENGINE_NOT_READY）', async () => {
  const r = await call('cover_setup', { singer: '测试', folder: HOME, refs: ['某首歌名'], refSearch: true, copyrightAck: true })
  assert.equal(r.error, 'ENGINE_NOT_READY', JSON.stringify(r))
})

await check('mode=local：合法（不需要 folder），config.sources 写 {local_clients:true, clients, keyword}', async () => {
  const r = await call('cover_setup', { singer: '测试', mode: 'local', steps: 5000 })
  assert.equal(r.ok, false, JSON.stringify(r))
  assert.equal(r.error, 'ENGINE_NOT_READY', 'local 模式不该要求 folder：' + JSON.stringify(r))
  const b = { singer: '周杰伦', mode: 'local', steps: 9000, shift: 0, outputDir: 'O', refs: [] }
  const c = buildJobConfig(b)
  // 引擎只认 mode=import/auto，本机客户端靠 sources.local_clients 识别
  assert.equal(c.mode, 'import', '落盘时 mode 要写成引擎认的 import')
  assert.equal(c.sources.local_clients, true)
  assert.deepEqual(c.sources.clients, ['netease', 'qqmusic', 'kugou', 'kuwo'], '默认全部客户端（id 对齐引擎）')
  assert.equal(c.sources.keyword, '周杰伦', 'keyword 默认取 singer')
  const picked = buildJobConfig({ ...b, clients: ['netease', 'qq'], keyword: '稻香' })
  assert.deepEqual(picked.sources.clients, ['netease', 'qqmusic'], "'qq' 是别名，落盘统一成 qqmusic")
  assert.equal(picked.sources.keyword, '稻香')
  const schema = byName('cover_setup').parameters.properties
  assert.ok(schema.mode.enum.includes('local'))
  assert.deepEqual(schema.clients.items.enum, ['netease', 'qqmusic', 'kugou', 'kuwo'])
})

await check('mode=local：非法客户端名 -> BAD_CLIENT（列出可选项）', async () => {
  const r = await call('cover_setup', { singer: '测试', mode: 'local', clients: ['netease', 'spotify'] })
  assert.equal(r.ok, false, JSON.stringify(r))
  assert.equal(r.error, 'BAD_CLIENT')
  assert.ok(String(r.message).includes('spotify'), r.message)
  assert.ok(String(r.message).includes('netease'), r.message)
  assert.deepEqual(r.bad, ['spotify'])
  assert.deepEqual(r.allowed, ['netease', 'qqmusic', 'kugou', 'kuwo'])
  assert.ok(!/[A-Za-z]:[\\/]/.test(r.message), 'message 泄漏路径：' + r.message)
})

await check('mode=local：中文客户端名与去重也认', () => {
  const c = buildJobConfig({ singer: '测试', mode: 'local', clients: ['网易云', 'QQ音乐', 'netease'] })
  assert.deepEqual(c.sources.clients, ['netease', 'qqmusic'], '中文别名归一 + 去重')
})

await check('voice：默认 auto，male/female 合法并透传 options.voice', () => {
  const b = { singer: '测试', mode: 'import', folder: 'F', steps: 9000, shift: 0, outputDir: 'O' }
  assert.equal(buildJobConfig(b).options.voice, 'auto', '默认应该是 auto')
  assert.equal(buildJobConfig({ ...b, voice: 'male' }).options.voice, 'male')
  assert.equal(buildJobConfig({ ...b, voice: 'female' }).options.voice, 'female')
  const schema = byName('cover_setup').parameters.properties.voice
  assert.deepEqual(schema.enum, ['auto', 'female', 'male'])
  assert.ok(schema.description.includes('男歌手'), '描述要提示男歌手选 male：' + schema.description)
  assert.equal(byName('cover_setup').parameters.required.includes('voice'), false)
})

await check('voice：非法值 -> BAD_VOICE（不建 job）', async () => {
  const r = await call('cover_setup', { singer: '测试', folder: HOME, voice: 'robot' })
  assert.equal(r.ok, false, JSON.stringify(r))
  assert.equal(r.error, 'BAD_VOICE')
  assert.ok(String(r.message).includes('male'), r.message)
  assert.ok(!/[A-Za-z]:[\\/]/.test(r.message), 'message 泄漏路径：' + r.message)
})

await check('voice：male/female 合法（引擎缺失时停在 ENGINE_NOT_READY）', async () => {
  for (const voice of ['male', 'female', 'auto', '男']) {
    const r = await call('cover_setup', { singer: '测试', folder: HOME, voice })
    assert.notEqual(r.error, 'BAD_VOICE', voice + ' 被误判：' + r.message)
    assert.equal(r.error, 'ENGINE_NOT_READY', voice + ' -> ' + JSON.stringify(r))
  }
})

await check('voiceGuess：从 select/report.json 提取每首声部判断与排除原因', () => {
  const s = summarizeSelectReport({
    voiceGuess: 'female',
    items: [
      { file: 'a.wav', kept: true, voice: 'female' },
      { file: 'b.wav', kept: false, voice: 'male', reason: '声部不符（期望 female）' },
      { file: 'c.wav', kept: false, reason: '男声区帧 38% > 15%（疑似对唱）' },
    ],
  }, 'female')
  assert.equal(s.voiceGuess.expected, 'female')
  assert.equal(s.voiceGuess.target, 'female')
  assert.equal(s.voiceGuess.songs.length, 3)
  assert.deepEqual(s.voiceGuess.songs.map((x) => x.voice), ['female', 'male', null])
  assert.equal(s.keptCount, 1)
  assert.equal(s.excludedCount, 2)
  assert.equal(s.excluded[0].voice, 'male')
  // 没有声部信息时也不崩
  const empty = summarizeSelectReport({ items: [{ file: 'x.wav', kept: true }] }, 'auto')
  assert.equal(empty.voiceGuess.expected, 'auto')
  assert.equal(empty.voiceGuess.songs[0].voice, null)
})

await check('refSearch：确认后 job 配置写 options.refSearch=true（import 同时写 copyrightAck）', () => {
  const c = buildJobConfig({ singer: '测试', mode: 'import', folder: 'F', refs: ['歌名'], refSearch: true, copyrightAck: true, steps: 9000 })
  assert.equal(c.options.refSearch, true)
  assert.equal(c.options.copyrightAck, true)
  assert.deepEqual(c.refs, ['歌名'], '歌名要原样进 job_config.refs')
})

// ── 3.3 依赖体检：normalizeDoctor / doctorBlockCreate（纯函数，不启引擎）──
await check('normalizeDoctor：全绿 -> green，missing 为空，不拦建任务', () => {
  const d = normalizeDoctor({
    ok: true,
    checks: [
      { id: 'python', label: 'Python', status: 'ok', required: true, detail: '3.12' },
      { id: 'ffmpeg', label: 'ffmpeg', status: 'ok', required: true },
      { id: 'ytdlp', label: 'yt-dlp', status: 'ok', required: false },
    ],
  })
  assert.equal(d.health, 'green')
  assert.deepEqual(d.missing, [])
  assert.deepEqual(d.missingRequired, [])
  assert.equal(d.checks.length, 3)
  const gate = doctorBlockCreate({ ok: true, health: d.health, ...d })
  assert.equal(gate.block, false)
})

await check('normalizeDoctor：缺必需 -> red，missing/hints 齐，建任务被拦', () => {
  const d = normalizeDoctor({
    ok: false,
    checks: [
      { id: 'python', label: 'Python', status: 'ok', required: true },
      { id: 'applio', label: 'Applio', status: 'missing', required: true, hint: 'git clone <applio> && pip install -r requirements.txt' },
      { id: 'msst', label: 'MSST', status: 'missing', required: true, hint: '下载 kim 权重' },
      { id: 'ytdlp', label: 'yt-dlp', status: 'ok', required: false },
    ],
  })
  assert.equal(d.health, 'red')
  assert.deepEqual(d.missingRequired.sort(), ['applio', 'msst'])
  assert.ok(d.missing.includes('applio'))
  assert.ok(d.hints.length >= 2, JSON.stringify(d.hints))
  const gate = doctorBlockCreate({ ok: true, health: d.health, ...d })
  assert.equal(gate.block, true)
  assert.deepEqual(gate.missing.sort(), ['applio', 'msst'])
  assert.ok(gate.hints.length >= 2)
})

await check('normalizeDoctor：只缺可选 -> yellow，不拦建任务', () => {
  const d = normalizeDoctor({
    checks: [
      { id: 'python', status: 'ok', required: true },
      { id: 'ytdlp', status: 'missing', required: false, hint: 'pip install yt-dlp' },
    ],
  })
  assert.equal(d.health, 'yellow')
  assert.deepEqual(d.missingOptional, ['ytdlp'])
  assert.deepEqual(d.missingRequired, [])
  assert.equal(doctorBlockCreate({ ok: true, health: d.health, ...d }).block, false)
})

await check('suggestedTorch：引擎真实 plan（嵌在 gpu_torch_match 里）也能取到', () => {
  const d = normalizeDoctor({
    checks: [
      { name: 'torch_cuda', ok: true, found: 'torch 2.8.0+cu128' },
      {
        name: 'gpu_torch_match', ok: false,
        found: 'RTX 4060 Laptop (sm_89) NOT in arch_list [sm_86,sm_90]',
        hint: '当前 torch 不含该架构，需换 cu 版本',
        suggestedTorch: {
          index: 'https://download.pytorch.org/whl/cu121',
          torch: 'torch 2.1–2.5',
          pip: 'D:/Apps/rvc-env/python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cu121',
          gpu: 'RTX 4060 Laptop', capability: 'sm_89',
          note: '40 系官方旧版 cu121 / cu118 都可用',
          alternatives: [{ index: 'https://download.pytorch.org/whl/cu118', torch: 'torch 2.1–2.5' }],
        },
      },
    ],
  })
  assert.equal(d.suggestedTorch.cuda, 'cu121', 'index URL 要归一成 cu121')
  assert.equal(d.suggestedTorch.capability, 'sm_89')
  assert.equal(d.suggestedTorch.torch, 'torch 2.1–2.5')
  assert.equal(d.suggestedTorch.reason, '40 系官方旧版 cu121 / cu118 都可用')
  assert.equal(d.suggestedTorch.alternatives[0].indexUrl, 'https://download.pytorch.org/whl/cu118')
  assert.equal(d.torchInstallCommand, 'D:/Apps/rvc-env/python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cu121')
  // gpu_torch_match 缺卡架构 → 应判红（不是随便装个 torch 就行）
  assert.equal(d.health, 'red')
  assert.ok(d.missingRequired.includes('gpu_torch_match'), JSON.stringify(d.missingRequired))
})

await check('suggestedTorch 透传：字段归一 + 命令按 python/indexUrl 自动拼', () => {
  const d = normalizeDoctor({
    ok: false,
    checks: [{ name: 'torch_cuda', ok: false, found: 'torch 没装' }],
    suggestedTorch: {
      gpu: 'RTX 4060 Laptop', capability: 'sm_89', cu: 'cu121',
      versions: '>=2.1,<2.6', index_url: 'https://download.pytorch.org/whl/cu121',
      python_path: 'D:/Apps/rvc-env/python.exe',
    },
  })
  assert.equal(d.suggestedTorch.gpu, 'RTX 4060 Laptop')
  assert.equal(d.suggestedTorch.capability, 'sm_89')
  assert.equal(d.suggestedTorch.cuda, 'cu121')
  assert.equal(d.suggestedTorch.torch, '>=2.1,<2.6')
  assert.equal(d.suggestedTorch.indexUrl, 'https://download.pytorch.org/whl/cu121')
  assert.equal(d.torchInstallCommand, 'D:/Apps/rvc-env/python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cu121')
  assert.equal(d.torchSuggestionMissing, false)
})

await check('torchInstallCommand 显式给出时原样透传', () => {
  const d = normalizeDoctor({
    checks: [{ name: 'torch_cuda', ok: true }],
    suggestedTorch: { gpu: 'RTX 5060', capability: 'sm_120', cuda: 'cu128' },
    torchInstallCommand: 'X -m pip install torch --index-url U',
  })
  assert.equal(d.torchInstallCommand, 'X -m pip install torch --index-url U')
  assert.equal(d.torchSuggestionMissing, false)
})

await check('suggestedTorch 缺失降级：null + 提示按 INSTALL 矩阵（torch 正常时不硬塞）', () => {
  const bad = normalizeDoctor({ checks: [{ name: 'torch_cuda', ok: false, found: 'cpu only' }] })
  assert.equal(bad.suggestedTorch, null)
  assert.equal(bad.torchInstallCommand, null)
  assert.equal(bad.torchSuggestionMissing, true)
  assert.ok(bad.hints.some((h) => h.includes('INSTALL')), JSON.stringify(bad.hints))
  const ok = normalizeDoctor({ checks: [{ name: 'torch_cuda', ok: true }] })
  assert.equal(ok.suggestedTorch, null)
  assert.equal(ok.hints.some((h) => h.includes('torch 推荐')), false, 'torch 正常时不该塞推荐提示')
  // 纯函数直测
  assert.deepEqual(normalizeTorchSuggestion(null), { suggestedTorch: null, torchInstallCommand: null })
})

await check('doctorBlockCreate：体检本身失败（ok:false）不拦建任务', () => {
  assert.equal(doctorBlockCreate({ ok: false, error: 'DOCTOR_UNAVAILABLE' }).block, false)
  assert.equal(doctorBlockCreate(null).block, false)
})

await check('引擎没就位时 cover_doctor 降级为 ok:false 且指向 INSTALL', async () => {
  const r = await call('cover_doctor', {})
  assert.equal(r.ok, false, JSON.stringify(r))
  assert.equal(r.error, 'ENGINE_NOT_READY')
  assert.ok(String(r.message).includes('INSTALL'), r.message)
  assert.ok(!/[A-Za-z]:[\\/]/.test(r.message), 'message 泄漏路径：' + r.message)
  // 失败时不能编造 torch 推荐
  assert.ok(!r.suggestedTorch, '失败时不该有 suggestedTorch')
  assert.ok(!r.torchInstallCommand, '失败时不该有 torchInstallCommand')
})

// ── 3.4 好球曲（refs）校验 ─────────────────────────────────────────────
await check('classifyRefs：歌名/路径混合正确分类，只有路径查存在性', () => {
  const good = join(HOME, 'ref-good.wav')
  writeFileSync(good, 'RIFF', 'utf8')
  const c = classifyRefs(['言っちゃいけないことばっか浮かぶよな', good, 'D:/nope/x.wav', '', '  '])
  assert.deepEqual(c.names, ['言っちゃいけないことばっか浮かぶよな'])
  assert.deepEqual(c.paths, [good, 'D:/nope/x.wav'])
  assert.deepEqual(c.missing, [{ line: 3, path: 'D:/nope/x.wav' }], '只报路径那一行，歌名不算缺')
  assert.equal(c.all.length, 3, '空行被过滤')
})

await check('refs：只填不存在的歌名不报错（当歌名放行，走到引擎检查）', async () => {
  const r = await call('cover_setup', { singer: '测试', folder: HOME, refs: ['不存在的歌名', '言っちゃいけないことばっか浮かぶよな'] })
  assert.notEqual(r.error, 'REF_NOT_FOUND', '歌名不该报 REF_NOT_FOUND：' + r.message)
  assert.equal(r.error, 'ENGINE_NOT_READY', JSON.stringify(r))
})

await check('refs：歌名/好路径/坏路径混合 -> 只报坏路径那一行', async () => {
  const good = join(HOME, 'ref-good.wav')
  const r = await call('cover_setup', { singer: '测试', folder: HOME, refs: ['某首歌名', good, 'D:/nope/x.wav'] })
  assert.equal(r.error, 'REF_NOT_FOUND')
  assert.ok(r.message.includes('第 3 行'), r.message)
  assert.ok(!r.message.includes('第 1 行'), '不该把歌名那行算错：' + r.message)
})

await check('refs：不存在的文件 -> REF_NOT_FOUND，并指出第几行', async () => {
  const good = join(HOME, 'ref-good.wav')
  writeFileSync(good, 'RIFF', 'utf8')
  const r = await call('cover_setup', { singer: '测试', folder: HOME, refs: [good, join(HOME, 'no-such-ref.wav')] })
  assert.equal(r.ok, false, JSON.stringify(r))
  assert.equal(r.error, 'REF_NOT_FOUND')
  assert.ok(r.message.includes('第 2 行'), 'message 要说第几行：' + r.message)
  assert.ok(!/[A-Za-z]:[\\/]/.test(r.message), 'message 泄漏路径：' + r.message)
})

await check('refs：空行/空白行被忽略，不算错误', async () => {
  const good = join(HOME, 'ref-good.wav')
  const r = await call('cover_setup', { singer: '测试', folder: HOME, refs: ['', '   ', good, ''] })
  assert.notEqual(r.error, 'REF_NOT_FOUND', r.message)
  assert.equal(r.error, 'ENGINE_NOT_READY', JSON.stringify(r))
})

await check('refs：超过 20 条 -> REF_TOO_MANY', async () => {
  const good = join(HOME, 'ref-good.wav')
  const refs = Array.from({ length: 21 }, () => good)
  const r = await call('cover_setup', { singer: '测试', folder: HOME, refs })
  assert.equal(r.ok, false)
  assert.equal(r.error, 'REF_TOO_MANY')
  assert.ok(r.message.includes('20'), r.message)
})

// ── 3.5 cover_run 的版权闸（用真实目录结构的假 job，不启引擎）──────────────
const STAGE_NAMES = ['fetch', 'separate', 'select', 'clean', 'dataset', 'train', 'convert', 'mix', 'qa', 'export']
const jobsRoot = join(HOME, 'cover-factory', 'jobs')

function writeFakeJob(id, mode, copyrightAck) {
  const dir = join(jobsRoot, id)
  mkdirSync(dir, { recursive: true })
  const stages = {}
  for (const s of STAGE_NAMES) stages[s] = { status: 'pending' }
  const job = {
    version: 1,
    id,
    singer: '测试',
    mode,
    sources: mode === 'import' ? { folder: HOME } : { search: '测试', limit: 8, author: '测试' },
    refs: [],
    target: null,
    options: mode === 'import' ? { steps: 9000, shift: 0 } : { steps: 9000, shift: 0, copyrightAck: copyrightAck },
    stages,
    result: null,
  }
  writeFileSync(join(dir, 'job.json'), JSON.stringify(job, null, 2), 'utf8')
  return id
}

await check('cover_run：抓取模式没版权确认 -> COPYRIGHT_ACK_REQUIRED（且 message 脱敏）', async () => {
  const id = writeFakeJob('fake-auto-noack', 'auto', false)
  const r = await call('cover_run', { jobId: id })
  assert.equal(r.ok, false)
  assert.equal(r.error, 'COPYRIGHT_ACK_REQUIRED')
  assert.ok(String(r.message).includes('版权'), r.message)
  assert.ok(!/[A-Za-z]:[\\/]/.test(r.message), 'message 泄漏路径：' + r.message)
})

await check('cover_run：抓取模式已确认 -> 不被版权闸拦（停在引擎检查）', async () => {
  const id = writeFakeJob('fake-auto-ack', 'auto', true)
  const r = await call('cover_run', { jobId: id })
  assert.equal(r.error, 'ENGINE_NOT_READY', JSON.stringify(r))
})

await check('cover_run：import 模式不受版权闸影响', async () => {
  const id = writeFakeJob('fake-import-1', 'import', false)
  const r = await call('cover_run', { jobId: id })
  assert.equal(r.error, 'ENGINE_NOT_READY', JSON.stringify(r))
})

// ── 3.6 cover_delete：软删 / purge / 运行中先取消 ───────────────────────
await check('cover_status 透出引擎 decision（LOW_VRAM）与 decisionCode', async () => {
  const id = writeFakeJob('fake-lowvram', 'import', false)
  const jobPath = join(jobsRoot, id, 'job.json')
  const j = JSON.parse(readFileSync(jobPath, 'utf8'))
  j.decision = { code: 'LOW_VRAM', stage: 'run', reason: '显存不足（当前空闲 3.2G < 5G）：继续训练有 OOM 风险' }
  writeFileSync(jobPath, JSON.stringify(j, null, 2), 'utf8')
  const r = await call('cover_status', { jobId: id })
  assert.equal(r.ok, true, JSON.stringify(r))
  assert.equal(r.state, 'needs_decision')
  assert.equal(r.decisionCode, 'LOW_VRAM')
  assert.equal(r.decision.code, 'LOW_VRAM')
  assert.ok(!/Users\\user/.test(String(r.decision.reason || '')), 'reason 泄漏路径：' + r.decision.reason)
})

await check('cover_delete：缺参 / 不存在 / 非法 mode（且非法 mode 不删）', async () => {
  const a = await call('cover_delete', {})
  assert.equal(a.error, 'MISSING_JOB_ID')
  const b = await call('cover_delete', { jobId: 'nope' })
  assert.equal(b.error, 'JOB_NOT_FOUND')
  const id = writeFakeJob('fake-del-mode', 'import', false)
  const c = await call('cover_delete', { jobId: id, mode: 'explode' })
  assert.equal(c.ok, false)
  assert.equal(c.error, 'BAD_DELETE_MODE')
  assert.ok(existsSync(join(jobsRoot, id)), 'mode 非法时不该删掉任何东西')
})

await check('cover_delete 软删：移到 _deleted/，原 job 消失、回收站不被当成 job', async () => {
  const id = writeFakeJob('fake-del-soft', 'import', false)
  const r = await call('cover_delete', { jobId: id })
  assert.equal(r.ok, true, JSON.stringify(r))
  assert.equal(r.mode, 'soft')
  assert.equal(r.deleted, true)
  assert.ok(String(r.recycledTo).includes('_deleted'), 'recycledTo=' + r.recycledTo)
  assert.ok(existsSync(join(r.recycledTo, 'job.json')), '回收目录里应保留 job.json')
  assert.equal(existsSync(join(jobsRoot, id)), false, '原目录应已移走')
  assert.equal((await call('cover_status', { jobId: id })).error, 'JOB_NOT_FOUND')
  const list = await call('cover_list', { limit: 100 })
  assert.ok(!list.jobs.some((j) => j.jobId === id), '软删后不该出现在列表')
  assert.ok(!list.jobs.some((j) => String(j.jobId).startsWith('_')), '_deleted 不能作为 job 列出')
})

await check('cover_delete purge：物理删除，目录不再存在', async () => {
  const id = writeFakeJob('fake-del-purge', 'import', false)
  const r = await call('cover_delete', { jobId: id, mode: 'purge' })
  assert.equal(r.ok, true, JSON.stringify(r))
  assert.equal(r.mode, 'purge')
  assert.equal(existsSync(join(jobsRoot, id)), false)
})

await check('cover_delete 运行中：先取消再删（进程真的被杀掉）', async () => {
  const id = writeFakeJob('fake-del-running', 'import', false)
  const dummy = spawn(process.execPath, ['-e', 'setInterval(()=>{},1000)'], { stdio: 'ignore' })
  try {
    writeFileSync(join(jobsRoot, id, 'plugin-run.json'), JSON.stringify({
      jobId: id, pid: dummy.pid, startedAt: new Date().toISOString(), alive: true,
    }), 'utf8')
    const r = await call('cover_delete', { jobId: id })
    assert.equal(r.ok, true, JSON.stringify(r))
    assert.equal(r.cancelled, true, '运行中的 job 删除前必须先取消')
    await new Promise((res) => setTimeout(res, 600))
    assert.equal(isAlive(dummy.pid), false, '取消后进程应已结束')
    assert.equal(existsSync(join(jobsRoot, id)), false)
  } finally {
    try { dummy.kill('SIGKILL') } catch {}
  }
})

await check('cover_list 返回 jobs 数组与 total（含刚造的假 job）', async () => {
  const r = await call('cover_list', {})
  assert.equal(r.ok, true)
  assert.ok(Array.isArray(r.jobs))
  assert.equal(typeof r.total, 'number')
  assert.ok(r.total >= 3, '应能列出 3 个假 job，实际 ' + r.total)
  const j = r.jobs[0]
  for (const k of ['jobId', 'singer', 'status', 'progress', 'done', 'total', 'running', 'hasMp3']) {
    assert.ok(k in j, 'cover_list 缺字段 ' + k)
  }
})

await check('任何工具对空对象调用都不抛异常', async () => {
  for (const def of registered) {
    const r = await def.execute({})
    assert.ok(r && typeof r === 'object', def.name + ' 没有返回对象')
    assert.equal(typeof r.ok, 'boolean', def.name + ' 返回缺少 ok')
  }
})

await check('未知参数不会让工具崩', async () => {
  const r = await call('cover_list', { nonsense: 1, limit: 'abc' })
  assert.equal(r.ok, true)
})

// ── 4. 设置：默认值 / 原子写 / 范围钳制 / 坏文件 ────────────────────────
const settingsMod = await import('../lib/settings.js')
const settingsPath = settingsMod.settingsFile()

await check('默认设置：步数 9000 / allowDuet false / autoDownload false / 桌面翻唱工厂', () => {
  const s = settingsMod.loadSettings()
  assert.equal(s.defaultSteps, 9000)
  assert.equal(s.allowDuet, false)
  assert.equal(s.autoDownload, false)
  assert.ok(s.outputDir.endsWith('翻唱工厂'), 'outputDir=' + s.outputDir)
  assert.ok(s.workRoot.includes('jobs'))
})

await check('saveSettings：白名单外键被回报 ignored，步数被钳制，落盘可解析', () => {
  const r = settingsMod.saveSettings({ defaultSteps: 999999, allowDuet: 'true', bogus: 1 })
  assert.equal(r.ok, true)
  assert.deepEqual(r.ignored, ['bogus'])
  assert.equal(r.settings.defaultSteps, 200000)
  assert.equal(r.settings.allowDuet, true)
  assert.ok(existsSync(settingsPath), '设置文件没落盘')
  const disk = JSON.parse(readFileSync(settingsPath, 'utf8'))
  assert.equal(disk.defaultSteps, 200000)
  // 原子写不该留下 tmp 残骸
  const leftovers = readdirSync(HOME).filter((f) => f.includes('.tmp-'))
  assert.deepEqual(leftovers, [])
})

await check('loadSettings 每读一次都与落盘一致', () => {
  const s = settingsMod.loadSettings()
  assert.equal(s.defaultSteps, 200000)
  assert.equal(s.allowDuet, true)
})

await check('设置文件写坏也不抛，回退默认值', () => {
  writeFileSync(settingsPath, '{ 这不是 json', 'utf8')
  const s = settingsMod.loadSettings()
  assert.equal(s.defaultSteps, 9000)
  assert.equal(s.autoDownload, false)
})

await check('resolvePython 在路径不存在时回退（不抛）', () => {
  const r = settingsMod.resolvePython({ python: join(HOME, 'no-python.exe') })
  assert.equal(typeof r.exe, 'string')
  assert.ok(r.exe.length > 0)
  assert.equal(r.source === 'settings', false)
})

// ── 5. 面板路由（可选件）────────────────────────────────────────────────
// 上一节故意把设置文件写坏/改过，这里重新钉回「引擎不存在」，让面板断言可控
writeFileSync(join(HOME, 'cover-factory-settings.json'), JSON.stringify({ engineDir: join(HOME, 'no-engine') }), 'utf8')

await check('注册了前缀路由 /api/cover-factory', () => {
  const r = routes.find((x) => x.path === '/api/cover-factory')
  assert.ok(r, '没注册面板路由')
  assert.equal(r.kind, 'prefix')
  assert.equal(typeof r.handler, 'function')
})

async function fakeRequest(url, opts = {}) {
  const req = {
    method: opts.method || 'GET',
    url,
    headers: { host: '127.0.0.1:1234', ...(opts.headers || {}) },
  }
  const res = {
    statusCode: 0,
    body: '',
    writeHead(code) { this.statusCode = code },
    end(buf) { this.body = String(buf) },
  }
  await routes[0].handler(req, res)
  let json = null
  try { json = JSON.parse(res.body) } catch {}
  return { code: res.statusCode, json }
}

await check('GET /settings 返回 ok:true', async () => {
  const r = await fakeRequest('/api/cover-factory/settings')
  assert.equal(r.code, 200)
  assert.equal(r.json.ok, true)
  assert.equal(typeof r.json.settings.defaultSteps, 'number')
})

await check('跨站 Origin 被 403 拒绝', async () => {
  const r = await fakeRequest('/api/cover-factory/settings', { headers: { origin: 'https://evil.example' } })
  assert.equal(r.code, 403)
  assert.equal(r.json.error, 'ORIGIN_REJECTED')
})

async function fakePost(url, body, opts = {}) {
  const text = JSON.stringify(body || {})
  const req = {
    method: 'POST',
    url,
    headers: { host: '127.0.0.1:1234', ...(opts.headers || {}) },
    async *[Symbol.asyncIterator]() { yield Buffer.from(text, 'utf8') },
  }
  const res = {
    statusCode: 0,
    body: '',
    writeHead(code) { this.statusCode = code },
    end(buf) { this.body = String(buf) },
  }
  await routes[0].handler(req, res)
  let json = null
  try { json = JSON.parse(res.body) } catch {}
  return { code: res.statusCode, json }
}

await check('POST /jobs/create 缺参：MISSING_SINGER / MISSING_FOLDER', async () => {
  const a = await fakePost('/api/cover-factory/jobs/create', {})
  assert.equal(a.code, 200)
  assert.equal(a.json.ok, false)
  assert.equal(a.json.error, 'MISSING_SINGER')
  const b = await fakePost('/api/cover-factory/jobs/create', { singer: '测试' })
  assert.equal(b.json.ok, false)
  assert.equal(b.json.error, 'MISSING_FOLDER', 'import 模式没 folder 要报 MISSING_FOLDER')
})

await check('POST /jobs/create 抓取未确认：needsConfirm，不建 job', async () => {
  const r = await fakePost('/api/cover-factory/jobs/create', { singer: '测试', mode: 'auto' })
  assert.equal(r.code, 200)
  assert.equal(r.json.ok, true)
  assert.equal(r.json.needsConfirm, true)
  assert.equal(r.json.jobId, undefined)
})

await check('POST /jobs/run 不存在的 job：JOB_NOT_FOUND', async () => {
  const r = await fakePost('/api/cover-factory/jobs/run', { jobId: 'nope' })
  assert.equal(r.code, 200)
  assert.equal(r.json.ok, false)
  assert.equal(r.json.error, 'JOB_NOT_FOUND')
})

await check('POST /jobs/cancel|export 不存在的 job：JOB_NOT_FOUND', async () => {
  const c = await fakePost('/api/cover-factory/jobs/cancel', { jobId: 'nope' })
  assert.equal(c.json.ok, false)
  assert.equal(c.json.error, 'JOB_NOT_FOUND')
  const e = await fakePost('/api/cover-factory/jobs/export', { jobId: 'nope' })
  assert.equal(e.json.ok, false)
  assert.equal(e.json.error, 'JOB_NOT_FOUND')
})

await check('面板 POST /doctor：引擎没就位时返回 ok:false（不假装依赖齐全）', async () => {
  const r = await fakePost('/api/cover-factory/doctor', {})
  assert.equal(r.code, 200)
  assert.equal(r.json.ok, false, JSON.stringify(r.json))
  assert.ok(['ENGINE_NOT_READY', 'DOCTOR_UNAVAILABLE'].includes(r.json.error), JSON.stringify(r.json))
  assert.ok(String(r.json.install || '').includes('INSTALL'), JSON.stringify(r.json))
})

await check('体检不可用时创建不被误拦（不会抛 DEPS_MISSING）', async () => {
  const r = await fakePost('/api/cover-factory/jobs/create', { singer: '测试', folder: HOME })
  assert.equal(r.json.ok, false)
  assert.notEqual(r.json.error, 'DEPS_MISSING', '体检失败不该把建任务拦掉：' + JSON.stringify(r.json))
  assert.equal(r.json.error, 'ENGINE_NOT_READY')
})

await check('GET /settings 带 doctor 字段（可为 null）', async () => {
  const r = await fakeRequest('/api/cover-factory/settings')
  assert.equal(r.json.ok, true)
  assert.ok('doctor' in r.json, 'settings 应带 doctor 字段')
})

await check('POST /jobs/delete：不存在的 job -> JOB_NOT_FOUND', async () => {
  const r = await fakePost('/api/cover-factory/jobs/delete', { jobId: 'nope' })
  assert.equal(r.code, 200)
  assert.equal(r.json.ok, false)
  assert.equal(r.json.error, 'JOB_NOT_FOUND')
})

await check('POST /jobs/delete：软删成功（purge 缺省 false），再删报 JOB_NOT_FOUND', async () => {
  const id = writeFakeJob('fake-panel-del', 'import', false)
  const r = await fakePost('/api/cover-factory/jobs/delete', { jobId: id })
  assert.equal(r.json.ok, true, JSON.stringify(r.json))
  assert.equal(r.json.mode, 'soft')
  assert.ok(String(r.json.recycledTo).includes('_deleted'))
  assert.equal(existsSync(join(jobsRoot, id)), false)
  const again = await fakePost('/api/cover-factory/jobs/delete', { jobId: id })
  assert.equal(again.json.error, 'JOB_NOT_FOUND')
})

await check('POST /jobs/create 的 refs 会透传（坏 ref -> REF_NOT_FOUND）', async () => {
  const r = await fakePost('/api/cover-factory/jobs/create', {
    singer: '测试', folder: HOME, refs: [join(HOME, 'nope-ref.wav')],
  })
  assert.equal(r.json.ok, false)
  assert.equal(r.json.error, 'REF_NOT_FOUND', JSON.stringify(r.json))
})

await check('POST /jobs/delete 跨站 Origin 被 403', async () => {
  const r = await fakePost('/api/cover-factory/jobs/delete', { jobId: 'x' }, { headers: { origin: 'https://evil.example' } })
  assert.equal(r.code, 403)
  assert.equal(r.json.error, 'ORIGIN_REJECTED')
})

await check('POST /jobs/run 跨站 Origin 被 403', async () => {
  const r = await fakePost('/api/cover-factory/jobs/run', { jobId: 'x' }, { headers: { origin: 'https://evil.example' } })
  assert.equal(r.code, 403)
  assert.equal(r.json.error, 'ORIGIN_REJECTED')
})

await check('GET /jobs 列表结构（状态/进度/在跑/mp3）', async () => {
  const r = await fakeRequest('/api/cover-factory/jobs')
  assert.equal(r.code, 200)
  assert.equal(r.json.ok, true)
  assert.ok(Array.isArray(r.json.jobs))
  assert.ok(r.json.jobs.length >= 3, '应列出假 job，实际 ' + r.json.jobs.length)
  for (const key of ['jobId', 'singer', 'status', 'progress', 'done', 'total', 'currentStage', 'running', 'hasMp3']) {
    assert.ok(key in r.json.jobs[0], 'jobs[] 缺字段 ' + key)
  }
  assert.equal(typeof r.json.jobs[0].running, 'boolean')
  assert.equal(typeof r.json.jobs[0].hasMp3, 'boolean')
  assert.equal(typeof r.json.running, 'number')
  assert.equal(typeof r.json.engine.ready, 'boolean')
})

await check('GET /settings 带引擎状态与在跑任务数', async () => {
  const r = await fakeRequest('/api/cover-factory/settings')
  assert.equal(r.json.ok, true)
  assert.equal(typeof r.json.engine.ready, 'boolean')
  assert.equal(r.json.engine.ready, false, 'engineDir 指向不存在的目录时必须是未就绪')
  assert.ok('engineDir' in r.json.engine)
  assert.equal(typeof r.json.running, 'number')
})

await check('POST /settings 保存生效 + 未知键回报 ignored', async () => {
  const r = await fakePost('/api/cover-factory/settings', { defaultSteps: 5000, bogusKey: 1 })
  assert.equal(r.code, 200)
  assert.equal(r.json.ok, true)
  assert.equal(r.json.settings.defaultSteps, 5000)
  assert.deepEqual(r.json.ignored, ['bogusKey'])
})

await check('未知子路由 404 而不是抛', async () => {
  const r = await fakeRequest('/api/cover-factory/nope')
  assert.equal(r.code, 404)
  assert.equal(r.json.ok, false)
})

// ── 6. engine/ 一个文件都没被写 ─────────────────────────────────────────
await check('测试期间没有在 engine/ 下留下文件', () => {
  const engineDir = here('engine')
  if (!existsSync(engineDir)) return
  const names = readdirSync(engineDir)
  assert.ok(!names.includes('job.json'), 'engine/ 里出现了 job.json')
  assert.ok(!names.includes('plugin-run.json'), 'engine/ 里出现了 plugin-run.json')
})

// ── 收尾 ────────────────────────────────────────────────────────────────
try { rmSync(HOME, { recursive: true, force: true }) } catch {}

console.log('')
console.log('checks passed: ' + checks + ', failed: ' + failures.length)
if (failures.length) {
  for (const f of failures) console.log(' - ' + f)
  process.exitCode = 1
} else {
  console.log('SMOKE OK')
}
