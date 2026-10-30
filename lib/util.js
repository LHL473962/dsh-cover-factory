// 通用小工具：错误封装 / 路径脱敏 / 原子写 / 桌面目录 / 文件名安全化
//
// 约定（学自 dsh-meme-lookup）：
// - 工具失败不抛异常，统一返回 { ok:false, error, message }；
// - message 一律过 redactPaths()，不把本机绝对路径回传给对话；
// - 返回值里的结构化字段可以带路径（用户自己要能看到产物在哪），脱敏只针对 message。

import { existsSync, mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { homedir } from 'node:os'

export const AUDIO_EXT = ['.wav', '.mp3', '.flac', '.m4a', '.ogg', '.aac', '.ape', '.wma', '.aiff', '.opus']

// 学自 dsh-meme-lookup 的 redactPaths：控制字符/URL/绝对路径一律替换，再压成一行
export function redactPaths(text) {
  let s = String(text == null ? '' : text).replace(/[\p{Cc}\p{Cf}]+/gu, ' ').replace(/\s+/g, ' ')
  s = s.replace(/https?:\/\/\S+/gi, '<url>')
  s = s.replace(/[A-Za-z]:\\[^\s"']+/g, '<path>')
  s = s.replace(/[A-Za-z]:\/[^\s"']+/g, '<path>')
  s = s.replace(/\/(?:home|Users|root)\/[^\s"']+/g, '<path>')
  s = s.trim().slice(0, 400)
  if (Buffer.byteLength(s) > 1200) s = s.slice(0, 300) + '...'
  return s
}

// 统一失败返回：任何工具都不许 throw
export function fail(error, message, extra) {
  const out = { ok: false, error: String(error || 'UNKNOWN'), message: redactPaths(message) }
  if (extra && typeof extra === 'object') {
    for (const [k, v] of Object.entries(extra)) if (v !== undefined) out[k] = v
  }
  return out
}

// 学自 dsh-meme-lookup / dsh-vision-toolkit：Windows 上杀毒/索引会短暂锁住刚写的文件
export function withWindowsRetry(fn, attempts = 4) {
  let last
  for (let i = 1; i <= attempts; i++) {
    try { return fn() } catch (e) {
      last = e
      const transient = process.platform === 'win32' && (e.code === 'EBUSY' || e.code === 'EPERM' || e.code === 'EACCES')
      if (!transient || i === attempts) throw e
      try { Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 120 * i) } catch {}
    }
  }
  throw last
}

// 原子写：tmp + rename，崩在写一半也不会留下半截 JSON
export function writeFileAtomic(file, content) {
  mkdirSync(dirname(file), { recursive: true })
  const tmp = file + '.tmp-' + process.pid + '-' + Date.now()
  try {
    withWindowsRetry(() => writeFileSync(tmp, content, 'utf8'))
    withWindowsRetry(() => renameSync(tmp, file))
  } catch (e) {
    try { rmSync(tmp, { force: true }) } catch {}
    throw e
  }
}

export function writeJsonAtomic(file, value) {
  writeFileAtomic(file, JSON.stringify(value, null, 2) + '\n')
}

// 读 JSON：文件不存在 / 半截 / 记事本加了 BOM 都返回 fallback，不抛
export function readJsonSafe(file, fallback = null) {
  try {
    let s = readFileSync(file, 'utf8')
    if (s.charCodeAt(0) === 0xfeff) s = s.slice(1)
    return JSON.parse(s)
  } catch {
    return fallback
  }
}

// OneDrive 把桌面重定向走了也要找对（Windows 中文名仍是 Desktop 目录）
export function desktopDir() {
  const home = homedir()
  const cands = []
  if (process.env.OneDrive) cands.push(join(process.env.OneDrive, 'Desktop'))
  cands.push(join(home, 'Desktop'), join(home, '桌面'), home)
  for (const c of cands) {
    try { if (existsSync(c)) return c } catch {}
  }
  return join(home, 'Desktop')
}

// 文件名安全化：成品名里不能有 \ / : * ? " < > |
export function safeName(input, fallback = 'unnamed') {
  let s = String(input == null ? '' : input)
    .replace(/[\\/:*?"<>|]/g, ' ')
    .replace(/[\p{Cc}\p{Cf}]+/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/[. ]+$/, '')
  if (!s) s = fallback
  // 中文一个字按 3 字节算，80 字符足够，别撞 Windows 260 路径限制
  if (s.length > 80) s = s.slice(0, 80).trim()
  return s || fallback
}

export function isAlive(pid) {
  const n = Number(pid)
  if (!Number.isFinite(n) || n <= 0) return false
  try {
    process.kill(n, 0)
    return true
  } catch (e) {
    // EPERM 说明进程还在，只是不归我们；ESRCH 才是真没了
    return !!(e && e.code === 'EPERM')
  }
}

export function toInt(value, fallback) {
  const n = Number.parseInt(String(value == null ? '' : value).trim(), 10)
  return Number.isFinite(n) ? n : fallback
}

export function clip(s, n = 600) {
  const t = String(s == null ? '' : s)
  return t.length > n ? t.slice(0, n) + '...' : t
}

export function hasAudioExt(name) {
  const t = String(name || '').toLowerCase()
  return AUDIO_EXT.some((ext) => t.endsWith(ext))
}
