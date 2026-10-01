/**
 * 奥奇桌宠 · 客户端面（DSH 界面内的那一半）。
 *
 * 这是**浏览器 bundle**，不是 ESM：格式必须是 `window.__ModuleLoader__.load({id, factory})`，
 * id 必须等于 package.json 的 name，文件执行时**零副作用**（所有副作用都放进 factory 闭包，
 * 官方模板 templates/decoration/client.js 就是这么写的）。React 由宿主的平台模块表提供，
 * 只允许 `require('react')`，不要去 require 任何 @deepseek-ai/dsh-client-* 包。
 *
 * 挂载点（全部是 asar 里查证过的公开席位，不是猜的）：
 *
 * | 席位 | 类型 | 作用 |
 * |---|---|---|
 * | `conversation.composer.dock` | list | 输入框旁的同步小宠物 |
 * | `tool.call.toolview` | keyed | `aoqi_pet_status` 的工具卡片 |
 * | `sidebar.panellist` × 2 | list (root) | **左侧栏入口**，和「插件」「自动化任务」同一列 |
 * | `main` × 2 | keyed (root) | 上面两个入口点开后的主面板正文 |
 *
 * 数据源：宿主插件已经在 webServer 上注册的 `GET /api/aoqi-pet` —— 和桌面那只桌宠
 * **同一份状态**（同一个 state.json），所以应用里和桌面上的表现永远一致。
 * 点一下它会 POST `?action=next-pet` 轮换五王，再点状态就会跟着变。
 *
 * 换肤为什么长这样：DSH 的 `--dsw-alias-*` token 由 ui-theme 持有、由 ui-layout 以
 * **inline style** 写到 `body` 上，第三方只能用官方接口
 * `ctx.theme.overrideTokens(source, { token: { light, dark } })` 叠一层。
 * 所以「换肤」= 一层 token 覆盖（把面板变半透明）+ html 上的底图（见 `.aoqi-skin-host`）。
 * 拿不到 theme 服务时退化成直接往 `body.style` 写同样的变量，效果一致。
 */
