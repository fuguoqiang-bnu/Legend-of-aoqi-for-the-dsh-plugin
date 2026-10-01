/**
 * 奥奇皮肤目录（host 侧，零依赖）。
 *
 * 一次「换肤」= 三件事，全部由这份目录描述、由客户端面执行：
 *   1. **底图** —— 官方场景图，或五王的高清立绘（透明 PNG，贴在右侧）；
 *   2. **底色/压暗层** —— 保证正文在任何底图上都读得清；
 *   3. **token 覆盖** —— 把 DSH 自己的 `--dsw-alias-*` 换成半透明的奥奇配色，
 *      于是侧栏、输入框、卡片一起变成「玻璃板」，底图才透得出来。
 *
 * 素材分两档，这是刻意的：
 *   * `assets/theme/` 与五王立绘 `assets/pets/<id>/portrait.png` —— **随仓库分发**，人人有；
 *   * `assets/fetched/**` —— 官网抓来的图，`.gitignore` 明确不入库（版权），
 *     只在**本机存在**时才出现在目录里（`optional: true` + 存在性过滤）。
 * 所以克隆一份干净仓库，皮肤中心少几张场景图，但功能与其余皮肤完全正常。
 *
 * 本文件不碰 fs：`buildSkinCatalog({ available })` 的存在性判断由调用方注入，
 * 纯函数部分因此可以直接单测（test/smoke.mjs）。
 */

/** 底图怎么摆：`cover` 满铺（场景图），或右侧立绘（五王）。 */
const SCENE = { size: 'cover', position: 'center' }
const KING = { size: 'auto 86%', position: 'right bottom' }

/** 两套配色（浅色 / 深色）下，玻璃面板的通用 token。accent 由每张皮肤给出。 */
function palette(accent) {
  return {
    light: {
      '--dsw-alias-bg-base': 'rgba(255,255,255,.52)',
      '--dsw-alias-bg-layer-1': 'rgba(255,255,255,.66)',
      '--dsw-alias-bg-layer-2': 'rgba(255,255,255,.78)',
      '--dsw-alias-bg-overlay': 'rgba(255,255,255,.94)',
      '--dsw-alias-border-l1': 'rgba(20,30,60,.14)',
      '--dsw-alias-border-l2': 'rgba(20,30,60,.24)',
      '--dsw-specific-sidebar-fill': 'rgba(255,255,255,.50)',
      '--dsw-alias-label-primary': '#101a2e',
      '--dsw-alias-label-secondary': 'rgba(16,26,46,.74)',
      '--dsw-alias-brand-primary': accent.light
    },
    dark: {
      '--dsw-alias-bg-base': 'rgba(7,11,26,.55)',
      '--dsw-alias-bg-layer-1': 'rgba(13,19,42,.64)',
      '--dsw-alias-bg-layer-2': 'rgba(18,26,54,.72)',
      '--dsw-alias-bg-overlay': 'rgba(9,13,30,.92)',
      '--dsw-alias-border-l1': 'rgba(255,255,255,.16)',
      '--dsw-alias-border-l2': 'rgba(255,255,255,.26)',
      '--dsw-specific-sidebar-fill': 'rgba(7,12,30,.52)',
      '--dsw-alias-label-primary': '#eef3ff',
      '--dsw-alias-label-secondary': 'rgba(238,243,255,.78)',
      '--dsw-alias-brand-primary': accent.dark
    }
  }
}

/**
 * 正文可读性压暗层：只给 RGB，渐变与透明度由客户端面按「压暗强度」现场拼
 * （`linear-gradient` 没法在 CSS 里乘系数，所以强度必须是数据而不是写死的字符串）。
 */
const SCRIM_RGB = { light: '255,255,255', dark: '3,6,18' }

/** 五王专属底色的渐层（立绘是透明 PNG，靠它撑起画面）。 */
function kingBase(top, bottom) {
  return {
    light: `radial-gradient(120% 80% at 78% 88%,${top.light} 0%,${bottom.light} 58%,#ffffff 100%)`,
    dark: `radial-gradient(120% 80% at 78% 88%,${top.dark} 0%,${bottom.dark} 58%,#04060f 100%)`
  }
}

/**
 * 皮肤目录。`source` 是相对仓库根的素材路径；`optional: true` 表示不入库、
 * 本机没有就自动从目录里消失（见文件头说明）。
 */
