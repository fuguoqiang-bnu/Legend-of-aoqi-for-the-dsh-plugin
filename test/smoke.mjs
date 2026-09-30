/**
 * dsh-aoqi-pet 冒烟测试。
 *
 * 分两层：
 *   A. 纯逻辑：自动续写的三重限流、配置合并、宠物名归一化、文件桥读写。
 *   B. 接线：把 lib/ 复制到临时目录、用桩包顶掉 @deepseek-ai/* 依赖，
 *      再用一个假 ctx 把 lib/index.js 真正 apply 起来，喂会话事件，
 *      断言「截断 → 续写」「完成 → 不续写」「连击上限 → 停手」等行为。
 *
 * 只用 Node 内置模块，`node test/smoke.mjs` 即可跑；退出码非 0 表示失败。
 */
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')

let passed = 0
const failures = []
function check(label, condition, detail) {
  if (condition) {
    passed += 1
    console.log(`  ✓ ${label}`)
  } else {
    failures.push(label)
    console.log(`  ✗ ${label}${detail === undefined ? '' : ` — ${detail}`}`)
  }
}
function section(title) {
  console.log(`\n${title}`)
}
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

// ─────────────────────────────────────────────────────────────────────────────
// A. 纯逻辑
// ─────────────────────────────────────────────────────────────────────────────
section('A. 纯逻辑')
const ac = await import(pathToFileURL(join(ROOT, 'lib', 'auto-continue.js')).href)
const bridgeModule = await import(pathToFileURL(join(ROOT, 'lib', 'bridge.js')).href)

const baseConfig = {
  enabled: true,
  continueText: '继续输出',
  stallText: '接着写',
  graceMs: 3000,
  cooldownMs: 8000,
  maxConsecutive: 3,
  detectStalled: true
}

{
  const state = ac.createBurstState()
  const decision = ac.decideContinuation({ state, config: baseConfig, reasonKind: 'max-tokens', hasVisibleOutput: true, now: 1000 })
  check('截断 → 决定续写', decision.action === 'continue' && decision.text === '继续输出', JSON.stringify(decision))
  check('宽限期 = graceMs', decision.delayMs === 3000, String(decision.delayMs))
}

{
  const state = ac.createBurstState()
  const decision = ac.decideContinuation({ state, config: baseConfig, reasonKind: 'completed', hasVisibleOutput: true, now: 1000 })
  check('正常完成且有输出 → 不续写', decision.action === 'skip' && decision.code === 'reason:completed', JSON.stringify(decision))
}

{
  const state = ac.createBurstState()
  const decision = ac.decideContinuation({ state, config: baseConfig, reasonKind: 'completed', hasVisibleOutput: false, now: 1000 })
  check('完成但无可见输出 → 判定空转并续写', decision.action === 'continue' && decision.cause === 'stalled', JSON.stringify(decision))
}

{
  const state = ac.createBurstState()
  state.consecutive = 3
  const decision = ac.decideContinuation({ state, config: baseConfig, reasonKind: 'max-tokens', hasVisibleOutput: true, now: 1000 })
  check('连击到上限 → 停手并封顶', decision.action === 'skip' && decision.code === 'burst-limit' && state.capped === true, JSON.stringify(decision))
}

{
  const state = ac.createBurstState()
  state.lastSentAt = 1000
  const decision = ac.decideContinuation({ state, config: baseConfig, reasonKind: 'max-tokens', hasVisibleOutput: true, now: 2000 })
  check('冷却期内 → 跳过', decision.action === 'skip' && decision.code === 'cooldown', JSON.stringify(decision))
}

{
  const state = ac.createBurstState()
  state.consecutive = 2
  const decision = ac.confirmContinuation({ state, config: baseConfig, now: 9000, agentAlive: true, agentRunning: false, lastUserAt: 0, cause: 'truncated' })
  check('宽限期后确认续写', decision.action === 'continue' && decision.text === '继续输出', JSON.stringify(decision))
}

{
  const state = ac.createBurstState()
  const gone = ac.confirmContinuation({ state, config: baseConfig, now: 9000, agentAlive: false, agentRunning: false, lastUserAt: 0, cause: 'truncated' })
  const running = ac.confirmContinuation({ state, config: baseConfig, now: 9000, agentAlive: true, agentRunning: true, lastUserAt: 0, cause: 'truncated' })
  const userSpoke = ac.confirmContinuation({ state, config: baseConfig, now: 9000, agentAlive: true, agentRunning: false, lastUserAt: 8000, cause: 'truncated' })
  check('agent 没了 → 放弃', gone.action === 'skip' && gone.code === 'agent-gone', JSON.stringify(gone))
  check('宿主已自行开跑 → 放弃', running.action === 'skip' && running.code === 'already-running', JSON.stringify(running))
  check('用户刚说过话 → 放弃', userSpoke.action === 'skip' && userSpoke.code === 'user-spoke', JSON.stringify(userSpoke))
}

