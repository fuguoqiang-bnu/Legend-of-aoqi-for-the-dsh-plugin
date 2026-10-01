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
function makeElement(tag) {
  const element = {
    tagName: String(tag).toUpperCase(),
    textContent: '',
    attributes: {},
    dataset: {},
    props: {},
    colorScheme: '',
    setAttribute(name, value) { this.attributes[name] = String(value); },
    getAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null; },
    hasAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attributes, name); },
    removeAttribute(name) { delete this.attributes[name]; },
    appendChild(node) { return node; },
    remove() { const i = styleNodes.indexOf(this); if (i >= 0) styleNodes.splice(i, 1); },
  };
  // style 必须是独立对象（浏览器里就是 CSSStyleDeclaration），且要闭包到宿主元素上。
  element.style = {
    setProperty(name, value) { element.props[name] = String(value); },
    removeProperty(name) { delete element.props[name]; },
    getPropertyValue(name) { return element.props[name] || ''; },
  };
  return element;
}
const docMock = {
  head: { appendChild: (node) => { styleNodes.push(node); return node; } },
  createElement: (tag) => makeElement(tag),
  documentElement: makeElement('html'),
  body: makeElement('body'),
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
        if (!(index in state)) state[index] = typeof initial === 'function' ? initial() : initial;
        return [state[index], (next) => { state[index] = typeof next === 'function' ? next(state[index]) : next; }];
      },
      useEffect(fn) { effects.push({ index: cursor++, fn }); },
      useRef(initial) { return { current: initial }; },
    },
  };
}

const SKIN_CATALOG = [
  {
    id: 'aurora', name: '极光苍穹', subtitle: '官方极光天幕 · 满铺场景', kind: 'scene', accent: '#5ec8ff',
    image: '/api/aoqi-pet/skin/aurora', imageSize: 'cover', imagePosition: 'center',
    base: { light: 'none', dark: 'none' }, fallbackColor: { light: '#eef3fb', dark: '#070b18' },
    scrimRgb: { light: '255,255,255', dark: '3,6,18' },
    tokens: {
      '--dsw-alias-bg-base': { light: 'rgba(255,255,255,.52)', dark: 'rgba(7,11,26,.55)' },
      '--dsw-alias-label-primary': { light: '#101a2e', dark: '#eef3ff' },
    },
  },
  {
    id: 'huo', name: '烈焰 · 龙炎', subtitle: '传说王者立绘 · 火', kind: 'king', accent: '#ff7a4d',
    image: '/api/aoqi-pet/skin/huo', imageSize: 'auto 86%', imagePosition: 'right bottom',
    base: { light: 'radial-gradient(#fff,#000)', dark: 'radial-gradient(#000,#111)' },
    fallbackColor: { light: '#fff', dark: '#000' },
    scrimRgb: { light: '255,255,255', dark: '3,6,18' },
    tokens: {
      '--dsw-alias-bg-base': { light: 'rgba(255,255,255,.52)', dark: 'rgba(7,11,26,.55)' },
      '--dsw-alias-label-primary': { light: '#101a2e', dark: '#eef3ff' },
    },
  },
];

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
    skin: { enabled: true, id: 'aurora', scrim: 0.45, catalog: SKIN_CATALOG },
    find: { enabled: true, title: 'DSH Find', url: 'https://dshfind.com/zh' },
  };
  return Promise.resolve({ ok: true, json: () => Promise.resolve(payload) });
};

