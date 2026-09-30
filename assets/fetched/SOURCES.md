# 抓取素材来源（只取页游）

本项目**只针对《奥奇传说》页游**（百田，`aoqi.100bt.com`），不使用手游（`aqsy.100bt.com`、`/aoqi/m/`）资源。

抓取脚本：`tools/fetch_assets.py`（纯标准库，`urllib` 为主，`curl` 兜底）。复现：

```bash
python tools/fetch_assets.py           # 抓内置 11 条清单
python tools/fetch_assets.py --dry-run # 只打印计划
python tools/fetch_assets.py --urls https://.../x.png
```

图片本体不入库（`.gitignore` 忽略 `assets/fetched/*`，只保留本文件与脚本），
完整抓取记录（最终 URL / HTTP 状态 / Content-Type / 字节数 / sha256 / 尺寸）见脚本运行后生成的
`assets/fetched/manifest.json`。

| # | 来源 URL | 用途 | 实测 |
| --- | --- | --- | --- |
| 1 | `resource.a0bi.com/marketnew/aoqi/dest/scss/img_s/xinshoubg.jpg` | 页游官网「新手玩转奥奇」KV（1920×574）——**桌宠收起图标的取图处**（左上官方 logo） | 200 / JPEG |
| 2 | `resource.a0bi.com/marketnew/aoqi/dest/scss/img_s/indexBg.jpg` | 页游官网首页主背景（1920×899） | 200 / JPEG |
| 3 | `resource.a0bi.com/marketnew/aoqi/dest/scss/img_s/jinglingBg.jpg` | 页游「精灵大全」区背景（1000×320） | 200 / JPEG |
| 4 | `resource.a0bi.com/marketnew/aoqi/dest/scss/img_s/tujianBg.jpg` | 页游「图鉴」区横幅（1000×370） | 200 / JPEG |
| 5 | `resource.a0bi.com/marketnew/aoqi/dest/scss/img_s/liteindexbg.jpg` | 页游「奥奇极速版」首页背景 | 200 / JPEG |
| 6 | `resource.a0bi.com/marketnew/aoqi/dest/scss/img_s/subBg.jpg` | 页游内页背景 | 200 / JPEG |
| 7 | `resource.a0bi.com/marketnew/aoqi/dest/scss/img_s/zhenxingShare.jpg` | 页游官方分享图（450×450，官方角色立绘） | 200 / JPEG |
| 8 | `resource.a0bi.com/marketnew/aoqi/dest/scss/spritedest/index.png` | 页游首页 UI 雪碧图（1285×510） | 200 / PNG |
| 9 | `resource.a0bi.com/marketnew/aoqi/dest/scss/img_s/logo.png` | 页游官网小 logo（透明 PNG，46×24） | 200 / PNG |
| 10 | `img4.a0bi.com/upload/articleResource/20240102/1704181670976.png` | 百田官方资讯里的精灵立绘（270×270） | 200 / PNG |
| 11 | `www.100bt.com/resource/zl/index_images/zl_btlogo.png` | 发行商百田网官方 logo | 200 / PNG |

**名称考证的官方入口**（页游口径）：`aoqi.100bt.com/jingling/daquan_list.html`（官方精灵大全，
含 7509 个精灵详情页链接与缩略图），详情页形如 `aoqi.100bt.com/jingling/<id>.html`。

⚠️ **版权**：《奥奇传说》及其角色形象版权归**百田信息科技（百奥家庭互动）**所有。
以上资源仅用于个人学习与本地桌宠装饰，仓库不主张任何所有权，**禁止商用**；
如果你是版权方并要求移除，请开 issue。
