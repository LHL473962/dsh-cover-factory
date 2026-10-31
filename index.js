// dsh-cover-factory —— 翻唱工厂：歌手 / 好球曲 / 目标曲 → 桌面成品 mp3 + 质检报告
//
// ── 工具契约（照抄 dsh-meme-lookup 的约定）────────────────────────────────
// - 所有工具**不抛异常**：失败返回 { ok: false, error: 'CODE', message }。
// - ok:false 只表示“这次没办成”（缺参数、引擎没就绪、job 不存在），不是插件 bug。
// - message 一律过 redactPaths()，不把本机绝对路径回传给对话。
// - 结构化返回字段里可以带路径（用户要能找到产物），脱敏只针对 message。
// - 引擎缺失/未就绪时全部降级为 ok:false + code，绝不让插件崩。
//
// 引擎契约（engine/run.py，由引擎侧维护，本插件只读不写）：
//   init   --config <job_config.json> --workdir <dir>
//   run    --job <job.json> [--from stage] [--to stage] [--resume] [--allow-duet]
//   status --job <job.json>
//   cancel --job <job.json>
// 退出码：0 成功 / 2 需要用户决策 / 1 失败。job.json 是唯一状态源。

import { registerPanel } from './lib/panel.js'
import { registerTools } from './lib/tools.js'
import { redactPaths } from './lib/util.js'

// 版本常量：tests/smoke.mjs 会核对它与 package.json 一致
export const VERSION = '0.1.11'

export const name = 'cover-factory'
export const inject = ['tools', 'webServer']

// 只做装配：工具注册 + 设置页路由。逻辑在 lib/ 下。
export function apply(ctx) {
  try {
    registerTools(ctx)
  } catch (e) {
    // 装不上工具也不能把 DSH 带崩：记一条脱敏日志就继续。
    try { console.error('[cover-factory] registerTools failed: ' + redactPaths(e && e.message)) } catch {}
  }
  try {
    registerPanel(ctx)
  } catch (e) {
    try { console.error('[cover-factory] registerPanel failed: ' + redactPaths(e && e.message)) } catch {}
  }
}