{
  const state = ac.createBurstState()
  state.consecutive = 2
  state.capped = true
  ac.noteProgress(state)
  check('回合正常完成会清零连击与封顶', state.consecutive === 0 && state.capped === false, JSON.stringify(state))
}

{
  const dir = mkdtempSync(join(tmpdir(), 'aoqi-bridge-'))
  const bridge = bridgeModule.createBridge({ dir })
  const first = bridge.writeState({ version: 1, animation: 'idle' })
  const second = bridge.writeState({ version: 1, animation: 'idle' })
  const third = bridge.writeState({ version: 1, animation: 'working' })
  check('状态写入：内容不变则不重写', first === true && second === false && third === true, `${first}/${second}/${third}`)
  check('状态文件可被重新解析', JSON.parse(readFileSync(bridge.paths.state, 'utf8')).animation === 'working')
  writeFileSync(bridge.paths.settings, JSON.stringify({ pet: 'huo', muted: true }))
  check('读桌宠设置', bridge.readSettings()?.pet === 'huo')
  writeFileSync(bridge.paths.command, '{ 这不是 JSON')
  check('读坏掉的指令文件返回 null 而不是抛错', bridge.readCommand() === null)
  writeFileSync(bridge.paths.command, JSON.stringify({ seq: 2, action: 'poke' }))
  check('读指令带 mtime', bridge.readCommand()?.action === 'poke' && bridge.readCommand().mtimeMs > 0)
  rmSync(dir, { recursive: true, force: true })
}

// ─────────────────────────────────────────────────────────────────────────────
// B. 接线：假 ctx，真正 apply 一遍
//    （直接 import 仓库里的 lib/index.js —— 这一步同时证明插件是零依赖的：
//     没有 node_modules、没有 @deepseek-ai/* 裸导入也能加载）
// ─────────────────────────────────────────────────────────────────────────────
section('B. 接线（假 ctx 端到端）')

