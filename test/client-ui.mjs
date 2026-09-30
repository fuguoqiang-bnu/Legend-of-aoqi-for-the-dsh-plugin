#!/usr/bin/env node
/**
 * 客户端面（lib/client.js）的契约测试 —— 不需要 DSH、不需要浏览器、不需要构建工具。
 *
 * 为什么要有它：客户端 bundle 的格式是硬约束（见 asar 内
 * @deepseek-ai/dsh-client-modules/lib/index.js 与官方模板 templates/decoration/client.js）：
 *   - 必须是 window.__ModuleLoader__.load({ id, factory })，id 必须等于 package.json 的 name
 *   - 文件执行时零副作用，所有副作用都关在 factory 闭包里
 *   - 不是 ESM（裸 export 不会被识别）
 *   - 只能 require 宿主平台模块表里的 9 个模块（我们只用 react）
 *   - 往未声明的 slot 注册会在加载期抛错；list slot 必须给 id，keyed slot 必须给 key
 * 这里用一个假的 __ModuleLoader__ / 假 React / 假 slots 把 bundle 真的跑一遍，
 * 把上面每一条都断言出来。真正的「界面里看得见」需要 DSH 重启（见 docs/IN-APP-UI.md）。
 *
 * 跑法：node test/client-ui.mjs
 */