window.__ModuleLoader__.load({
  id: 'dsh-aoqi-pet',
  factory(require) {
    const React = require('react');
    const h = React.createElement;

    const POLL_MS = 1500;
    const SKIN_POLL_MS = 3000;
    const ROUTE = '/api/aoqi-pet';
    const SKIN_SOURCE = 'dsh-aoqi-pet/skin';
    const PANEL_SKINS = 'aoqi-skins';
    const PANEL_FIND = 'aoqi-find';
    const SKIN_NONE = 'default';

    // 五种状态的外观（和桌面桌宠的动画名一一对应）
    const STATES = {
      idle: { label: '待机', color: '#8fa6c4', accent: '#5b6d8a', glyph: '' },
      working: { label: '干活中', color: '#37c7ff', accent: '#1d7ea8', glyph: '⚙' },
      done: { label: '完成', color: '#3ddc84', accent: '#1f8a50', glyph: '★' },
      error: { label: '出错', color: '#ff5c4d', accent: '#a82b21', glyph: '!' },
      waiting: { label: '等你', color: '#ffb020', accent: '#a86f0c', glyph: '…' },
    };

    const CSS = `
.aoqi-dock{display:inline-flex;align-items:center;gap:8px;padding:4px 10px 4px 6px;border-radius:999px;
  border:1px solid var(--dsw-alias-border-secondary,rgba(127,127,127,.28));
  background:var(--dsw-alias-bg-secondary,rgba(127,127,127,.08));
  color:var(--dsw-alias-text-primary,inherit);font-size:12px;line-height:1.2;
  cursor:pointer;user-select:none;transition:background .15s ease;}
.aoqi-dock:hover{background:var(--dsw-alias-bg-tertiary,rgba(127,127,127,.16));}
.aoqi-dock__art{display:block;flex:0 0 auto;}
.aoqi-dock__name{font-weight:600;}
.aoqi-dock__state{color:var(--dsw-alias-text-secondary,currentColor);opacity:.85;}
.aoqi-dock__stats{color:var(--dsw-alias-text-tertiary,currentColor);opacity:.7;font-variant-numeric:tabular-nums;}
.aoqi-bob{animation:aoqi-bob 2.4s ease-in-out infinite;transform-origin:50% 100%;}
.aoqi-bob--fast{animation-duration:.7s;}
.aoqi-shake{animation:aoqi-shake .5s ease-in-out infinite;}
.aoqi-pulse{animation:aoqi-pulse 1.4s ease-in-out infinite;}
@keyframes aoqi-bob{0%,100%{transform:translateY(0)}50%{transform:translateY(-3px)}}
@keyframes aoqi-shake{0%,100%{transform:translateX(0)}25%{transform:translateX(-2px)}75%{transform:translateX(2px)}}
@keyframes aoqi-pulse{0%,100%{opacity:.55}50%{opacity:1}}
@media (prefers-reduced-motion:reduce){.aoqi-bob,.aoqi-shake,.aoqi-pulse{animation:none;}}

/* ── 换肤：底图挂在 <html> 上，面板本身由 token 覆盖层变半透明 ─────────────── */
/* 三层从下往上：base（底色/渐晕，垫在立绘下面）→ image（透明底立绘或场景图）
   → scrim（压暗层，最上面，强度由面板上的滑杆给）。顺序写反会让不透明的 base
   把立绘整个盖住 —— 渲染 harness 里就是这么发现的。 */
html[data-aoqi-skin]{
  background-color:var(--aoqi-skin-fallback,transparent);
  background-image:var(--aoqi-skin-scrim,none),var(--aoqi-skin-image,none),var(--aoqi-skin-base,none);
  background-size:cover,var(--aoqi-skin-size,cover),cover;
  background-position:center,var(--aoqi-skin-position,center),center;
  background-repeat:no-repeat,no-repeat,no-repeat;
  background-attachment:fixed,fixed,fixed;}
html[data-aoqi-skin] body{background-color:transparent;}

/* ── 面板外壳（两个入口共用） ─────────────────────────────────────────────── */
.aoqi-panel{height:100%;min-height:0;display:flex;flex-direction:column;box-sizing:border-box;
  padding:20px 22px;overflow:auto;color:var(--dsw-alias-label-primary,inherit);}
.aoqi-panel__head{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:16px;}
.aoqi-panel__title{margin:0;font-size:18px;font-weight:650;line-height:1.3;}
.aoqi-panel__sub{margin:6px 0 0;font-size:12px;line-height:1.5;color:var(--dsw-alias-label-secondary,currentColor);opacity:.86;}
.aoqi-panel__actions{display:flex;align-items:center;gap:8px;}
.aoqi-btn{appearance:none;font:inherit;font-size:12px;line-height:1;padding:7px 12px;border-radius:8px;cursor:pointer;
  color:var(--dsw-alias-label-primary,inherit);
  border:1px solid var(--dsw-alias-border-l2,rgba(127,127,127,.32));
  background:var(--dsw-alias-bg-layer-1,rgba(127,127,127,.10));}
.aoqi-btn:hover{background:var(--dsw-alias-bg-layer-2,rgba(127,127,127,.18));}
.aoqi-btn--primary{border-color:transparent;background:var(--dsw-alias-brand-primary,#3b82f6);color:#fff;}
.aoqi-note{margin:0 0 14px;font-size:12px;line-height:1.6;color:var(--dsw-alias-label-secondary,currentColor);opacity:.8;}

/* ── 皮肤中心 ────────────────────────────────────────────────────────────── */
.aoqi-skin-grid{display:grid;gap:14px;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));}
.aoqi-skin-card{appearance:none;font:inherit;text-align:left;cursor:pointer;padding:0;overflow:hidden;
  display:flex;flex-direction:column;border-radius:14px;
  border:1px solid var(--dsw-alias-border-l1,rgba(127,127,127,.24));
  background:var(--dsw-alias-bg-layer-1,rgba(127,127,127,.10));
  box-shadow:0 1px 2px rgba(0,0,0,.06);transition:transform .12s ease,border-color .12s ease;}
.aoqi-skin-card:hover{transform:translateY(-2px);}
.aoqi-skin-card[data-active="true"]{border-color:var(--aoqi-card-accent,var(--dsw-alias-brand-primary,#3b82f6));
  box-shadow:0 0 0 1px var(--aoqi-card-accent,var(--dsw-alias-brand-primary,#3b82f6)) inset;}
.aoqi-skin-card__thumb{display:block;height:112px;background-color:var(--dsw-alias-bg-layer-2,rgba(127,127,127,.2));
  background-repeat:no-repeat;border-bottom:1px solid var(--dsw-alias-border-l1,rgba(127,127,127,.2));}
.aoqi-skin-card__meta{display:flex;flex-direction:column;gap:3px;padding:10px 12px 12px;}
.aoqi-skin-card__name{font-size:13px;font-weight:600;display:flex;align-items:center;gap:6px;}
.aoqi-skin-card__dot{width:8px;height:8px;border-radius:50%;background:var(--aoqi-card-accent,#3b82f6);flex:0 0 auto;}
.aoqi-skin-card__sub{font-size:11px;line-height:1.4;color:var(--dsw-alias-label-secondary,currentColor);opacity:.78;}
.aoqi-skin-card__tag{font-size:10px;padding:1px 6px;border-radius:999px;border:1px solid var(--dsw-alias-border-l1,rgba(127,127,127,.3));opacity:.75;}
.aoqi-scrim{display:flex;align-items:center;gap:10px;margin:18px 0 0;font-size:12px;color:var(--dsw-alias-label-secondary,currentColor);}
.aoqi-scrim input[type=range]{width:190px;accent-color:var(--dsw-alias-brand-primary,#3b82f6);}
.aoqi-empty{padding:26px;border-radius:12px;border:1px dashed var(--dsw-alias-border-l2,rgba(127,127,127,.35));
  font-size:12px;line-height:1.7;color:var(--dsw-alias-label-secondary,currentColor);}

/* ── 内置网页（DSH Find） ────────────────────────────────────────────────── */
.aoqi-find{height:100%;min-height:0;display:flex;flex-direction:column;box-sizing:border-box;color:var(--dsw-alias-label-primary,inherit);}
.aoqi-find__bar{display:flex;align-items:center;gap:8px;padding:8px 14px;flex:0 0 auto;
  border-bottom:1px solid var(--dsw-alias-border-l1,rgba(127,127,127,.24));
  background:var(--dsw-alias-bg-layer-1,rgba(127,127,127,.08));}
.aoqi-find__title{font-size:12px;font-weight:600;margin-right:auto;display:flex;align-items:center;gap:7px;}
.aoqi-find__url{font-size:11px;color:var(--dsw-alias-label-secondary,currentColor);opacity:.7;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:38ch;}
.aoqi-find__frame{flex:1 1 auto;width:100%;min-height:0;border:0;background:var(--dsw-alias-bg-base,#fff);}
.aoqi-find__link{font-size:12px;text-decoration:none;color:var(--dsw-alias-brand-primary,#3b82f6);}
`;

    /** 一只用 SVG 画的小宠物（不依赖任何美术素材，也不依赖 DSH 组件库）。 */
    function PetArt({ state, size }) {
      const look = STATES[state] || STATES.idle;
      const motion = state === 'working' ? 'aoqi-bob aoqi-bob--fast'
        : state === 'error' ? 'aoqi-shake'
          : state === 'waiting' ? 'aoqi-pulse' : 'aoqi-bob';
      const children = [
        h('ellipse', { key: 'shadow', cx: 16, cy: 29.3, rx: 9, ry: 1.6, fill: 'rgba(0,0,0,.18)' }),
        h('path', { key: 'ear-l', d: 'M8 12 L10.5 4 L15 12 Z', fill: look.accent, opacity: 0.95 }),
        h('path', { key: 'ear-r', d: 'M17 12 L21.5 4 L24 12 Z', fill: look.accent, opacity: 0.95 }),
        h('ellipse', { key: 'body', cx: 16, cy: 19, rx: 9, ry: 9.6, fill: look.color, stroke: look.accent, strokeWidth: 1.2 }),
        h('ellipse', { key: 'eye-l', cx: 12.8, cy: 18, rx: 1.5, ry: 1.9, fill: '#22304a' }),
        h('ellipse', { key: 'eye-r', cx: 19.2, cy: 18, rx: 1.5, ry: 1.9, fill: '#22304a' }),
        h('circle', { key: 'spark', cx: 12.3, cy: 17.3, r: 0.5, fill: '#fff' }),
        h('circle', { key: 'spark2', cx: 18.7, cy: 17.3, r: 0.5, fill: '#fff' }),
      ];
      if (look.glyph) {
        children.push(h('text', {
          key: 'glyph', x: state === 'done' ? 25 : 16, y: state === 'done' ? 5 : 3.6,
          textAnchor: 'middle', fontSize: state === 'done' ? 9 : 8, fontWeight: 700,
          fill: look.accent,
        }, look.glyph));
      }
      return h('svg', {
        className: motion, width: size, height: size, viewBox: '0 0 32 32',
        'aria-hidden': true, focusable: false,
      }, children);
    }

    /** composer 停靠位里的那只小宠物。 */
    function AoqiPetDock() {
      const [data, setData] = React.useState(null);
      const [failed, setFailed] = React.useState(false);
      const [busy, setBusy] = React.useState(false);

      React.useEffect(() => {
        let alive = true;
        if (typeof fetch !== 'function') return undefined;
        const pull = () => {
          fetch(ROUTE, { headers: { accept: 'application/json' } })
            .then((response) => (response.ok ? response.json() : Promise.reject(new Error(String(response.status)))))
            .then((payload) => { if (alive) { setData(payload); setFailed(false); } })
            .catch(() => { if (alive) setFailed(true); });
        };
        pull();
        const timer = setInterval(pull, POLL_MS);
        return () => { alive = false; clearInterval(timer); };
      }, []);

      const state = data && data.state ? data.state : null;
      const animation = state && STATES[state.animation] ? state.animation : 'idle';
      const look = STATES[animation];
      const name = state && state.petName ? state.petName : '小五王';
      const stats = (state && state.stats) || {};

      const onPoke = () => {
        if (busy || typeof fetch !== 'function') return;
        setBusy(true);
        fetch(`${ROUTE}?action=next-pet`, { method: 'POST' })
          .then((response) => (response.ok ? response.json() : Promise.reject(new Error(String(response.status)))))
          .then((payload) => { if (payload && payload.state) setData(payload); })
          .catch(() => setFailed(true))
          .then(() => setBusy(false));
      };

      const meta = failed
        ? '（宿主状态路由没连上）'
        : `${look.label}${state && state.headline ? ` · ${state.headline}` : ''}`;

      return h('div', {
        className: 'aoqi-dock',
        'data-aoqi-pet': animation,
        role: 'button',
        tabIndex: 0,
        title: `${name} · ${meta}（点一下换一只五王）`,
        onClick: onPoke,
        onKeyDown: (event) => { if (event.key === 'Enter' || event.key === ' ') onPoke(); },
      }, [
        h(PetArt, { key: 'art', state: animation, size: 20 }),
        h('span', { key: 'name', className: 'aoqi-dock__name' }, name),
        h('span', { key: 'state', className: 'aoqi-dock__state' }, ` ${look.label}`),
        h('span', { key: 'stats', className: 'aoqi-dock__stats' },
          ` · 回合${stats.turns || 0} 完成${stats.completions || 0} 续写${stats.autoContinues || 0}`),
      ]);
    }

    /** 给 aoqi_pet_status 工具结果做的宠物化卡片（keyed slot，key = 工具名）。 */
    function AoqiToolRow(props) {
      const result = props && props.result ? props.result : null;
      const text = result && typeof result.text === 'string' ? result.text : '';
      if (text === '') return null;
      const first = text.split('\n').find((line) => line.trim() !== '') || '';
      return h('div', {
        className: 'aoqi-dock', style: { alignItems: 'flex-start', borderRadius: 10 },
        'data-aoqi-tool': 'status',
      }, [
        h(PetArt, { key: 'art', state: 'idle', size: 18 }),
        h('span', { key: 'text', style: { whiteSpace: 'pre-wrap' } }, first),
      ]);
    }

    // ── 界面状态仓（皮肤中心 / 内置网页共用一次轮询） ──────────────────────────
    const uiStore = {
      value: {
        ready: false,
        failed: false,
        skin: { enabled: true, id: SKIN_NONE, scrim: 0.45, catalog: [] },
        find: { enabled: true, title: 'DSH Find', url: 'https://dshfind.com/zh' },
      },
      listeners: new Set(),
      subscribe(listener) {
        this.listeners.add(listener);
        return () => { this.listeners.delete(listener); };
      },
      set(next) {
        this.value = { ...this.value, ...next, ready: true };
        for (const listener of [...this.listeners]) {
          try { listener(this.value); } catch { /* 订阅方自己的问题，不影响别人 */ }
        }
      },
    };

    /** 组件读状态仓的小钩子（不引入任何 DSH 组件库）。 */
    function useUiState() {
      const [value, setValue] = React.useState(uiStore.value);
      React.useEffect(() => uiStore.subscribe(setValue), []);
      return value;
    }

    // ── 换肤引擎 ─────────────────────────────────────────────────────────────
    let skinCtx = null;
    let appliedSkin = null;     // 最近一次真正应用的皮肤定义（null = 默认外观）
    let appliedScrim = 0.45;
    let tokenDispose = null;    // theme 覆盖层的 disposer
    const inlineTokens = new Set();  // 退化路径下我们自己写进 body 的 token

    /** 当前是浅色还是深色。优先问主题服务，其次看 ui-layout / ui-theme 写在 DOM 上的标记。 */
    function currentScheme() {
      try {
        const theme = skinCtx && skinCtx.get ? skinCtx.get('theme') : undefined;
        const snapshot = theme && typeof theme.getTheme === 'function' ? theme.getTheme() : null;
        const scheme = snapshot && snapshot.active ? snapshot.active.colorScheme : null;
        if (scheme === 'light' || scheme === 'dark') return scheme;
      } catch { /* 主题服务没起来就用 DOM 兜底 */ }
      if (typeof document !== 'undefined' && document) {
        const root = document.documentElement;
        const body = document.body;
        // ui-theme 把深色标记写在 body 上（design-platform.css: body[data-ds-dark-theme]）。
        if (body && typeof body.hasAttribute === 'function') {
          if (body.hasAttribute('data-ds-dark-theme')) return 'dark';
          if (body.hasAttribute('data-ds-light-theme')) return 'light';
        }
        if (root && root.style && root.style.colorScheme === 'dark') return 'dark';
        if (root && typeof root.getAttribute === 'function') {
          const marked = root.getAttribute('data-dsw-theme-source');
          if (marked === 'dark') return 'dark';
          if (marked === 'light') return 'light';
        }
        if (typeof matchMedia === 'function' && matchMedia('(prefers-color-scheme: dark)').matches) return 'dark';
      }
      return 'light';
    }

    /** 由「压暗强度」现场拼渐变：0 = 完全不压，1 = 压到最暗。 */
    function scrimGradient(rgb, scrim) {
      const edge = (scrim * 1.0).toFixed(3);
      const mid = (scrim * 0.4).toFixed(3);
      return `linear-gradient(180deg,rgba(${rgb},${edge}),rgba(${rgb},${mid}) 42%,rgba(${rgb},${edge}))`;
    }

    /** 把 token 覆盖层换成皮肤配色。优先官方 ctx.theme.overrideTokens，其次直接写 body。 */
    function applyTokens(tokens, scheme) {
      const body = typeof document !== 'undefined' && document ? document.body : null;
      if (tokenDispose !== null) {
        try { tokenDispose(); } catch { /* 已经被拆掉的层 */ }
        tokenDispose = null;
      }
      if (body && body.style) {
        for (const name of [...inlineTokens]) {
          try { body.style.removeProperty(name); } catch { /* 忽略 */ }
          inlineTokens.delete(name);
        }
      }
      if (tokens === null) return;
      let theme;
      try { theme = skinCtx && skinCtx.get ? skinCtx.get('theme') : undefined; } catch { theme = undefined; }
      if (theme && typeof theme.overrideTokens === 'function') {
        try {
          tokenDispose = theme.overrideTokens(SKIN_SOURCE, tokens);
          return;
        } catch { /* 服务形状变了就走退化路径 */ }
      }
      if (!body || !body.style) return;
      for (const [name, modes] of Object.entries(tokens)) {
        const value = modes && (modes[scheme] || modes.light || modes.dark);
        if (typeof value !== 'string') continue;
        try { body.style.setProperty(name, value); inlineTokens.add(name); } catch { /* 忽略 */ }
      }
    }

    /**
     * 换上一张皮肤（或 `null` 恢复默认）。
     * 一次调用同时管三件事：html 上的底图变量、token 覆盖层、以及状态仓里的当前值。
     */
    function applySkin(skin, scrim) {
      if (typeof document === 'undefined' || !document || !document.documentElement) return;
      const root = document.documentElement;
      const scheme = currentScheme();
      appliedSkin = skin || null;
      appliedScrim = typeof scrim === 'number' ? scrim : appliedScrim;

      if (!skin) {
        if (typeof root.removeAttribute === 'function') root.removeAttribute('data-aoqi-skin');
        for (const name of ['--aoqi-skin-image', '--aoqi-skin-base', '--aoqi-skin-scrim', '--aoqi-skin-size', '--aoqi-skin-position', '--aoqi-skin-fallback']) {
          try { root.style.removeProperty(name); } catch { /* 忽略 */ }
        }
        applyTokens(null, scheme);
        return;
      }

      const setVar = (name, value) => {
        try { root.style.setProperty(name, value); } catch { /* 忽略 */ }
      };
      const base = (skin.base && skin.base[scheme]) || skin.base && skin.base.light || 'none';
      const fallback = (skin.fallbackColor && skin.fallbackColor[scheme]) || 'transparent';
      const rgb = (skin.scrimRgb && skin.scrimRgb[scheme]) || (scheme === 'dark' ? '3,6,18' : '255,255,255');
      setVar('--aoqi-skin-image', `url("${skin.image}")`);
      setVar('--aoqi-skin-base', base);
      setVar('--aoqi-skin-scrim', scrimGradient(rgb, appliedScrim));
      setVar('--aoqi-skin-size', skin.imageSize || 'cover');
      setVar('--aoqi-skin-position', skin.imagePosition || 'center');
      setVar('--aoqi-skin-fallback', fallback);
      if (typeof root.setAttribute === 'function') root.setAttribute('data-aoqi-skin', skin.id);
      applyTokens(skin.tokens || null, scheme);
    }

    /** 从 `GET /api/aoqi-pet` 的 payload 里取出换肤/网页那段，并落到状态仓。 */
    function adoptPayload(payload) {
      const skin = payload && payload.skin ? payload.skin : null;
      const find = payload && payload.find ? payload.find : null;
      const catalog = skin && Array.isArray(skin.catalog) ? skin.catalog : [];
      const id = skin && typeof skin.id === 'string' ? skin.id : SKIN_NONE;
      const scrim = skin && typeof skin.scrim === 'number' ? skin.scrim : 0.45;
      const enabled = !skin || skin.enabled !== false;
      uiStore.set({
        failed: false,
        skin: { enabled, id: enabled ? id : SKIN_NONE, scrim, catalog },
        find: find
          ? { enabled: find.enabled !== false, title: find.title || 'DSH Find', url: find.url || '' }
          : uiStore.value.find,
      });
      const next = enabled ? catalog.find((entry) => entry.id === id) || null : null;
      const same = (appliedSkin && next && appliedSkin.id === next.id) || (appliedSkin === null && next === null);
      if (same && appliedScrim === scrim) {
        // 首帧可能主题服务还没起来、走了「直接写 body」的降级路径。
        // 轮询发现服务上线了就升级成官方的分层覆盖（tokenDispose 非空 = 走的官方接口）。
        if (next !== null && tokenDispose === null) applySkin(next, scrim);
        return;
      }
      applySkin(next, scrim);
    }

    /** 轮询宿主路由：既喂状态仓，也负责「重启后自动恢复上次的皮肤」。 */
    function startSkinEngine(ctx) {
      skinCtx = ctx;
      const pull = () => {
        if (typeof fetch !== 'function') return;
        fetch(ROUTE, { headers: { accept: 'application/json' } })
          .then((response) => (response.ok ? response.json() : Promise.reject(new Error(String(response.status)))))
          .then((payload) => adoptPayload(payload))
          .catch(() => uiStore.set({ failed: true }));
      };
      ctx.effect(() => {
        pull();
        const timer = setInterval(pull, SKIN_POLL_MS);
        // 浏览器里没有 unref；Node 下的契约测试（假 __ModuleLoader__）靠它才能自然退出。
        if (timer && typeof timer.unref === 'function') timer.unref();
        return () => clearInterval(timer);
      }, 'aoqi-pet: skin engine');
      // 明暗切换时，皮肤的底色/压暗层要跟着换一套。
      if (typeof ctx.on === 'function') {
        ctx.on('theme/change', () => {
          if (appliedSkin === null) return;
          applySkin(appliedSkin, appliedScrim);
        });
      }
    }

    /** 供面板使用：立刻换肤（不等下一次轮询），并写回宿主。 */
    function pickSkin(id, scrim) {
      const catalog = uiStore.value.skin.catalog || [];
      const next = id === SKIN_NONE ? null : catalog.find((entry) => entry.id === id) || null;
      const nextScrim = typeof scrim === 'number' ? scrim : uiStore.value.skin.scrim;
      uiStore.set({ skin: { ...uiStore.value.skin, id: next ? next.id : SKIN_NONE, scrim: nextScrim } });
      applySkin(next, nextScrim);
      if (typeof fetch === 'function') {
        const query = `action=set-skin&id=${encodeURIComponent(next ? next.id : SKIN_NONE)}&scrim=${encodeURIComponent(String(nextScrim))}`;
        fetch(`${ROUTE}?${query}`, { method: 'POST', headers: { accept: 'application/json' } }).catch(() => { /* 宿主没连上就只在本页生效 */ });
      }
    }

    // ── 侧栏入口图标（`sidebar.panellist` 渲染的就是这个，规格 16/18px） ────────
    function SkinGlyph({ size }) {
      const s = typeof size === 'number' ? size : 18;
      return h('svg', { width: s, height: s, viewBox: '0 0 24 24', fill: 'none', 'aria-hidden': true, focusable: false }, [
        h('circle', { key: 'sun', cx: 12, cy: 12, r: 6.4, fill: 'currentColor', fillOpacity: 0.85 }),
        h('path', { key: 'ring', d: 'M12 2.6v2M12 19.4v2M2.6 12h2M19.4 12h2M5.4 5.4l1.4 1.4M17.2 17.2l1.4 1.4M18.6 5.4l-1.4 1.4M6.8 17.2l-1.4 1.4', stroke: 'currentColor', strokeWidth: 1.6, strokeLinecap: 'round' }),
      ]);
    }

    function FindGlyph({ size }) {
      const s = typeof size === 'number' ? size : 18;
      return h('svg', { width: s, height: s, viewBox: '0 0 24 24', fill: 'none', 'aria-hidden': true, focusable: false }, [
        h('circle', { key: 'globe', cx: 12, cy: 12, r: 9, stroke: 'currentColor', strokeWidth: 1.6 }),
        h('path', { key: 'x', d: 'M3 12h18', stroke: 'currentColor', strokeWidth: 1.4 }),
        h('path', { key: 'ellipse', d: 'M12 3c2.6 2.6 3.9 5.6 3.9 9s-1.3 6.4-3.9 9c-2.6-2.6-3.9-5.6-3.9-9S9.4 5.6 12 3Z', stroke: 'currentColor', strokeWidth: 1.4 }),
      ]);
    }

    // ── 皮肤中心面板 ─────────────────────────────────────────────────────────
    function SkinPanel() {
      const state = useUiState();
      const skin = state.skin;
      const catalog = skin.catalog || [];
      const active = skin.id;
      const post = (url) => { if (typeof fetch === 'function') fetch(url, { method: 'POST' }).catch(() => {}); };

      const onScrim = (event) => {
        const value = Number(event && event.target ? event.target.value : NaN);
        if (!Number.isFinite(value)) return;
        const next = Math.min(1, Math.max(0, value));
        uiStore.set({ skin: { ...skin, scrim: next } });
        if (appliedSkin) applySkin(appliedSkin, next);
        post(`${ROUTE}?action=set-skin&id=${encodeURIComponent(active)}&scrim=${encodeURIComponent(String(next))}`);
      };

      return h('div', { className: 'aoqi-panel', 'data-aoqi-panel': 'skins' }, [
        h('div', { key: 'head', className: 'aoqi-panel__head' }, [
          h('div', { key: 'text' }, [
            h('h2', { key: 'title', className: 'aoqi-panel__title' }, '奥奇皮肤中心'),
            h('p', { key: 'sub', className: 'aoqi-panel__sub' },
              '底图 + 半透明配色一起换，和传说五王共用一份主题。选择会存进 ~/.dsh/aoqi-pet/skins.json，重开界面还在。'),
          ]),
          h('div', { key: 'actions', className: 'aoqi-panel__actions' }, [
            h('button', {
              key: 'reset', type: 'button', className: 'aoqi-btn',
              onClick: () => pickSkin(SKIN_NONE),
            }, '恢复默认外观'),
          ]),
        ]),
        state.failed
          ? h('p', { key: 'warn', className: 'aoqi-note' }, '宿主状态路由没连上：下面显示的是缓存的目录，换肤可能只在本页生效。')
          : null,
        catalog.length === 0
          ? h('div', { key: 'empty', className: 'aoqi-empty' },
            '这台机器上还没有可用素材。官方场景图需要 assets/theme/，五王立绘需要 assets/pets/<id>/portrait.png；' +
            '官网抓图（assets/fetched/）因为版权不入库，只在本地存在时才会出现在这里。')
          : h('div', { key: 'grid', className: 'aoqi-skin-grid' }, catalog.map((entry) => h('button', {
            key: entry.id,
            type: 'button',
            className: 'aoqi-skin-card',
            'data-skin': entry.id,
            'data-active': entry.id === active ? 'true' : 'false',
            style: { '--aoqi-card-accent': entry.accent },
            title: `换上「${entry.name}」`,
            onClick: () => pickSkin(entry.id),
          }, [
            h('span', {
              key: 'thumb', className: 'aoqi-skin-card__thumb',
              style: {
                backgroundImage: `url("${entry.image}")`,
                backgroundSize: entry.kind === 'king' ? 'auto 108%' : 'cover',
                backgroundPosition: entry.kind === 'king' ? 'center bottom' : 'center',
              },
            }),
            h('span', { key: 'meta', className: 'aoqi-skin-card__meta' }, [
              h('span', { key: 'name', className: 'aoqi-skin-card__name' }, [
                h('span', { key: 'dot', className: 'aoqi-skin-card__dot' }),
                entry.name,
                entry.optional ? h('span', { key: 'tag', className: 'aoqi-skin-card__tag' }, '本机') : null,
                entry.id === active ? h('span', { key: 'tag2', className: 'aoqi-skin-card__tag' }, '使用中') : null,
              ]),
              h('span', { key: 'sub', className: 'aoqi-skin-card__sub' }, entry.subtitle),
            ]),
          ]))),
        h('label', { key: 'scrim', className: 'aoqi-scrim' }, [
          h('span', { key: 'label' }, `压暗层 ${Math.round((skin.scrim || 0) * 100)}%`),
          h('input', {
            key: 'range', type: 'range', min: 0, max: 1, step: 0.05,
            value: skin.scrim, disabled: active === SKIN_NONE,
            onChange: onScrim,
          }),
        ]),
      ]);
    }

    // ── 内置网页面板（DSH Find） ─────────────────────────────────────────────
    function FindPanel() {
      const state = useUiState();
      const find = state.find || {};
      const [nonce, setNonce] = React.useState(0);
      const url = find.url || '';
      const title = find.title || 'DSH Find';
      const open = () => { if (url !== '' && typeof window !== 'undefined' && window.open) window.open(url, '_blank', 'noopener'); };

      return h('div', { className: 'aoqi-find', 'data-aoqi-panel': 'find' }, [
        h('div', { key: 'bar', className: 'aoqi-find__bar' }, [
          h('span', { key: 'title', className: 'aoqi-find__title' }, [h(FindGlyph, { key: 'g', size: 16 }), title]),
          h('span', { key: 'url', className: 'aoqi-find__url', title: url }, url),
          h('button', { key: 'reload', type: 'button', className: 'aoqi-btn', onClick: () => setNonce((value) => value + 1) }, '刷新'),
          h('a', {
            key: 'open', className: 'aoqi-find__link', href: url || '#', target: '_blank',
            rel: 'noreferrer noopener', onClick: url === '' ? (event) => event.preventDefault() : undefined,
          }, '在新标签页打开'),
        ]),
        find.enabled === false
          ? h('div', { key: 'off', className: 'aoqi-empty', style: { margin: '20px' } }, '内置网页已在宿主配置里关闭（config.find.enabled = false）。')
          : h('iframe', {
            key: `frame-${nonce}`,
            className: 'aoqi-find__frame',
            src: url,
            title,
            loading: 'eager',
            referrerPolicy: 'no-referrer-when-downgrade',
            sandbox: 'allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox allow-downloads',
          }),
      ]);
    }

    return {
      inject: ['slots'],
      apply(ctx) {
        // 样式表属于「资源」，必须在 ctx.effect 里注册并返回清理函数（官方 practices.md 要求）
        ctx.effect(() => {
          const style = document.createElement('style');
          style.setAttribute('data-aoqi-pet', 'styles');
          style.textContent = CSS;
          document.head.appendChild(style);
          return () => style.remove();
        }, 'aoqi-pet: dock styles');

        // 主挂载点：输入框旁边的停靠位（list slot → 需要 id 与展示序 order）
        ctx.slots.inject('conversation.composer.dock', () => ctx.slots.register({
          name: 'conversation.composer.dock',
          id: 'aoqi-pet',
          order: 50,
        }, AoqiPetDock));

        // 附加：把 aoqi_pet_status 的结果渲染成同样的宠物卡片（keyed slot → 需要 key）
        ctx.slots.inject('tool.call.toolview', () => ctx.slots.register({
          name: 'tool.call.toolview',
          key: 'aoqi_pet_status',
        }, AoqiToolRow));

        // 换肤引擎：常驻轮询，负责「打开界面就有皮肤」以及明暗切换时重画。
        startSkinEngine(ctx);

        // 左侧栏入口一：奥奇皮肤中心（和「插件」「自动化任务」同一列）
        ctx.slots.inject('sidebar.panellist', () => ctx.slots.register({
          name: 'sidebar.panellist',
          id: PANEL_SKINS,
          order: 60,
          label: '奥奇换肤',
        }, SkinGlyph));
        ctx.slots.inject('main', () => ctx.slots.register({
          name: 'main',
          key: PANEL_SKINS,
        }, SkinPanel));

        // 左侧栏入口二：内置网页 dshfind.com（点开即在主区域打开）
        ctx.slots.inject('sidebar.panellist', () => ctx.slots.register({
          name: 'sidebar.panellist',
          id: PANEL_FIND,
          order: 61,
          label: 'DSH Find',
        }, FindGlyph));
        ctx.slots.inject('main', () => ctx.slots.register({
          name: 'main',
          key: PANEL_FIND,
        }, FindPanel));
      },
    };
  },
});
