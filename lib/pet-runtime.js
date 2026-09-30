/**
 * 桌宠进程（companion）的定位、启动与守护。
 *
 * 为什么必须另起进程：DSH 桌面版是「Electron 外壳 + Node 模式宿主」，
 * 插件的 apply() 跑在 Node 宿主里，进程里没有 BrowserWindow，也没有托盘/通知服务
 * （dsh-desktop-host 全仓库 0 处 electron 引用）。想在 DSH 最小化时仍然看得见宠物，
 * 只能在 DSH 之外开一个自己的窗口进程。
 *
 * 这里用 Python + tkinter：Windows 自带、零第三方依赖，DSH 主运行时里还自带一份
 * （<DSH_HOME>/dsh-runtimes/*\/dependencies/python/python.exe），所以连解释器都不用用户装。
 */
import { spawn, spawnSync } from 'node:child_process';
import { existsSync, readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');

/** 仓库内固定路径。 */
export function resolveCompanionPaths() {
  return {
    root: REPO_ROOT,
    script: join(REPO_ROOT, 'companion', 'aoqi_pet.py'),
    assets: join(REPO_ROOT, 'assets'),
    toast: join(REPO_ROOT, 'companion', 'toast.ps1')
  };
}

/** 探测一个 python 是否真能 import tkinter（装了个壳但没有 Tk 的发行版很常见）。 */
function probePython(command, prefixArgs) {
  try {
    const result = spawnSync(command, [...prefixArgs, '-c', 'import tkinter, sys; print(sys.version.split()[0])'], {
      timeout: 12000,
      windowsHide: true,
      encoding: 'utf8'
    });
    if (result.status !== 0) return null;
    return { path: command, args: prefixArgs, version: String(result.stdout ?? '').trim() };
  } catch {
    return null;
  }
}

/** 找出 DSH 自带 Python 的候选路径（优先 primary runtime）。 */
function bundledPythonCandidates(home) {
  const root = join(home, 'dsh-runtimes');
  const found = [];
  try {
    if (!existsSync(root)) return found;
    const runtimes = readdirSync(root, { withFileTypes: true })
      .filter((entry) => entry.isDirectory())
      .map((entry) => entry.name)
      .sort((a, b) => (a === 'dsh-primary-runtime' ? -1 : b === 'dsh-primary-runtime' ? 1 : a.localeCompare(b)));
    for (const name of runtimes) {
      found.push(join(root, name, 'dependencies', 'python', 'python.exe'));
    }
  } catch {
    /* 忽略 */
  }
  return found;
}

/**
 * 选一个可用的解释器。顺序：显式配置 → 环境变量 → DSH 自带 → PATH 上的 python3/python/py。
 *
 * @param {object} input
 * @param {string} input.home DSH 主目录
 * @param {string} [input.configured] 配置里写的 pythonPath
 * @returns {{path:string, args:string[], source:string, version:string} | null}
 */
export function findPython({ home, configured }) {
  const attempts = [];
  if (typeof configured === 'string' && configured.trim() !== '') {
    attempts.push({ path: configured.trim(), args: [], source: 'config' });
  }
  const fromEnv = process.env.DSH_AOQI_PYTHON;
  if (typeof fromEnv === 'string' && fromEnv.trim() !== '') {
    attempts.push({ path: fromEnv.trim(), args: [], source: 'env:DSH_AOQI_PYTHON' });
  }
  for (const candidate of bundledPythonCandidates(home)) {
    attempts.push({ path: candidate, args: [], source: 'dsh-runtime' });
  }
  attempts.push({ path: 'python3', args: [], source: 'PATH' });
  attempts.push({ path: 'python', args: [], source: 'PATH' });
  attempts.push({ path: 'py', args: ['-3'], source: 'PATH' });

  for (const attempt of attempts) {
    if (attempt.source !== 'PATH' && !existsSync(attempt.path)) continue;
    const probed = probePython(attempt.path, attempt.args);
    if (probed !== null) return { ...probed, source: attempt.source };
  }
  return null;
}

/** 读 pid 文件并判断进程是否还活着。 */
function pidAlive(path) {
  try {
    const text = readFileSync(path, 'utf8').trim();
    const pid = Number.parseInt(text, 10);
    if (!Number.isInteger(pid) || pid <= 0) return 0;
    process.kill(pid, 0);
    return pid;
  } catch {
    return 0;
  }
}

/**
 * 建一个守护器。所有方法都是同步或「发射后不管」的，绝不在 apply() 里 await，
 * 否则会踩到 DSH「不要在首个 await 之后注册贡献」的红线。
 *
 * @param {object} input
 * @param {object} input.bridge createBridge() 的返回值
 * @param {object} input.config 已归一化的插件配置
 * @param {(message:string)=>void} input.log 日志函数
 */
export function createCompanionSupervisor({ bridge, config, log }) {
  const paths = resolveCompanionPaths();
  const companionConfig = config.companion;
  let python = null;
  let pythonResolvedAt = 0;
  let lastAttemptAt = 0;
  let failures = 0;
  let lastError = '';
  let launchedByUs = 0;

  function resolvePython(force = false) {
    const now = Date.now();
    if (!force && python !== null) return python;
    // 找解释器是同步 spawn，比较贵；失败后 30 秒才重试，成功则缓存。
    if (!force && now - pythonResolvedAt < 30000) return python;
    pythonResolvedAt = now;
    python = findPython({ home: bridge.home, configured: companionConfig.pythonPath });
    if (python !== null) {
      log(`companion: 使用解释器 ${python.path} (${python.source}, ${python.version})`);
    } else {
      log('companion: 没找到带 tkinter 的 Python，桌宠窗口不会启动（可用 companion.pythonPath 指定）');
    }
    return python;
  }

  function runningPid() {
    const pid = pidAlive(bridge.paths.pid);
    if (pid !== 0) return pid;
    // pid 文件可能被删/过期，但我们自己拉起来的子进程还在。
    if (launchedByUs !== 0) {
      try {
        process.kill(launchedByUs, 0);
        return launchedByUs;
      } catch {
        launchedByUs = 0;
      }
    }
    return 0;
  }

  /** 该不该拉起桌宠；返回一条人类可读的结论，供状态文件展示。 */
  function ensure() {
    if (!companionConfig.autoLaunch) return { running: runningPid() !== 0, note: 'autoLaunch=false' };
    if (!existsSync(paths.script)) return { running: false, note: `缺少 companion 脚本：${paths.script}` };
    if (runningPid() !== 0) return { running: true, note: 'running' };

    const now = Date.now();
    if (failures >= 5) return { running: false, note: `连续启动失败 ${failures} 次，已放弃；${lastError}` };
    if (now - lastAttemptAt < 15000) return { running: false, note: '启动节流中' };
    lastAttemptAt = now;

    const interpreter = resolvePython();
    if (interpreter === null) return { running: false, note: 'no-python' };

    const args = [
      ...interpreter.args,
      paths.script,
      '--state', bridge.paths.state,
      '--settings', bridge.paths.settings,
      '--command', bridge.paths.command,
      '--assets', paths.assets,
      '--log', bridge.paths.log,
      '--pet', String(config.petId),
      '--scale', String(companionConfig.scale),
      '--opacity', String(companionConfig.opacity),
      '--topmost', companionConfig.alwaysOnTop ? '1' : '0',
      '--sound', companionConfig.sound ? '1' : '0',
      '--stale-exit-ms', String(companionConfig.staleExitMs)
    ];
    try {
      const child = spawn(interpreter.path, args, {
        detached: true,
        stdio: 'ignore',
        windowsHide: true,
        cwd: paths.root,
        env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' }
      });
      child.on('error', (error) => {
        failures += 1;
        lastError = String(error && error.message ? error.message : error);
        log(`companion: spawn 失败 ${lastError}`);
      });
      child.unref();
      launchedByUs = child.pid ?? 0;
      if (launchedByUs !== 0) {
        try {
          writeFileSync(bridge.paths.pid, String(launchedByUs), 'utf8');
        } catch {
          /* pid 文件写不了也不影响 */
        }
      }
      failures = 0;
      log(`companion: 已启动 pid=${launchedByUs} interpreter=${interpreter.path}`);
      return { running: true, note: 'launched', pid: launchedByUs };
    } catch (error) {
      failures += 1;
      lastError = String(error && error.message ? error.message : error);
      log(`companion: 启动异常 ${lastError}`);
      return { running: false, note: lastError };
    }
  }

  /** 主动结束桌宠（默认不做：插件热重载时不该把用户的宠物窗口杀掉）。 */
  function stop() {
    const pid = runningPid();
    if (pid === 0) return false;
    try {
      if (process.platform === 'win32') spawnSync('taskkill', ['/PID', String(pid), '/T', '/F'], { windowsHide: true });
      else process.kill(pid, 'SIGTERM');
      launchedByUs = 0;
      log(`companion: 已结束 pid=${pid}`);
      return true;
    } catch (error) {
      log(`companion: 结束失败 ${String(error)}`);
      return false;
    }
  }

  /**
   * 弹一个 Windows 气泡通知（不依赖任何模块：Windows PowerShell + NotifyIcon）。
   * 文本走 base64，规避 PowerShell 脚本的编码坑。发射后不管。
   */
  function toast(title, message) {
    if (!companionConfig.balloonTips) return;
    if (!existsSync(paths.toast)) return;
    try {
      const encode = (value) => Buffer.from(String(value ?? ''), 'utf8').toString('base64');
      const child = spawn(
        'powershell.exe',
        ['-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden', '-ExecutionPolicy', 'Bypass', '-File', paths.toast,
          '-TitleB64', encode(title), '-MessageB64', encode(message)],
        { detached: true, stdio: 'ignore', windowsHide: true }
      );
      child.on('error', () => { /* 没有 powershell.exe 就算了 */ });
      child.unref();
    } catch {
      /* 忽略 */
    }
  }

  return {
    paths,
    ensure,
    stop,
    toast,
    runningPid,
    resolvePython,
    status() {
      return {
        running: runningPid() !== 0,
        pid: runningPid(),
        python: python === null ? null : python.path,
        pythonSource: python === null ? null : python.source,
        failures,
        lastError
      };
    }
  };
}
