// 八个 cover_* 工具：cover_setup / cover_run / cover_status / cover_cancel / cover_list / cover_export / cover_delete / cover_doctor
//
// 契约（照抄 dsh-meme-lookup）：
// - 参数是完整 JSON Schema（{ type:'object', properties, required }），扁平 map 会被 provider 拒；
// - execute 永不抛异常，失败返回 { ok:false, error, message }，message 已脱敏；
// - 单个工具注册失败不影响其他工具（逐个 try/catch）。

import { spawn } from 'node:child_process'
import { copyFileSync, existsSync, mkdirSync, readdirSync, renameSync, rmSync, statSync } from 'node:fs'
import { basename, dirname, extname, join, resolve } from 'node:path'
import {
  STAGES,
  cancelJob,
  engineLogPath,
  engineReady,
  findJobFile,
  isJobRunning,
  listJobs,
  normalizeJob,
  parseJsonFrom,
  readJob,
  readLogTail,
  runCli,
  startJob,
  stageLogPath,
  waitForEarlyExit,
} from './engineClient.js'
import { runDoctorReport } from './doctor.js'
import { normalizeName, resolveSingerAlias } from './aliases.js'
import { STEPS_MAX, STEPS_MIN, loadSettings } from './settings.js'
import { clip, fail, hasAudioExt, readJsonSafe, redactPaths, safeName, toInt, withWindowsRetry, writeFileAtomic, writeJsonAtomic } from './util.js'

const textOutput = {
  schema: { type: 'object', properties: {}, additionalProperties: true },
  render: (_args, value) => [{
    type: 'text',
    text: typeof value === 'string' ? value : JSON.stringify(value, null, 2),
  }],
}

// 耗时参考（RTX 5060 Laptop 实测，约 0.37 秒/步）：
//   rime7 145e ≈17800 步 111 分钟；suis5 60e ≈16000 步 95 分钟。
// 表里是分钟数，只是常用档位的速查；其他步数按 0.37 秒/步线性估。
const ETA_TABLE = [[3000, 18], [5000, 30], [9000, 55], [18000, 110]]
const SECONDS_PER_STEP = 0.37

// 抓取模式最容易「刚启动就 exit 2」：等这么久把结构化错误带回对话。
// import 模式不等（保持纯非阻塞）。
const EARLY_EXIT_MS = 2500

// 好球曲上限（UI 是多行输入，超过就是挑错了）
export const REF_MAX = 20
// 软删除的回收目录名（listJobs 会把 _ 开头的目录当内部目录跳过）
export const DELETED_DIR = '_deleted'

// 分钟数 -> 「55 分钟」/「1 小时 50 分」
function fmtMinutes(minutes) {
  const m = Math.max(1, Math.round(minutes))
  if (m < 60) return m + ' 分钟'
  const h = Math.floor(m / 60)
  const rest = m % 60
  return rest ? h + ' 小时 ' + rest + ' 分' : h + ' 小时'
}

export function etaText(steps) {
  const n = toInt(steps, 9000)
  for (const [s, m] of ETA_TABLE) if (s === n) return '约 ' + fmtMinutes(m) + '（' + n + ' 步，RTX 5060 Laptop 实测）'
  const m = (n * SECONDS_PER_STEP) / 60
  return '约 ' + fmtMinutes(m) + '（按 0.37 秒/步线性估，RTX 5060 Laptop）'
}

const MODE_ALIAS = {
  import: 'import',
  auto: 'auto', fetch: 'auto', download: 'auto', scrape: 'auto',
  local: 'local', client: 'local', clients: 'local', local_clients: 'local', music: 'local',
}

// 素材来源（多选，至少 1 个）：folder=本地目录 / local=本机客户端已下载 / auto=B 站联网抓取
export const SOURCE_KINDS = ['folder', 'local', 'auto']
export const SOURCE_LABEL = { folder: '本地目录', local: '本机客户端', auto: 'B站搜索' }
const SOURCE_ALIAS = {
  folder: 'folder', import: 'folder', dir: 'folder', local_dir: 'folder', directory: 'folder', 目录: 'folder', 本地目录: 'folder',
  local: 'local', local_clients: 'local', client: 'local', clients: 'local', music: 'local', 本机: 'local', 本机客户端: 'local',
  auto: 'auto', search: 'auto', bili: 'auto', bilibili: 'auto', fetch: 'auto', download: 'auto', 抓取: 'auto', b站: 'auto',
}

// 把 sources（数组/字符串/对象）或旧 mode 归一成 kinds 数组；未知项不在这里报错（由 cover_setup 校验）
export function normalizeSourceKinds(input) {
  const v = input || {}
  const raw = []
  const push = (x) => {
    const key = String(x == null ? '' : x).trim().toLowerCase()
    if (key) raw.push(key)
  }
  if (Array.isArray(v.sources)) v.sources.forEach(push)
  else if (typeof v.sources === 'string') push(v.sources)
  else if (v.sources && typeof v.sources === 'object') Object.keys(v.sources).forEach((k) => { if (v.sources[k]) push(k) })
  else if (v.mode) push(v.mode)
  const out = []
  for (const item of raw) {
    const kind = SOURCE_ALIAS[item]
    if (kind && !out.includes(kind)) out.push(kind)
  }
  return out
}

export function sourceLabelOf(kind) {
  return SOURCE_LABEL[kind] || String(kind || '')
}

// 本机音乐客户端（只读它们已下载的文件；不需要账号/登录态）
// 引擎侧（engine/stages/fetch.py）认的 id 是 netease / qqmusic / kugou / kuwo；"qq" 只作为别名收下
export const LOCAL_CLIENTS = [
  { id: 'netease', label: '网易云音乐' },
  { id: 'qqmusic', label: 'QQ音乐' },
  { id: 'kugou', label: '酷狗音乐' },
  { id: 'kuwo', label: '酷我音乐' },
]
const CLIENT_ALIAS = {
  netease: 'netease', neteasecloud: 'netease', cloudmusic: 'netease', '网易云': 'netease', '网易云音乐': 'netease',
  qqmusic: 'qqmusic', qq: 'qqmusic', 'qq音乐': 'qqmusic',
  kugou: 'kugou', kg: 'kugou', '酷狗': 'kugou', '酷狗音乐': 'kugou',
  kuwo: 'kuwo', '酷我': 'kuwo', '酷我音乐': 'kuwo',
}

// 目标歌手声部：auto=按目标曲人声自动判断；female/male 显式指定（男歌手建议直接选 male）
export const VOICE_MODES = ['auto', 'female', 'male']
const VOICE_ALIAS = { auto: 'auto', female: 'female', f: 'female', male: 'male', m: 'male', '女': 'female', '男': 'male' }
const VOICE_LABEL = { auto: '自动（按目标曲人声判断）', female: '女声', male: '男声' }

// 分离方案（人声/伴奏分离模型）：kim = 默认最稳；bs317 = BS-RoFormer ep317，串音指标更低
export const SEPARATE_MODELS = ['kim', 'bs317']
export const SEPARATE_MODEL_LABEL = {
  kim: 'kim（默认，稳）',
  bs317: 'bs317（BS-RoFormer ep317，串音更低）',
}
// 键统一去掉空格/连字符/下划线后再比（BS-317 / bs_roformer / ep317 都认）
const SEPARATE_ALIAS = {
  kim: 'kim', melband: 'kim', melbandroformer: 'kim', default: 'kim', mel: 'kim',
  bs317: 'bs317', bs: 'bs317', bsroformer: 'bs317', ep317: 'bs317', roformer: 'bs317',
}
function sepKey(raw) {
  return String(raw == null ? '' : raw).trim().toLowerCase().replace(/[^a-z0-9]/g, '')
}

// shift：整首移调半音数。只认数字或数字字符串，绝不静默截断（2.5 必须报错而不是变成 2）
export const SHIFT_MIN = -5
export const SHIFT_MAX = 5

function toShift(raw) {
  if (raw === undefined || raw === null || raw === '') return 0
  if (typeof raw === 'number') return raw
  if (typeof raw === 'string') {
    const s = raw.trim()
    if (!s) return 0
    const n = Number(s)
    return Number.isFinite(n) ? n : NaN
  }
  return NaN
}

function shiftNoteOf(shift) {
  if (shift === 0) return '原调（不移动）'
  const dir = shift > 0 ? '升高' : '降低'
  return '整首' + dir + ' ' + Math.abs(shift) + ' 个半音（人声+伴奏同比例，不允许只动人声）'
}

function guard(name, fn) {
  return async (args) => {
    try {
      return await fn(args && typeof args === 'object' && !Array.isArray(args) ? args : {})
    } catch (e) {
      // 最后一道网：任何意外都变成 ok:false，绝不冒泡到 DSH
      return fail('TOOL_ERROR', name + ' 执行出错：' + String((e && e.message) || e))
    }
  }
}

function normVoice(v) {
  const s = String(v == null ? '' : v).trim().toLowerCase()
  if (!s) return null
  if (s === 'male' || s === 'm' || s.includes('男')) return 'male'
  if (s === 'female' || s === 'f' || s.includes('女')) return 'female'
  if (s === 'duet' || s.includes('对唱') || s.includes('合唱')) return 'duet'
  return s
}

function selectReportPaths(workdir) {
  return [join(workdir, 'select', 'report.json'), join(workdir, 'work', 'select', 'report.json')]
}

