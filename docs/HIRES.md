# 高清素材（hires）：怎么把「放大糊」换成「缩小清」

> 结论先说：桌宠现在有**两套素材**，右键菜单一键切换。
> **官方高清**（默认）= 页游官网图鉴立绘 370×344 → **降采样**到显示尺寸，边缘干净；
> **原味逐帧** = 你给的 `R.gif` 里抠出来的 16 帧真·逐帧动画，Q 版可爱，但源只有约 122px。
> 想看效果直接对比：[crispness-compare.png](crispness-compare.png)、[names-compare.png](names-compare.png)。

## 1. 为什么原来会糊（量出来的，不是感觉）

| | 原味逐帧素材 | 官方高清素材 |
|---|---|---|
| 素材来源 | 用户提供的 `R.gif`（16 帧） | 页游官网精灵图鉴立绘（`img4.a0bi.com/...png`，370×344 透明） |
| 到桌宠手里时的尺寸 | 每帧约 **122px** 高 | **370px** 高 |
| 落盘尺寸 | `base/f00.png` 画布 131×160 等（**放大**到 160） | `hires/f00.png` 画布 204×194（**缩小**到 ≤196×190） |
| 显示端 | 200 逻辑 px 的精灵区 × 系统 1.25 倍缩放 = 250 物理 px | 同左 |
| 真实细节 vs 物理像素 | 122px 撑 250px → **放大约 2.05 倍** | 约 190px 撑 238px → **放大约 1.25 倍** |
| 重采样方向 | 上采样（凭空插值，边缘发毛） | 下采样（LANCZOS，边缘干净） |

也就是说：不是「换了一套更好看的图」这么简单，而是**把插值方向从放大改成了缩小**，
每帧的真实细节从约 122px 提到约 190px（约 1.5 倍），并且在 1.25 倍 DPI 缩放下几乎不损失。

## 2. 两套素材的区别（诚实版）

| | 原味逐帧（`material: frames`） | 官方高清（`material: hires`） |
|---|---|---|
| 形态 | 你素材里的 **Q 版初始形态**（小炎 / 小诺 / 小天 / 阿修 / 阿瑞） | 页游图鉴的 **传说王者形态**（龙炎 / 诺亚 / 帝释天 / 修尔 / 阿瑞斯，同角色的进化形态） |
| 动作 | 16 帧**真**逐帧（姿势是画出来的） | 官方立绘是**一张静态图**，动作用程序化位移 + 头顶装饰生成 |
| 清晰度 | 低（源 122px） | 高（源 370px，缩小使用） |
| 版权来源 | 你提供的图 | 页游官网图鉴（默认不入库的第三方图，见下方说明） |

**为什么不直接把 Q 版做成高清？** 因为页游官网里初始形态的透明图**更小**（小炎/小诺/小天/阿修/阿瑞的图鉴图最高只有 **104×102**，官方卡面 575×273 里角色本体也只有约 80px），
没有任何一处存在高清 Q 版原画。所以这是一个取舍，不是遗漏：

* 想要「可爱 Q 版 + 真逐帧」→ 选 `frames`（保持你原来看到的样子：小炎/小诺/小天/阿修/阿瑞）。
* 想要「清晰 + 官方原画」→ 选 `hires`（形态是传说王者，但细节是这个网站能给到的最好水平）。

两套都保留，随时切，谁也不删谁。

## 3. 怎么生成（可复现）

```powershell
# 下载官方立绘 + 生成 hires/f00.png 与 5 个状态 GIF（会打印每帧尺寸/帧数/时长做自检）
python tools\hires_build.py

# 只用已在本地下载好的 source.png（不联网）
python tools\hires_build.py --offline

# 换尺寸 / 只做部分宠物
python tools\hires_build.py --height 220 --width 230
python tools\hires_build.py --only huo,an
```

内置的官方链接（`tools/hires_build.py` 里的 `SOURCES`，名字出处见 [NAMES.md](NAMES.md)）：

```
huo  龙炎    http://img4.a0bi.com/upload/articleResource/20190126/1548494939099.png
jin  诺亚    http://img4.a0bi.com/upload/articleResource/20181119/1542622829659.png
shui 帝释天  http://img4.a0bi.com/upload/articleResource/20190318/1552911544803.png
an   修尔    http://img4.a0bi.com/upload/articleResource/20180827/1535363199494.png
mu   阿瑞斯  http://img4.a0bi.com/upload/articleResource/20190603/1559552204681.png
```

## 4. 实测产物（2026-09-30 生成）

* 5 只 × 5 状态 = **25 个 GIF**，每个 **16 帧**、时长与另一套完全一致（idle 110ms / working 70ms / done 90ms / error 90ms / waiting 120ms），
  由 `make_anim.save_state_gif()` 的自检逐帧核对（帧数、单帧时长、透明像素三项，任何一项不对就直接抛错）。
* 单文件 139–181KB，合计 4.0MB；`hires/f00.png` 静态帧画布统一 204×194。
* 尺寸上限特意做成 **≤196×190**：官方立绘是「张开翅膀」的横构图（帝释天缩完还有 257px 宽），
  而桌宠窗口只有 216px 宽 —— 限宽后才不会把两翼裁掉。

自检命令（不弹窗，不干扰你）：

```powershell
python companion\aoqi_pet.py --state s.json --settings c.json --command cmd.json `
  --assets assets --selftest-materials
# 素材自检：偏好=auto   实际=hires  帧数=16 首帧=204x198 delay=110ms
# 素材自检：偏好=hires  实际=hires  帧数=16 首帧=204x198 delay=110ms
# 素材自检：偏好=frames 实际=frames 帧数=16 首帧=129x160 delay=110ms
```

## 5. 桌宠怎么选素材

* 配置文件：`~/.dsh/aoqi-pet/companion-settings.json` 的 `material` 字段，
  取值 `auto`（默认，有 hires 就用）/ `hires` / `frames`。改成别的**即时生效**（200ms 热重载）。
* 右键菜单：**「素材用官方高清立绘」** 勾选框。勾上 = `hires`，取消 = `frames`，
  切换后立刻换帧并弹个气泡提示，无需重启。
* 命令行：`--material auto|hires|frames`（给测试与守护进程用）。

## 6. 版权

官方立绘与角色形象版权归**百田信息科技（百奥家庭互动）**所有，本项目把下载到的
`hires/source.png` 与派生帧放在 `assets/` 下**仅供个人本地装饰与学习**；
仓库的 MIT 许可**只覆盖代码**（`lib/`、`companion/`、`tools/`、`test/`），不覆盖 `assets/` 里的美术资源。
如果你不希望官方图随仓库分发，删除 `assets/pets/*/hires/` 后跑一次 `--offline` 也不会报错
（桌宠会自动回退到 `frames`，因为 `auto` 会检查文件是否存在）。
