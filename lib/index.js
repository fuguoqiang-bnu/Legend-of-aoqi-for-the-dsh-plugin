/**
 * 奥奇桌宠 · 小五王 —— DeepSeek Harness 宿主侧插件。
 *
 * 三件事：
 *   1. 把 DSH 的运行状态（在想、在调工具、完成、出错、等待输入）翻译成一份小的状态文件，
 *      由 DSH 之外的桌宠窗口进程渲染成动画和气泡 —— 这样即使 DSH 被最小化，宠物还在桌面上动。
 *   2. 回合结束时通知用户（桌宠跳跃 + 气泡 + 可选 Windows 气泡提醒 + 提示音）。
 *   3. 输出被 max-tokens 截断时自动接着写（宽限期 / 冷却 / 连击上限三重限流）。
 *
 * 设计原则（对齐 DSH 官方红线）：只挂已文档化的扩展点（session/event、agent/*、tools、
 * webServer），不改内核；所有注册都发生在 apply() 里、首个 await 之前；贡献全部走
 * ctx.effect / ctx.on / ctx.tools.register，插件卸载即回收。
 *
 * 为什么**一个 @deepseek-ai/* 都不 import**：
 *   bare 说明符（`import Schema from '@deepseek-ai/schemastery'`）只有插件位于 profile 的
 *   node_modules 解析链上才成立。本插件同时支持「直接拷目录到 profile / 用绝对路径挂载」
 *   这种社区最省事的安装方式，所以工具用 ctx.tools.register 的裸定义、续写消息自己按
 *   `{ id, role, content, source }` 造，配置用自带默认表深合并。结果：零依赖、零构建、
 *   放哪儿都能跑（Node 内置模块除外）。
 */
import { randomUUID } from 'node:crypto'

import { createBridge } from './bridge.js'
import {
  AUTO_CONTINUE_SOURCE,
  confirmContinuation,
  createBurstState,
  decideContinuation,
  noteProgress,
  uncap
} from './auto-continue.js'
import { createCompanionSupervisor, resolveCompanionPaths } from './pet-runtime.js'

export const name = 'aoqi-pet'
/** tools 注册三个宠物工具；agents 提供会话/回合事件源。webServer 是可选增强，用 ctx.get 拿。 */
export const inject = ['tools', 'agents']

/**
 * 出场的五只小五王——名字全部来自页游官方图鉴，不是编的。
 *
 * 同一批角色在桌宠里有**两个形态**，所以每只记两级名字：
 *   * `baby` 初始形态：桌宠默认帧素材（frames）就是这个形态的图，官方条目见 docs/NAMES.md
 *   * `name` 五王本名：官方高清素材（hires）用的是「传说王者」立绘，名字同样以本名为准
 * 传说五王 = 龙炎 / 诺亚 / 帝释天 / 修尔 / 阿瑞斯；诺雅是诺亚的妹妹，不在五王之列。
 */
export const PETS = {
  huo: { name: '龙炎', baby: '小炎', element: '火', official: '传说王者·龙炎', color: '#ff8a6b' },
  jin: { name: '诺亚', baby: '小诺', element: '超能', official: '传说王者·诺亚', color: '#ffd166' },
  shui: { name: '帝释天', baby: '小天', element: '光', official: '传说王者·帝释天', color: '#7fd4ff' },
  an: { name: '修尔', baby: '阿修', element: '恶魔', official: '传说王者·修尔', color: '#8b93e8' },
  mu: { name: '阿瑞斯', baby: '阿瑞', element: '草', official: '传说王者·阿瑞斯', color: '#7fe08a' }
}