const { React, hooks } = makeReact();
const registrations = [];
const injected = [];
const disposers = [];
const themeOverrides = [];
const themeEvents = [];
const themeMock = {
  getTheme: () => ({ active: { colorScheme: 'light' }, preference: 'light' }),
  overrideTokens: (source, tokens) => { themeOverrides.push({ source, tokens }); return () => {}; },
};
const ctxMock = {
  effect: (fn) => { const dispose = fn(); disposers.push(dispose); return dispose; },
  get: (name) => (name === 'theme' ? themeMock : undefined),
  on: (type, handler) => { themeEvents.push({ type, handler }); return () => {}; },
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
function findAll(node, predicate, out = []) {
  if (!node || typeof node !== 'object') return out;
  if (Array.isArray(node)) { for (const child of node) findAll(child, predicate, out); return out; }
  if (predicate(node)) out.push(node);
  findAll(node.children, predicate, out);
  return out;
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

// ── 6. 换肤引擎：轮询 → 应用底图 + token 覆盖层 ───────────────────────────────
console.log('\n[6] 换肤引擎（换肤真的作用到 DOM 上）');
await new Promise((r) => setTimeout(r, 0));
check('引擎轮询了宿主状态路由', fetches.some((f) => f.url.startsWith('/api/aoqi-pet')));
check('皮肤落到 <html data-aoqi-skin>', docMock.documentElement.getAttribute('data-aoqi-skin') === 'aurora',
  JSON.stringify(docMock.documentElement.attributes));
check('底图变量指向宿主素材路由',
  docMock.documentElement.style.getPropertyValue('--aoqi-skin-image') === 'url("/api/aoqi-pet/skin/aurora")',
  docMock.documentElement.style.getPropertyValue('--aoqi-skin-image'));
check('压暗层是现场拼的渐变（强度可调）',
  /^linear-gradient\(180deg,rgba\(255,255,255,0\.4/.test(docMock.documentElement.style.getPropertyValue('--aoqi-skin-scrim')),
  docMock.documentElement.style.getPropertyValue('--aoqi-skin-scrim'));
check('底图尺寸/位置来自目录（场景图满铺）',
  docMock.documentElement.style.getPropertyValue('--aoqi-skin-size') === 'cover' &&
  docMock.documentElement.style.getPropertyValue('--aoqi-skin-position') === 'center');
check('走官方 ctx.theme.overrideTokens（而不是自己写死 body 样式）',
  themeOverrides.length === 1 && themeOverrides[0].source === 'dsh-aoqi-pet/skin',
  JSON.stringify(themeOverrides.map((o) => o.source)));
check('覆盖层给的是 { light, dark } 成对值（官方校验要求）',
  themeOverrides[0]?.tokens?.['--dsw-alias-bg-base']?.light === 'rgba(255,255,255,.52)' &&
  themeOverrides[0]?.tokens?.['--dsw-alias-bg-base']?.dark === 'rgba(7,11,26,.55)');
check('订阅了 theme/change，明暗切换时重画皮肤', themeEvents.some((e) => e.type === 'theme/change'));

// ── 7. 左侧栏两个入口：皮肤中心 + 内置网页 ───────────────────────────────────
console.log('\n[7] 左侧栏入口与主面板');
const panelEntries = registrations.filter((r) => r.options.name === 'sidebar.panellist');
check('注册了 2 个 sidebar.panellist 入口（和「插件」「自动化任务」同一列）', panelEntries.length === 2,
  JSON.stringify(panelEntries.map((r) => r.options.id)));
check('两个入口都有 id / order / label（list slot 契约）',
  panelEntries.every((r) => typeof r.options.id === 'string' && typeof r.options.order === 'number' && typeof r.options.label === 'string'),
  JSON.stringify(panelEntries.map((r) => r.options)));
const mainEntries = registrations.filter((r) => r.options.name === 'main');
check('注册了 2 个 main 面板（keyed slot，key 必须等于入口 id）', mainEntries.length === 2,
  JSON.stringify(mainEntries.map((r) => r.options.key)));
check('main 的 key 与 panellist 的 id 一一对应',
  mainEntries.map((r) => r.options.key).sort().join(',') === panelEntries.map((r) => r.options.id).sort().join(','),
  `${mainEntries.map((r) => r.options.key)} vs ${panelEntries.map((r) => r.options.id)}`);
check('入口之一是内置网页 dshfind', panelEntries.some((r) => r.options.id === 'aoqi-find'));

const skinPanelReg = mainEntries.find((r) => r.options.key === 'aoqi-skins');
hooks.reset();
const skinTree = skinPanelReg.component({});
check('皮肤中心渲染出标题', findText(skinTree, '奥奇皮肤中心'), JSON.stringify(skinTree).slice(0, 160));
check('皮肤中心列出目录里的皮肤', findText(skinTree, '极光苍穹') && findText(skinTree, '烈焰 · 龙炎'));
check('当前皮肤被标成 data-active', JSON.stringify(skinTree).includes('"data-active":"true"'));
const cards = findAll(skinTree, (node) => node.props && node.props['data-skin'] !== undefined);
check('每张皮肤是一个可点的卡片', cards.length === 2, String(cards.length));
const beforePosts = fetches.filter((f) => f.options.method === 'POST').length;
cards[1].props.onClick();
const skinPost = fetches.filter((f) => f.options.method === 'POST').slice(beforePosts).map((f) => f.url);
check('点卡片会 POST set-skin（并立刻在本页生效）',
  skinPost.some((url) => url.includes('action=set-skin') && url.includes('id=huo')), JSON.stringify(skinPost));
check('点卡片后底图变量立刻换成新皮肤',
  docMock.documentElement.style.getPropertyValue('--aoqi-skin-image') === 'url("/api/aoqi-pet/skin/huo")',
  docMock.documentElement.style.getPropertyValue('--aoqi-skin-image'));
check('立绘类皮肤的尺寸/位置是「右侧贴底」',
  docMock.documentElement.style.getPropertyValue('--aoqi-skin-size') === 'auto 86%' &&
  docMock.documentElement.style.getPropertyValue('--aoqi-skin-position') === 'right bottom');

const findPanelReg = mainEntries.find((r) => r.options.key === 'aoqi-find');
hooks.reset();
const findTree = findPanelReg.component({});
check('网页面板渲染出标题与地址', findText(findTree, 'https://dshfind.com/zh'), JSON.stringify(findTree).slice(0, 200));
const frames = findAll(findTree, (node) => node.type === 'iframe');
check('网页面板里是一个真的 iframe', frames.length === 1, String(frames.length));
check('iframe 指向宿主配置里的地址', frames[0]?.props?.src === 'https://dshfind.com/zh', String(frames[0]?.props?.src));
check('iframe 带 sandbox（不允许顶层导航）',
  typeof frames[0]?.props?.sandbox === 'string' && frames[0].props.sandbox.includes('allow-scripts') &&
  !frames[0].props.sandbox.includes('allow-top-navigation'), String(frames[0]?.props?.sandbox));

// ── 8. 降级路径：宿主还没提供 theme 服务时，也要能用 ─────────────────────────
console.log('\n[8] 拿不到 theme 服务时的降级路径');
{
  const loaded2 = [];
  const doc2 = {
    head: { appendChild: (node) => node },
    createElement: (tag) => makeElement(tag),
    documentElement: makeElement('html'),
    body: makeElement('body'),
  };
  const window2 = { __ModuleLoader__: { load: (record) => loaded2.push(record) } };
  const { React: React2 } = makeReact();
  const ctx2 = {
    effect: (fn) => { fn(); return () => {}; },
    // 故意不给 theme：模拟「主题服务还没起来 / 名字变了」
    get: () => undefined,
    on: () => () => {},
    slots: { inject: (key, callback) => callback(), register: (options, component) => ({ options, component }) },
  };
  new Function('window', 'document', 'fetch', 'setInterval', 'clearInterval', 'console', src)(
    window2, doc2, fetchMock, setInterval, clearInterval, console,
  );
  const mod2 = loaded2[0].factory((name) => { if (name === 'react') return React2; throw new Error(name); });
  mod2.apply(ctx2);
  await new Promise((r) => setTimeout(r, 0));
  check('降级路径下底图仍然铺上', doc2.documentElement.getAttribute('data-aoqi-skin') === 'aurora',
    JSON.stringify(doc2.documentElement.attributes));
  check('降级路径改成直接往 body 写 token（而不是什么都不做）',
    doc2.body.style.getPropertyValue('--dsw-alias-bg-base') === 'rgba(255,255,255,.52)',
    doc2.body.style.getPropertyValue('--dsw-alias-bg-base'));
  check('降级路径写的是浅色那一套（按当前色系挑）',
    doc2.body.style.getPropertyValue('--dsw-alias-label-primary') === '#101a2e',
    doc2.body.style.getPropertyValue('--dsw-alias-label-primary'));
  check('没有 theme 服务也不会抛异常（整段跑完就是证明）', true);
}

// 清理：把 apply 注册的所有 effect 都拆掉（否则引擎的轮询定时器会吊住进程）
for (const dispose of disposers) {
  try { if (typeof dispose === 'function') dispose(); } catch { /* 忽略 */ }
}

console.log('');
if (failures.length) {
  console.log(`失败 ${failures.length} 项，通过 ${passed} 项`);
  for (const item of failures) console.log(`  - ${item}`);
  process.exit(1);
}
console.log(`通过 ${passed} 项，失败 0 项`);
