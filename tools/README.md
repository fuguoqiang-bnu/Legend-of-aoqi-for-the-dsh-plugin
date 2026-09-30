# tools/ —— 奥奇桌宠素材工具链

三个可复跑的 Python 脚本，负责把**用户提供的粉丝素材**（`R.gif` / `R.png`）切分成桌宠插件可用的帧与立绘，
程序化生成 5 种状态动画，并抓取《奥奇传说》官方公开图片用于 README / 主题装饰。

| 脚本 | 作用 | 产物 |
| --- | --- | --- |
| `slice_pets.py` | 切帧、抠白底、分割立绘、生成验证图与索引 | `assets/pets/<id>/base/f00…f15.png`、`assets/pets/<id>/portrait.png`、`assets/pets/contact-sheet.png`、`assets/pets/index.json` |
| `make_anim.py` | 由 base 帧程序化生成 5 状态 GIF，并回写 `index.json` | `assets/pets/<id>/{idle,working,done,error,waiting}.gif`、刷新 `index.json` 的 `states`/`animation`、重建验证图 |
| `fetch_assets.py` | 抓取奥奇传说官方公开图片 | `assets/fetched/<host>/<slug>.<ext>`、`assets/fetched/manifest.json` |

## 依赖

* **Python 3.10+**：本仓库在 DSH 自带运行时上验证通过
  （`C:\Users\big_m\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe`）。
* **Pillow**（必需，`slice_pets.py` / `make_anim.py` 依赖；`fetch_assets.py` 只拿它做额外校验）：
  实测 Pillow 12.3。
* **numpy**（必需，掩码/连通域/索引图运算）。
* **curl**（可选）：`fetch_assets.py` 在 `urllib` 失败时会自动回退到系统 `curl`，没有也能跑。
* 除以上之外**不引入任何第三方依赖**（无 scipy、无 OpenCV）。

## 复跑命令

```powershell
# 0) 约定：以下命令都在仓库根目录执行
$py = 'C:\Users\big_m\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe'

# 1) 切分素材（默认读 D:\Desktop\R.gif + D:\Desktop\R.png，输出到 assets\pets）
& $py tools\slice_pets.py
& $py tools\slice_pets.py --gif D:\Desktop\R.gif --png D:\Desktop\R.png --out assets\pets `
      --base-size 160 --portrait-size 512

# 2) 生成 5 状态动画（必须在 slice_pets.py 之后跑）
& $py tools\make_anim.py
& $py tools\make_anim.py --colors 96 --max-kb 80 --only jin,shui   # 只处理部分宠物

# 3) 抓官方图片
& $py tools\fetch_assets.py --dry-run          # 先看计划
& $py tools\fetch_assets.py                    # 真抓（14 条内置清单）
& $py tools\fetch_assets.py --urls my.txt      # 用文件覆盖清单（每行 url 或 url,note）
& $py tools\fetch_assets.py --urls "https://a/x.png,备注A;https://b/y.jpg,备注B"
```

## 参数速查

### `slice_pets.py`

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--gif` | `D:\Desktop\R.gif` | 源 GIF（16 帧，5 只宠物并排） |
| `--png` | `D:\Desktop\R.png` | 源静态立绘（5 只宠物） |
| `--out` | `<repo>\assets\pets` | 输出目录 |
| `--base-size` | `160` | 帧最长边像素（LANCZOS） |
| `--portrait-size` | `512` | 立绘最长边像素 |
| `--skip-portrait` / `--skip-sheet` | 关 | 跳过立绘 / 跳过验证图 |

处理要点：

* **抠白底**：源图背景其实是**不透明纯白**（GIF 的 `transparency=255` 索引并未被任何像素使用），
  因此不能简单按“近白即透明”一刀切，否则宠物身上的白色毛发会被打洞。
  脚本从画布四边做 4 邻域 flood fill，只把**与边缘连通的**近白像素判为背景，
  再对紧贴主体的一圈过渡像素按亮度赋半透明 alpha（抗锯齿边缘保留）。
* **统一画布**：先求 16 帧内容并集 bbox，再用同一矩形裁切所有帧，避免逐帧抖动。
* **立绘分割**：先用列投影 + DP 最小代价切分得到 5 个粗区间（宠物横向重叠，投影没有 0 谷底），
  再在主体掩码内做**多源 BFS（测地距离）**把重叠部位归给正确的主体——
  例如深蓝宠物的洋红色尾刃会正确留在它自己身上，而不是被直线切断塞进邻居的图里。
  最终区间写进 `index.json` 的 `portraitBand`。