/** 用户可能在工具参数里写的别名，统一折到 id 上（本名、初始形态名、俗称、单字、英文）。 */
const PET_ALIASES = {
  huo: 'huo', 火: 'huo', fire: 'huo', flame: 'huo', 龙炎: 'huo', 小炎: 'huo', 炎: 'huo',
  jin: 'jin', 金: 'jin', gold: 'jin', 诺亚: 'jin', 小诺: 'jin', 超能: 'jin',
  shui: 'shui', 水: 'shui', water: 'shui', ice: 'shui', 冰: 'shui', 帝释天: 'shui', 小天: 'shui', 光: 'shui',
  an: 'an', 暗: 'an', dark: 'an', shadow: 'an', 修尔: 'an', 阿修: 'an', 恶魔: 'an',
  mu: 'mu', 木: 'mu', wood: 'mu', leaf: 'mu', 阿瑞斯: 'mu', 阿瑞: 'mu', 草: 'mu'
}

/** 插件默认配置（配置文件里的 config: 会深合并到这份表上）。 */
export const DEFAULTS = {
  enabled: true,
  petId: 'shui',
  companion: {
    autoLaunch: true,
    pythonPath: '',
    scale: 1,
    opacity: 0.97,
    alwaysOnTop: true,
    sound: true,
    balloonTips: true,
    staleExitMs: 120000,
    killOnUnload: false
  },
  autoContinue: {
    enabled: true,
    continueText: '继续输出，不要重复已经生成的内容。',
    stallText: '请接着上一个回合继续，把结论补完整。',
    graceMs: 3000,
    cooldownMs: 8000,
    maxConsecutive: 3,
    detectStalled: true
  },
  notify: {
    onComplete: true,
    onError: true,
    onTruncate: true,
    onWaiting: true,
    minTurnMs: 4000
  }
}

/** 深合并配置：只认识默认表里出现过的键，其余忽略；类型不符就退回默认值。 */
export function normalizeConfig(raw) {
  const source = raw !== null && typeof raw === 'object' ? raw : {}
  const merge = (base, patch) => {
    const out = { ...base }
    if (patch === null || typeof patch !== 'object') return out
    for (const key of Object.keys(base)) {
      const value = patch[key]
      if (value === undefined || value === null) continue
      if (base[key] !== null && typeof base[key] === 'object') out[key] = merge(base[key], value)
      else if (typeof value === typeof base[key]) out[key] = value
    }
    return out
  }
  return merge(DEFAULTS, source)
}

/** 把用户给的宠物名折成 id。 */
export function normalizePetId(value, fallback) {
  if (typeof value !== 'string') return fallback
  const key = value.trim().toLowerCase()
  return PET_ALIASES[key] ?? (PETS[key] !== undefined ? key : fallback)
}

/**
 * 插件入口。
 * @param {object} ctx Cordis 上下文（用到 ctx.on / ctx.effect / ctx.tools / ctx.agents / ctx.get）
 * @param {object} rawConfig 组合层给的配置
 */