// 引擎报告结构还没定稿：对象/数组、kept/selected 都尽量认，认不出就只报条数。
// voiceGuess：引擎逐首给的声部判断（字段名兼容 voice/voiceGuess/vocal_type/gender），
// 让用户看得出「这首为什么被排除」——是声部不符，还是对唱/时长/立体声。
export function summarizeSelectReport(raw, expectedVoice) {
  const kept = []
  const excluded = []
  const songs = []
  const voiceOf = (it) => normVoice(it.voice || it.voiceGuess || it.voice_guess || it.vocalType || it.vocal_type || it.gender || it.register)
  const nameOf = (it) => basename(String(it.file || it.path || it.name || it.song || it.id || '')) || 'unknown'
  const reasonOf = (it) => clip(String(it.reason || it.why || it.note || ''), 80) || null
  const decide = (it) => {
    if (typeof it.kept === 'boolean') return it.kept
    if (typeof it.selected === 'boolean') return it.selected
    if (typeof it.keep === 'boolean') return it.keep
    const s = String(it.status || '').toLowerCase()
    if (s === 'excluded' || s === 'rejected' || s === 'skip' || s === 'skipped') return false
    if (s === 'kept' || s === 'selected' || s === 'ok') return true
    return true
  }
  const list = Array.isArray(raw) ? raw : raw && (raw.items || raw.songs || raw.candidates || raw.entries)
  if (Array.isArray(list)) {
    for (const it of list) {
      if (!it || typeof it !== 'object') continue
      const ok = decide(it)
      const name = nameOf(it)
      const voice = voiceOf(it)
      if (ok) kept.push(name)
      else excluded.push({ name, reason: reasonOf(it), voice })
      songs.push({ name, voice, kept: ok, reason: reasonOf(it) })
    }
  } else if (raw && typeof raw === 'object') {
    for (const it of Array.isArray(raw.selected) ? raw.selected : []) {
      const name = typeof it === 'string' ? basename(it) : nameOf(it)
      kept.push(name)
      songs.push({ name, voice: it && typeof it === 'object' ? voiceOf(it) : null, kept: true, reason: null })
    }
    for (const it of Array.isArray(raw.excluded) ? raw.excluded : []) {
      if (typeof it === 'string') {
        excluded.push({ name: basename(it), reason: null, voice: null })
        songs.push({ name: basename(it), voice: null, kept: false, reason: null })
      } else if (it && typeof it === 'object') {
        excluded.push({ name: nameOf(it), reason: reasonOf(it), voice: voiceOf(it) })
        songs.push({ name: nameOf(it), voice: voiceOf(it), kept: false, reason: reasonOf(it) })
      }
    }
  }
  // 目标曲的声部判断（引擎可能放在 target/targetVoice/voiceGuess 里）
  const targetRaw = raw && typeof raw === 'object'
    ? (raw.voiceGuess && typeof raw.voiceGuess === 'string' ? raw.voiceGuess : null) ||
      raw.targetVoice || raw.target_voice || raw.expectedVoice ||
      (raw.target && typeof raw.target === 'object' ? (raw.target.voice || raw.target.voiceGuess) : null)
    : null
  return {
    kept: kept.slice(0, 20),
    excluded: excluded.slice(0, 20),
    keptCount: kept.length,
    excludedCount: excluded.length,
    voiceGuess: {
      expected: expectedVoice || 'auto',
      target: normVoice(targetRaw),
      songs: songs.slice(0, 20),
    },
  }
}

// 素材目录只做本地扫描（只读、只报文件名），不进引擎
function scanFolder(folder) {
  if (!folder) return null
  const out = { dirName: basename(folder), audioCount: 0, totalMB: 0, files: [], skippedDirs: 0 }
  let names = []
  try { names = readdirSync(folder) } catch { return out }
  for (const name of names) {
    const p = join(folder, name)
    let st
    try { st = statSync(p) } catch { continue }
    if (st.isDirectory()) { out.skippedDirs += 1; continue }
    if (!st.isFile() || !hasAudioExt(name)) continue
    out.audioCount += 1
    out.totalMB += st.size / (1024 * 1024)
    if (out.files.length < 12) out.files.push(name)
  }
  out.totalMB = Math.round(out.totalMB * 10) / 10
  return out
}

// 本机客户端模式：fetch 阶段会把扫描结果写报告；预筛预览里展示文件数与格式分布
function fetchReportPaths(workdir) {
  return [
    join(workdir, 'raw', 'local_client_report.json'),
    join(workdir, 'work', 'raw', 'local_client_report.json'),
    join(workdir, 'fetch', 'local_client_report.json'),
    join(workdir, 'work', 'fetch', 'local_client_report.json'),
    join(workdir, 'fetch', 'report.json'),
    join(workdir, 'work', 'fetch', 'report.json'),
    join(workdir, 'raw', 'report.json'),
  ]
}

export function summarizeFetchReport(workdir, clients, keyword) {
  const list = Array.isArray(clients) && clients.length ? clients : LOCAL_CLIENTS.map((c) => c.id)
  for (const p of fetchReportPaths(workdir)) {
    const raw = readJsonSafe(p, null)
    if (!raw || typeof raw !== 'object') continue
    const files = Array.isArray(raw.files) ? raw.files
      : (Array.isArray(raw.items) ? raw.items : (Array.isArray(raw.scanned) ? raw.scanned : []))
    const counts = raw.counts && typeof raw.counts === 'object' ? raw.counts : {}
    const num = (v) => (Number.isFinite(v) ? v : null)
    // 引擎 local_client_report 的 counts：scanned / usable / used / encrypted
    const count = num(raw.fileCount) != null ? raw.fileCount
      : num(counts.scanned) != null ? counts.scanned
        : num(raw.count) != null ? raw.count
          : files.length
    let formats = []
    if (raw.formats && typeof raw.formats === 'object' && !Array.isArray(raw.formats)) {
      formats = Object.entries(raw.formats).map(([ext, c]) => ({ ext: String(ext).replace(/^\./, '').toLowerCase(), count: Number(c) || 0 }))
    } else if (Array.isArray(raw.formats)) {
      formats = raw.formats.map((f) => ({
        ext: String((f && (f.ext || f.format)) || '').replace(/^\./, '').toLowerCase(),
        count: Number((f && f.count) || 0),
      }))
    } else {
      const map = {}
      for (const f of files) {
        const fp = typeof f === 'string' ? f : ((f && (f.file || f.path || f.name)) || '')
        const declared = f && typeof f === 'object' ? String(f.format || f.ext || '').replace(/^\./, '').toLowerCase() : ''
        const ext = declared || extname(String(fp)).replace(/^\./, '').toLowerCase() || 'unknown'
        map[ext] = (map[ext] || 0) + 1
      }
      formats = Object.entries(map).map(([ext, c]) => ({ ext, count: c }))
    }
    formats = formats.filter((f) => f.ext).sort((a, b) => b.count - a.count).slice(0, 8)
    return {
      available: true,
      source: 'fetch-report',
      fileCount: count,
      // 引擎额外给的计数：可用 / 采用 / 加密跳过
      usable: num(counts.usable),
      used: num(counts.used),
      encrypted: num(counts.encrypted),
      formats,
      clients: Array.isArray(raw.clients) ? raw.clients : list,
      keyword: String(raw.keyword || keyword || ''),
      roots: Array.isArray(raw.roots) ? raw.roots.slice(0, 8).map((r) => (r && (r.path || r.client)) || '') : undefined,
      note: raw.note ? clip(String(raw.note), 200) : null,
    }
  }
  return {
    available: false,
    reason: 'fetch-pending',
    fileCount: 0,
    formats: [],
    clients: list,
    keyword: String(keyword || ''),
    note: '还没跑 fetch：先 cover_run，跑完这里会显示扫描到的文件数和格式分布（ncm 会自动解密）。',
  }
}

// 每个来源各贡献几首、总候选数（只读引擎 fetch 报告；没跑就 pending，不瞎猜）
export function summarizeContributions(workdir, opts) {
  const o = opts || {}
  const kinds = Array.isArray(o.kinds) && o.kinds.length ? o.kinds : ['folder']
  const folderScan = o.folder ? scanFolder(o.folder) : null
  const localReport = kinds.includes('local') ? summarizeFetchReport(workdir, o.clients, o.keyword) : null
  const searchPaths = [
    join(workdir, 'raw', 'fetch_report.json'),
    join(workdir, 'work', 'raw', 'fetch_report.json'),
    join(workdir, 'fetch', 'fetch_report.json'),
    join(workdir, 'work', 'fetch', 'fetch_report.json'),
  ]
  let searchReport = null
  for (const p of searchPaths) {
    const raw = readJsonSafe(p, null)
    if (raw && typeof raw === 'object') { searchReport = raw; break }
  }
  const num = (v) => (Number.isFinite(v) ? v : null)
  // 组合模式：引擎 fetch_report.json 里带 sources/counts（见 engine/README.md「多来源组合」）
  const combined = searchReport && (searchReport.mode === 'combined' || (searchReport.sources && typeof searchReport.sources === 'object'))
  const combinedCounts = combined ? (searchReport.counts && typeof searchReport.counts === 'object' ? searchReport.counts : {}) : null
  const sourceKey = { folder: 'folder', local: 'local_clients', auto: 'search' }
  const combinedContrib = (kind) => {
    if (!combined) return null
    const key = sourceKey[kind]
    const node = searchReport.sources && searchReport.sources[key]
    if (!node || typeof node !== 'object') return null
    const status = String(node.status || '')
    const count = num(node.candidates) != null ? node.candidates
      : (num(node.used) != null ? node.used : (num(node.count) != null ? node.count : null))
    const failed = status === 'failed' || status === 'error'
    return {
      count: failed ? 0 : count,
      available: count != null || failed || status === 'done' || status === 'skipped',
      note: node.error ? ('失败：' + clip(String(node.error), 120)) : (status ? ('状态 ' + status) : ''),
    }
  }
  const searchCount = (() => {
    if (!searchReport) return null
    const counts = searchReport.counts && typeof searchReport.counts === 'object' ? searchReport.counts : {}
    if (num(counts.used) != null) return counts.used
    if (num(counts.kept) != null) return counts.kept
    if (Array.isArray(searchReport.songs)) return searchReport.songs.length
    if (Array.isArray(searchReport.files)) return searchReport.files.length
    if (Array.isArray(searchReport.items)) return searchReport.items.length
    return null
  })()

  const contributions = kinds.map((kind) => {
    const c = combinedContrib(kind)
    if (c) return { source: kind, label: SOURCE_LABEL[kind], ...c }
    if (kind === 'folder') {
      const count = folderScan ? folderScan.audioCount : 0
      return { source: 'folder', label: SOURCE_LABEL.folder, count, available: true, note: folderScan ? folderScan.dirName : '等 folder' }
    }
    if (kind === 'local') {
      const available = !!(localReport && localReport.available)
      const count = available ? (num(localReport.usable) != null ? localReport.usable : localReport.fileCount) : null
      return {
        source: 'local',
        label: SOURCE_LABEL.local,
        count,
        available,
        note: available ? (localReport.fileCount + ' 个文件扫描到，采用 ' + count) : '等 fetch',
      }
    }
    const available = searchCount != null
    return {
      source: 'auto',
      label: SOURCE_LABEL.auto,
      count: available ? searchCount : null,
      available,
      note: available ? ('关键词「' + String((searchReport && searchReport.keyword) || o.search || '') + '」') : '等 fetch',
    }
  })
  const known = contributions.filter((c) => typeof c.count === 'number')
  const engineTotal = combined ? num(combinedCounts.total) : null
  return {
    sources: kinds.slice(),
    contributions,
    totalCandidates: engineTotal != null ? engineTotal : known.reduce((n, c) => n + c.count, 0),
    ready: contributions.every((c) => c.available),
  }
}