export const SKINS = [
  {
    id: 'aurora',
    name: '极光苍穹',
    subtitle: '官方极光天幕 · 满铺场景',
    kind: 'scene',
    accent: '#5ec8ff',
    source: 'assets/theme/aurora-sky-raw.png',
    ...SCENE
  },
  {
    id: 'stage',
    name: '决战之台',
    subtitle: '官方舞台台面 · 满铺场景',
    kind: 'scene',
    accent: '#ffb03a',
    source: 'assets/theme/stage-platform-raw.png',
    ...SCENE
  },
  {
    id: 'huo',
    name: '烈焰 · 龙炎',
    subtitle: '传说王者立绘 · 火',
    kind: 'king',
    accent: '#ff7a4d',
    source: 'assets/pets/huo/portrait.png',
    base: kingBase({ light: '#ffe2cf', dark: '#3a1206' }, { light: '#fff6f0', dark: '#150608' }),
    ...KING
  },
  {
    id: 'jin',
    name: '超能 · 诺亚',
    subtitle: '传说王者立绘 · 超能',
    kind: 'king',
    accent: '#f2b93c',
    source: 'assets/pets/jin/portrait.png',
    base: kingBase({ light: '#ffeec2', dark: '#3a2a04' }, { light: '#fffaf0', dark: '#161003' }),
    ...KING
  },
  {
    id: 'shui',
    name: '光辉 · 帝释天',
    subtitle: '传说王者立绘 · 光',
    kind: 'king',
    accent: '#5fb8ff',
    source: 'assets/pets/shui/portrait.png',
    base: kingBase({ light: '#d7ecff', dark: '#062036' }, { light: '#f3f9ff', dark: '#030d1b' }),
    ...KING
  },
  {
    id: 'an',
    name: '恶魔 · 修尔',
    subtitle: '传说王者立绘 · 恶魔',
    kind: 'king',
    accent: '#9aa0ff',
    source: 'assets/pets/an/portrait.png',
    base: kingBase({ light: '#e2e3ff', dark: '#141340' }, { light: '#f6f6ff', dark: '#07061a' }),
    ...KING
  },
  {
    id: 'mu',
    name: '翠灵 · 阿瑞斯',
    subtitle: '传说王者立绘 · 草',
    kind: 'king',
    accent: '#5fd07f',
    source: 'assets/pets/mu/portrait.png',
    base: kingBase({ light: '#d9f6e0', dark: '#062914' }, { light: '#f4fff7', dark: '#03130a' }),
    ...KING
  },
  {
    id: 'xinshou',
    name: '新手村 · 晨曦',
    subtitle: '官网 KV 原图（本机素材，不入库）',
    kind: 'scene',
    accent: '#ffc861',
    source: 'assets/fetched/resource.a0bi.com/aoqi-site-kv-xinshou.jpg',
    optional: true,
    ...SCENE
  },
  {
    id: 'tujian',
    name: '精灵图鉴',
    subtitle: '官网图鉴底图（本机素材，不入库）',
    kind: 'scene',
    accent: '#7fc4ff',
    source: 'assets/fetched/resource.a0bi.com/aoqi-site-tujian-bg.jpg',
    optional: true,
    ...SCENE
  },
  {
    id: 'jingling',
    name: '精灵之国',
    subtitle: '官网精灵页底图（本机素材，不入库）',
    kind: 'scene',
    accent: '#7fe0b0',
    source: 'assets/fetched/resource.a0bi.com/aoqi-site-jingling-bg.jpg',
    optional: true,
    ...SCENE
  },
  {
    id: 'zhenxing',
    name: '星阵战场',
    subtitle: '官网星阵分享图（本机素材，不入库）',
    kind: 'scene',
    accent: '#b98cff',
    source: 'assets/fetched/resource.a0bi.com/aoqi-site-zhenxing-share.jpg',
    optional: true,
    ...SCENE
  },
  {
    id: 'lite',
    name: '轻简奥奇',
    subtitle: '官网轻量底图（本机素材，不入库）',
    kind: 'scene',
    accent: '#8fd0ff',
    source: 'assets/fetched/resource.a0bi.com/aoqi-site-lite-bg.jpg',
    optional: true,
    ...SCENE
  }
]

/** 关闭皮肤时用的「无」条目 id。 */
export const SKIN_NONE = 'default'

/** 素材扩展名 → Content-Type。只认这几种，别的当作不可服务。 */
export const MIME = {
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.webp': 'image/webp',
  '.gif': 'image/gif',
  '.svg': 'image/svg+xml'
}

/** 皮肤 id 归一化：认不出就用 fallback（默认皮肤关不掉）。 */
export function normalizeSkinId(value, fallback = SKIN_NONE) {
  if (typeof value !== 'string') return fallback
  const key = value.trim().toLowerCase()
  if (key === '' || key === SKIN_NONE || key === 'none' || key === 'off') return SKIN_NONE
  return SKINS.some((skin) => skin.id === key) ? key : fallback
}