* 宠物对应关系（GIF 区间 → id）由实际图像确认：
  `shui`(15,103) 蓝白狼 / `an`(135,207) 深蓝红角 / `mu`(253,329) 翠绿 / `jin`(396,460) 金黄 / `huo`(479,567) 红金。

### `make_anim.py`

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--out` | `<repo>\assets\pets` | 素材目录（读 `<id>\base\f*.png`） |
| `--colors` | `96` | 调色板颜色上限；超预算时自动沿 `96→64→48→40→32→24` 降色 |
| `--max-kb` | `80` | 单文件大小预算（KB），`0` = 不限 |
| `--only` | 空 | 仅处理指定 id（逗号分隔） |
| `--skip-sheet` | 关 | 不重建 `contact-sheet.png` |

状态定义（都保持透明背景，坐标为相对画布自适应）：

| 状态 | 帧时长 | 变换 / 装饰 |
| --- | --- | --- |
| `idle` | 110ms | 16 帧原样，无限循环 |
| `working` | 70ms | 竖直弹跳 `dy = -round(3*abs(sin(2πi/16)))` + 横向摆动 ±1px + 头顶 2 个白色光点循环淡入淡出 |
| `done` | 90ms | 前 8 帧起跳（`dy` 0 → -14 → 0），后 8 帧回原样 + 头顶 3 颗金色四角星（多边形绘制）先放大后淡出 |
| `error` | 90ms | 横向抖动 `dx = round(4*sin(3πi/16))` + 头顶红色感叹号圆牌（红圆 + 白竖条 + 白点） |
| `waiting` | 120ms | 竖直 ±2px + 头顶蓝色对话气泡（圆角矩形 + 尾巴 + 三个白点即 `…`） |

实现备注（踩过的坑，别再踩）：

* **为什么自己写 GIF 编码器**：源素材 16 帧里有成对的重复姿势，
  而 Pillow 的 GIF 编码器会把「与上一帧像素完全相同」的帧合并到上一帧并累加时长，
  结果 16 帧 / 110ms 会被写成 9 帧 / 220ms。
  `make_anim.py` 因此内置了一个 GIF89a 编码器（含 LZW），保证帧数、时长、透明索引精确落地，
  写完还会用 Pillow 重新读一遍做自检（帧数 / 时长 / 透明像素）。
* **画布**：`idle` 保持 base 画布不变；需要头顶空间的 4 个状态先按足够余量铺画布，
  再按 16 帧共用的内容并集 bbox 统一裁切，既不裁掉装饰也不浪费面积（面积直接决定 GIF 体积）。
* **调色板**：全部帧拼成一张长条图后用 `FASTOCTREE` 一次量化得到**全局调色板**，
  透明索引取「最后一个用到的颜色 + 1」，全局色表按 2 的幂裁剪（不写满 256 色）——同样画质下文件更小。

### `fetch_assets.py`

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--urls` | 内置 11 条清单（**只含页游**） | 覆盖清单：`;` 分隔的内联 URL（可写 `url,note`），或一个文件路径（每行 `url` 或 `url,note`） |
| `--out` | `<repo>\assets\fetched` | 输出根目录，按 `<host>/<slug>.<ext>` 落盘 |
| `--dry-run` | 关 | 只打印计划，不下载 |
| `--no-pillow` | 关 | 跳过 Pillow 额外校验 |

* 内置清单来源：**《奥奇传说》页游**官网 `aoqi.100bt.com`、其静态资源域 `resource.a0bi.com/marketnew/aoqi/`、
  官方资讯图床 `img4.a0bi.com`、发行商百田网 `www.100bt.com`（全部实测 HTTP 200）。
  **不含手游（`aqsy.100bt.com` / `/aoqi/m/`）资源**——本项目只做页游口径，来源明细见 `assets/fetched/SOURCES.md`。
* **礼貌抓取**：串行 + 每条间隔 0.3s + 15s 超时 + 普通浏览器 UA + Accept-Encoding 协商 + 失败重试 1 次。
* **合法性校验**：按 magic bytes 判断 PNG / JPEG / GIF / WEBP，非法内容**不落盘**并在 manifest 里标 `invalid`。
* **优雅失败**：403/404/超时/证书问题只记进 manifest（`state=failed` + `reason`），不会抛栈退出，
  命令整体仍以退出码 0 结束。