async function buildPreview(ctx) {
  const { jobFile, workdir, folder, mode, settings, voice, clients, keyword, kinds, search } = ctx
  const expectedVoice = voice || 'auto'
  const sourceKinds = Array.isArray(kinds) && kinds.length ? kinds : (mode === 'local' ? ['local'] : (mode === 'auto' ? ['auto'] : ['folder']))
  // 本机客户端：fetch 报告 → 文件数 + 格式分布（扫描是引擎做的，这里只读报告）
  const localFetch = sourceKinds.includes('local') ? summarizeFetchReport(workdir, clients, keyword) : null
  // 每个来源贡献几首 + 总候选数
  const contrib = summarizeContributions(workdir, { kinds: sourceKinds, folder, clients, keyword, search })
  const withFetch = (obj) => ({
    ...obj,
    ...(localFetch ? { localFetch } : {}),
    contributions: contrib.contributions,
    totalCandidates: contrib.totalCandidates,
  })
  for (const p of selectReportPaths(workdir)) {
    const raw = readJsonSafe(p, null)
    if (raw && typeof raw === 'object') {
      return withFetch({
        available: true,
        source: 'report',
        voice: expectedVoice,
        ...summarizeSelectReport(raw, expectedVoice),
        note: '来自已有的 select/report.json（--resume 会复用）。',
      })
    }
  }
  const stems = ['stems', 'work/stems'].map((s) => join(workdir, s)).find((p) => existsSync(p))
  if (stems) {
    const dry = await runCli(['run', '--job', jobFile, '--from', 'select', '--to', 'select', '--dry-run'], {
      timeoutMs: 120000,
      cwd: workdir,
      settings,
    })
    for (const p of selectReportPaths(workdir)) {
      const raw = readJsonSafe(p, null)
      if (raw && typeof raw === 'object') {
        return withFetch({
          available: true,
          source: 'dry-run',
          voice: expectedVoice,
          ...summarizeSelectReport(raw, expectedVoice),
          note: '引擎 select --dry-run 的预筛结果，没有真正切片。',
        })
      }
    }
    return withFetch({
      available: false,
      reason: 'dry-run-failed',
      voice: expectedVoice,
      voiceGuess: null,
      detail: clip(redactPaths(dry.stderr || dry.stdout), 200),
      note: '引擎可能还没实现 select --dry-run；等 separate 跑完后再看 report.json 即可。',
      sources: mode === 'import' ? scanFolder(folder) : null,
    })
  }
  if (sourceKinds.length === 1 && sourceKinds[0] === 'local') {
    const got = localFetch && localFetch.available
    return withFetch({
      available: false,
      reason: got ? 'select-pending' : 'fetch-pending',
      voice: expectedVoice,
      voiceGuess: null,
      note: got
        ? '已扫描到素材（见 localFetch 的文件数与格式分布），等 separate/select 跑完再看逐首声部判断。'
        : '先 cover_run 跑 fetch：会扫描所选客户端（' + ((clients && clients.length ? clients : ['netease', 'qqmusic', 'kugou', 'kuwo']).join(' / ')) + '）的下载目录，关键词「' + String(keyword || '') + '」；跑完这里显示文件数与格式分布，ncm 会自动解密。',
    })
  }
  if (sourceKinds.length === 1 && sourceKinds[0] === 'auto') {
    return withFetch({
      available: false,
      reason: 'sources-pending',
      voice: expectedVoice,
      voiceGuess: null,
      note: '抓取模式要先 fetch 素材，预筛要等 separate 之后。',
    })
  }
  const multi = sourceKinds.length > 1
  return withFetch({
    available: false,
    reason: 'stems-pending',
    voice: expectedVoice,
    voiceGuess: null,
    sources: sourceKinds.includes('folder') ? scanFolder(folder) : null,
    note: multi
      ? '多来源（' + sourceKinds.map(sourceLabelOf).join(' + ') + '）：先 cover_run 跑 fetch，再看 contributions 里每个来源各贡献几首、总候选数；之后 separate/select 会逐首比对期望声部（voice=' + expectedVoice + '）。'
      : '预筛要等 fetch/separate 跑完：先 cover_run，再用 cover_status 看进度。智选会区分对唱/合唱，并逐首比对期望声部（voice=' + expectedVoice + '）；结果在 select/report.json 的 voiceGuess 里。',
  })
}

