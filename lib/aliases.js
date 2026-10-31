// 歌手别名/模糊解析：让 suis / 夜鹿 / ヨルシカ / Yorushika / すい 都定向到 canonical 'suis'
// 纯函数、无 IO：cover_setup 用它把用户输入归一到声库名，smoke 直接断言。

// 别名表：canonical 是声库/成品名用的规范名，aliases 里放各种写法（中日英、假名、常见错拼）。
// 扩展方式：往数组里加一条即可；同一 canonical 可以有多条别名。
export const ALIAS_TABLE = [
  {
    canonical: 'suis',
    aliases: [
      'suis', 'すい', 'スイ', 'スイス',
      '夜鹿', 'よるしか', 'ヨルシカ', 'yorushika', 'yorushika suis',
      'suis (yorushika)', 'suis-yorushika',
    ],
  },
  {
    canonical: 'yoasobi',
    aliases: ['yoasobi', 'ヨアソビ', 'よあそび', '夜遊び', '夜游', 'ikura', '幾田りら', '几田莉拉'],
  },
]

const KANA_ROMAJI = {
  あ: 'a', い: 'i', う: 'u', え: 'e', お: 'o',
  か: 'ka', き: 'ki', く: 'ku', け: 'ke', こ: 'ko',
  が: 'ga', ぎ: 'gi', ぐ: 'gu', げ: 'ge', ご: 'go',
  さ: 'sa', し: 'shi', す: 'su', せ: 'se', そ: 'so',
  ざ: 'za', じ: 'ji', ず: 'zu', ぜ: 'ze', ぞ: 'zo',
  た: 'ta', ち: 'chi', つ: 'tsu', て: 'te', と: 'to',
  だ: 'da', ぢ: 'ji', づ: 'zu', で: 'de', ど: 'do',
  な: 'na', に: 'ni', ぬ: 'nu', ね: 'ne', の: 'no',
  は: 'ha', ひ: 'hi', ふ: 'fu', へ: 'he', ほ: 'ho',
  ば: 'ba', び: 'bi', ぶ: 'bu', べ: 'be', ぼ: 'bo',
  ぱ: 'pa', ぴ: 'pi', ぷ: 'pu', ぺ: 'pe', ぽ: 'po',
  ま: 'ma', み: 'mi', む: 'mu', め: 'me', も: 'mo',
  や: 'ya', ゆ: 'yu', よ: 'yo',
  ら: 'ra', り: 'ri', る: 'ru', れ: 're', ろ: 'ro',
  わ: 'wa', を: 'wo', ん: 'n', ゔ: 'vu',
  ぁ: 'a', ぃ: 'i', ぅ: 'u', ぇ: 'e', ぉ: 'o',
  ゃ: 'ya', ゅ: 'yu', ょ: 'yo', っ: '', ー: '',
}

const DIGRAPHS = {
  'きゃ': 'kya', 'きゅ': 'kyu', 'きょ': 'kyo',
  'しゃ': 'sha', 'しゅ': 'shu', 'しょ': 'sho',
  'ちゃ': 'cha', 'ちゅ': 'chu', 'ちょ': 'cho',
  'にゃ': 'nya', 'にゅ': 'nyu', 'にょ': 'nyo',
  'ひゃ': 'hya', 'ひゅ': 'hyu', 'ひょ': 'hyo',
  'みゃ': 'mya', 'みゅ': 'myu', 'みょ': 'myo',
  'りゃ': 'rya', 'りゅ': 'ryu', 'りょ': 'ryo',
  'ぎゃ': 'gya', 'ぎゅ': 'gyu', 'ぎょ': 'gyo',
  'じゃ': 'ja', 'じゅ': 'ju', 'じょ': 'jo',
  'びゃ': 'bya', 'びゅ': 'byu', 'びょ': 'byo',
  'ぴゃ': 'pya', 'ぴゅ': 'pyu', 'ぴょ': 'pyo',
  'ふぁ': 'fa', 'ふぃ': 'fi', 'ふぇ': 'fe', 'ふぉ': 'fo',
  'てぃ': 'ti', 'でぃ': 'di', 'うぇ': 'we', 'うぃ': 'wi', 'ゔぁ': 'va',
}

