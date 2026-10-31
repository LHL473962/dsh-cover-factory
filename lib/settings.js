// 设置：~/.dsh/cover-factory-settings.json（原子写，读坏了就用默认值，绝不抛）
//
// 键（全部可缺省）：
//   engineDir     引擎目录，默认 <插件目录>/engine
//   python        解释器路径；空/不存在则回退 DSH_PYTHON，再回退 PATH 的 python
//   outputDir     成品输出目录，默认 桌面/翻唱工厂
//   defaultSteps  旧配置兼容：只作为新建任务卡片步数的初值，设置页不再展示；范围 100–100000
//   allowDuet     是否允许对唱/合唱素材过预筛（引擎 --allow-duet）
//   autoDownload  旧开关，默认 false；v0.2 起抓取由每次调用的 copyrightAck 决定，这里只作记录
//   workRoot      job 工作目录根，默认 ~/.dsh/cover-factory/jobs
//   ffmpeg        ffmpeg 可执行文件（cover_export 兜底转 mp3 时用）

import { existsSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'
import { desktopDir, readJsonSafe, toInt, withWindowsRetry, writeJsonAtomic } from './util.js'

// 解释器：DSH_PYTHON 可指定；否则用 PATH 里的 python（v0.3 起不再硬编码某台机器的绝对路径）
export const DEFAULT_PYTHON = process.env.DSH_PYTHON || 'python'
export const OUTPUT_FOLDER = '翻唱工厂'
// 训练步数范围（用户在新建卡片自己填；覆盖 cover_setup 与设置里的旧 defaultSteps）
export const STEPS_MIN = 100
export const STEPS_MAX = 100000
export const DEFAULT_STEPS = 9000

export const SETTING_KEYS = ['engineDir', 'python', 'outputDir', 'defaultSteps', 'allowDuet', 'autoDownload', 'workRoot', 'ffmpeg']

// 测试可覆盖：DSH_COVER_FACTORY_HOME
export function dshHome() {
  const override = process.env.DSH_COVER_FACTORY_HOME
  return override ? String(override) : join(homedir(), '.dsh')
}

export function settingsFile() {
  return join(dshHome(), 'cover-factory-settings.json')
}

export function defaultSettings() {
  return {
    engineDir: '',
    python: '',
    outputDir: join(desktopDir(), OUTPUT_FOLDER),
    defaultSteps: DEFAULT_STEPS,
    allowDuet: false,
    autoDownload: false,
    workRoot: join(dshHome(), 'cover-factory', 'jobs'),
    ffmpeg: 'ffmpeg',
  }
}

function asBool(v, def) {
  if (typeof v === 'boolean') return v
  if (v === 'true' || v === 1 || v === '1') return true
  if (v === 'false' || v === 0 || v === '0') return false
  return def
}

function asStr(v, def) {
  const s = String(v == null ? '' : v).trim()
  return s ? s : def
}

// 学自 dsh-ops：配置严格校验（范围检查、类型兜底），坏值不静默写回
export function normalizeSettings(raw) {
  const def = defaultSettings()
  const out = { ...def }
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return out
  out.engineDir = asStr(raw.engineDir, def.engineDir)
  out.python = asStr(raw.python, def.python)
  out.outputDir = asStr(raw.outputDir, def.outputDir)
  out.workRoot = asStr(raw.workRoot, def.workRoot)
  out.ffmpeg = asStr(raw.ffmpeg, def.ffmpeg)
  const steps = toInt(raw.defaultSteps, def.defaultSteps)
  out.defaultSteps = Math.min(Math.max(steps, STEPS_MIN), STEPS_MAX)
  out.allowDuet = asBool(raw.allowDuet, def.allowDuet)
  out.autoDownload = asBool(raw.autoDownload, def.autoDownload)
  return out
}

export function loadSettings() {
  const raw = readJsonSafe(settingsFile(), null)
  return normalizeSettings(raw)
}

// 只认白名单键；未知键回报 ignored 而不是默默写丢
export function saveSettings(patch) {
  const cur = loadSettings()
  const incoming = patch && typeof patch === 'object' && !Array.isArray(patch) ? patch : {}
  const ignored = Object.keys(incoming).filter((k) => !SETTING_KEYS.includes(k))
  const merged = {}
  for (const k of SETTING_KEYS) merged[k] = incoming[k] === undefined ? cur[k] : incoming[k]
  const next = normalizeSettings(merged)
  const file = settingsFile()
  try {
    withWindowsRetry(() => writeJsonAtomic(file, next))
  } catch (e) {
    return { ok: false, error: 'SETTINGS_WRITE_FAILED', message: '设置写盘失败（文件可能被占用）', ignored, settings: cur }
  }
  return { ok: true, settings: next, ignored, file }
}

// python：设置 > DSH_PYTHON > PATH。返回 warning 供工具提示，不抛。
export function resolvePython(s) {
  const st = s || loadSettings()
  if (st.python && existsSync(st.python)) return { exe: st.python, source: 'settings' }
  if (existsSync(DEFAULT_PYTHON)) {
    return {
      exe: DEFAULT_PYTHON,
      source: 'builtin',
      warning: st.python ? '设置里的 python 路径不存在，已回退到 DSH_PYTHON / PATH。' : undefined,
    }
  }
  return { exe: 'python', source: 'PATH', warning: '没找到可用的 Python 解释器，已回退到 PATH 里的 python。' }
}

export function resolveEngineDir(s, pluginDir) {
  const st = s || loadSettings()
  return st.engineDir ? st.engineDir : join(pluginDir, 'engine')
}
