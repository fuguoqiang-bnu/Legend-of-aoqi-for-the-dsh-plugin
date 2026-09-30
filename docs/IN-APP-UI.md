# 让宠物进入 DSH 界面（客户端面 / Client Face）

用户原话：「没有变动 DSH 软件内部的界面，没有参与进来。」
这一节就是那件事：在**输入框旁边**放一只跟着状态变的小宠物，点一下能换五王。

## 1. 为什么能这么做（都是 asar 里的原文，不是猜的）

DSH 有一套官方文档化的第三方客户端插件体系，`dsh-agent-preset` 里还自带模板
`skills/cordis-plugin-development/templates/decoration/`。关键结论：

| 结论 | 证据 |
|---|---|
| 客户端面由 `package.json` 的 `dsh.client` 声明 | `@deepseek-ai/dsh-client-modules/lib/index.js:64-72` |
| 浏览器入口 = `exports["./client"]` 指向的文件 | 同上 `:171-181` |
| `dsh.client.platform` 必须是 `"web"`，否则**整包被忽略** | 同上 `:714` |
| 客户端文件**不是 ESM**，是 `window.__ModuleLoader__.load({id, factory})` 懒加载包装 | `dsh-client-ui-brand-official/lib/client.js:1-3` |
| 导出 `{ apply(ctx), inject }`（cordis 插件形状） | 同上 `:42-44` |
| bundle 由宿主用 `/plugins/...` 路由 serve（不是 vite） | `dsh-client-modules/lib/index.js:201` |
| **本地路径挂载**也能有客户端面（只要目录里有带 `dsh.client` 的 package.json） | 同上 `:743-790` |
| 必须在启动前把 `lib/client.js` 落盘，否则激活期抛 `MissingClientBundleError` | 同上 `:132-146` |
| 平台只提供 9 个模块：`react` / `react/jsx-runtime` / `react-dom` / `react-dom/client` / `@deepseek-ai/cordis` / `dsh-client-store` / `dsh-client-ui-slots` / `dsh-client-ui-primitives` / `dsh-client-ui-dockkit` | `dsh-web-frontend/dist/assets/index-5SrrfWpU.js` @628285 |
| 可用 slot 共 90 个；我们选了官方点名的 `conversation.composer.dock`（list/session） | `dsh-client-ui-conversation/lib/client.js:18329` |

⇒ **不需要 npm 发布、不需要构建工具、零依赖**：一个手写的 `lib/client.js` 就行。
宿主面（`lib/index.js`）一行都不用为了客户端面改动 —— 官方 `dsh-client-ui-brand-official` 的宿主半边就是空壳。

## 2. 我们做了什么

### 2.1 `lib/client.js`（浏览器 bundle）

* 格式：`window.__ModuleLoader__.load({ id: 'dsh-aoqi-pet', factory(require) {...} })`，
  **文件执行时零副作用**（样式表也是在 `apply` 里用 `ctx.effect` 注册、并返回清理函数）。
* 只 `require('react')`，不 require 任何 `@deepseek-ai/dsh-client-*`（官方 practices.md 明确要求）。
* 注册两处：
  * `conversation.composer.dock`（**list** slot ⇒ 必须给 `id` 与 `order`）→ 输入框旁的圆角小条：
    一只 SVG 小宠物 + 宠物名 + 状态 + 「回合/完成/续写」统计。
  * `tool.call.toolview`（**keyed** slot ⇒ 必须给 `key`）→ 把 `aoqi_pet_status` 的结果渲染成同样的宠物卡片。
* 视觉只用主题 token（`--dsw-alias-*`），所以跟随明暗主题，不写死颜色。
* 动画用 CSS keyframes（`prefers-reduced-motion` 下自动关闭）。

### 2.2 状态来源：和桌面那只桌宠**同一份**

客户端不自己造状态，它轮询宿主插件已经注册的路由（每 1.5s）：

```
GET  /api/aoqi-pet                     → { ok, state, pets, files }
POST /api/aoqi-pet?action=next-pet     → 轮换下一只五王（写 companion-settings.json，桌面桌宠跟着换）
POST /api/aoqi-pet?action=poke         → 桌宠冒泡「我在这儿！」
```

所以「应用内的小宠物」和「屏幕上的桌宠」永远一致：点一下应用内的小条，桌面那只真的会换人。
（`state` 就是宿主写进 `~/.dsh/aoqi-pet/state.json` 的那一份：动画、气泡、统计、自动续写状态。）

## 3. 怎么看到它

1. 安装本插件（`dsh plugin add github:<你>/dsh-aoqi-pet`，或把目录挂进 profile）。
2. **重启 DSH**（客户端 bundle 在启动时被 snapshot；页面刷新**不会**重读磁盘上的 bundle）。
3. 打开任意会话 → 输入框旁边就有那只小宠物。点它换五王。

## 4. 验证到什么程度（诚实分界）

**已经用自动化验证的**（`node test/client-ui.mjs`，34 项全过，不需要 DSH/浏览器/构建工具）：

* `dsh.client.platform === "web"`、`exports["./client"]` 指向真实文件；
* bundle 不是 ESM、无顶层副作用、只 require 平台模块、不 require 业务组件包；
* 在假 `__ModuleLoader__` + 假 React + 假 `ctx.slots` 上真跑一遍：注册的 `id` 等于包名、
  `factory` 返回 `{inject:['slots'], apply}`、两个 slot 的注册参数形状正确（list 有 id/order、keyed 有 key）、
  样式作为资源注册且清理函数真的删掉 DOM 节点；
* 组件能渲染出宠物名/状态/统计，拉到状态后更新，`onClick` 走 `POST ...?action=next-pet`；
* 工具卡片拿到文本渲染第一行、拿不到结果时返回 `null`（不破坏别人的卡片）。

**配套的宿主侧验证**（`node test/smoke.mjs`，53 项）：`GET` 返回 `state/pets/files` 与 `no-store`、
`POST next-pet` 真的轮换并写设置文件（`an → mu`）、`POST poke` 冒泡、未知 action 返回 400 不崩。

**还没验证的（写清楚，不含糊）**：

* **界面里真的出现的样子**：需要 DSH 重启加载 bundle，我没法在不重启的前提下截图核实 ——
  落地后请自己看一眼（输入框旁边）。
* **第三方场景下改 `lib/client.js` 靠「重装」还是「重启」生效**：官方 HMR 的 watcher 由 DSH 源码
  checkout 的 `pnpm run dev:web` 提供，第三方仓库没有这个脚本，**未确证**。稳妥做法：改完重启 DSH。
* **`conversation.composer.dock` 的真实 props 契约**没读到（文档标了未确证），所以组件**不依赖任何 props**，
  只靠自己 fetch —— 即使宿主传了新字段也不会崩。
* 若宿主版本里没有 `conversation.composer.dock` 或 `tool.call.toolview` 声明，
  往未声明的 slot 注册会在加载期抛错；那时应当把对应那一段 `ctx.slots.inject(...)` 注释掉（两处互不影响）。