// 全角 ASCII → 半角（全角カナは NFKC 与 katakanaToHiragana 处理）
function toHalfWidth(s) {
  let out = ''
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i)
    if (c === 0x3000) out += ' '
    else if (c >= 0xff01 && c <= 0xff5e) out += String.fromCharCode(c - 0xfee0)
    else out += s[i]
  }
  return out
}

// 片假名 → 平假名（统一后再罗马字化）
function katakanaToHiragana(s) {
  let out = ''
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i)
    if (c >= 0x30a1 && c <= 0x30f6) out += String.fromCharCode(c - 0x60)
    else out += s[i]
  }
  return out
}

function romanizeRest(s) {
  let out = ''
  let i = 0
  while (i < s.length) {
    const ch = s[i]
    if (ch === 'っ') { i += 1; continue }
    const pair = s.slice(i, i + 2)
    if (DIGRAPHS[pair]) { out += DIGRAPHS[pair]; i += 2; continue }
    if (KANA_ROMAJI[ch] !== undefined) { out += KANA_ROMAJI[ch]; i += 1; continue }
    out += ch
    i += 1
  }
  return out
}

function kanaToRomaji(s) {
  let out = ''
  let i = 0
  while (i < s.length) {
    const ch = s[i]
    if (ch === 'っ') {
      const rest = romanizeRest(s.slice(i + 1))
      if (rest) out += rest[0]
      i += 1
      continue
    }
    const pair = s.slice(i, i + 2)
    if (DIGRAPHS[pair]) { out += DIGRAPHS[pair]; i += 2; continue }
    if (KANA_ROMAJI[ch] !== undefined) { out += KANA_ROMAJI[ch]; i += 1; continue }
    out += ch
    i += 1
  }
  return out
}

// 归一化：NFKC → 全角转半角 → 小写 → 片假名转平假名 → 罗马字化 → 只留字母数字（CJK 保留）
export function normalizeName(input) {
  let v = String(input == null ? '' : input).trim()
  if (!v) return ''
  try { v = v.normalize('NFKC') } catch {}
  v = toHalfWidth(v).toLowerCase()
  v = katakanaToHiragana(v)
  v = kanaToRomaji(v)
  return v.replace(/[^a-z0-9\u3040-\u30ff\u4e00-\u9fff]/g, '')
}

export function levenshtein(a, b) {
  const s = String(a || '')
  const t = String(b || '')
  if (s === t) return 0
  if (!s.length) return t.length
  if (!t.length) return s.length
  let prev = new Array(t.length + 1)
  for (let j = 0; j <= t.length; j++) prev[j] = j
  for (let i = 1; i <= s.length; i++) {
    const cur = [i]
    for (let j = 1; j <= t.length; j++) {
      const cost = s[i - 1] === t[j - 1] ? 0 : 1
      cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
    }
    prev = cur
  }
  return prev[t.length]
}

function entriesOf(entry) {
  const list = []
  const add = (name, kind) => {
    const norm = normalizeName(name)
    if (!norm) return
    list.push({ name: String(name), norm, kind })
  }
  add(entry.canonical, 'canonical')
  for (const a of entry.aliases || []) add(a, 'alias')
  return list
}

const TABLE = ALIAS_TABLE.map((e) => ({ canonical: e.canonical, entries: entriesOf(e) }))