// jobId 要有路径分隔符就直接拒：不允许拿 jobId 穿越目录
function resolveJob(args, settings) {
  const jobId = String((args && (args.jobId || args.id)) || '').trim()
  if (!jobId) {
    return { ok: false, result: fail('MISSING_JOB_ID', '要给 jobId（cover_setup 返回的那个）。') }
  }
  if (jobId.length > 120 || /[\\/:*?"<>|]/.test(jobId) || jobId.includes('..') || jobId.startsWith('_')) {
    return { ok: false, result: fail('BAD_JOB_ID', 'jobId 只能是字母/数字/中文/. _ -；不能带路径分隔符，也不能以 _ 开头（_deleted 等是内部目录）。', { jobId: clip(jobId, 80) }) }
  }
  const workdir = join(settings.workRoot, jobId)
  if (!existsSync(workdir)) {
    return { ok: false, result: fail('JOB_NOT_FOUND', '没有这个 job：用 cover_list 看已有 jobId。', { jobId }) }
  }
  const jobFile = findJobFile(workdir)
  if (!jobFile) {
    return { ok: false, result: fail('JOB_NOT_FOUND', '这个 job 目录里没有 job.json（可能没建成功），重新 cover_setup。', { jobId }) }
  }
  const job = readJob(jobFile)
  if (!job) {
    return { ok: false, result: fail('JOB_UNREADABLE', 'job.json 读不出来（可能正在被引擎写，稍后重试）。', { jobId }) }
  }
  return { ok: true, jobId, workdir, jobFile, job }
}

// 引擎把「需要用户决策」写进 job.json 的 decision 字段（LOW_VRAM / COPYRIGHT_ACK_REQUIRED …）
// 这里只读透出，绝不改写 job.json（引擎是唯一写方）
function readDecision(jobFile) {
  const raw = readJsonSafe(jobFile, null)
  const d = raw && typeof raw === 'object' ? raw.decision : null
  if (!d || typeof d !== 'object') return null
  return {
    code: String(d.code || ''),
    stage: d.stage ? String(d.stage) : null,
    reason: d.reason ? clip(redactPaths(String(d.reason)), 300) : null,
  }
}

// 好球曲（选填）：像路径的才检查存在性；其余当「歌名」放行（引擎会在素材目录里自动匹配）。
// 纯函数，便于单测；line 是用户输入里的行号（空行/空串已过滤）。
export function looksLikePath(ref) {
  const s = String(ref == null ? '' : ref).trim()
  return /[\\/]/.test(s) || /^[A-Za-z]:/.test(s)
}

export function classifyRefs(list) {
  const all = []
  const paths = []
  const names = []
  const missing = []
  const items = Array.isArray(list) ? list : (list ? [list] : [])
  for (const item of items) {
    const raw = item && typeof item === 'object' ? (item.path || item.file || item.name || '') : item
    const s = String(raw == null ? '' : raw).trim()
    if (!s) continue
    all.push(s)
    if (looksLikePath(s)) {
      paths.push(s)
      if (!existsSync(s)) missing.push({ line: all.length, path: s })
    } else {
      names.push(s)
    }
  }
  return { all, paths, names, missing }
}

function findArtifact(workdir, exts) {
  const dirs = ['export', 'mix', 'out', join('work', 'export'), join('work', 'mix')]
  let best = ''
  let bestAt = 0
  for (const d of dirs) {
    const dir = join(workdir, d)
    let names = []
    try { names = readdirSync(dir) } catch { continue }
    for (const name of names) {
      if (!exts.includes(extname(name).toLowerCase())) continue
      const p = join(dir, name)
      let st
      try { st = statSync(p) } catch { continue }
      if (!st.isFile() || st.size < 1024) continue
      if (st.mtimeMs > bestAt) { bestAt = st.mtimeMs; best = p }
    }
  }
  return best
}

// ffmpeg 兜底：引擎只给了 wav 时，成品要 mp3
function encodeMp3(src, dst, settings) {
  const ff = (settings && settings.ffmpeg) || 'ffmpeg'
  return new Promise((done) => {
    let child
    try {
      child = spawn(ff, ['-y', '-hide_banner', '-loglevel', 'error', '-i', src, '-codec:a', 'libmp3lame', '-b:a', '320k', dst], {
        windowsHide: true,
        stdio: ['ignore', 'ignore', 'pipe'],
      })
    } catch (e) {
      return done({ ok: false, error: 'FFMPEG_MISSING', detail: redactPaths(e && e.message) })
    }
    let err = ''
    try {
      child.stderr.on('data', (d) => { if (err.length < 4000) err += d.toString() })
    } catch {}
    child.on('error', (e) => done({ ok: false, error: 'FFMPEG_MISSING', detail: redactPaths(e && e.message) }))
    child.on('close', (code) => done(code === 0 ? { ok: true } : { ok: false, error: 'FFMPEG_FAILED', detail: clip(redactPaths(err), 200) }))
  })
}

function buildReportMd(job, qaRaw, settings) {
  const lines = []
  lines.push('# 翻唱工厂 · ' + (job.singer || '未命名') + ' - ' + (job.target ? basename(String(job.target)) : '未指定目标曲'))
  lines.push('')
  lines.push('- 生成时间：' + new Date().toISOString())
  lines.push('- jobId：' + (job.jobId || ''))
  lines.push('- 模式：' + (job.mode || 'import'))
  if (job.options && job.options.steps) lines.push('- 训练步数：' + job.options.steps + '（' + etaText(job.options.steps) + '）')
  lines.push('')
  lines.push('## 阶段')
  lines.push('')
  lines.push('| 阶段 | 状态 |')
  lines.push('| --- | --- |')
  for (const s of job.stages) lines.push('| ' + s.stage + ' | ' + s.status + ' |')
  lines.push('')
  if (qaRaw && typeof qaRaw === 'object') {
    lines.push('## 质检')
    lines.push('')
    lines.push('    ' + clip(JSON.stringify(qaRaw, null, 2), 3000).split('\n').join('\n    '))
    lines.push('')
  } else {
    lines.push('## 质检')
    lines.push('')
    lines.push('引擎没留下 work/qa/report.json：开工前用 cover_status 确认 qa 阶段是 done。')
    lines.push('')
  }
  lines.push('---')
  lines.push('')
  lines.push('指标合格线：响度 -13 ± 1.5 LUFS；断音率 < 12 次/分；音域覆盖 ≥ 70%。')
  return lines.join('\n') + '\n'
}

function writeReport(workdir, job, outPath) {
  const fromEngine = ['export', 'qa'].map((d) => join(workdir, d, 'report.md')).find((p) => existsSync(p))
  if (fromEngine) {
    try {
      copyFileSync(fromEngine, outPath)
      return { path: outPath, source: 'engine' }
    } catch {}
  }
  const qaRaw = ['qa', join('work', 'qa')].map((d) => join(workdir, d, 'report.json')).find((p) => existsSync(p))
  try {
    const md = buildReportMd(job, qaRaw ? readJsonSafe(qaRaw, null) : null)
    writeFileAtomic(outPath, md)
    return { path: outPath, source: 'plugin' }
  } catch (e) {
    return { path: '', source: 'none', error: redactPaths(e && e.message) }
  }
}

// 生成交给引擎 init 的 job_config.json（纯函数，smoke 直接断言，不需要引擎）
// - 结构对齐引擎侧 fixtures/config_import_*.json：target 是 {file,title}、output 单独一段、qa 阈值显式给
// - 抓取模式：sources = { search, limit, author }，并把 options.copyrightAck 交给引擎（引擎要求 === true 才抓）
// - 导入模式：忽略 search/limit/copyrightAck，一律只写 { folder }
// - 不写 id：引擎自己生成；插件侧 jobId 只用 workdir 目录名
// 把选中的来源落成 job_config.sources：
// - 单一来源沿用旧扁平形状（folder 字符串 / local_clients:true / search 字符串），现有引擎可直接认；
// - 多来源用引擎认的扁平组合 { folder, local_clients:true, clients, keyword, search, limit, author }，
//   引擎按 folder → local_clients → search 顺序合并去重（engine/README.md「多来源组合」）；
//   kinds 始终带上，方便引擎/插件判断组合（引擎忽略未知键）。
export function buildSourcesConfig(input, info) {
  const v = input || {}
  const i = info || {}
  const kinds = (Array.isArray(i.kinds) && i.kinds.length ? i.kinds : normalizeSourceKinds(v)).slice()
  if (!kinds.length) kinds.push('folder')
  const singer = String(i.singer == null ? (v.singer || '') : i.singer)
  const allClients = LOCAL_CLIENTS.map((c) => c.id)
  const clients = Array.isArray(i.clients) && i.clients.length ? i.clients : (Array.isArray(v.clients) && v.clients.length
    ? (() => {
      const out = []
      for (const c of v.clients) {
        const id = CLIENT_ALIAS[String(c || '').trim().toLowerCase()] || CLIENT_ALIAS[String(c || '').trim()]
        if (id && !out.includes(id)) out.push(id)
      }
      return out.length ? out : allClients
    })()
    : allClients)
  const folder = String(v.folder || '')
  const keyword = String(v.keyword || singer)
  const search = String(v.search || singer)
  const limit = toInt(v.limit, 8) || 8
  const kindsTag = { kinds: kinds.slice() }

  // 引擎（fetch.py 多来源组合）认的是扁平组合：
  //   { folder, local_clients: true, clients, keyword, search, limit, author }
  // 单一来源保持完全相同的旧形状；kinds 是插件侧标记，引擎会忽略未知键。
  const out = { ...kindsTag }
  if (kinds.includes('folder')) out.folder = folder
  if (kinds.includes('local')) {
    out.local_clients = true
    out.clients = clients
    out.keyword = keyword
  }
  if (kinds.includes('auto')) {
    out.search = search
    out.limit = limit
    out.author = singer
  }
  return out
}

export function buildJobConfig(input) {
  const v = input || {}
  const singer = String(v.singer || '')
  const sourceKinds = (() => {
    const k = normalizeSourceKinds(v)
    return k.length ? k : ['folder']
  })()
  const has = (k) => sourceKinds.includes(k)
  const auto = has('auto')
  const allClients = LOCAL_CLIENTS.map((c) => c.id)
  const clients = (() => {
    if (!Array.isArray(v.clients) || !v.clients.length) return allClients
    const out = []
    for (const c of v.clients) {
      const id = CLIENT_ALIAS[String(c || '').trim().toLowerCase()] || CLIENT_ALIAS[String(c || '').trim()]
      if (id && !out.includes(id)) out.push(id)
    }
    return out.length ? out : allClients
  })()
  const steps = toInt(v.steps, 9000)
  const shift = toInt(v.shift, 0)
  const target = String(v.target || '')
  // options：选了 auto（或开了 refSearch）一定要 copyrightAck：联网抓取/联网搜好球曲都是显式授权
  const refSearch = v.refSearch === true
  const voice = VOICE_ALIAS[String(v.voice || 'auto').trim().toLowerCase()] || 'auto'
  const separateModel = SEPARATE_ALIAS[sepKey(v.separateModel || 'kim')] || 'kim'
  // voice 显式写进 options（auto 也写）：引擎据此决定「期望声部」，避免把男声素材当对唱全排掉
  // separateModel 也显式写（默认 kim），引擎 separate 阶段按它选模型
  const options = { steps, shift, voice, separateModel }
  if (auto) options.copyrightAck = v.copyrightAck === true
  if (refSearch) {
    options.refSearch = true
    options.copyrightAck = true
  }
  return {
    version: 1,
    singer,
    // 引擎 mode 只认 import/auto/…：只有「单一 auto 来源」写 auto，其余（含多来源）写 import，
    // 具体来源靠 sources（folder/local_clients/search + kinds 标记）表达。
    mode: sourceKinds.length === 1 && auto ? 'auto' : 'import',
    sources: buildSourcesConfig(v, { kinds: sourceKinds, singer, clients }),
    refs: Array.isArray(v.refs) ? v.refs : [],
    target: target
      ? { file: target, title: safeName(basename(target).replace(/\.[^.]+$/, ''), 'target') }
      : null,
    options,
    output: { dir: String(v.outputDir || '') },
    qa: { minGapRate: 12, minLoudness: -14.5, maxLoudness: -11.5 },
  }
}

// ── 给设置页（lib/panel.js）复用同一套校验/返回 ─────────────────────────
// 不复制逻辑、也不绕过 guard：工具注册时把守卫后的 execute 记进 IMPL。
const IMPL = new Map()

export async function runTool(name, args) {
  const fn = IMPL.get(String(name || ''))
  if (typeof fn !== 'function') return fail('TOOL_NOT_AVAILABLE', '工具还没装配好（插件没完成 apply），稍后重试。')
  return fn(args && typeof args === 'object' && !Array.isArray(args) ? args : {})
}

export const createJob = (args) => runTool('cover_setup', args)
export const runJob = (args) => runTool('cover_run', args)
export const statusJob = (args) => runTool('cover_status', args)
export const cancelJobRun = (args) => runTool('cover_cancel', args)
export const exportJob = (args) => runTool('cover_export', args)
export const listJobsView = (args) => runTool('cover_list', args)

// 依赖体检：直接导出实现，panel 的 POST /doctor 与工具走同一份逻辑
export async function doctorCheck(_args) {
  return runDoctorReport(loadSettings())
}

export const doctorView = (args) => runTool('cover_doctor', args)

// 删除 job：运行中先取消；soft=移到 <workRoot>/_deleted/（可找回），purge=物理删除。
// 用真实导出的函数（而不是 runTool 间接层），panel 里直接调同一份逻辑。
export async function deleteJob(args) {
  const settings = loadSettings()
  const r = resolveJob(args, settings)
  if (!r.ok) return r.result

  const modeRaw = String((args && args.mode) || 'soft').trim().toLowerCase()
  let mode = 'soft'
  if (modeRaw === 'purge' || modeRaw === 'hard' || modeRaw === 'delete') mode = 'purge'
  else if (modeRaw !== 'soft' && modeRaw !== 'trash') {
    return fail('BAD_DELETE_MODE', 'mode 只能是 soft（默认：移到 _deleted/，可找回）或 purge（物理删除，不可恢复）。', { got: clip(modeRaw, 20) })
  }

  // 运行中：先取消，避免删到一半引擎还在写
  let cancelled = false
  let pid = 0
  const live = isJobRunning(r.jobId, r.workdir)
  if (live.running) {
    const c = await cancelJob({ jobId: r.jobId, workdir: r.workdir, jobFile: r.jobFile }, settings)
    if (!c.ok) {
      return fail('DELETE_ABORTED', '这个 job 还在跑，取消失败，所以没有删：' + (c.message || ''), { jobId: r.jobId, pid: live.pid })
    }
    cancelled = true
    pid = c.pid || live.pid || 0
  }

  if (mode === 'purge') {
    try {
      rmSync(r.workdir, { recursive: true, force: true, maxRetries: 5, retryDelay: 150 })
    } catch (e) {
      return fail('DELETE_FAILED', '物理删除失败（可能被占用）：' + (e && e.message), { jobId: r.jobId })
    }
    return {
      ok: true,
      jobId: r.jobId,
      deleted: true,
      mode: 'purge',
      cancelled,
      pid,
      message: '已物理删除（不可恢复）' + (cancelled ? '；删除前已停止运行中的引擎' : ''),
    }
  }

  const ts = new Date().toISOString().replace(/[-:T]/g, '').slice(0, 14)
  const dest = join(settings.workRoot, DELETED_DIR, r.jobId + '-' + ts)
  try {
    mkdirSync(join(settings.workRoot, DELETED_DIR), { recursive: true })
    withWindowsRetry(() => renameSync(r.workdir, dest))
  } catch (e) {
    return fail('DELETE_FAILED', '移到 _deleted 失败（可能被占用）：' + (e && e.message), { jobId: r.jobId })
  }
  return {
    ok: true,
    jobId: r.jobId,
    deleted: true,
    mode: 'soft',
    cancelled,
    pid,
    recycledTo: dest,
    message: '已软删除，工作目录在 _deleted/' + r.jobId + '-' + ts + '（要找回来就把它移回 jobs/ 目录）' + (cancelled ? '；删除前已停止运行中的引擎' : ''),
  }
}

export function registerTools(ctx) {
  if (!ctx || !ctx.tools || typeof ctx.tools.register !== 'function') return

  const defs = [
    {
      name: 'cover_setup',
      description: '建翻唱 job：歌手名（必填，支持别名/模糊归一）+ 素材来源（sources 多选：folder 本地目录 / local 本机客户端 / auto B站搜索，至少 1 个，可组合）+ 目标曲，返回 jobId、resolvedSinger 与素材预筛预览（含每个来源贡献几首）。可选 separateModel（默认 kim；bs317 串音更低但实验无听感提升）。开工前先问清三件事：歌手、好球曲（音色参考）、目标曲。',
      parameters: {
        type: 'object',
        properties: {
          singer: { type: 'string', description: '歌手名（必填）：决定声库名与成品文件名' },
          refs: { type: 'array', items: { type: 'string' }, description: '好球曲（选填）：每项可以是歌名或完整路径，可混填；歌名会在素材目录里自动匹配' },
          target: { type: 'string', description: '目标曲音频文件完整路径；不给只能跑到 train，出不了成品' },
          steps: { type: 'number', description: '训练步数，默认 9000（约 55 分钟）；5000≈30 分钟，18000≈1 小时 50 分（RTX 5060 Laptop 实测）' },
          shift: { type: 'number', description: '整首移调半音数：-5..+5 的整数，默认 0；正=升高。人声与伴奏同比例移，不允许只动人声' },
          sources: {
            type: 'array',
            items: { type: 'string', enum: ['folder', 'local', 'auto'] },
            description: '素材来源（多选，至少 1 个）：folder=本地目录（要给 folder）；local=本机音乐客户端已下载的文件（只读扫描，不需要账号）；auto=B站搜索抓取，必须带 copyrightAck:true。可同时用，例如 ["folder","local"]；多来源落盘为扁平组合并由引擎合并',
          },
          mode: {
            type: 'string',
            enum: ['import', 'local', 'auto'],
            description: '旧参数（兼容保留）：等价于 sources 的单一来源。有 sources 时以 sources 为准；新调用请用 sources',
          },
          voice: { type: 'string', enum: ['auto', 'female', 'male'], description: '目标歌手声部：auto=按目标曲人声自动判断（默认）；男歌手请选 male，或先用 auto 并确认判断正确。写进 job_config.options.voice' },
          separateModel: { type: 'string', enum: ['kim', 'bs317'], description: '分离方案：kim=默认（稳）；bs317=BS-RoFormer ep317（串音指标更低，但实验结论是无可测听感提升）。写进 job_config.options.separateModel' },
          folder: { type: 'string', description: 'sources 含 folder 时必填：素材目录（插件只读，不删不改）' },
          search: { type: 'string', description: 'sources 含 auto 时的搜索词，默认取归一后的歌手名（canonical）' },
          limit: { type: 'number', description: 'sources 含 auto 时最多抓几首，默认 8，范围 1-50' },
          copyrightAck: { type: 'boolean', description: '显式为 true = 用户已确认联网的版权风险（auto 抓取与 refSearch 联网搜索都需要）。默认 false（只返回风险提示，不建 job）' },
          refSearch: { type: 'boolean', description: '好球曲只在素材目录找不到时联网搜索（默认 false）。需要 copyrightAck:true（或 mode=auto）才生效' },
          clients: { type: 'array', items: { type: 'string', enum: ['netease', 'qqmusic', 'kugou', 'kuwo'] }, description: 'sources 含 local 时扫描哪些本机客户端（网易云 netease / QQ音乐 qqmusic（qq 也认）/ 酷狗 kugou / 酷我 kuwo），默认全部' },
          keyword: { type: 'string', description: 'sources 含 local 时的搜索关键词，默认取归一后的歌手名（canonical）' },
        },
        required: ['singer'],
        additionalProperties: false,
      },
      output: textOutput,
      execute: guard('cover_setup', async (args) => {
        const settings = loadSettings()
        const warnings = []

        const singerInput = String(args.singer || '').trim()
        if (!singerInput) return fail('MISSING_SINGER', '需要歌手名（singer）：它同时决定声库名和成品文件名。', { next: 'cover_setup({ singer: "张三", sources: ["folder"], folder: "<素材目录>", target: "<目标曲.wav>" })' })
        if (singerInput.length > 60) return fail('BAD_SINGER', '歌手名太长（上限 60 字）。')

        // 歌手名模糊/别名解析：suis / 夜鹿 / ヨルシカ / Yorushika / すい → canonical
        const resolvedSinger = resolveSingerAlias(singerInput)
        const singer = resolvedSinger.canonical || singerInput
        if (singer !== singerInput && resolvedSinger.confidence > 0) {
          warnings.push('歌手名已归一：' + singerInput + ' → ' + singer + '（' + resolvedSinger.matchedBy + '，置信度 ' + resolvedSinger.confidence + '）')
        } else if (resolvedSinger.matchedBy === 'none') {
          warnings.push(
            '歌手名「' + singerInput + '」不在别名表里，按原名使用；' +
            (resolvedSinger.candidates.length
              ? '最接近的是：' + resolvedSinger.candidates.map((c) => c.canonical + '(' + (c.alias || '-') + ', 距离 ' + c.distance + ')').join('；')
              : '别名表为空') + '。',
          )
        }

        // 素材来源多选（至少 1 个）：优先 args.sources；否则兼容旧的 mode
        const sourcesProvided = args.sources !== undefined && args.sources !== null && args.sources !== ''
        const modeRaw = String(args.mode || '').trim().toLowerCase()
        const rawKinds = []
        const badSources = []
        const pushKind = (item) => {
          const key = String(item == null ? '' : item).trim().toLowerCase()
          if (!key) return
          const kind = SOURCE_ALIAS[key]
          if (kind) {
            if (!rawKinds.includes(kind)) rawKinds.push(kind)
          } else {
            badSources.push(String(item))
          }
        }
        if (sourcesProvided) {
          const list = Array.isArray(args.sources) ? args.sources : [args.sources]
          for (const item of list) {
            if (item && typeof item === 'object') {
              for (const [k, v] of Object.entries(item)) if (v) pushKind(k)
              continue
            }
            pushKind(item)
          }
        } else if (modeRaw) {
          const legacy = MODE_ALIAS[modeRaw]
          if (!legacy) {
            return fail('BAD_MODE', 'mode 只能是 import（本地目录，默认）、local（本机音乐客户端已下载）或 auto（按歌手名联网抓取，需显式开启）；也可以改用 sources: ["folder"|"local"|"auto"]（可多选）。', { got: clip(modeRaw, 40) })
          }
          pushKind(legacy)
        } else {
          pushKind('folder')
        }
        if (badSources.length) {
          return fail(
            'BAD_SOURCES',
            'sources 只认 folder（本地目录）/ local（本机客户端已下载）/ auto（B站搜索），可多选；不认识：' + badSources.join('、'),
            { bad: badSources, allowed: SOURCE_KINDS },
          )
        }
        if (!rawKinds.length) {
          return fail('BAD_SOURCES', '至少要选 1 个素材来源：sources: ["folder","local","auto"]（可多选，不能空）。', { allowed: SOURCE_KINDS })
        }
        const sourceKinds = rawKinds.slice()
        const hasSource = (k) => sourceKinds.includes(k)
        const autoMode = hasSource('auto')
        // 兼容旧字段：单一来源映射回 import/local/auto，多来源是 'mixed'
        const mode = sourceKinds.length === 1 ? (sourceKinds[0] === 'folder' ? 'import' : sourceKinds[0]) : 'mixed'

        // 本机客户端（只读已下载文件，不需要账号/登录态）：clients 可指定，默认全部
        const CLIENT_IDS = LOCAL_CLIENTS.map((c) => c.id)
        const clientsIn = Array.isArray(args.clients) ? args.clients : (args.clients ? [args.clients] : [])
        const pickedClients = []
        const badClients = []
        for (const item of clientsIn) {
          const raw = String(item == null ? '' : item).trim()
          if (!raw) continue
          const id = CLIENT_ALIAS[raw.toLowerCase()] || CLIENT_ALIAS[raw]
          if (id) {
            if (!pickedClients.includes(id)) pickedClients.push(id)
          } else {
            badClients.push(raw)
          }
        }
        if (badClients.length) {
          return fail(
            'BAD_CLIENT',
            'clients 只认 ' + CLIENT_IDS.join(' / ') + '（也可以写中文名：网易云 / QQ音乐 / 酷狗 / 酷我），不认识：' + badClients.join('、'),
            { bad: badClients, allowed: CLIENT_IDS },
          )
        }
        const clients = pickedClients.length ? pickedClients : CLIENT_IDS.slice()
        const keyword = String(args.keyword || '').trim() || singer

        const voiceRaw = String(args.voice || 'auto').trim().toLowerCase()
        const voice = VOICE_ALIAS[voiceRaw]
        if (!voice) {
          return fail('BAD_VOICE', 'voice 只能是 auto（默认，按目标曲人声自动判断）、female（女声）或 male（男声）。男歌手请选 male，或先用 auto 并确认判断正确。', { got: clip(voiceRaw, 20) })
        }

        // 分离方案（二选一）：kim 默认稳；bs317 串音更低但实验无可测听感提升
        const sepRaw = String(args.separateModel || 'kim').trim().toLowerCase()
        const separateModel = SEPARATE_ALIAS[sepKey(sepRaw)]
        if (!separateModel) {
          return fail('BAD_SEPARATE_MODEL', 'separateModel 只能是 kim（默认，稳）或 bs317（BS-RoFormer ep317，串音指标更低；实验结论：无可测听感提升）。', { got: clip(sepRaw, 30), allowed: SEPARATE_MODELS })
        }

        const stepsRaw = args.steps === undefined || args.steps === null || args.steps === '' ? settings.defaultSteps : args.steps
        const steps = toInt(stepsRaw, NaN)
        if (!Number.isFinite(steps) || steps < STEPS_MIN || steps > STEPS_MAX) {
          return fail('BAD_STEPS', '步数要在 ' + STEPS_MIN + '-' + STEPS_MAX + ' 之间；9000 步约 55 分钟（RTX 5060 Laptop 实测）。', { got: clip(String(args.steps), 20), defaultSteps: settings.defaultSteps })
        }

        const shift = toShift(args.shift)
        if (!Number.isInteger(shift) || shift < SHIFT_MIN || shift > SHIFT_MAX) {
          return fail('BAD_SHIFT', 'shift 只能是 ' + SHIFT_MIN + ' 到 +' + SHIFT_MAX + ' 的整数（半音）：0=原调，+2=整首升高 2 个半音，-2=降低 2 个半音；人声与伴奏同比例移，不允许只动人声。', { got: clip(String(args.shift), 20) })
        }

        const folder = String(args.folder || '').trim()
        if (hasSource('folder')) {
          if (!folder) return fail('MISSING_FOLDER', '选了「本地目录」就要给 folder（素材目录，插件只读）；不要目录请从 sources 里去掉 folder。', { next: 'cover_setup({ singer: "张三", sources: ["folder"], folder: "<素材目录>", target: "<目标曲.wav>" })' })
          let st = null
          try { st = statSync(folder) } catch { st = null }
          if (!st || !st.isDirectory()) return fail('FOLDER_NOT_FOUND', '找不到素材目录，或者它不是一个文件夹。', { folder: clip(folder, 200) })
        }

        // ── B站搜索（sources 含 auto）：联网抓取要逐次显式确认版权 ──────────────
        // 没选 auto 就忽略 search/limit（不校验、也不写进 config）
        const search = autoMode ? (String(args.search || '').trim() || singer) : ''
        const limit = autoMode
          ? (args.limit === undefined || args.limit === null || args.limit === '' ? 8 : toInt(args.limit, NaN))
          : 0
        if (autoMode && (!Number.isFinite(limit) || limit < 1 || limit > 50)) {
          return fail('BAD_LIMIT', 'limit 只能是 1-50 的整数（默认 8）：抓取时最多下载几首素材。', { got: clip(String(args.limit), 20) })
        }
        const ackRaw = args.copyrightAck === true || args.copyrightAck === 'true'
        // refSearch：好球曲本地找不到时允许联网搜（只为了让歌名能联网补齐，不是新错误码）
        const refSearch = args.refSearch === true || args.refSearch === 'true'
        // 选了 auto / 开了 refSearch 都需要显式版权确认；只选本地目录+本机客户端不需要
        const copyrightAck = (autoMode || refSearch) ? ackRaw : false

        // 没确认就不建 job、不联网：只回风险提示 + 二次确认指引（不是失败）
        const needsAck = (autoMode || refSearch) && !ackRaw
        if (needsAck) {
          const reasons = []
          if (autoMode) reasons.push('B站搜索会联网搜索并下载「' + search + '」的音频')
          if (refSearch) reasons.push('好球曲找不到时也会联网搜索')
          return {
            ok: true,
            needsConfirm: true,
            jobCreated: false,
            reason: autoMode ? 'COPYRIGHT_ACK_REQUIRED' : 'REF_SEARCH_NEEDS_ACK',
            singer,
            singerInput,
            resolvedSinger,
            sources: sourceKinds,
            mode,
            search,
            limit,
            refSearch,
            copyrightAck: false,
            warnings: [
              reasons.join('；') + '：版权风险自负，也可能触发平台风控。',
              '引擎要求 options.copyrightAck === true 才会联网；本次没有创建 job，也没有下载任何东西。',
              refSearch
                ? '好球曲联网搜索需要版权确认：确认后带 copyrightAck:true 再调用（import 模式也可以，只影响好球曲补齐）。'
                : '合规做法仍是自己准备素材（import 模式 + folder）；确认后再带 copyrightAck:true 调用。',
            ],
            next: '确认风险后带 copyrightAck:true 再调用：cover_setup({ singer: "' + singerInput + '", ' +
              'sources: ' + JSON.stringify(sourceKinds) + ', ' + (refSearch ? 'refSearch: true, ' : '') + 'copyrightAck: true })',
            message: (autoMode
              ? 'B站搜索需要你明确同意：会联网、版权风险自负。'
              : '好球曲联网搜索需要你明确同意：会联网、版权风险自负。') + '同意就带 copyrightAck:true 再调一次。',
          }
        }

        const target = String(args.target || '').trim()
        if (target) {
          let st = null
          try { st = statSync(target) } catch { st = null }
          if (!st || !st.isFile()) return fail('TARGET_NOT_FOUND', '目标曲文件不存在（target 要传到音频文件的完整路径）。', { target: clip(target, 200) })
          if (!hasAudioExt(target)) warnings.push('目标曲扩展名不像音频（' + extname(target) + '），引擎可能解不开。')
        } else {
          warnings.push('没给 target：这个 job 只能跑到 train，出不了成品。')
        }

        const refsIn = Array.isArray(args.refs) ? args.refs : (args.refs ? [args.refs] : [])
        const refs = []
        for (const item of refsIn) {
          // 兼容两种写法：字符串路径，或引擎风格的 { path } / { name }
          const raw = item && typeof item === 'object' ? (item.path || item.file || item.name || '') : item
          const s = String(raw == null ? '' : raw).trim()
          if (s) refs.push(s)
        }
        // 好球曲（选填）：最多 20 条。
        // - 像路径（含 / \ 或盘符）→ 必须存在，否则按行号报 REF_NOT_FOUND（对着输入框好改）；
        // - 不像路径 → 当歌名放行，引擎会在素材目录里自动匹配，找不到只警告、不中断。
        if (refs.length > REF_MAX) {
          return fail('REF_TOO_MANY', '好球曲最多 ' + REF_MAX + ' 条（建议 2-5 首），现在给了 ' + refs.length + ' 条。', { count: refs.length })
        }
        const refCheck = classifyRefs(refs)
        if (refCheck.missing.length) {
          return fail(
            'REF_NOT_FOUND',
            '第 ' + refCheck.missing.map((m) => m.line).join('、') + ' 行找不到文件：' +
              refCheck.missing.map((m) => basename(m.path)).join('、') +
              '。检查这一行路径；歌名可以直接填（不用路径），留空也行。',
            { missing: refCheck.missing.map((m) => ({ line: m.line, path: m.path })) },
          )
        }
        if (refCheck.names.length) {
          warnings.push('好球曲里有 ' + refCheck.names.length + ' 个歌名：会在素材目录里自动匹配，找不到会被忽略（不会中断）。')
        }
        if (refs.length === 0) warnings.push('好球曲是选填的：不填就让引擎从素材目录里自动筛；填了通常更准。')

        const ready = engineReady(settings)
        if (!ready.ok) return fail(ready.error, ready.message + (ready.hint ? ' ' + ready.hint : ''), { engineDir: ready.engineDir })

        const stamp = new Date().toISOString().replace(/[-:T]/g, '').slice(0, 14)
        let jobId = safeName(singer, 'job') + '-' + stamp
        let workdir = join(settings.workRoot, jobId)
        let n = 2
        while (existsSync(workdir)) {
          jobId = safeName(singer, 'job') + '-' + stamp + '-' + n
          workdir = join(settings.workRoot, jobId)
          n += 1
        }
        try {
          mkdirSync(workdir, { recursive: true })
        } catch (e) {
          return fail('WORKDIR_CREATE_FAILED', '建不了工作目录：' + (e && e.message))
        }

        const config = buildJobConfig({
          singer,
          mode,
          sources: sourceKinds,
          folder,
          search,
          limit,
          copyrightAck,
          refSearch,
          refs,
          target,
          steps,
          shift,
          voice,
          separateModel,
          clients,
          keyword,
          outputDir: settings.outputDir,
        })
        const configPath = join(workdir, 'job_config.json')
        try {
          writeJsonAtomic(configPath, config)
        } catch (e) {
          return fail('CONFIG_WRITE_FAILED', '写 job_config.json 失败：' + (e && e.message))
        }

        const init = await runCli(['init', '--config', configPath, '--workdir', workdir], {
          timeoutMs: 60000,
          cwd: workdir,
          settings,
        })
        const jobFile = findJobFile(workdir)
        if (!init.ok || !jobFile) {
          return fail('ENGINE_INIT_FAILED', '引擎 init 没成功（退出码 ' + init.code + (init.timedOut ? '，超时' : '') + '）：' + clip(init.stderr || init.stdout, 200), {
            jobId,
            workdir,
            config: configPath,
            jobFile: jobFile || null,
          })
        }

        const job = readJob(jobFile)
        const preview = await buildPreview({
          jobFile, workdir, folder, mode, settings, voice, clients, keyword, kinds: sourceKinds, search,
        })
        return {
          ok: true,
          jobId,
          workdir,
          jobFile,
          config: configPath,
          singer,
          singerInput,
          resolvedSinger,
          sources: sourceKinds,
          sourceLabels: sourceKinds.map(sourceLabelOf),
          mode,
          steps,
          shift,
          shiftNote: shiftNoteOf(shift),
          voice,
          voiceNote: VOICE_LABEL[voice] || voice,
          separateModel,
          separateModelNote: SEPARATE_MODEL_LABEL[separateModel] || separateModel,
          // 好球曲分类：对话里可以直接展示「几个歌名 / 几个路径」
          refs: { paths: refCheck.paths, names: refCheck.names },
          refCount: refs.length,
          refSearch: refSearch === true,
          clients: sourceKinds.includes('local') ? clients : undefined,
          keyword: sourceKinds.includes('local') ? keyword : undefined,
          target: target || null,
          stages: job ? job.stages.map((s) => s.stage + ':' + s.status) : [],
          preview,
          warnings: warnings.length ? warnings : undefined,
          eta: etaText(steps),
          next: 'cover_run({ jobId: "' + jobId + '" })，然后 cover_status 轮询',
          message: 'job 已建，引擎会在后台跑 0→9。' + etaText(steps) + (shift === 0 ? '' : '按' + shiftNoteOf(shift) + '出片。') + '训练期间随时可以 cover_status 看进度，不用一直等。',
        }
      }),
    },
    {
      name: 'cover_run',
      description: '后台启动引擎跑翻唱管线（不阻塞，立刻返回 pid）。可按 --from/--to 指定阶段、--resume 断点续跑。9000 步约 55 分钟（RTX 5060 Laptop 实测），用 cover_status 轮询。exit 2 只可能是版权确认（COPYRIGHT_ACK_REQUIRED）或缺显存（LOW_VRAM，空闲<5G 且未放行，唯一的硬件决策），训练阶段不会因硬件退出。',
      parameters: {
        type: 'object',
        properties: {
          jobId: { type: 'string', description: 'cover_setup 返回的 jobId' },
          from: { type: 'string', enum: STAGES, description: '从哪个阶段开始（默认 fetch）' },
          to: { type: 'string', enum: STAGES, description: '跑到哪个阶段为止（默认 export）' },
          resume: { type: 'boolean', description: '断点续跑：跳过已 done 的阶段' },
        },
        required: ['jobId'],
        additionalProperties: false,
      },
      output: textOutput,
      execute: guard('cover_run', async (args) => {
        const settings = loadSettings()
        const r = resolveJob(args, settings)
        if (!r.ok) return r.result

        const from = String(args.from || '').trim()
        const to = String(args.to || '').trim()
        for (const [label, v] of [['from', from], ['to', to]]) {
          if (v && !STAGES.includes(v)) return fail('BAD_STAGE', label + ' 必须是 ' + STAGES.join(' / ') + ' 之一。', { got: clip(v, 30) })
        }
        if (from && to && STAGES.indexOf(from) > STAGES.indexOf(to)) {
          return fail('BAD_RANGE', 'from 不能排在 to 后面（阶段顺序：' + STAGES.join(' → ') + '）。')
        }

        // 抓取模式（非 import）：引擎要求 options.copyrightAck === true，否则 run 直接 exit 2。
        // 先在本地拦一道（省得白起进程、也让错误立刻可见），保留引擎侧 exit 2 的兜底。
        const jobMode = String(r.job.mode || 'import').toLowerCase()
        const isFetchMode = jobMode !== 'import'
        const jobOpts = r.job.options || {}
        if (isFetchMode && jobOpts.copyrightAck !== true) {
          return fail('COPYRIGHT_ACK_REQUIRED', '这个 job 是抓取模式，但没记录版权确认（options.copyrightAck）。重新 cover_setup(带 copyrightAck:true) 建 job，或改用 import 模式给素材目录。', {
            jobId: r.jobId,
            mode: jobMode,
            next: 'cover_setup({ singer: "<歌手>", mode: "auto", copyrightAck: true })',
          })
        }

        const ready = engineReady(settings)
        if (!ready.ok) return fail(ready.error, ready.message)

        const running = isJobRunning(r.jobId, r.workdir)
        if (running.running) {
          return fail('JOB_RUNNING', '这个 job 已经在跑了（pid ' + running.pid + '）：用 cover_status 看进度，或先 cover_cancel。', { jobId: r.jobId, pid: running.pid })
        }

        const started = startJob({
          jobId: r.jobId,
          workdir: r.workdir,
          jobFile: r.jobFile,
          from,
          to,
          resume: !!args.resume,
          allowDuet: !!settings.allowDuet,
        }, settings)
        if (!started.ok) return started

        // run 启动时就会 exit 2 的只有两种：抓取版权闸（COPYRIGHT_ACK_REQUIRED）和硬件闸
        // （LOW_VRAM，空闲显存 <5G 且没放行 —— 这是唯一的硬件决策）。等一个短窗口把结构化错误带回对话；
        // 训练阶段本身不再因为硬件 exit 2。
        let early = null
        const probe = await waitForEarlyExit(r.jobId, EARLY_EXIT_MS)
        if (probe.exited) {
          const tail = readLogTail(started.log, 20)
          const text = tail.join(' | ')
          const decision = readDecision(r.jobFile)
          const decisionCode = decision && decision.code ? decision.code : ''
          if (probe.code === 2 && (decisionCode === 'COPYRIGHT_ACK_REQUIRED' || /copyright/i.test(text))) {
            return fail('COPYRIGHT_ACK_REQUIRED', '引擎没有版权确认，拒绝抓取（exit 2）：' + clip(text, 300), {
              jobId: r.jobId,
              exitCode: 2,
              decision,
              log: started.log,
              next: 'cover_setup({ singer: "<歌手>", mode: "auto", copyrightAck: true }) 重建 job 后再 cover_run',
            })
          }
          if (probe.code === 2) {
            const lowVram = decisionCode === 'LOW_VRAM' || /LOW_VRAM/i.test(text)
            const hint = lowVram
              ? '唯一硬件决策 LOW_VRAM：空闲显存 < 5G 且没放行。二选一：① 减步数重建 job；② 显式放行低显存（引擎 --allow-lowmem / job 配置 options.allowLowmem=true；v0.2 插件还没摆到 UI）。'
              : (decision && decision.reason ? decision.reason : '看 job.json 的 decision 字段。')
            return fail('NEEDS_USER_DECISION', '引擎要求你先做决定（exit 2，decision.code=' + (decisionCode || 'UNKNOWN') + '）：' + hint + ' 日志尾部：' + clip(text, 200), {
              jobId: r.jobId,
              exitCode: 2,
              decision,
              lowVram,
              log: started.log,
            })
          }
          if (probe.code !== 0) {
            return fail('ENGINE_EXIT_EARLY', '引擎刚启动就退出了（exit ' + probe.code + '）：' + clip(text, 300), { jobId: r.jobId, exitCode: probe.code, log: started.log })
          }
          early = { exitCode: 0, logTail: tail }
        }

        const steps = (r.job.options && r.job.options.steps) || settings.defaultSteps
        return {
          ok: true,
          jobId: r.jobId,
          pid: started.pid,
          log: started.log,
          startedAt: started.startedAt,
          from: from || 'fetch',
          to: to || 'export',
          resume: !!args.resume,
          ...(early ? { exited: true, exitCode: early.exitCode, logTail: early.logTail } : {}),
          eta: etaText(steps),
          next: 'cover_status({ jobId: "' + r.jobId + '" })',
          message: '引擎已在后台启动（pid ' + started.pid + '）。' + etaText(steps) + '训练期间不用一直等，隔一阵 cover_status 看一眼就行。',
        }
      }),
    },
    {
      name: 'cover_status',
      description: '看 job 各阶段状态、进度百分比、日志尾部和引擎的 decision（日志已脱敏）。引擎不在时回退读 job.json，仍然可用。decision.code 里 LOW_VRAM 是唯一的硬件决策（空闲显存 <5G 且未放行），训练阶段不会因硬件退出。',
      parameters: {
        type: 'object',
        properties: {
          jobId: { type: 'string', description: 'jobId' },
          tailLines: { type: 'number', description: '日志尾部行数，默认 30，最多 200' },
        },
        required: ['jobId'],
        additionalProperties: false,
      },
      output: textOutput,
      execute: guard('cover_status', async (args) => {
        const settings = loadSettings()
        const r = resolveJob(args, settings)
        if (!r.ok) return r.result
        const tailLines = Math.min(Math.max(toInt(args.tailLines, 30), 1), 200)

        let view = r.job
        let cliState = 'skipped'
        const ready = engineReady(settings)
        if (ready.ok) {
          const res = await runCli(['status', '--job', r.jobFile], { timeoutMs: 20000, cwd: r.workdir, settings })
          if (res.ok) {
            const parsed = parseJsonFrom(res.stdout, null)
            if (parsed && typeof parsed === 'object') {
              const n2 = normalizeJob({ ...parsed, id: parsed.id || r.jobId })
              view = {
                ...n2,
                jobId: n2.jobId || r.jobId,
                singer: n2.singer || r.job.singer,
                target: n2.target || r.job.target,
                createdAt: n2.createdAt || r.job.createdAt,
                options: n2.options || r.job.options,
                total: n2.total || r.job.total,
                done: n2.total ? n2.done : r.job.done,
                stages: n2.stages.length ? n2.stages : r.job.stages,
                failedStage: n2.failedStage || r.job.failedStage,
                cancelled: n2.cancelled || r.job.cancelled,
                decision: n2.decision || r.job.decision || null,
              }
              cliState = 'ok'
            } else {
              cliState = 'unparsable'
            }
          } else {
            cliState = res.timedOut ? 'timeout' : 'failed'
          }
        }

        const running = isJobRunning(r.jobId, r.workdir)
        let logTail = []
        if (view.currentStage) logTail = readLogTail(stageLogPath(r.workdir, view.currentStage), tailLines)
        if (!logTail.length) logTail = readLogTail(engineLogPath(r.workdir), tailLines)

        let state = 'idle'
        if (view.cancelled) state = 'cancelled'
        else if (view.decision) state = 'needs_decision'
        else if (view.failedStage) state = 'failed'
        else if (view.total && view.done >= view.total) state = 'done'
        else if (running.running) state = 'running'

        const steps = (view.options && view.options.steps) || (r.job.options && r.job.options.steps) || settings.defaultSteps
        return {
          ok: true,
          jobId: r.jobId,
          state,
          progress: {
            done: view.done,
            total: view.total,
            percent: Math.round((view.done / Math.max(view.total, 1)) * 100),
            currentStage: view.currentStage,
          },
          stages: view.stages.map((s) => ({ stage: s.stage, status: s.status, startedAt: s.startedAt, finishedAt: s.finishedAt, error: s.error })),
          running: running.running,
          pid: running.pid || 0,
          engineCli: cliState,
          // 引擎的 stem 检测（引擎算，插件只透出；没跑 separate 前为 null）
          stemCheck: view.stemCheck || r.job.stemCheck || null,
          polarityFixed: view.polarityFixed !== undefined && view.polarityFixed !== null
            ? view.polarityFixed
            : (r.job.polarityFixed !== undefined ? r.job.polarityFixed : null),
          decision: view.decision || null,
          decisionCode: view.decision && view.decision.code ? String(view.decision.code) : null,
          updatedAt: view.updatedAt || null,
          eta: state === 'running' ? etaText(steps) : null,
          logTail,
          result: view.result || null,
        }
      }),
    },
    {
      name: 'cover_cancel',
      description: '取消正在跑的 job：先让引擎自己收尾（CLI cancel），不行再结束整个进程树。',
      parameters: {
        type: 'object',
        properties: { jobId: { type: 'string', description: 'jobId' } },
        required: ['jobId'],
        additionalProperties: false,
      },
      output: textOutput,
      execute: guard('cover_cancel', async (args) => {
        const settings = loadSettings()
        const r = resolveJob(args, settings)
        if (!r.ok) return r.result
        const res = await cancelJob({ jobId: r.jobId, workdir: r.workdir, jobFile: r.jobFile }, settings)
        if (!res.ok) return res
        return {
          ok: true,
          jobId: r.jobId,
          pid: res.pid,
          killed: res.killed,
          engineCli: res.cli,
          note: res.note,
          next: 'cover_status({ jobId: "' + r.jobId + '" }) 确认 stage 变成 cancelled',
          message: '已请求取消' + (res.killed ? '，进程树已结束' : '') + '。已完成的阶段不会丢，之后可以 cover_run({ resume: true }) 续跑。',
        }
      }),
    },
    {
      name: 'cover_list',
      description: '列历史 job（按最近改动排序）：jobId / 歌手 / 进度 / 当前阶段 / 是否有成品。',
      parameters: {
        type: 'object',
        properties: { limit: { type: 'number', description: '返回条数，默认 20，最多 100' } },
        additionalProperties: false,
      },
      output: textOutput,
      execute: guard('cover_list', async (args) => {
        const settings = loadSettings()
        const limit = Math.min(Math.max(toInt(args.limit, 20), 1), 100)
        const res = listJobs(settings.workRoot, { limit })
        if (!res.ok) return res
        if (!res.jobs.length) {
          return { ok: true, total: 0, jobs: [], message: '还没有 job：用 cover_setup 建第一个（歌手 + 素材目录 + 目标曲）。' }
        }
        return {
          ok: true,
          total: res.total,
          jobs: res.jobs.map((j) => ({
            jobId: j.jobId,
            engineId: j.engineId || null,
            singer: j.singer,
            mode: j.mode,
            target: j.target,
            status: j.status,
            progress: j.progress,
            done: j.done,
            total: j.total,
            currentStage: j.currentStage,
            running: !!j.running,
            hasMp3: !!j.hasMp3,
            updatedAt: j.updatedAt,
            hasMix: j.hasMix,
          })),
          next: 'cover_status({ jobId }) 看单个 job 的细节',
        }
      }),
    },
    {
      name: 'cover_export',
      description: '把成品复制到 桌面/翻唱工厂 并返回路径（幂等：同名同大小不重复复制）。只有 wav 时用 ffmpeg 转 mp3，ffmpeg 不在就交付 wav 并说明。',
      parameters: {
        type: 'object',
        properties: {
          jobId: { type: 'string', description: 'jobId' },
          overwrite: { type: 'boolean', description: '目标已存在且大小不同时是否覆盖，默认 true' },
        },
        required: ['jobId'],
        additionalProperties: false,
      },
      output: textOutput,
      execute: guard('cover_export', async (args) => {
        const settings = loadSettings()
        const r = resolveJob(args, settings)
        if (!r.ok) return r.result
        const job = r.job
        const overwrite = args.overwrite === undefined ? true : !!args.overwrite

        const singerName = safeName(job.singer || 'cover', 'cover')
        const targetBase = safeName(basename(String(job.target || '')).replace(/\.[^.]+$/, ''), 'target')
        const outName = singerName + '-' + targetBase
        const outDir = settings.outputDir
        try {
          mkdirSync(outDir, { recursive: true })
        } catch (e) {
          return fail('OUTPUT_DIR_FAILED', '成品目录建不了：' + (e && e.message), { outputDir: outDir })
        }

        const warnings = []
        let format = 'mp3'
        let converted = false

        // 引擎 stage 9 已经产出并复制到 outputDir，job.result 里记了最终路径：
        // 直接认它，别再复制一遍（也避免引擎与本插件命名规则不同时留下两份）
        const engineMp3 = job.result && typeof job.result.mp3 === 'string' ? job.result.mp3 : ''
        const engineReport = job.result && typeof job.result.report === 'string' ? job.result.report : ''
        let src = engineMp3 && existsSync(engineMp3) ? engineMp3 : ''
        if (!src) src = findArtifact(r.workdir, ['.mp3'])

        if (!src) {
          const wav = findArtifact(r.workdir, ['.wav'])
          if (wav) {
            const targetPath = join(outDir, outName + '.mp3')
            const enc = await encodeMp3(wav, targetPath, settings)
            if (enc.ok) {
              src = targetPath
              converted = true
            } else {
              warnings.push(enc.error === 'FFMPEG_MISSING' ? '没找到 ffmpeg，先把 wav 交付到桌面。' : 'ffmpeg 转 mp3 失败，先把 wav 交付到桌面。')
              src = wav
              format = 'wav'
            }
          }
        }

        if (!src) {
          const ready = engineReady(settings)
          if (ready.ok) {
            await runCli(['run', '--job', r.jobFile, '--from', 'export', '--to', 'export'], { timeoutMs: 300000, cwd: r.workdir, settings })
            src = findArtifact(r.workdir, ['.mp3']) || findArtifact(r.workdir, ['.wav'])
            if (src) format = src.toLowerCase().endsWith('.mp3') ? 'mp3' : 'wav'
          }
        }
        if (!src) {
          return fail('NO_ARTIFACT', '还没有可导出的成品：mix/export 没产出音频文件。先用 cover_status 看卡在哪一步。', { jobId: r.jobId })
        }

        const alreadyInOutDir = resolve(dirname(src)).toLowerCase() === resolve(outDir).toLowerCase()
        let outputPath = alreadyInOutDir ? src : join(outDir, outName + (format === 'wav' ? '.wav' : '.mp3'))
        if (!alreadyInOutDir && resolve(src) !== resolve(outputPath)) {
          let same = false
          try {
            const a = statSync(src)
            const b = statSync(outputPath)
            same = a.size === b.size
          } catch {}
          if (!same || overwrite) {
            try {
              copyFileSync(src, outputPath)
            } catch (e) {
              return fail('COPY_FAILED', '复制到桌面失败（同名文件可能正被播放器占用）：' + (e && e.message), { jobId: r.jobId, target: outputPath })
            }
          }
        }

        let reportPath = engineReport && existsSync(engineReport) ? engineReport : ''
        let reportSource = 'engine'
        if (!reportPath) {
          const rep = writeReport(r.workdir, job, join(outDir, outName + '-report.md'))
          if (rep.error) warnings.push('报告没写成：' + rep.error)
          reportPath = rep.path || ''
          reportSource = rep.source
        }

        return {
          ok: true,
          jobId: r.jobId,
          output: outputPath,
          format,
          converted,
          report: reportPath || null,
          reportSource,
          warnings: warnings.length ? warnings : undefined,
          message: '成品在 ' + outName + (format === 'wav' ? '（wav：ffmpeg 不在或转码失败）' : '') + '，报告同目录。',
        }
      }),
    },
    {
      name: 'cover_delete',
      description: '删除 job：运行中会先自动取消；默认软删（整个工作目录移到 _deleted/，可手动找回），mode="purge" 才物理删除。',
      parameters: {
        type: 'object',
        properties: {
          jobId: { type: 'string', description: '要删的 jobId' },
          mode: { type: 'string', enum: ['soft', 'purge'], description: 'soft=软删（默认，移到 _deleted/ 可找回）；purge=物理删除，不可恢复' },
        },
        required: ['jobId'],
        additionalProperties: false,
      },
      output: textOutput,
      execute: guard('cover_delete', deleteJob),
    },
    {
      name: 'cover_doctor',
      description: '依赖体检：调引擎 run.py doctor，返回 {ok, health, checks, missing, hints, suggestedTorch, torchInstallCommand}。health=green 全齐 / yellow 缺可选 / red 缺必需（此时插件会拦住新建任务）。suggestedTorch 是引擎按显卡推荐的 torch（含 indexUrl 与版本范围），torchInstallCommand 可直接复制执行；引擎没给时为 null。引擎没有 doctor 或起不来时 ok:false，并指向 docs/INSTALL.md。',
      parameters: {
        type: 'object',
        properties: {},
        additionalProperties: false,
      },
      output: textOutput,
      execute: guard('cover_doctor', doctorCheck),
    },
  ]

  for (const def of defs) {
    IMPL.set(def.name, def.execute)
    try {
      ctx.tools.register(def)
    } catch (e) {
      try {
        console.error('[cover-factory] register ' + def.name + ' failed: ' + redactPaths(e && e.message))
      } catch {}
    }
  }
}
