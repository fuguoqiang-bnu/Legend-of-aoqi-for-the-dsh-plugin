/**
 * 奥奇桌宠 · 客户端面（DSH 界面内的那一半）。
 *
 * 这是**浏览器 bundle**，不是 ESM：格式必须是 `window.__ModuleLoader__.load({id, factory})`，
 * id 必须等于 package.json 的 name，文件执行时**零副作用**（所有副作用都放进 factory 闭包，
 * 官方模板 templates/decoration/client.js 就是这么写的）。React 由宿主的平台模块表提供，
 * 只允许 `require('react')`，不要去 require 任何 @deepseek-ai/dsh-client-* 包。
 *
 * 挂载点：`conversation.composer.dock`（list / session，官方 ui-plugin.md 点名推荐）。
 * 数据源：宿主插件已经在 webServer 上注册的 `GET /api/aoqi-pet` —— 和桌面那只桌宠
 * **同一份状态**（同一个 state.json），所以应用里和桌面上的表现永远一致。
 * 点一下它会 POST `?action=next-pet` 轮换五王，再点状态就会跟着变。
 */
window.__ModuleLoader__.load({
  id: 'dsh-aoqi-pet',
  factory(require) {
    const React = require('react');
    const h = React.createElement;

    const POLL_MS = 1500;
    const ROUTE = '/api/aoqi-pet';

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
      },
    };
  },
});