// 返回 { input, canonical, matchedBy, confidence, candidates }
// matchedBy: canonical | alias | contains | fuzzy | none
export function resolveSingerAlias(input) {
  const raw = String(input == null ? '' : input).trim()
  const norm = normalizeName(raw)
  if (!raw) return { input: '', canonical: '', matchedBy: 'none', confidence: 0, candidates: [] }

  // 1) 精确 canonical
  for (const t of TABLE) {
    if (raw.toLowerCase() === String(t.canonical).toLowerCase()) {
      return { input: raw, canonical: t.canonical, matchedBy: 'canonical', confidence: 1, candidates: [{ canonical: t.canonical, matchedBy: 'canonical', confidence: 1 }] }
    }
  }

  // 2) 别名精确（比较原始小写与归一化结果）
  const hits = []
  for (const t of TABLE) {
    for (const e of t.entries) {
      if (e.norm === norm || e.name.toLowerCase() === raw.toLowerCase()) {
        hits.push({ canonical: t.canonical, alias: e.name, matchedBy: e.kind === 'canonical' ? 'canonical' : 'alias', confidence: e.kind === 'canonical' ? 1 : 0.95, distance: 0 })
      }
    }
  }
  if (hits.length) {
    hits.sort((a, b) => b.confidence - a.confidence)
    return { input: raw, canonical: hits[0].canonical, matchedBy: hits[0].matchedBy, confidence: hits[0].confidence, candidates: dedupeCandidates(hits) }
  }

  // 3) 归一化后包含（防「suis 的夜鹿」这类长输入；要求输入至少 3 个字符）
  if (norm.length >= 3) {
    const contained = []
    for (const t of TABLE) {
      for (const e of t.entries) {
        if (Math.min(norm.length, e.norm.length) >= 4 && (e.norm.includes(norm) || norm.includes(e.norm))) {
          contained.push({ canonical: t.canonical, alias: e.name, matchedBy: 'contains', confidence: 0.8, distance: 0 })
        }
      }
    }
    if (contained.length) {
      contained.sort((a, b) => b.confidence - a.confidence)
      return { input: raw, canonical: contained[0].canonical, matchedBy: 'contains', confidence: contained[0].confidence, candidates: dedupeCandidates(contained) }
    }
  }

  // 4) 编辑距离 ≤2 的最近邻
  const fuzzy = []
  for (const t of TABLE) {
    for (const e of t.entries) {
      const d = levenshtein(norm, e.norm)
      if (d <= 2 && d > 0) {
        fuzzy.push({ canonical: t.canonical, alias: e.name, matchedBy: 'fuzzy', confidence: Math.max(0.5, 0.85 - 0.1 * d), distance: d })
      }
    }
  }
  if (fuzzy.length) {
    fuzzy.sort((a, b) => (a.distance - b.distance) || (b.confidence - a.confidence))
    const best = fuzzy[0]
    return { input: raw, canonical: best.canonical, matchedBy: 'fuzzy', confidence: Math.round(best.confidence * 100) / 100, candidates: dedupeCandidates(fuzzy) }
  }

  // 5) 未命中：canonical=输入，给出最近邻建议
  const near = []
  for (const t of TABLE) {
    for (const e of t.entries) {
      const d = norm ? levenshtein(norm, e.norm) : 99
      near.push({ canonical: t.canonical, alias: e.name, matchedBy: 'suggestion', confidence: 0, distance: d })
    }
  }
  near.sort((a, b) => a.distance - b.distance)
  return { input: raw, canonical: raw, matchedBy: 'none', confidence: 0, candidates: dedupeCandidates(near).slice(0, 3) }
}

function dedupeCandidates(list) {
  const seen = new Set()
  const out = []
  for (const c of list) {
    const key = c.canonical + '|' + (c.alias || '')
    if (seen.has(key)) continue
    seen.add(key)
    out.push({
      canonical: c.canonical,
      alias: c.alias || null,
      matchedBy: c.matchedBy,
      confidence: Math.round((c.confidence || 0) * 100) / 100,
      distance: typeof c.distance === 'number' ? c.distance : null,
    })
    if (out.length >= 5) break
  }
  return out
}