* manifest 每条记录：`url`、`status`、`contentType`、`bytes`、`sha256`、`path`、`note`、`state`、
  以及 `pillow`（格式/尺寸/模式，校验用）；文件末尾有 `stats` 成功/失败统计。

### `hires_build.py`

用**页游官网精灵图鉴立绘**（370×344 透明 PNG，内置 5 条 URL）生成桌宠的「官方高清」素材。
它解决的是「原味 16 帧只有约 122px、放大就糊」：这里每一步都是**降采样**，并把结果放进
`assets/pets/<id>/hires/`（与 `base/` 并存，互不覆盖）。

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--root` | 仓库根目录 | 输出根目录（写到 `<root>/assets/pets/<id>/hires/`） |
| `--height` | `190` | 生成帧高度上限（像素） |
| `--width` | `196` | 生成帧宽度上限（官方立绘是张开翅膀的横构图，限宽才不会被窗口裁掉两翼） |
| `--colors` | `200` | 调色板颜色上限 |
| `--max-kb` | `200` | 单个 GIF 体积预算（KB），超了就沿颜色阶梯降色 |
| `--only` | 全部 | 只做部分宠物，逗号分隔（如 `huo,an`） |
| `--offline` | 关 | 不联网，只用已下载的 `hires/source.png` |

产物：

```
assets/pets/<id>/hires/source.png                  官方原图（留档，便于复现与核对）
assets/pets/<id>/hires/f00.png                     静态高清帧（收起图标/文档用）
assets/pets/<id>/hires/{idle,working,done,error,waiting}.gif   16 帧动画（复用 make_anim 的自写 GIF 编码器）
assets/pets/hires-index.json                       每只的官方名、图鉴链接、立绘 URL、尺寸
```

* 复用了 `make_anim.py` 的 `make_state_frames()`（状态位移 + 头顶装饰）与 `save_state_gif()`
  （自写 GIF89a + 全局调色板 + 透明索引 + 帧数/时长/透明三重自检），所以两套素材的观感与时长完全一致。
* `idle` 因为官方立绘只有一张静态图，改用程序化「呼吸」（上下 3px + 1px 横向微摆），保证待机也在动。
* 名字与形态的对应关系（哪只叫什么、为什么）见 [`docs/NAMES.md`](../docs/NAMES.md)，
  清晰度取舍与实测数据见 [`docs/HIRES.md`](../docs/HIRES.md)。

## index.json 结构

```jsonc
{
  "generatedAt": "2026-09-30T10:58:31+08:00",   // slice_pets.py 运行时间
  "animGeneratedAt": "2026-09-30T11:13:52+08:00",// make_anim.py 运行时间
  "source": { "gif": "D:\\Desktop\\R.gif", "png": "D:\\Desktop\\R.png" },
  "pets": [
    {
      "id": "shui", "name": "帝释天", "element": "水", "color": "#7fd4ff",
      "band": [15, 103],          // GIF 十六帧里的稳定横向区间（含端点）
      "portraitBand": [358, 443], // 立绘自动分割得到的区间（含端点）
      "frames": 16,
      "size": [129, 160],         // base 帧画布
      "states": ["idle", "working", "done", "error", "waiting"],
      "animation": { "idle": { "frames": 16, "duration": 110, "size": [129,160], "bytes": 77707, "colors": 48 } }
    }
  ]
}
```

---

## 素材版权与免责声明

* 本仓库 `assets/pets/**` 中的宠物帧与立绘，是**脚本从使用者自行提供的粉丝素材**
  （`R.gif` / `R.png`）切割、抠底、缩放得到的衍生文件；脚本与仓库**不主张**对这些形象的任何所有权。
* **《奥奇传说》及其角色形象、logo、官网美术资源的著作权归百田信息科技（百奥家庭互动）所有**，
  商标权及相关权利同样归其所有。本仓库与百田信息科技**没有任何隶属、合作或授权关系**。
* `assets/fetched/**` 由 `tools/fetch_assets.py` 从公网公开页面按原 URL 下载，
  仅用于**个人学习、技术演示与桌面装饰**；下载行为不改变原作品的版权归属，
  manifest 中每条记录都保留了来源 URL 与 `note` 以便追溯。
* 请勿将本仓库中的任何美术资源用于商业用途、二次分发或任何可能侵犯著作权的场景；
  如需商用，请自行联系版权方获得授权。
* 若权利人认为本仓库内容不当，请通过 issue 联系，我们会在确认后**立即删除**相关文件。