/** 相对路径 → 扩展名（小写）。 */
export function extensionOf(relativePath) {
  const index = String(relativePath).lastIndexOf('.')
  return index < 0 ? '' : String(relativePath).slice(index).toLowerCase()
}

/**
 * 组装给客户端面的目录。
 *
 * @param {object} [options]
 * @param {(relativePath: string) => boolean} [options.available] 素材是否存在（默认全当存在）
 * @param {string} [options.baseUrl] 素材路由前缀（默认 `/api/aoqi-pet/skin`）
 * @returns {Array<object>} JSON 安全的皮肤数组；`optional` 但缺素材的条目会被剔除
 */
export function buildSkinCatalog(options = {}) {
  const available = typeof options.available === 'function' ? options.available : () => true
  const baseUrl = options.baseUrl ?? '/api/aoqi-pet/skin'
  const catalog = []
  for (const skin of SKINS) {
    const extension = extensionOf(skin.source)
    if (MIME[extension] === undefined) continue
    if (skin.optional === true && !available(skin.source)) continue
    if (!available(skin.source)) continue
    // 官方 `overrideTokens` 要的是 `{ token: { light, dark } }`，而 palette() 按色系分组，
    // 所以这里转置一次 —— 写反了客户端拿到的就不是成对值，官方校验会直接抛。
    const byScheme = palette({ light: skin.accent, dark: skin.accent })
    const tokens = {}
    for (const [name, value] of Object.entries(byScheme.light)) {
      tokens[name] = { light: value, dark: byScheme.dark[name] }
    }
    catalog.push({
      id: skin.id,
      name: skin.name,
      subtitle: skin.subtitle,
      kind: skin.kind,
      accent: skin.accent,
      optional: skin.optional === true,
      image: `${baseUrl}/${skin.id}`,
      imageSize: skin.size,
      imagePosition: skin.position,
      // `base` 必须是合法的 <image> 层：场景图自己铺满，所以默认 `none`（不是颜色）。
      base: skin.base ?? { light: 'none', dark: 'none' },
      fallbackColor: { light: '#eef3fb', dark: '#070b18' },
      scrimRgb: SCRIM_RGB,
      tokens
    })
  }
  return catalog
}

/** 按 id 找皮肤定义（原始定义，带 `source`）。 */
export function findSkin(id) {
  const key = typeof id === 'string' ? id.trim().toLowerCase() : ''
  return SKINS.find((skin) => skin.id === key) ?? null
}

/**
 * 解析一个皮肤的素材请求。
 *
 * @param {string} id 皮肤 id
 * @param {string} [variant] `''`（底图）或 `thumb`
 * @returns {{ ok: true, source: string, mime: string } | { ok: false, code: number, error: string }}
 */
export function resolveSkinAsset(id, variant = '') {
  const skin = findSkin(id)
  if (skin === null) return { ok: false, code: 404, error: `未知皮肤：${id}` }
  const source = variant === 'thumb' && typeof skin.thumb === 'string' ? skin.thumb : skin.source
  const mime = MIME[extensionOf(source)]
  if (mime === undefined) return { ok: false, code: 415, error: `不支持的素材类型：${extensionOf(source)}` }
  return { ok: true, source, mime }
}

/**
 * 把皮肤 id 前面的可选前缀（如 `/api/aoqi-pet/skin/`）剥掉，返回 `{ id, variant }`。
 * 路径写错、带查询串、想穿越目录，一律返回 null —— 路由按「认不出就 404」处理。
 *
 * @param {string} pathname 请求路径
 * @param {string} [prefix] 前缀（默认 `/api/aoqi-pet/skin`）
 */
export function parseSkinPath(pathname, prefix = '/api/aoqi-pet/skin') {
  const text = String(pathname ?? '')
  if (!text.startsWith(prefix)) return null
  const rest = text.slice(prefix.length)
  // `/api/aoqi-pet/skins` 也以 `/api/aoqi-pet/skin` 开头，所以下一个字符必须是分隔符。
  if (!rest.startsWith('/')) return null
  const tail = rest.replace(/^\/+/, '')
  if (tail === '') return null
  const parts = tail.split('/').filter((part) => part !== '')
  if (parts.length === 0 || parts.length > 2) return null
  const id = decodeURIComponent(parts[0])
  const variant = parts.length === 2 ? decodeURIComponent(parts[1]) : ''
  if (variant !== '' && variant !== 'thumb') return null
  // id 只允许 [a-z0-9-]：素材路径永远由目录给出，请求方给不了路径。
  if (!/^[a-z0-9-]+$/i.test(id)) return null
  if (!/^[a-z0-9-]*$/i.test(variant)) return null
  return { id: id.toLowerCase(), variant }
}
