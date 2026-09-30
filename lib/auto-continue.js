/**
 * 自动续写（Auto-continue）的纯决策逻辑。
 *
 * DSH 内核在模型输出触顶时只落一条 `turn/end { reason: { kind: 'max-tokens' } }`
 * 就结束回合，**不会自己接着写**（内核源码 dsh-agent-loop 里 max-tokens 直接返回，
 * 没有任何重试分支）。这里把「要不要替用户发一条继续指令」的全部判断收敛成纯函数：
 * 不碰定时器、不碰 I/O，只吃 (状态, 配置, 事件) 吐结论，因此可以脱离宿主单测。
 *
 * 三重限流，防止把 token 无限烧下去：
 *   - graceMs     ：检测到截断后先等一会儿，宿主自己开了新回合就取消（避免插队）
 *   - cooldownMs  ：两次自动续写之间的最小间隔
 *   - maxConsecutive：连续自动续写上限；用户一说话或回合正常完成就清零
 */

/** 自动续写消息的 source.kind，用于在会话日志里区分「人发的」和「插件发的」。 */
export const AUTO_CONTINUE_SOURCE = 'aoqi-pet/auto-continue';

/** 内核的截断原因码。 */
export const MAX_TOKENS = 'max-tokens';

/** 兼容未来/第三方内核：无可见输出的「完成」，同样值得续跑。 */
export const NO_VISIBLE_OUTPUT = 'no-visible-output';

/** 新建一份突发计数状态。 */
export function createBurstState() {
  return {
    /** 连续自动续写了多少次（未被打断）。 */
    consecutive: 0,
    /** 上一次自动续写的时间戳（ms）。 */
    lastSentAt: 0,
    /** 是否已因为触到上限而封顶（等用户介入或点击桌宠解除）。 */
    capped: false,
    /** 累计自动续写次数。 */
    total: 0,
    /** 最近一次判断结果，仅用于状态展示与排错。 */
    lastDecision: null
  };
}

/** 回合正常完成 / 用户说话 / 用户点击桌宠后，解除封顶并清零连击。 */
export function noteProgress(state) {
  state.consecutive = 0;
  state.capped = false;
  state.lastDecision = null;
  return state;
}

/** 封顶解除：只清 capped，保留连击数的语义（点击桌宠 = 再给几次机会）。 */
export function uncap(state) {
  state.capped = false;
  state.consecutive = 0;
  return state;
}

/**
 * 这一条 `turn/end` 值不值得续写？
 *
 * @param {object} input
 * @param {object} input.state   `createBurstState()` 的结果（会被读取，不被改动）
 * @param {object} input.config  autoContinue 配置
 * @param {string} input.reasonKind `turn/end` 的 `reason.kind`
 * @param {boolean} input.hasVisibleOutput 本回合是否产生过可见输出（文本或工具调用）
 * @param {number} input.now     Date.now()
 * @returns {{action:'continue', text:string, delayMs:number, cause:string} | {action:'skip', code:string}}
 */
export function decideContinuation(input) {
  const { state, config, reasonKind, hasVisibleOutput, now } = input;
  const stamp = { reasonKind, at: now };

  if (!config.enabled) return skip(state, 'disabled', stamp);

  // 1) 判定「这次结束是不是需要接着写」。
  let cause = null;
  if (reasonKind === MAX_TOKENS) cause = 'truncated';
  else if (config.detectStalled && (reasonKind === NO_VISIBLE_OUTPUT || (reasonKind === 'completed' && hasVisibleOutput === false))) cause = 'stalled';
  if (cause === null) return skip(state, `reason:${reasonKind}`, stamp);

  // 2) 连击上限：封顶后只通知、不再自动发，等用户介入。
  if (state.consecutive >= config.maxConsecutive) {
    state.capped = true;
    return skip(state, 'burst-limit', stamp);
  }
  // 3) 冷却。
  if (state.lastSentAt !== 0 && now - state.lastSentAt < config.cooldownMs) {
    return skip(state, 'cooldown', stamp);
  }

  const text = cause === 'truncated' ? config.continueText : config.stallText;
  const decision = { action: 'continue', text, delayMs: Math.max(0, config.graceMs), cause };
  state.lastDecision = { ...decision, ...stamp };
  return decision;
}

/**
 * 宽限期结束时再确认一次：宿主已经自己跑起来了 / 用户插话了，就不要发。
 *
 * @param {object} input
 * @param {object} input.state
 * @param {object} input.config
 * @param {number} input.now
 * @param {boolean} input.agentAlive   agent 是否还在注册表里
 * @param {boolean} input.agentRunning agent 当前是否 running（宿主已自行开跑）
 * @param {number} input.lastUserAt    最后一次用户消息时间戳
 * @returns {{action:'continue', text:string} | {action:'skip', code:string}}
 */
export function confirmContinuation(input) {
  const { state, config, now, agentAlive, agentRunning, lastUserAt, cause } = input;
  if (!config.enabled) return skip(state, 'disabled');
  if (!agentAlive) return skip(state, 'agent-gone');
  if (agentRunning) return skip(state, 'already-running');
  if (lastUserAt !== 0 && now - lastUserAt < config.graceMs) return skip(state, 'user-spoke');
  if (state.consecutive >= config.maxConsecutive) {
    state.capped = true;
    return skip(state, 'burst-limit');
  }
  if (state.lastSentAt !== 0 && now - state.lastSentAt < config.cooldownMs) return skip(state, 'cooldown');
  return { action: 'continue', text: cause === 'stalled' ? config.stallText : config.continueText };
}

function skip(state, code, stamp) {
  state.lastDecision = { action: 'skip', code, ...(stamp ?? {}) };
  return { action: 'skip', code };
}