export function apply(ctx, rawConfig) {
  const config = normalizeConfig(rawConfig)
  const paths = resolveCompanionPaths()
  const bridge = createBridge()
  const log = (message) => bridge.log(message)

  const clog = (message) => {
    log(message)
    try {
      ctx.logger?.info?.(`aoqi-pet: ${message}`)
    } catch {
      /* logger 形状不认识就算了，文件日志已经写了 */
    }
  }

  if (!config.enabled) {
    clog('enabled=false，不启用')
    return
  }

  // ── 运行态（内存里，状态文件由 publish() 落盘） ───────────────────────────────
  const burst = createBurstState()
  const sessions = new Map()
  const pending = new Map()
  let lastOutcome = { kind: 'idle', at: 0 }
  let bubble = null
  let waiting = false
  let currentPetId = normalizePetId(config.petId, 'shui')
  let muted = false
  let paused = false
  let lastUserAt = 0
  let lastCommandMtime = 0
  let commandSeq = 0
  const stats = { turns: 0, completions: 0, errors: 0, truncations: 0, autoContinues: 0, skipped: 0, tools: 0 }
  const supervisor = createCompanionSupervisor({ bridge, config, log })

  // ── 状态发布 ────────────────────────────────────────────────────────────────
  function liveAgents() {
    try {
      const list = ctx.agents?.list?.()
      return Array.isArray(list) ? list : []
    } catch {
      return []
    }
  }

  function safeAgent(sessionId) {
    try {
      return ctx.agents?.get?.(sessionId) ?? null
    } catch {
      return null
    }
  }

  function syncSessions() {
    const seen = new Set()
    for (const agent of liveAgents()) {
      const id = String(agent?.id ?? '')
      if (id === '') continue
      seen.add(id)
      const status = agent?.status === 'running' ? 'running' : 'idle'
      const previous = sessions.get(id)
      if (previous === undefined) {
        sessions.set(id, { id, status, startedAt: Date.now(), lastEventAt: Date.now(), visible: false, lastTool: null })
      } else {
        previous.status = status
      }
    }
    for (const id of [...sessions.keys()]) if (!seen.has(id)) sessions.delete(id)
    return sessions
  }

  /** 现在该播哪个动画。优先级：刚出错 > 刚完成 > 等确认 > 正在跑 > 待机。 */
  function animationFor(sessionsNow) {
    const now = Date.now()
    if (lastOutcome.kind === 'error' && now - lastOutcome.at < 12000) return 'error'
    if (lastOutcome.kind === 'done' && now - lastOutcome.at < 9000) return 'done'
    if (waiting) return 'waiting'
    for (const session of sessionsNow.values()) if (session.status === 'running') return 'working'
    return 'idle'
  }

  function headlineFor(animation) {
    switch (animation) {
      case 'working': return '正在努力中…'
      case 'done': return '任务完成！'
      case 'error': return '出错了，看看日志'
      case 'waiting': return '在等你确认'
      default: return paused ? '自动续写已暂停' : '静候中'
    }
  }

  function buildState(companionStatus) {
    const sessionsNow = syncSessions()
    const animation = animationFor(sessionsNow)
    const now = Date.now()
    if (bubble !== null && (bubble.ttlMs <= 0 || now - bubble.at > bubble.ttlMs)) bubble = null
    return {
      version: 1,
      updatedAt: now,
      hostPid: process.pid,
      petId: currentPetId,
      petName: PETS[currentPetId]?.name ?? currentPetId,
      animation,
      headline: headlineFor(animation),
      bubble,
      muted,
      paused,
      sessions: [...sessionsNow.values()].map((session) => ({ id: session.id, status: session.status, lastTool: session.lastTool })),
      stats: { ...stats, burst: burst.consecutive, burstTotal: burst.total, capped: burst.capped },
      autoContinue: {
        enabled: config.autoContinue.enabled && !paused,
        consecutive: burst.consecutive,
        maxConsecutive: config.autoContinue.maxConsecutive,
        lastSentAt: burst.lastSentAt,
        capped: burst.capped,
        lastDecision: burst.lastDecision
      },
      companion: companionStatus,
      config: { scale: config.companion.scale, opacity: config.companion.opacity, alwaysOnTop: config.companion.alwaysOnTop }
    }
  }

  let lastCompanionStatus = supervisor.status()
  function publish(force = false) {
    try {
      bridge.writeState(buildState(lastCompanionStatus), force)
    } catch (error) {
      log(`publish failed: ${String(error)}`)
    }
  }

  function setBubble(text, kind, ttlMs = 6000) {
    bubble = { text: String(text), kind, at: Date.now(), ttlMs }
    publish()
  }

  // ── 自动续写 ────────────────────────────────────────────────────────────────
  function cancelPending(sessionId, code) {
    const timer = pending.get(sessionId)
    if (timer === undefined) return false
    clearTimeout(timer)
    pending.delete(sessionId)
    stats.skipped += 1
    log(`auto-continue: 取消（${code}）`)
    return true
  }

  function scheduleAutoContinue(sessionId, reasonKind, hasVisibleOutput) {
    const decision = decideContinuation({
      state: burst,
      config: config.autoContinue,
      reasonKind,
      hasVisibleOutput,
      now: Date.now()
    })
    if (decision.action === 'skip') {
      stats.skipped += 1
      if (decision.code === 'burst-limit') {
        setBubble(`已经连着帮你续了 ${burst.consecutive} 次，先停一下～点我一下可以继续`, 'warn', 15000)
        if (config.notify.onTruncate && !muted) {
          supervisor.toast('奥奇桌宠', `连续自动续写达到上限（${config.autoContinue.maxConsecutive} 次），已暂停等你确认。点一下桌宠即可继续。`)
        }
      }
      log(`auto-continue: 跳过 ${sessionId} (${decision.code})`)
      publish()
      return false
    }
    cancelPending(sessionId, 'reschedule')
    const timer = setTimeout(() => {
      pending.delete(sessionId)
      const agent = safeAgent(sessionId)
      const confirmed = confirmContinuation({
        state: burst,
        config: config.autoContinue,
        now: Date.now(),
        agentAlive: agent !== null,
        agentRunning: agent?.status === 'running',
        lastUserAt,
        cause: decision.cause
      })
      if (confirmed.action === 'skip') {
        stats.skipped += 1
        log(`auto-continue: 宽限期后放弃 ${sessionId} (${confirmed.code})`)
        publish()
        return
      }
      try {
        // 与「发送」按钮同一条排队通道：next-turn 的用户消息。消息形状照 dsh-llm 的
        // createUserMessage（{ id, role, content, source }）自己造，省掉跨包 import。
        agent.followup({
          id: randomUUID(),
          role: 'user',
          content: [{ type: 'text', text: confirmed.text }],
          source: { kind: AUTO_CONTINUE_SOURCE, cause: decision.cause, sessionId }
        })
        burst.consecutive += 1
        burst.lastSentAt = Date.now()
        burst.total += 1
        stats.autoContinues += 1
        log(`auto-continue: 已续写 ${sessionId}（第 ${burst.consecutive} 次，原因 ${decision.cause}）`)
        setBubble(decision.cause === 'truncated' ? '输出被截断了，我帮你接着说～' : '好像卡住了，我推一把～', 'info', 7000)
        if (config.notify.onTruncate && !muted) {
          supervisor.toast('奥奇桌宠 · 自动续写', decision.cause === 'truncated' ? '模型输出到达上限，已自动发送「继续」。' : '回合空转，已自动推一下。')
        }
      } catch (error) {
        log(`auto-continue: followup 失败 ${String(error)}`)
      }
      publish()
    }, decision.delayMs)
    timer.unref?.()
    pending.set(sessionId, timer)
    log(`auto-continue: ${decision.delayMs}ms 后为 ${sessionId} 续写（${decision.cause}）`)
    publish()
    return true
  }

  // ── 事件监听（全部同步注册，不 await） ───────────────────────────────────────
  ctx.on('session/event', (session, event) => {
    try {
      const sessionId = String(session?.id ?? '')
      if (sessionId === '') return
      let record = sessions.get(sessionId)
      if (record === undefined) {
        record = { id: sessionId, status: 'running', startedAt: Date.now(), lastEventAt: Date.now(), visible: false, lastTool: null }
        sessions.set(sessionId, record)
      }
      record.lastEventAt = Date.now()
      switch (event?.type) {
        case 'turn/start':
          record.visible = false
          record.turnStartedAt = Date.now()
          waiting = false
          stats.turns += 1
          lastOutcome = { kind: 'running', at: Date.now() }
          publish()
          return
        case 'assistant/message': {
          const blocks = event?.data?.message?.content
          if (Array.isArray(blocks) && blocks.some((block) => block?.type === 'text' || block?.type === 'tool-call')) record.visible = true
          return
        }
        case 'tool/call': {
          const toolName = String(event?.data?.name ?? '工具')
          record.lastTool = toolName
          record.visible = true
          stats.tools += 1
          setBubble(`正在使用 ${toolName}…`, 'info', 4000)
          return
        }
        case 'user/message':
          lastUserAt = Date.now()
          noteProgress(burst)
          cancelPending(sessionId, 'user-spoke')
          waiting = false
          publish()
          return
        case 'turn/end': {
          const reasonKind = String(event?.data?.reason?.kind ?? 'unknown')
          const duration = record.turnStartedAt === undefined ? 0 : Date.now() - record.turnStartedAt
          record.turnStartedAt = undefined
          record.lastTool = null
          if (reasonKind === 'max-tokens') {
            stats.truncations += 1
            lastOutcome = { kind: 'truncated', at: Date.now() }
            log(`turn/end: ${sessionId} 被 max-tokens 截断`)
            scheduleAutoContinue(sessionId, reasonKind, record.visible)
            return
          }
          if (reasonKind === 'error') {
            stats.errors += 1
            lastOutcome = { kind: 'error', at: Date.now() }
            setBubble('这一回合出错了…', 'error', 12000)
            if (config.notify.onError && !muted) supervisor.toast('奥奇桌宠 · 回合出错', `会话 ${sessionId.slice(0, 8)} 的回合以错误结束，回来看看。`)
            publish()
            return
          }
          if (reasonKind === 'blocked') {
            waiting = true
            if (config.notify.onWaiting && !muted) setBubble('需要你确认一下才能继续～', 'warn', 20000)
            publish()
            return
          }
          if (reasonKind === 'aborted' || reasonKind === 'interrupted') {
            lastOutcome = { kind: 'idle', at: Date.now() }
            publish()
            return
          }
          // completed / no-visible-output / 其它
          noteProgress(burst)
          const visible = record.visible
          if (reasonKind === 'no-visible-output' || (reasonKind === 'completed' && visible === false)) {
            if (scheduleAutoContinue(sessionId, reasonKind === 'no-visible-output' ? 'no-visible-output' : 'completed', false)) return
          }
          stats.completions += 1
          lastOutcome = { kind: 'done', at: Date.now() }
          const quiet = duration > 0 && duration < config.notify.minTurnMs
          if (quiet) {
            publish()
            return
          }
          setBubble(paused ? '这一回合完成啦（自动续写暂停中）' : '这一回合完成啦！', 'done', 9000)
          if (config.notify.onComplete && !muted) {
            supervisor.toast('奥奇桌宠 · 完成', `会话 ${sessionId.slice(0, 8)} 的回合已结束，用时 ${Math.round(duration / 1000)} 秒。`)
          }
          publish()
          return
        }
        default:
          return
      }
    } catch (error) {
      log(`session/event handler failed: ${String(error)}`)
    }
  })

  ctx.on('agent/status', ({ agent, status }) => {
    try {
      const record = sessions.get(String(agent?.id ?? ''))
      if (record !== undefined) record.status = status === 'running' ? 'running' : 'idle'
      publish()
    } catch (error) {
      log(`agent/status handler failed: ${String(error)}`)
    }
  })

  ctx.on('agent/error', ({ agent }) => {
    try {
      stats.errors += 1
      lastOutcome = { kind: 'error', at: Date.now() }
      const id = String(agent?.id ?? '')
      setBubble('有会话报错了，回来看一眼', 'error', 15000)
      if (config.notify.onError && !muted) supervisor.toast('奥奇桌宠 · Agent 错误', `会话 ${id.slice(0, 8)} 发生错误。`)
      publish()
    } catch (error) {
      log(`agent/error handler failed: ${String(error)}`)
    }
  })

  ctx.on('agent/disposed', ({ agent }) => {
    sessions.delete(String(agent?.id ?? ''))
    publish()
  })

  // ── 心跳：1 秒一次，管心跳/气泡过期/会话状态/桌宠守护/指令轮询 ─────────────────
  ctx.effect(() => {
    const timer = setInterval(() => {
      try {
        readCompanionSettings()
        pollCommand()
        lastCompanionStatus = supervisor.ensure()
        publish()
      } catch (error) {
        log(`tick failed: ${String(error)}`)
      }
    }, 1000)
    timer.unref?.()
    return () => {
      clearInterval(timer)
      for (const pendingTimer of pending.values()) clearTimeout(pendingTimer)
      pending.clear()
      if (config.companion.killOnUnload) supervisor.stop()
      log('aoqi-pet: 已卸载')
    }
  }, 'aoqi-pet: heartbeat')

  /** 桌宠窗口写的设置（宠物选择 / 暂停 / 静音）优先于插件配置。 */
  function readCompanionSettings() {
    const settings = bridge.readSettings()
    if (settings === null) return
    const nextPet = normalizePetId(settings.pet, currentPetId)
    if (nextPet !== currentPetId) {
      currentPetId = nextPet
      log(`桌宠切换为 ${currentPetId}`)
    }
    if (typeof settings.muted === 'boolean') muted = settings.muted
    if (typeof settings.autoContinuePaused === 'boolean') {
      if (settings.autoContinuePaused && !paused) {
        paused = true
        for (const id of [...pending.keys()]) cancelPending(id, 'paused')
        log('自动续写被桌宠暂停')
      } else if (!settings.autoContinuePaused && paused) {
        paused = false
        log('自动续写恢复')
      }
    }
  }

  /** 桌宠点一下 → 解除封顶；也可用来手动恢复自动续写。 */
  function pollCommand() {
    const command = bridge.readCommand()
    if (command === null) return
    if (command.seq !== undefined && Number(command.seq) <= commandSeq) return
    if (command.seq === undefined && command.mtimeMs <= lastCommandMtime) return
    commandSeq = Number(command.seq ?? commandSeq + 1)
    lastCommandMtime = command.mtimeMs
    const action = String(command.action ?? '')
    log(`收到桌宠指令 ${action}`)
    switch (action) {
      case 'poke':
        uncap(burst)
        setBubble('我在这儿！自动续写已经重新开闸～', 'info', 6000)
        break
      case 'resume-auto-continue':
        uncap(burst)
        paused = false
        setBubble('好，继续帮你盯着～', 'info', 5000)
        break
      case 'switch-pet':
        currentPetId = normalizePetId(command.pet, currentPetId)
        setBubble(`${PETS[currentPetId]?.name ?? currentPetId} 来了！`, 'info', 5000)
        break
      case 'pause-auto-continue':
        paused = true
        for (const id of [...pending.keys()]) cancelPending(id, 'paused')
        break
      default:
        break
    }
    publish()
  }

  // ── 工具：让模型也能直接指挥桌宠（裸定义 = 不依赖 defineTool） ─────────────────
  const stringOutput = {
    schema: { type: 'string' },
    render: (_args, value) => [{ type: 'text', text: String(value) }]
  }

  ctx.tools.register({
    name: 'aoqi_pet_say',
    description: '让桌面上的奥奇小五王说一句话（显示为桌宠气泡）。适合在长任务的关键节点主动汇报进度：用户即使把 DeepSeek Harness 最小化也能看到。',
    parameters: {
      type: 'object',
      required: ['text'],
      properties: {
        text: { type: 'string', description: '气泡里显示的文本，建议不超过 40 个字。' },
        mood: { type: 'string', description: '心情，决定宠物播哪个动画：idle（默认）/ working / done / error / waiting。' }
      }
    },
    output: stringOutput,
    async execute(args) {
      const mood = ['idle', 'working', 'done', 'error', 'waiting'].includes(String(args.mood)) ? String(args.mood) : 'idle'
      const text = String(args.text ?? '').slice(0, 120)
      if (text === '') return '气泡文本为空，没有显示。'
      if (mood === 'done') lastOutcome = { kind: 'done', at: Date.now() }
      else if (mood === 'error') lastOutcome = { kind: 'error', at: Date.now() }
      else if (mood === 'working') lastOutcome = { kind: 'running', at: Date.now() }
      waiting = mood === 'waiting'
      setBubble(text, mood === 'error' ? 'error' : mood === 'done' ? 'done' : 'info', 8000)
      if ((mood === 'done' || mood === 'error') && !muted) supervisor.toast(mood === 'done' ? '奥奇桌宠 · 完成' : '奥奇桌宠 · 提醒', text)
      return `桌宠已显示：${text}`
    }
  })

  ctx.tools.register({
    name: 'aoqi_pet_status',
    description: '读取奥奇桌宠的当前状态：动画、气泡、活跃会话、自动续写计数、桌宠进程是否在运行。',
    parameters: {
      type: 'object',
      properties: {
        verbose: { type: 'string', description: '填 "1" 返回完整 JSON 状态，其它值只返回摘要。' }
      }
    },
    output: stringOutput,
    async execute(args) {
      const state = buildState(supervisor.status())
      if (String(args.verbose) === '1') return JSON.stringify(state, null, 2)
      return [
        `宠物：${state.petName}（${state.petId}）`,
        `动画：${state.animation} · ${state.headline}`,
        `桌宠进程：${state.companion.running ? `运行中 pid=${state.companion.pid}` : '未运行'}`,
        `Python：${state.companion.python ?? '未找到'}`,
        `活跃会话：${state.sessions.length}`,
        `统计：回合 ${state.stats.turns} · 完成 ${state.stats.completions} · 出错 ${state.stats.errors} · 截断 ${state.stats.truncations} · 自动续写 ${state.stats.autoContinues} · 跳过 ${state.stats.skipped}`,
        `自动续写：${state.autoContinue.enabled ? '开启' : '关闭'}（连续 ${state.autoContinue.consecutive}/${state.autoContinue.maxConsecutive}${state.autoContinue.capped ? '，已封顶' : ''}）`,
        `状态文件：${bridge.paths.state}`
      ].join('\n')
    }
  })

  ctx.tools.register({
    name: 'aoqi_pet_switch',
    description: '切换桌面上的传说五王：huo=龙炎(初始形态小炎，火)、jin=诺亚(小诺)、shui=帝释天(小天)、an=修尔(阿修，用户俗称)、mu=阿瑞斯(阿瑞)。也接受本名/初始形态名/单字。',
    parameters: {
      type: 'object',
      required: ['pet'],
      properties: {
        pet: { type: 'string', description: '宠物 id 或中文名（金/木/水/火/暗）。' },
        reason: { type: 'string', description: '切换原因，会记进日志。' }
      }
    },
    output: stringOutput,
    async execute(args) {
      const next = normalizePetId(args.pet, currentPetId)
      currentPetId = next
      setBubble(`${PETS[next]?.name ?? next} 上场！`, 'info', 5000)
      // 同时写回设置文件：那是宠物选择的唯一真相，否则下一拍会被桌宠的旧设置覆盖回去。
      bridge.writeSettings({ pet: next })
      log(`工具切换宠物 → ${next}${args.reason ? `（${String(args.reason)}）` : ''}`)
      return `已切换到 ${PETS[next]?.name ?? next}`
    }
  })

  // ── 可选 HTTP 路由：方便外部脚本/自检读取状态 ────────────────────────────────
  const webServer = ctx.get?.('webServer')
  if (webServer !== undefined && typeof webServer.register === 'function') {
    ctx.effect(() => webServer.register({
      kind: 'prefix',
      path: '/api/aoqi-pet',
      handler: (req, res) => {
        try {
          const body = JSON.stringify({
            ok: true,
            state: buildState(supervisor.status()),
            pets: PETS,
            files: { state: bridge.paths.state, settings: bridge.paths.settings, command: bridge.paths.command, log: bridge.paths.log }
          }, null, 2)
          res.writeHead(200, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' })
          res.end(body)
        } catch (error) {
          res.writeHead(500, { 'content-type': 'application/json; charset=utf-8' })
          res.end(JSON.stringify({ ok: false, error: String(error) }))
        }
      }
    }), 'aoqi-pet: status route')
  }

  // ── 收尾：写初始状态，稍后拉起桌宠（不阻塞插件加载） ──────────────────────────
  publish(true)
  const bootstrap = setTimeout(() => {
    try {
      lastCompanionStatus = supervisor.ensure()
      publish(true)
    } catch (error) {
      log(`bootstrap failed: ${String(error)}`)
    }
  }, 800)
  bootstrap.unref?.()

  clog(`已加载；宠物=${currentPetId} 自动续写=${config.autoContinue.enabled ? 'on' : 'off'} 状态文件=${bridge.paths.state}`)
  log(`companion 脚本=${paths.script}`)
}
