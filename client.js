/**
 * dsh-cover-factory —— 浏览器半端：设置页里的「翻唱工厂」一页（v0.2）
 *
 * 写法照 dsh-meme-lookup/client.js：自注册脚本，不是 ESM：
 *   window.__ModuleLoader__.load({ id, factory })
 * 不引外部依赖，只用 require('react') + React.createElement。
 *
 * 数据全部来自插件进程内的 /api/cover-factory/*（同源、64KB 上限）：
 *   GET  /settings              设置 + 引擎状态 + 在跑任务数
 *   POST /settings              保存设置（白名单键）
 *   GET  /jobs                  任务表（5 秒轮询）
 *   POST /jobs/create           新建任务（复用 cover_setup 的校验）
 *   POST /jobs/run|cancel|export 对应 cover_run / cover_cancel / cover_export
 */
window.__ModuleLoader__.load({
  id: 'dsh-cover-factory',
  factory: (require) => {
    const React = require('react')
    const h = React.createElement
    const API = '/api/cover-factory'
    const STEPS = [5000, 9000, 18000]
    // 训练耗时：RTX 5060 Laptop 实测（约 0.37 秒/步）→ 5000≈30 分钟 / 9000≈55 分钟 / 18000≈1 小时 50 分
    const ETA = { 5000: '约 30 分钟', 9000: '约 55 分钟', 18000: '约 1 小时 50 分' }
    const ETA_HINT = '耗时按 RTX 5060 Laptop 实测（约 0.37 秒/步）；没有可用 GPU 会走 CPU，慢约 10 倍。'
    // 素材来源：多选，至少 1 个
    const SOURCES = [
      ['folder', '本地目录'],
      ['local', '本机客户端'],
      ['auto', 'B站搜索'],
    ]
    const SOURCE_HINT = {
      folder: '自己准备好的素材文件夹（插件只读）',
      local: '网易云 / QQ音乐等「已下载」的文件',
      auto: '联网搜索并下载（需要版权确认）',
    }
    const CLIENTS = [
      ['netease', '网易云音乐'],
      ['qqmusic', 'QQ音乐'],
      ['kugou', '酷狗音乐'],
      ['kuwo', '酷我音乐'],
    ]
    const ALL_CLIENTS = CLIENTS.map((c) => c[0])
    // 训练期间的硬件风险（与 docs/QUICKSTART.md、docs/FAQ.md 的「硬件与风险」一节同一份内容）
    const RISK_LINES = [
      '训练时显卡会跑满（接近 100%），显存占用约 7.5–8G；笔记本会明显发热、风扇全速，可能降频。',
      '期间不要开游戏 / 剪辑 / 渲染等吃显卡的程序，容易 OOM 或崩溃。',
      '请插着电源、保持散热；长时间满载对老笔记本有风险（可能过热关机）。',
      '不要中途关机：引擎每 5 个 epoch 存一次盘，中断后可以用 resume 续训（同一个 job 重新运行选「续跑」）。',
      '磁盘需要预留 ≥ 10G（素材 + 切片 + 特征 + 模型）。',
      '显存 <5G 会先停下来让你选：减步数 或 加 --allow-lowmem（引擎侧放行，唯一的硬件硬闸）；5–8G 只警告，继续跑。',
      '没有可用 GPU 会走 CPU，慢约 10 倍（9000 步约 9 小时）。',
      'RTX 50 系必须用 torch cu128；30/40 系用 cu118/cu121；插件会按你的显卡自动推荐（设置页体检展开可看，命令可直接复制）。',
    ]

    // 复制安装命令：优先剪贴板 API，失败就提示手动选中
    const copyText = async (text) => {
      const t = String(text || '')
      try {
        if (typeof navigator !== 'undefined' && navigator.clipboard && navigator.clipboard.writeText) {
          await navigator.clipboard.writeText(t)
          return { ok: true }
        }
      } catch (e) { /* 落到下面的手动提示 */ }
      return { ok: false, text: t }
    }

    const asJson = async (res) => {
      let d = {}
      try { d = await res.json() } catch (e) { d = {} }
      if (!res.ok && !d.ok) throw new Error(d.message || ('HTTP ' + res.status))
      return d || {}
    }
    const jget = (p) => fetch(API + p, { credentials: 'same-origin' }).then(asJson)
    const jpost = (p, body) => fetch(API + p, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body || {}),
    }).then(asJson)

    const S = {
      wrap: { color: '#E2E8F0', fontSize: 13, display: 'flex', flexDirection: 'column', gap: 12 },
      bar: { display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', background: '#18212F', border: '1px solid #2A3A52', borderRadius: 10, padding: '8px 12px', color: '#94A3B8', fontSize: 12 },
      dot: (c) => ({ width: 8, height: 8, borderRadius: 4, background: c, display: 'inline-block', marginRight: 6 }),
      card: { background: '#1E2A3C', borderRadius: 12, padding: 16 },
      title: { color: '#E2E8F0', fontSize: 13, fontWeight: 600, marginBottom: 10, display: 'flex', alignItems: 'center', justifyContent: 'space-between' },
      grid: { display: 'flex', flexWrap: 'wrap', gap: 12 },
      field: { flex: '1 1 220px', minWidth: 170 },
      label: { display: 'block', color: '#94A3B8', fontSize: 12, margin: '4px 0' },
      input: { width: '100%', height: 32, background: '#161B26', border: '1px solid #2A3A52', borderRadius: 6, color: '#E2E8F0', padding: '0 8px', boxSizing: 'border-box' },
      check: { display: 'flex', alignItems: 'flex-start', gap: 8, color: '#94A3B8', fontSize: 12, marginTop: 10, lineHeight: 1.5, cursor: 'pointer' },
      btn: { height: 32, borderRadius: 8, border: 0, background: '#7C6BFF', color: '#fff', cursor: 'pointer', padding: '0 16px', fontSize: 12 },
      btnGhost: { height: 26, borderRadius: 6, border: 0, background: '#2A3A52', color: '#E2E8F0', cursor: 'pointer', fontSize: 11, padding: '0 9px' },
      btnDanger: { height: 26, borderRadius: 6, border: 0, background: '#43293A', color: '#FCA5A5', cursor: 'pointer', fontSize: 11, padding: '0 9px' },
      msg: { background: '#18212F', border: '1px solid #2A3A52', borderRadius: 8, padding: '8px 10px', color: '#C4B5FD', fontSize: 12, whiteSpace: 'pre-wrap', lineHeight: 1.6 },
      healthBody: { background: '#161B26', border: '1px solid #2A3A52', borderRadius: 8, padding: '10px 12px', marginTop: 6, fontSize: 12, lineHeight: 1.7 },
      checkRow: { display: 'flex', alignItems: 'baseline', gap: 6, color: '#C9D3E3', padding: '2px 0' },
      mono: { fontFamily: 'ui-monospace, Consolas, monospace', fontSize: 11, color: '#9FB3D1', marginTop: 3, wordBreak: 'break-all' },
      blockNotice: { marginTop: 12, border: '1px solid #5A2A2A', background: '#2A1414', borderRadius: 8, padding: '10px 12px', fontSize: 12, lineHeight: 1.7, color: '#F3C6C6' },
      head: { display: 'grid', gridTemplateColumns: '1fr 1.1fr .7fr .55fr .8fr .4fr 2.6fr', gap: 8, alignItems: 'center', color: '#94A3B8', fontSize: 11, paddingBottom: 4 },
      row: { display: 'grid', gridTemplateColumns: '1fr 1.1fr .7fr .55fr .8fr .4fr 2.6fr', gap: 8, alignItems: 'center', padding: '7px 0', borderTop: '1px solid #24344A', fontSize: 12 },
      muted: { color: '#64748B', fontSize: 12 },
      actions: { display: 'flex', gap: 6, flexWrap: 'wrap' },
      cell: { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
      textarea: { width: '100%', minHeight: 84, background: '#161B26', border: '1px solid #2A3A52', borderRadius: 6, color: '#E2E8F0', padding: '6px 8px', boxSizing: 'border-box', fontFamily: 'ui-monospace, Consolas, monospace', fontSize: 12, lineHeight: 1.6, resize: 'vertical' },
      hint: { color: '#64748B', fontSize: 11, lineHeight: 1.6, marginTop: 4 },
      risk: { marginTop: 12, border: '1px solid #5A4420', background: '#221B10', borderRadius: 8, padding: '8px 10px' },
      riskToggle: { background: 'transparent', border: 0, color: '#F5A524', cursor: 'pointer', fontSize: 12, fontWeight: 600, padding: 0, textAlign: 'left' },
      riskBody: { marginTop: 8, color: '#C9B79B', fontSize: 12, lineHeight: 1.7 },
      riskLine: { marginBottom: 4 },
    }

    const statusText = (j) => {
      if (j.running) return '运行中'
      const map = { done: '完成', failed: '失败', cancelled: '已取消', 'running-or-idle': '待续跑' }
      return map[j.status] || j.status || '—'
    }
    const statusColor = (j) => {
      if (j.running) return '#FBBF24'
      const map = { done: '#34D399', failed: '#F87171', cancelled: '#94A3B8' }
      return map[j.status] || '#FBBF24'
    }
    const ellipsis = (s) => {
      const t = String(s == null ? '' : s)
      return t.length > 46 ? '…' + t.slice(-45) : t
    }

    function Panel() {
      const [st, setSt] = React.useState(null)
      const [engine, setEngine] = React.useState(null)
      const [running, setRunning] = React.useState(0)
      const [jobs, setJobs] = React.useState([])
      const [msg, setMsg] = React.useState('')
      const [busy, setBusy] = React.useState('')
      const [form, setForm] = React.useState({
        singer: '', folder: '', target: '', refs: '',
        steps: 9000, sources: ['folder'], copyrightAck: false, refSearch: false, voice: 'auto',
        clients: ALL_CLIENTS.slice(), keyword: '',
      })
      // 风险提示默认收起
      const [riskOpen, setRiskOpen] = React.useState(false)
      // 依赖体检：doctor 为 {ok, health, checks, missing, hints, message}
      const [doctor, setDoctor] = React.useState(null)
      const [healthOpen, setHealthOpen] = React.useState(false)
      const [doctorBusy, setDoctorBusy] = React.useState(false)

      const runDoctor = React.useCallback(async () => {
        setDoctorBusy(true)
        try {
          const d = await jpost('/doctor', {})
          setDoctor(d || { ok: false, health: 'unknown', message: '体检没有返回结果' })
        } catch (e) {
          setDoctor({ ok: false, health: 'unknown', message: '体检失败：' + e.message })
        } finally {
          setDoctorBusy(false)
        }
      }, [])


      const reloadSettings = React.useCallback(async () => {
        try {
          const d = await jget('/settings')
          setSt(d.settings || null)
          setEngine(d.engine || null)
          if (typeof d.running === 'number') setRunning(d.running)
        } catch (e) { setMsg('读设置失败：' + e.message) }
      }, [])

      const reloadJobs = React.useCallback(async () => {
        try {
          const d = await jget('/jobs')
          if (Array.isArray(d.jobs)) setJobs(d.jobs)
          if (d.engine) setEngine(d.engine)
          if (typeof d.running === 'number') setRunning(d.running)
        } catch (e) { /* 轮询失败不刷屏，下一轮再说 */ }
      }, [])

      React.useEffect(() => {
        reloadSettings()
        reloadJobs()
        runDoctor()
      }, [reloadSettings, reloadJobs, runDoctor])

      // 任务表 5 秒轮询；组件卸载时清定时器
      React.useEffect(() => {
        const timer = setInterval(() => { reloadJobs() }, 5000)
        return () => { clearInterval(timer) }
      }, [reloadJobs])

      const patch = (p) => setSt(Object.assign({}, st || {}, p))

      const save = async () => {
        if (!st) return
        setBusy('save')
        setMsg('')
        try {
          const d = await jpost('/settings', st)
          if (d.ok) {
            setSt(d.settings || st)
            setMsg('设置已保存' + (d.ignored && d.ignored.length ? '（忽略未知键：' + d.ignored.join('、') + '）' : ''))
            await reloadSettings()
            await runDoctor()
          } else {
            setMsg('保存失败：' + (d.error || '') + ' ' + (d.message || ''))
          }
        } catch (e) {
          setMsg('保存失败：' + e.message)
        } finally { setBusy('') }
      }

      const act = async (jobId, kind, extra) => {
        setBusy(kind + ':' + jobId)
        setMsg('')
        try {
          const d = await jpost('/jobs/' + kind, Object.assign({ jobId: jobId }, extra || {}))
          if (d.ok) {
            let extra = ''
            if (kind === 'export' && d.output) extra = ' → ' + d.output
            if (kind === 'run' && d.pid) extra = '（pid ' + d.pid + '）'
            setMsg(kind + ' 成功：' + jobId + extra)
          } else {
            setMsg((d.error || '失败') + '：' + (d.message || ''))
          }
          await reloadJobs()
        } catch (e) {
          setMsg(kind + ' 失败：' + e.message)
        } finally { setBusy('') }
      }

      const del = async (job) => {
        const reclaim = '默认是软删除：整个工作目录会移到 _deleted/ 下（之后可以手动移回 jobs/ 找回）；已经导出到桌面的成品不会被删。'
        const ok = window.confirm('删除任务「' + job.jobId + '」？\n\n' + reclaim + '\n\n继续吗？')
        if (!ok) return
        setBusy('delete:' + job.jobId)
        setMsg('')
        try {
          const d = await jpost('/jobs/delete', { jobId: job.jobId })
          if (d.ok) {
            setMsg('已删除：' + job.jobId + (d.recycledTo ? '（已移到 _deleted，可找回）' : '（已物理删除）'))
          } else {
            setMsg((d.error || '删除失败') + '：' + (d.message || ''))
          }
          await reloadJobs()
        } catch (e) {
          setMsg('删除失败：' + e.message)
        } finally { setBusy('') }
      }

      const submit = async () => {
        setBusy('create')
        setMsg('')
        const refLines = String(form.refs || '')
          .split(/\r?\n/)
          .map((s) => s.trim())
          .filter(Boolean)
        const picked = (form.sources || []).slice()
        if (!picked.length) {
          setBusy('')
          setMsg('至少选 1 个素材来源：本地目录 / 本机客户端 / B站搜索（可多选）')
          return
        }
        const body = {
          singer: form.singer,
          target: form.target,
          steps: form.steps,
          sources: picked,
          refs: refLines,
        }
        if (picked.includes('folder')) body.folder = form.folder
        if (picked.includes('local')) {
          body.clients = (form.clients && form.clients.length ? form.clients : ALL_CLIENTS).slice()
          if (String(form.keyword || '').trim()) body.keyword = String(form.keyword).trim()
        }
        body.voice = form.voice || 'auto'
        body.refSearch = !!form.refSearch
        // B站搜索一直需要确认；只用本地来源时，只有要联网搜好球曲才需要
        if (picked.includes('auto') || form.refSearch) body.copyrightAck = !!form.copyrightAck
        try {
          const d = await jpost('/jobs/create', body)
          if (d.needsConfirm) {
            const lines = (d.warnings || []).map((w) => '· ' + w)
            setMsg('需要二次确认，本次没有建 job：\n' + lines.join('\n') + '\n' + (d.next || ''))
          } else if (d.ok) {
            // 预筛预览里若有逐首声部判断，直接列出来（看出为什么被排除）
            let extra = ''
            // 歌手名归一提示
            if (d.resolvedSinger && d.singer && d.singerInput && d.singer !== d.singerInput) {
              extra += '\n歌手名已归一：' + d.singerInput + ' → ' + d.singer +
                '（' + (d.resolvedSinger.matchedBy || '') + '，置信度 ' + (d.resolvedSinger.confidence != null ? d.resolvedSinger.confidence : '?') + '）'
            }
            // 每个来源各贡献几首 + 总候选数
            const contribs = d.preview && Array.isArray(d.preview.contributions) ? d.preview.contributions : []
            if (contribs.length) {
              const parts = contribs.map((c) => (c.label || c.source) + '：' +
                (typeof c.count === 'number' ? c.count + ' 首' : '等 fetch') +
                (c.available === false ? '（' + (c.note || '未跑') + '）' : ''))
              extra += '\n素材来源：' + (d.sources || []).map((s) => ({ folder: '本地目录', local: '本机客户端', auto: 'B站搜索' }[s] || s)).join(' + ') +
                '\n各来源候选：' + parts.join('；') + '；合计 ' + (d.preview.totalCandidates || 0) + ' 首'
            }
            const vg = d.preview && d.preview.voiceGuess
            if (vg && Array.isArray(vg.songs) && vg.songs.length) {
              const rows = vg.songs.slice(0, 8).map((s) =>
                '· ' + s.name + '：' + (s.voice || '未判断') + (s.kept ? '（保留）' : '（排除' + (s.reason ? '：' + s.reason : '') + '）'))
              extra = '\n期望声部：' + (vg.expected || d.voice || 'auto') + (vg.target ? '；目标曲判断：' + vg.target : '') + '\n' + rows.join('\n')
            } else if (d.preview && d.preview.reason === 'stems-pending') {
              extra = '\n素材预览要等 fetch/separate 跑完；届时 select/report.json 里能看到每首的声部判断。'
            }
            setMsg('已建 job：' + d.jobId + '（' + (d.eta || '') + '）' + extra)
            setForm(Object.assign({}, form, { singer: '', target: '' }))
            await reloadJobs()
          } else {
            setMsg((d.error || '创建失败') + '：' + (d.message || ''))
          }
        } catch (e) {
          setMsg('创建失败：' + e.message)
        } finally { setBusy('') }
      }

      const health = doctor && doctor.health ? doctor.health : 'unknown'
      const healthColor = health === 'green' ? '#34D399' : health === 'yellow' ? '#F5A524' : health === 'red' ? '#F87171' : '#64748B'
      const healthText = health === 'green' ? '依赖齐全' : health === 'yellow' ? '缺可选依赖' : health === 'red' ? '缺必需依赖' : (doctor && doctor.ok === false ? '体检没跑成' : '体检中…')
      // 体检红 → 不让建 job（服务端 POST /jobs/create 也会用同一条规则拦一次）
      const depsBlocked = !!(doctor && doctor.ok !== false && doctor.health === 'red')

      const healthBar = h('div', null,
        h('div', { style: S.bar },
          h('span', {
            style: { cursor: 'pointer', userSelect: 'none' },
            title: '点击看缺哪项、怎么装',
            onClick: () => setHealthOpen(!healthOpen),
          },
            h('span', { style: S.dot(healthColor) }),
            '依赖体检：' + healthText + (healthOpen ? ' ▴' : ' ▾'),
          ),
          health === 'red' && doctor.missingRequired && doctor.missingRequired.length
            ? h('span', { style: { color: '#F87171' } }, '缺：' + doctor.missingRequired.join('、'))
            : null,
          h('span', { style: { color: '#2A3A52' } }, '|'),
          h('span', null, h('span', { style: S.dot(running > 0 ? '#FBBF24' : '#64748B') }), running > 0 ? running + ' 个任务在跑' : '当前没有任务在跑'),
          h('button', { style: S.btnGhost, disabled: doctorBusy, onClick: runDoctor }, doctorBusy ? '体检中…' : '重新体检'),
        ),
        healthOpen
          ? h('div', { style: S.healthBody },
            !doctor
              ? h('div', { style: S.muted }, doctorBusy ? '正在体检…' : '还没有体检结果，点「重新体检」。')
              : doctor.ok === false
                ? h('div', null,
                  h('div', { style: { color: '#F5A524', fontWeight: 600 } }, '体检没跑成：' + (doctor.message || doctor.error || '未知原因')),
                  h('div', { style: S.mono }, '装法见 ' + (doctor.install || 'docs/INSTALL.md') + '；装完点「重新体检」。'),
                )
                : h('div', null,
                  (doctor.checks || []).map((c, i) => h('div', { key: i, style: S.checkRow },
                    h('span', { style: { color: c.status === 'ok' ? '#34D399' : c.status === 'warn' ? '#F5A524' : '#F87171' } },
                      c.status === 'ok' ? '✓' : c.status === 'warn' ? '!' : '✗'),
                    h('span', null, c.label + (c.required ? '' : '（可选）') + (c.detail ? '：' + c.detail : '')),
                  )),
                  (doctor.hints && doctor.hints.length)
                    ? h('div', { style: { marginTop: 8, color: '#F5A524' } }, '怎么装：')
                    : null,
                  (doctor.hints || []).map((t, i) => h('div', { key: 'h' + i, style: S.mono }, '· ' + t)),
                  // 按显卡推荐的 torch + 一行可复制的安装命令
                  doctor.suggestedTorch
                    ? h('div', { style: { marginTop: 10, borderTop: '1px solid #24344A', paddingTop: 8 } },
                      h('div', { style: { color: '#F5A524', fontWeight: 600 } },
                        '你的显卡：' + (doctor.suggestedTorch.gpu || '未知') +
                        (doctor.suggestedTorch.capability ? '（' + doctor.suggestedTorch.capability + '）' : '') +
                        ' → 推荐 torch ' + (doctor.suggestedTorch.cuda || doctor.suggestedTorch.torch || '见下') +
                        (doctor.suggestedTorch.cuda && doctor.suggestedTorch.torch ? '（' + doctor.suggestedTorch.torch + '）' : '')),
                      doctor.suggestedTorch.reason
                        ? h('div', { style: S.hint }, doctor.suggestedTorch.reason)
                        : null,
                      doctor.torchInstallCommand
                        ? h('div', { style: S.mono }, doctor.torchInstallCommand)
                        : null,
                      doctor.torchInstallCommand
                        ? h('button', {
                          style: Object.assign({}, S.btnGhost, { marginTop: 6 }),
                          onClick: async () => {
                            const r = await copyText(doctor.torchInstallCommand)
                            setMsg(r.ok ? '安装命令已复制到剪贴板' : '复制失败，请手动选中上面的命令：' + r.text)
                          },
                        }, '复制安装命令')
                        : null,
                      h('div', { style: { marginTop: 6, color: '#F5A524' } }, '约 2.5–3G；装错版本会跑不了（50 系尤其）。'),
                    )
                    : h('div', { style: { marginTop: 8, color: '#64748B' } },
                      '引擎没给 torch 推荐（版本较旧）：按 ' + (doctor.install || 'docs/INSTALL.md') + ' 的显卡矩阵自己选 cu 版本。'),
                  h('div', { style: { marginTop: 8, color: '#64748B' } },
                    '完整安装路径（全自动 bootstrap / 半自动 / 已有环境指过去）见 ' + (doctor.install || 'docs/INSTALL.md') + '。'),
                ),
          )
          : null,
      )

      const settingsCard = h('div', { style: S.card },
        h('div', { style: S.title }, h('span', null, '设置')),
        st ? h('div', null,
          h('div', { style: S.grid },
            h('div', { style: S.field },
              h('label', { style: S.label }, '引擎目录 engineDir'),
              h('input', { style: S.input, value: st.engineDir || '', onChange: (e) => patch({ engineDir: e.target.value }) }),
              h('div', { style: S.hint }, '留空 = 插件自带 engine/ 目录'),
            ),
            h('div', { style: S.field },
              h('label', { style: S.label }, 'Python 解释器 python'),
              h('input', { style: S.input, value: st.python || '', onChange: (e) => patch({ python: e.target.value }) }),
              h('div', { style: S.hint }, '留空 = 环境变量 DSH_PYTHON → PATH 里的 python'),
            ),
            h('div', { style: S.field },
              h('label', { style: S.label }, '成品输出目录 outputDir'),
              h('input', { style: S.input, value: st.outputDir || '', onChange: (e) => patch({ outputDir: e.target.value }) }),
              h('div', { style: S.hint }, '默认 桌面/翻唱工厂；成品 mp3 与报告都放这里'),
            ),
            h('div', { style: S.field },
              h('label', { style: S.label }, '默认训练步数 defaultSteps'),
              h('select', { style: S.input, value: st.defaultSteps || 9000, onChange: (e) => patch({ defaultSteps: Number(e.target.value) }) },
                STEPS.map((n) => h('option', { key: n, value: n }, String(n) + '（' + ETA[n] + '）')),
              ),
              h('div', { style: S.hint }, ETA_HINT),
            ),
          ),
          h('label', { style: S.check },
            h('input', { type: 'checkbox', checked: !!st.allowDuet, onChange: (e) => patch({ allowDuet: e.target.checked }) }),
            h('span', null, '允许对唱/合唱素材过预筛（allowDuet）——默认排除双声部混杂（低/高音区各占 20% 以上且中位比 >1.5）；与目标声部不符的素材也会被排除'),
          ),
          h('label', { style: S.check },
            h('input', { type: 'checkbox', checked: !!st.autoDownload, onChange: (e) => patch({ autoDownload: e.target.checked }) }),
            h('span', null, '允许抓取模式的旧开关（autoDownload）——v0.2 起抓取必须每次带 copyrightAck，这里仅作记录'),
          ),
          h('div', { style: { display: 'flex', gap: 10, alignItems: 'center', marginTop: 12 } },
            h('button', { style: Object.assign({}, S.btn, busy === 'save' ? { opacity: 0.6 } : {}), disabled: busy === 'save', onClick: save }, busy === 'save' ? '保存中…' : '保存设置'),
            engine ? h('span', { style: S.muted }, engine.ready ? '引擎：' + ellipsis(engine.engineDir || '') : '引擎未就绪') : null,
          ),
        ) : h('div', { style: S.muted }, '读取设置中…'),
      )

      const jobsCard = h('div', { style: S.card },
        h('div', { style: S.title },
          h('span', null, '任务（' + jobs.length + '）'),
          h('button', { style: S.btnGhost, onClick: reloadJobs }, '刷新'),
        ),
        jobs.length === 0
          ? h('div', { style: S.muted }, '还没有任务：在下面新建一个，或在对话里用 cover_setup。')
          : h('div', null,
            h('div', { style: S.head },
              h('span', null, '歌手'), h('span', null, '目标曲'), h('span', null, '状态'),
              h('span', null, '进度'), h('span', null, '当前阶段'), h('span', null, 'mp3'), h('span', null, '操作'),
            ),
            jobs.map((j) => h('div', { key: j.jobId, style: S.row },
              h('span', { style: S.cell, title: j.jobId }, j.singer || '—'),
              h('span', { style: S.cell, title: j.target || '' }, j.target ? ellipsis(j.target) : '—'),
              h('span', null, h('span', { style: { color: statusColor(j) } }, statusText(j))),
              h('span', null, (j.done == null ? '—' : String(j.done)) + '·' + (j.total == null ? '?' : String(j.total))),
              h('span', { style: S.cell }, j.currentStage || '—'),
              h('span', { style: { color: j.hasMp3 ? '#34D399' : '#64748B' } }, j.hasMp3 ? '有' : '无'),
              h('div', { style: S.actions },
                h('button', {
                  style: S.btnGhost,
                  disabled: !!busy || j.running,
                  onClick: () => act(j.jobId, 'run'),
                }, busy === 'run:' + j.jobId ? '启动中…' : '运行'),
                h('button', {
                  style: S.btnGhost,
                  disabled: !!busy || j.running,
                  title: '从已完成的阶段继续（resume=true，不重做 separate/select）',
                  onClick: () => act(j.jobId, 'run', { resume: true }),
                }, busy === 'run:' + j.jobId ? '续跑中…' : '续跑'),
                h('button', {
                  style: S.btnDanger,
                  disabled: !!busy || !j.running,
                  onClick: () => act(j.jobId, 'cancel'),
                }, busy === 'cancel:' + j.jobId ? '取消中…' : '取消'),
                h('button', {
                  style: S.btnGhost,
                  disabled: !!busy,
                  onClick: () => act(j.jobId, 'export'),
                }, busy === 'export:' + j.jobId ? '导出中…' : '导出'),
                h('button', {
                  style: S.btnDanger,
                  disabled: !!busy,
                  title: '默认软删除：移到 _deleted/，可以找回',
                  onClick: () => del(j),
                }, busy === 'delete:' + j.jobId ? '删除中…' : '删除'),
              ),
            )),
          ),
      )

      const createCard = h('div', { style: S.card },
        h('div', { style: S.title }, h('span', null, '新建任务')),
        h('div', { style: S.grid },
          h('div', { style: S.field },
            h('label', { style: S.label }, '歌手名（必填）'),
            h('input', { style: S.input, value: form.singer, onChange: (e) => setForm(Object.assign({}, form, { singer: e.target.value })) }),
            h('div', { style: S.hint }, '例：刘德华；决定声库名与成品文件名'),
          ),
          h('div', { style: { flex: '1 1 100%' } },
            h('label', { style: S.label }, '素材来源（多选，至少 1 个）'),
            h('div', { style: { display: 'flex', flexWrap: 'wrap', gap: 16, marginTop: 4 } },
              SOURCES.map(([id, label]) => h('label', { key: id, style: { display: 'flex', alignItems: 'flex-start', gap: 6, cursor: 'pointer' } },
                h('input', {
                  type: 'checkbox',
                  checked: (form.sources || []).includes(id),
                  onChange: (e) => {
                    const cur = (form.sources || []).slice()
                    const next = e.target.checked ? cur.concat([id]) : cur.filter((x) => x !== id)
                    // 去掉 B站搜索时顺手清掉版权确认，避免残留
                    const patch = { sources: next }
                    if (!next.includes('auto') && !form.refSearch) patch.copyrightAck = false
                    setForm(Object.assign({}, form, patch))
                  },
                }),
                h('span', { style: { display: 'flex', flexDirection: 'column' } },
                  h('span', { style: { color: '#E5E7EB', fontSize: 13 } }, label),
                  h('span', { style: { color: '#64748B', fontSize: 11 } }, SOURCE_HINT[id]),
                ),
              )),
            ),
            h('div', { style: S.hint }, '可以同时用多个来源（例如 本地目录 + 本机客户端）；至少选 1 个，否则服务端返回 BAD_SOURCES。'),
          ),
          (form.sources || []).includes('local')
            ? h('div', { style: { flex: '1 1 100%' } },
              h('label', { style: S.label }, '扫描哪些本机客户端（默认全选）'),
              h('div', { style: { display: 'flex', flexWrap: 'wrap', gap: 14, marginTop: 4 } },
                CLIENTS.map(([id, label]) => h('label', { key: id, style: { display: 'flex', alignItems: 'center', gap: 6, color: '#94A3B8', fontSize: 12, cursor: 'pointer' } },
                  h('input', {
                    type: 'checkbox',
                    checked: (form.clients || []).includes(id),
                    onChange: (e) => {
                      const cur = (form.clients || []).slice()
                      const next = e.target.checked ? cur.concat([id]) : cur.filter((x) => x !== id)
                      setForm(Object.assign({}, form, { clients: next }))
                    },
                  }),
                  label,
                )),
              ),
            )
            : null,
          (form.sources || []).includes('local')
            ? h('div', { style: S.field },
              h('label', { style: S.label }, '关键词（默认=歌手名）'),
              h('input', { style: S.input, value: form.keyword, onChange: (e) => setForm(Object.assign({}, form, { keyword: e.target.value })) }),
              h('div', { style: S.hint }, '留空就用歌手名；按关键词匹配这些客户端里已下载的歌曲名 / 文件名。'),
            )
            : null,
          h('div', { style: S.field },
            h('label', { style: S.label }, '目标歌手声部'),
            h('select', {
              style: S.input,
              value: form.voice || 'auto',
              onChange: (e) => setForm(Object.assign({}, form, { voice: e.target.value })),
            },
              h('option', { value: 'auto' }, '自动（按目标曲判断）'),
              h('option', { value: 'female' }, '女声'),
              h('option', { value: 'male' }, '男声'),
            ),
            h('div', { style: S.hint }, '自动按目标曲人声判断；若素材被大量排除，请手动指定（男歌手建议直接选男声）。'),
          ),
          (form.sources || []).includes('folder')
            ? h('div', { style: S.field },
              h('label', { style: S.label }, '素材目录（只读）'),
              h('input', { style: S.input, value: form.folder, onChange: (e) => setForm(Object.assign({}, form, { folder: e.target.value })) }),
              h('div', { style: S.hint }, '例：D:/素材/某某歌手；插件只读，不删不改'),
            )
            : null,
          h('div', { style: S.field },
            h('label', { style: S.label }, '目标曲路径'),
            h('input', { style: S.input, value: form.target, onChange: (e) => setForm(Object.assign({}, form, { target: e.target.value })) }),
            h('div', { style: S.hint }, '例：D:/素材/目标曲.wav；不填只能训到 train，出不了成品'),
          ),
          h('div', { style: S.field },
            h('label', { style: S.label }, '训练步数'),
            h('select', { style: S.input, value: form.steps, onChange: (e) => setForm(Object.assign({}, form, { steps: Number(e.target.value) })) },
              STEPS.map((n) => h('option', { key: n, value: n }, String(n) + '（' + ETA[n] + '）')),
            ),
            h('div', { style: S.hint }, ETA_HINT),
          ),
          h('div', { style: { flex: '1 1 100%' } },
            h('label', { style: S.label }, '好球曲（选填）'),
            h('textarea', {
              style: S.textarea,
              value: form.refs,
              onChange: (e) => setForm(Object.assign({}, form, { refs: e.target.value })),
            }),
            h('div', { style: S.hint },
              '好球曲 = 最能代表你想听的那种音色的几首（建议 2–5 首），选填；留空则用素材目录里自动筛。',
              h('br'),
              '可以填歌名：言っちゃいけないことばっか浮かぶよな',
              h('br'),
              '也可以填完整路径：D:/.../xxx.wav（一行一个，歌名路径可混填；最多 20 条）',
              h('br'),
              '填歌名时会在素材目录里自动找；找不到会忽略并提示，不会中断。写了路径就必须存在。',
            ),
            h('label', { style: S.check },
              h('input', {
                type: 'checkbox',
                checked: !!form.refSearch,
                onChange: (e) => setForm(Object.assign({}, form, { refSearch: e.target.checked, copyrightAck: e.target.checked ? form.copyrightAck : false })),
              }),
              h('span', null, '好球曲找不到时联网搜索（需要版权确认）'),
            ),
            form.refSearch
              ? h('div', { style: S.hint },
                '本地素材目录里找不到的歌名，会去 B 站搜并下载；仍然失败就忽略（不会中断）。',
                !(form.sources || []).includes('auto') ? h('span', { style: { color: '#F5A524' } }, ' 需要同时确认抓取风险：勾选下面的版权确认。') : null,
              )
              : h('div', { style: S.hint }, '默认只在你给的素材目录里找；勾选后才会联网补。'),
          ),
        ),
        (form.sources || []).includes('local')
          ? h('div', { style: S.hint },
            '会自动扫描常见下载目录；ncm 自动解密；qmc / kgm 等加密格式需手动转换；只读取不修改原文件，不需要账号、不需要登录态。',
          )
          : null,
        ((form.sources || []).includes('auto') || form.refSearch)
          ? h('label', { style: S.check },
            h('input', { type: 'checkbox', checked: !!form.copyrightAck, onChange: (e) => setForm(Object.assign({}, form, { copyrightAck: e.target.checked })) }),
            h('span', null, ((form.sources || []).includes('auto')
              ? '我确认：B站搜索会联网搜索并下载音频'
              : '我确认：好球曲会联网搜索并下载音频') + '，可能触发平台风控，版权风险自负。不勾选只会得到风险提示，不会建 job、不会下载。'),
          )
          : h('div', { style: S.muted }, '素材来自你自己的文件夹 / 本机客户端已下载文件：插件只读不删，不会联网。'),
        h('div', { style: S.risk },
          h('button', {
            style: S.riskToggle,
            'aria-expanded': riskOpen ? 'true' : 'false',
            onClick: () => setRiskOpen(!riskOpen),
          }, (riskOpen ? '▾ ' : '▸ ') + '训练期间会发生什么（风险提示）'),
          riskOpen
            ? h('div', { style: S.riskBody }, RISK_LINES.map((line, i) => h('div', { key: i, style: S.riskLine }, '· ' + line)))
            : null,
        ),
        depsBlocked
          ? h('div', { style: S.blockNotice },
            h('div', { style: { color: '#F87171', fontWeight: 600, marginBottom: 4 } }, '依赖没齐，先别建 job（建了也跑不起来）'),
            h('div', null, '缺必需：' + ((doctor.missingRequired && doctor.missingRequired.length) ? doctor.missingRequired.join('、') : (doctor.missing || []).join('、') || '未知')),
            (doctor.hints || []).slice(0, 4).map((t, i) => h('div', { key: i, style: S.mono }, '· ' + t)),
            h('div', { style: { marginTop: 6 } }, '按 docs/INSTALL.md 装好后，点顶部「重新体检」；变绿这里就能建了。'),
          )
          : null,
        h('div', { style: { display: 'flex', gap: 10, alignItems: 'center', marginTop: 12 } },
          h('button', {
            style: Object.assign({}, S.btn, (busy === 'create' || depsBlocked) ? { opacity: 0.5, cursor: 'not-allowed' } : {}),
            disabled: busy === 'create' || depsBlocked,
            title: depsBlocked ? '依赖体检是红的：先装依赖' : '',
            onClick: submit,
          }, busy === 'create' ? '提交中…' : (depsBlocked ? '依赖没齐，不能建' : '新建任务')),
          h('span', { style: S.muted }, depsBlocked ? '装完点顶部「重新体检」。' : '建完不会自动开跑：在任务表点「运行」。'),
        ),
      )

      return h('div', { style: S.wrap },
        healthBar,
        msg ? h('div', { style: S.msg }, msg) : null,
        settingsCard,
        jobsCard,
        createCard,
      )
    }

    return {
      inject: ['slots'],
      apply(ctx) {
        const slots = ctx.get('slots')
        if (!slots) return
        slots.inject('settings.section', () => slots.register(
          { name: 'settings.section', id: 'cover-factory-panel', order: 42, label: '翻唱工厂' },
          () => h(Panel),
        ))
      },
    }
  },
})