const sandbox = mkdtempSync(join(tmpdir(), 'aoqi-wiring-'))
const source = readFileSync(join(ROOT, 'lib', 'index.js'), 'utf8')
check('宿主入口不 import @deepseek-ai/*（保证放哪儿都能加载）', !/^\s*import[^\n]*['"]@deepseek-ai\//m.test(source), '发现裸导入')

const plugin = await import(pathToFileURL(join(ROOT, 'lib', 'index.js')).href)

// 假 ctx
const listeners = new Map()
const registeredTools = new Map()
const effects = []
const agents = new Map()
let webRoutes = 0
let capturedRoute = null
const ctx = {
  logger: { info: () => {} },
  on(type, handler) {
    const list = listeners.get(type) ?? []
    list.push(handler)
    listeners.set(type, list)
  },
  effect(fn) {
    const disposer = fn()
    effects.push(disposer)
    return () => {}
  },
  tools: { register: (definition) => { registeredTools.set(definition.name, definition); return () => {} } },
  agents: { list: () => [...agents.values()], get: (id) => agents.get(id) ?? undefined },
  get: (serviceName) => (serviceName === 'webServer' ? { register: (route) => { webRoutes += 1; capturedRoute = route; return () => {} } } : undefined)
}
function emit(type, ...args) {
  for (const handler of listeners.get(type) ?? []) handler(...args)
}

const sent = []
const fakeAgent = { id: 'session-1', status: 'idle', session: { id: 'session-1' }, followup: (message) => sent.push(message) }
agents.set('session-1', fakeAgent)

check('导出了 name / inject / apply 与配置工具函数', plugin.name === 'aoqi-pet' && Array.isArray(plugin.inject) && typeof plugin.apply === 'function' && typeof plugin.normalizeConfig === 'function' && typeof plugin.normalizePetId === 'function')

{
  const merged = plugin.normalizeConfig({ petId: 'huo', autoContinue: { maxConsecutive: 7, 未知键: 1 }, companion: { scale: 2 } })
  check('配置深合并保留未覆盖的默认值', merged.autoContinue.maxConsecutive === 7 && merged.autoContinue.graceMs === 3000 && merged.companion.scale === 2 && merged.companion.balloonTips === true, JSON.stringify(merged.autoContinue))
  check('类型不符的配置退回默认值', plugin.normalizeConfig({ autoContinue: { graceMs: '很快' } }).autoContinue.graceMs === 3000)
  check('宠物别名归一化', plugin.normalizePetId('冰', 'shui') === 'shui' && plugin.normalizePetId('HUO', 'shui') === 'huo' && plugin.normalizePetId('不存在', 'shui') === 'shui')
}

const homeDir = join(sandbox, 'dsh-home')
process.env.DSH_HOME = homeDir
plugin.apply(ctx, {
  petId: 'huo',
  companion: { autoLaunch: false, balloonTips: false },
  autoContinue: { enabled: true, graceMs: 20, cooldownMs: 0, maxConsecutive: 2 }
})

check('注册了三个宠物工具', registeredTools.size === 3 && registeredTools.has('aoqi_pet_say') && registeredTools.has('aoqi_pet_status') && registeredTools.has('aoqi_pet_switch'), [...registeredTools.keys()].join(','))
check('监听 session/event 与 agent/status', listeners.has('session/event') && listeners.has('agent/status'))
check('注册了心跳 effect', effects.length >= 2, String(effects.length))
check('注册了可选 HTTP 路由', webRoutes === 1, String(webRoutes))

const statePath = join(homeDir, 'aoqi-pet', 'state.json')
const firstState = JSON.parse(readFileSync(statePath, 'utf8'))
check('初始状态落盘且宠物正确', firstState.petId === 'huo' && firstState.animation === 'idle', JSON.stringify(firstState.petId))

// 1) 截断 → 自动续写
const sessionRef = { id: 'session-1' }
emit('session/event', sessionRef, { type: 'turn/start', data: { turn: 1 } })
emit('session/event', sessionRef, { type: 'assistant/message', data: { message: { content: [{ type: 'text', text: '写了一半' }] } } })
fakeAgent.status = 'idle'
emit('session/event', sessionRef, { type: 'turn/end', data: { turn: 1, reason: { kind: 'max-tokens' } } })
await sleep(160)
check('截断后真的发出了续写消息', sent.length === 1, `sent=${sent.length}`)
check('续写消息带可达来源标记', sent[0]?.source?.kind === 'aoqi-pet/auto-continue', JSON.stringify(sent[0]?.source))
check('续写消息是 user 角色且含文本', sent[0]?.role === 'user' && sent[0]?.content?.[0]?.type === 'text', JSON.stringify(sent[0]))
check('提示语按配置下发', sent[0]?.content?.[0]?.text === '继续输出，不要重复已经生成的内容。', String(sent[0]?.content?.[0]?.text))

// 2) 连击到上限 → 不再续写
emit('session/event', sessionRef, { type: 'turn/start', data: { turn: 2 } })
emit('session/event', sessionRef, { type: 'turn/end', data: { turn: 2, reason: { kind: 'max-tokens' } } })
await sleep(160)
check('第 2 次截断仍然续写（上限 2）', sent.length === 2, `sent=${sent.length}`)
emit('session/event', sessionRef, { type: 'turn/start', data: { turn: 3 } })
emit('session/event', sessionRef, { type: 'turn/end', data: { turn: 3, reason: { kind: 'max-tokens' } } })
await sleep(160)
check('触到连击上限后不再续写', sent.length === 2, `sent=${sent.length}`)

// 3) 用户说话 → 解除封顶 + 清零
emit('session/event', sessionRef, { type: 'user/message', data: { id: 'm1' } })
const afterUser = JSON.parse(readFileSync(statePath, 'utf8'))
check('用户消息后连击清零', afterUser.stats.burst === 0, JSON.stringify(afterUser.stats))

// 4) 正常完成 → 不续写、计一次完成
const before = afterUser.stats.completions
emit('session/event', sessionRef, { type: 'turn/start', data: { turn: 4 } })
emit('session/event', sessionRef, { type: 'assistant/message', data: { message: { content: [{ type: 'text', text: '写完了' }] } } })
emit('session/event', sessionRef, { type: 'turn/end', data: { turn: 4, reason: { kind: 'completed' } } })
await sleep(120)
const afterDone = JSON.parse(readFileSync(statePath, 'utf8'))
check('正常完成不会续写', sent.length === 2, `sent=${sent.length}`)
check('完成计数 +1', afterDone.stats.completions === before + 1, `${before} → ${afterDone.stats.completions}`)
check('完成后动画切到 done', afterDone.animation === 'done', afterDone.animation)

// 5) 工具执行
const sayResult = await registeredTools.get('aoqi_pet_say').execute({ text: '我在盯着你的任务', mood: 'working' })
check('aoqi_pet_say 可用', typeof sayResult === 'string' && sayResult.includes('我在盯着你的任务'), String(sayResult))
const statusResult = await registeredTools.get('aoqi_pet_status').execute({})
check('aoqi_pet_status 返回摘要', typeof statusResult === 'string' && statusResult.includes('宠物：'), String(statusResult).split('\n')[0])
const switchResult = await registeredTools.get('aoqi_pet_switch').execute({ pet: '冰' })
check('aoqi_pet_switch 支持中文别名', typeof switchResult === 'string' && switchResult.includes('帝释天'), String(switchResult))
const aliasResult = await registeredTools.get('aoqi_pet_switch').execute({ pet: '阿修' })
check('aoqi_pet_switch 认识真名与俗称（阿修→修尔）', typeof aliasResult === 'string' && aliasResult.includes('修尔'), String(aliasResult))

// 6) 桌宠写设置/指令 → 宿主跟随
writeFileSync(join(homeDir, 'aoqi-pet', 'companion-settings.json'), JSON.stringify({ pet: 'an', muted: true, autoContinuePaused: true }))
writeFileSync(join(homeDir, 'aoqi-pet', 'command.json'), JSON.stringify({ seq: 1, action: 'poke' }))
await sleep(1250)
const afterSettings = JSON.parse(readFileSync(statePath, 'utf8'))
check('桌宠切换宠物后宿主状态跟随', afterSettings.petId === 'an', afterSettings.petId)
check('桌宠静音生效', afterSettings.muted === true, String(afterSettings.muted))
check('桌宠暂停自动续写生效', afterSettings.autoContinue.enabled === false, JSON.stringify(afterSettings.autoContinue))
check('心跳在持续刷新 updatedAt', afterSettings.updatedAt >= afterDone.updatedAt, `${afterDone.updatedAt} → ${afterSettings.updatedAt}`)

// 7) HTTP 路由：给 DSH 界面内的客户端面喂数据 + 接受点击（换宠物）
//    lib/client.js 里的输入框小宠物就是拉这个路由；这里的 POST 就是「点一下换一只」。
function callRoute(method, url) {
  const res = {
    code: 0, headers: null, body: '',
    writeHead(code, headers) { this.code = code; this.headers = headers },
    end(body) { this.body = body },
  }
  capturedRoute.handler({ method, url }, res)
  return { code: res.code, headers: res.headers, json: () => JSON.parse(res.body) }
}
check('注册了 /api/aoqi-pet 路由', webRoutes === 1 && capturedRoute !== null && capturedRoute.path === '/api/aoqi-pet')

{
  const got = callRoute('GET', '/api/aoqi-pet')
  const payload = got.json()
  check('GET 返回 200 与 no-store（客户端轮询用）', got.code === 200 && /no-store/.test(got.headers['cache-control'] || ''))
  check('GET 带 state/pets/files 三段', payload.ok === true && typeof payload.state === 'object' && Object.keys(payload.pets).length === 5 && Boolean(payload.files.state))
  check('GET 的 state 与最新心跳一致（petId=an）', payload.state.petId === 'an', payload.state.petId)

  const next = callRoute('POST', '/api/aoqi-pet?action=next-pet')
  const nextPayload = next.json()
  check('POST next-pet 返回 200 且轮换到下一只（an → mu）', next.code === 200 && nextPayload.state.petId === 'mu', nextPayload.state?.petId)
  const settingsAfterNext = JSON.parse(readFileSync(join(homeDir, 'aoqi-pet', 'companion-settings.json'), 'utf8'))
  check('POST next-pet 会把选择写进 companion-settings.json（桌宠跟着换）', settingsAfterNext.pet === 'mu', settingsAfterNext.pet)
  check('POST next-pet 会附带气泡反馈', typeof nextPayload.state.bubble?.text === 'string' && nextPayload.state.bubble.text.includes('阿瑞斯'), JSON.stringify(nextPayload.state.bubble))

  const poke = callRoute('POST', '/api/aoqi-pet?action=poke')
  check('POST poke 让桌宠冒泡「我在这儿！」', poke.code === 200 && poke.json().state.bubble.text === '我在这儿！')

  const bad = callRoute('POST', '/api/aoqi-pet?action=不存在')
  check('未知 action 返回 400（不打崩路由）', bad.code === 400 && bad.json().ok === false, String(bad.code))
}

// 清理
for (const disposer of effects) {
  try {
    if (typeof disposer === 'function') disposer()
  } catch {
    /* 忽略 */
  }
}
rmSync(sandbox, { recursive: true, force: true })

// ─────────────────────────────────────────────────────────────────────────────
console.log(`\n通过 ${passed} 项，失败 ${failures.length} 项`)
if (failures.length > 0) {
  console.log('失败清单：')
  for (const failure of failures) console.log(`  - ${failure}`)
  process.exit(1)
}
console.log('全部通过 ✅')
