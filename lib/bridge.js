/**
 * 宿主 ↔ 桌宠进程之间的「文件桥」。
 *
 * DSH 的桌面外壳跑在 Node 模式的宿主进程里（`process.type === undefined`，
 * 全仓库没有任何 `from 'electron'`），也没有官方的新开窗口 / 托盘 / 通知服务，
 * 所以桌宠只能另起一个外部进程。两边共享两个小文件：
 *
 *   <DSH_HOME>/aoqi-pet/state.json             宿主 → 桌宠：当前状态、气泡、统计
 *   <DSH_HOME>/aoqi-pet/companion-settings.json 桌宠 → 宿主：宠物选择、位置、静音、暂停自动续写
 *   <DSH_HOME>/aoqi-pet/command.json           桌宠 → 宿主：一次性指令（点击/恢复自动续写）
 *
 * 用文件而不是 HTTP，是为了让桌宠在宿主还没起来、端口还没开的时候也能先出现，
 * 并且不依赖 webServer 这个可选服务。
 */
import { appendFileSync, existsSync, mkdirSync, readFileSync, renameSync, statSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

/** 日志文件上限，超过就清空重来，避免长跑把磁盘写满。 */
const LOG_MAX_BYTES = 512 * 1024;

/** DSH 主目录：优先环境变量，其次 ~/.dsh（宿主进程里 DSH_HOME 常常是空的）。 */
export function resolveDshHome() {
  const fromEnv = process.env.DSH_HOME;
  if (typeof fromEnv === 'string' && fromEnv.trim() !== '') return fromEnv.trim();
  return join(homedir(), '.dsh');
}

/** 安全读 JSON：文件不存在、正在被写、内容半截，都返回 null 而不是抛异常。 */
export function readJsonSafe(path) {
  try {
    if (!existsSync(path)) return null;
    const text = readFileSync(path, 'utf8');
    if (text.trim() === '') return null;
    return JSON.parse(text);
  } catch {
    return null;
  }
}

/**
 * 建一个桥。所有路径都能覆盖，方便单测直接指到临时目录。
 *
 * @param {object} [options]
 * @param {string} [options.home] DSH 主目录
 * @param {string} [options.dir]  桥目录（默认 <home>/aoqi-pet）
 */
export function createBridge(options = {}) {
  const home = options.home ?? resolveDshHome();
  const dir = options.dir ?? join(home, 'aoqi-pet');
  mkdirSync(dir, { recursive: true });

  const paths = {
    dir,
    home,
    state: join(dir, 'state.json'),
    settings: join(dir, 'companion-settings.json'),
    command: join(dir, 'command.json'),
    log: join(dir, 'aoqi-pet.log'),
    pid: join(dir, 'companion.pid')
  };

  /** 上次写入的字符串，用来跳过无变化的写入（心跳除外，见 writeState 的 force）。 */
  let lastWritten = '';

  /**
   * 写状态文件。
   *
   * 直接覆盖写而不是「写临时文件再 rename」：Windows 上 Python 侧可能正握着
   * 目标文件的句柄，rename 会 EPERM。状态文件很小，读方对解析失败做了重试。
   *
   * @param {object} state 状态对象
   * @param {boolean} [force] 内容没变也写（心跳用）
   * @returns {boolean} 是否真的写了
   */
  function writeState(state, force = false) {
    let json;
    try {
      json = JSON.stringify(state, null, 2);
    } catch (error) {
      log(`state serialization failed: ${String(error)}`);
      return false;
    }
    if (!force && json === lastWritten) return false;
    try {
      writeFileSync(paths.state, json, 'utf8');
      lastWritten = json;
      return true;
    } catch (error) {
      log(`state write failed: ${String(error)}`);
      return false;
    }
  }

  /** 读桌宠写过来的设置（不存在 = 用宿主配置兜底）。 */
  function readSettings() {
    return readJsonSafe(paths.settings);
  }

  /**
   * 合并写回设置文件。桌宠窗口是主要写者，宿主只在「用户用工具换宠物」时补一笔，
   * 免得下一拍被桌宠内存里的旧值覆盖回去。
   *
   * @param {object} patch 要合并进去的键
   * @returns {boolean} 是否写入成功
   */
  function writeSettings(patch) {
    const current = readSettings() ?? {};
    const next = { ...current, ...patch };
    try {
      const tmp = `${paths.settings}.tmp`;
      writeFileSync(tmp, JSON.stringify(next, null, 2), 'utf8');
      // rename 在 Windows 上会替换目标文件；桌宠那边读失败会下一拍重试，不会卡死。
      renameSync(tmp, paths.settings);
      return true;
    } catch (error) {
      log(`settings write failed: ${String(error)}`);
      return false;
    }
  }

  /** 读一次性指令，并带上文件修改时间，方便宿主去重。 */
  function readCommand() {
    const value = readJsonSafe(paths.command);
    if (value === null || typeof value !== 'object') return null;
    let mtimeMs = 0;
    try {
      mtimeMs = statSync(paths.command).mtimeMs;
    } catch {
      /* 刚被删掉就当没读到 */
      return null;
    }
    return { ...value, mtimeMs };
  }

  /** 往宿主日志追加一行（时间戳 + 文本）。任何写失败都静默，日志不能拖垮插件。 */
  function log(message) {
    try {
      if (existsSync(paths.log)) {
        try {
          if (statSync(paths.log).size > LOG_MAX_BYTES) writeFileSync(paths.log, '', 'utf8');
        } catch {
          /* 忽略 */
        }
      }
      appendFileSync(paths.log, `[${new Date().toISOString()}] ${message}\n`, 'utf8');
    } catch {
      /* 忽略 */
    }
  }

  return { home, dir, paths, writeState, readSettings, writeSettings, readCommand, log };
}