import { readFileSync, existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const pkg = JSON.parse(readFileSync(join(ROOT, 'package.json'), 'utf8'));

let passed = 0;
const failures = [];
function check(name, ok, detail = '') {
  if (ok) { passed += 1; console.log(`  [✓] ${name}`); } else {
    failures.push(`${name}${detail ? ` → ${detail}` : ''}`);
    console.log(`  [✗] ${name}${detail ? `\n        ${detail}` : ''}`);
  }
}

// ── 1. package.json 的客户端面声明 ─────────────────────────────────────────────
console.log('\n[1] package.json 的 dsh.client 声明');
const clientDecl = pkg.dsh && pkg.dsh.client;
check('dsh.client 存在', Boolean(clientDecl));
check('dsh.client.platform === "web"（其它值整包会被忽略）', clientDecl?.platform === 'web');
check('dsh.client.inject 是字符串数组', Array.isArray(clientDecl?.inject) && clientDecl.inject.every((v) => typeof v === 'string'));
const clientExport = pkg.exports && pkg.exports['./client'];
const clientRel = typeof clientExport === 'string' ? clientExport
  : (clientExport && typeof clientExport.default === 'string' ? clientExport.default : null);
check('exports["./client"] 是字符串或 {default: string}', clientRel !== null);
const clientPath = clientRel ? join(ROOT, clientRel) : '';
check(`exports["./client"] 指向的文件存在（${clientRel}）`, Boolean(clientRel) && existsSync(clientPath));

// ── 2. 文件形态（不是 ESM、零副作用、只能用平台模块）─────────────────────────
console.log('\n[2] lib/client.js 的形态约束');
const src = readFileSync(clientPath, 'utf8');
check('用 window.__ModuleLoader__.load 注册（而不是 ESM 导出）', src.includes('__ModuleLoader__.load'));
check('没有顶层 import 语句', !/^\s*import\s/m.test(src));
check('没有顶层 export 语句', !/^\s*export\s/m.test(src));
const requires = [...src.matchAll(/require\(\s*['"]([^'"]+)['"]\s*\)/g)].map((m) => m[1]);
const allowed = ['react', 'react/jsx-runtime', 'react-dom', 'react-dom/client', '@deepseek-ai/cordis',
  '@deepseek-ai/dsh-client-store', '@deepseek-ai/dsh-client-ui-slots',
  '@deepseek-ai/dsh-client-ui-primitives', '@deepseek-ai/dsh-client-ui-dockkit'];
check(`只 require 平台模块表里的模块（实际：${requires.join(', ') || '无'}）`,
  requires.every((name) => allowed.includes(name)), `出现了非平台模块：${requires.filter((n) => !allowed.includes(n)).join(', ')}`);
check('没有 require 任何 @deepseek-ai/dsh-client-* 业务组件包（官方 practices.md 明确禁止）',
  !requires.some((name) => name.startsWith('@deepseek-ai/dsh-client-')));

// ── 3. 把 bundle 跑在假的宿主环境里 ───────────────────────────────────────────
console.log('\n[3] 在假 __ModuleLoader__ / 假 React / 假 slots 上真跑一遍');
const loaded = [];
const styleNodes = [];
const docMock = {
  head: { appendChild: (node) => { styleNodes.push(node); return node; } },
  createElement: (tag) => ({
    tagName: tag.toUpperCase(), textContent: '', attributes: {},
    setAttribute(k, v) { this.attributes[k] = v; },
    remove() { const i = styleNodes.indexOf(this); if (i >= 0) styleNodes.splice(i, 1); },
  }),
};
const windowMock = { __ModuleLoader__: { load: (record) => loaded.push(record) } };

// 极简 React：createElement 造树，useState/useEffect 记录 hook 调用
function makeReact() {
  const state = [];
  const effects = [];
  let cursor = 0;
  return {
    hooks: { state, effects, reset: () => { cursor = 0; } },
    React: {
      createElement: (type, props, ...children) => ({ type, props: props || {}, children: children.flat(Infinity) }),
      useState(initial) {
        const index = cursor++;
        if (!(index in state)) state[index] = initial;
        return [state[index], (next) => { state[index] = typeof next === 'function' ? next(state[index]) : next; }];
      },
      useEffect(fn) { effects.push({ index: cursor++, fn }); },
      useRef(initial) { return { current: initial }; },
    },
  };
}

const fetches = [];
const fetchMock = (url, options) => {
  fetches.push({ url: String(url), options: options || {} });
  const payload = {
    ok: true,
    state: {
      petId: 'huo', petName: '龙炎', animation: url.includes('next-pet') ? 'working' : 'idle',
      headline: '正在努力中…', stats: { turns: 7, completions: 2, autoContinues: 1 },
    },
    pets: { huo: { name: '龙炎' } },
  };
  return Promise.resolve({ ok: true, json: () => Promise.resolve(payload) });
};

const { React, hooks } = makeReact();
const registrations = [];
const injected = [];
const disposers = [];
const ctxMock = {
  effect: (fn) => { const dispose = fn(); disposers.push(dispose); return dispose; },
  slots: {
    inject: (key, callback) => { injected.push(key); return callback(); },
    register: (options, component) => { registrations.push({ options, component }); return () => {}; },
  },
};

const runBundle = () => new Function('window', 'document', 'fetch', 'setInterval', 'clearInterval', 'console', src)(
  windowMock, docMock, fetchMock, setInterval, clearInterval, console,
);
runBundle();

check('执行 bundle 只注册了一个 factory（没有别的顶层副作用）', loaded.length === 1, `实际注册 ${loaded.length} 个`);
check('注册的 id 等于 package.json 的 name', loaded[0]?.id === pkg.name, `实际 ${loaded[0]?.id}`);
check('执行阶段没有往 DOM 里塞样式（副作用必须在 factory 闭包里）', styleNodes.length === 0,
  `执行时已插入 ${styleNodes.length} 个 style`);

const moduleExports = loaded[0].factory((name) => {
  if (name === 'react') return React;
  throw new Error(`bundle require 了非平台模块：${name}`);
});
check('factory 返回 { inject, apply }（cordis 插件形状）',
  typeof moduleExports === 'object' && Array.isArray(moduleExports.inject) && typeof moduleExports.apply === 'function');
check('inject 里声明了 slots 服务', moduleExports.inject.includes('slots'));

moduleExports.apply(ctxMock);
check('通过 ctx.slots.inject 等 slot 声明后再注册（官方推荐写法）', injected.length >= 1);
const dockReg = registrations.find((r) => r.options.name === 'conversation.composer.dock');
check('注册进 conversation.composer.dock（list slot，需要 id）',
  Boolean(dockReg) && typeof dockReg.options.id === 'string' && dockReg.options.id !== '');
check('dock 注册带了 order（展示序）', typeof dockReg?.options?.order === 'number');
const toolReg = registrations.find((r) => r.options.name === 'tool.call.toolview');
check('注册进 tool.call.toolview（keyed slot，需要 key = 工具名）',
  Boolean(toolReg) && toolReg.options.key === 'aoqi_pet_status');
check('样式是在 apply 里通过 ctx.effect 注入的资源', styleNodes.length === 1);
check('资源注册返回了清理函数', disposers.length >= 1 && typeof disposers[0] === 'function');
disposers[0]();
check('清理函数真的把 style 移除了', styleNodes.length === 0);

// ── 4. 组件真的能渲染，并且点一下会 POST 换宠物 ───────────────────────────────
console.log('\n[4] 组件渲染与交互');
function findText(node, needle) {
  if (typeof node === 'string') return node.includes(needle);
  if (!node || typeof node !== 'object') return false;
  if (Array.isArray(node)) return node.some((child) => findText(child, needle));
  return findText(node.children, needle);
}
function findProps(node, key) {
  if (!node || typeof node !== 'object') return null;
  if (Array.isArray(node)) { for (const child of node) { const hit = findProps(child, key); if (hit) return hit; } return null; }
  if (node.props && typeof node.props[key] === 'function') return node.props[key];
  return findProps(node.children, key);
}

hooks.reset();
let tree = null;
let renderError = null;
try { tree = dockReg.component({}); } catch (error) { renderError = String(error); }
check('组件能直接渲染出元素树', tree !== null && renderError === null, renderError || '');

// 跑 useEffect 里那一拍 fetch（模拟宿主路由在线）
const effect = hooks.effects.find((e) => e.index !== undefined);
check('组件注册了 useEffect 用于拉状态', Boolean(effect));
if (effect) {
  const cleanup = effect.fn();
  await new Promise((r) => setTimeout(r, 0));
  hooks.reset();
  tree = dockReg.component({});
  check('拉到状态后渲染出宠物名（龙炎）', findText(tree, '龙炎'), JSON.stringify(tree).slice(0, 200));
  check('拉到状态后渲染出状态标签（待机）', findText(tree, '待机'));
  check('渲染出统计（回合/完成/续写）', findText(tree, '回合') && findText(tree, '续写'));
  check('根节点带 data-aoqi-pet 标记（便于人肉确认真的挂上了）',
    JSON.stringify(tree).includes('data-aoqi-pet'));
  const onClick = findProps(tree, 'onClick');
  check('组件挂了 onClick', typeof onClick === 'function');
  if (typeof onClick === 'function') {
    onClick();
    const post = fetches.find((f) => f.options.method === 'POST');
    check('点击走的是 POST（换下一只五王）', Boolean(post), JSON.stringify(fetches.map((f) => `${f.options.method || 'GET'} ${f.url}`)));
    check('POST 目标是 /api/aoqi-pet?action=next-pet', Boolean(post && post.url.includes('/api/aoqi-pet') && post.url.includes('action=next-pet')));
  }
  if (typeof cleanup === 'function') cleanup();
  check('useEffect 的清理函数不抛异常', true);
}

// ── 5. 工具卡片组件 ──────────────────────────────────────────────────────────
console.log('\n[5] aoqi_pet_status 的工具卡片');
const toolTree = toolReg.component({ result: { text: '宠物：龙炎（huo）\n动画：idle' } });
check('拿到工具结果文本时渲染第一行', findText(toolTree, '宠物：龙炎'));
check('没有结果文本时返回 null（不破坏别人的卡片）', toolReg.component({}) === null);

console.log('');
if (failures.length) {
  console.log(`失败 ${failures.length} 项，通过 ${passed} 项`);
  for (const item of failures) console.log(`  - ${item}`);
  process.exit(1);
}
console.log(`通过 ${passed} 项，失败 0 项`);
