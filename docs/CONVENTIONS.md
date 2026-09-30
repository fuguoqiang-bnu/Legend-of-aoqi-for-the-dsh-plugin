# 参考了哪些现成插件、核对了哪些前提

> 这份文档回答两件事：**「你是怎么参考别人插件的」** 和 **「为什么桌宠必须是另一个进程」**。
> 每条结论都带可复查的路径/数字，不是转述。

## 1. 直接参考的现成插件：本 profile 里的 `image-generation`

路径：`C:\Users\big_m\.dsh\profiles\desktop\plugins\image-generation\index.js`（297 行 / 12.6KB）

它是一只**本地插件**：同样是「不依赖任何 `@deepseek-ai/*`、由 profile 从磁盘直接挂载」的写法，
我按它的骨架来做 `aoqi-pet`。逐项对照：

| 它怎么写 | 我怎么写 | 说明 |
|---|---|---|
| `const name = 'image-generation'`（loader 身份） | `export const name = 'aoqi-pet'` | 与 `insert:` 里的 `id` 对应 |
| `const inject = ['tools']` | `export const inject = ['tools', 'agents']` | 我多了 `agents`：需要 `session/event`、`agent/*` 事件源 |
| `ctx.tools.register({ name, description, parameters, output:{schema, render}, isConcurrencySafe, execute })` | 同样走**裸** `ctx.tools.register`，三个工具都带 `output.schema` + `render` | 这是它文档字符串里点明的接缝：「registered through the raw `ctx.tools.register` seam, so this plugin stays a dependency-free local module」 |
| `output.render(_args, value)` 返回内容块数组（`{type:'text'}` / `{type:'image'}`） | 三个工具各自 `render` 成文本块 | 让模型看到结构化结果，而不是纯字符串 |
| `isConcurrencySafe: () => false` | 同 | 工具不是并发安全的 |
| `exec.agent?.session?.header?.cwd` 解析相对路径 | 同类用法（桌宠路径/素材路径按 `DSH_HOME` 与 profile 解析） | |
| `resolveApiKey`：先 `ctx.get('credentials')`，再退到 `~/.dsh/.credentials.yaml` | **不需要**（我不联网、不读凭据） | 少一处故障源 |
| 通过 `cordis.patch.yml` 的 `insert:` 行挂载 | 同（见第 3 节） | |

**我没有照抄的部分**（因为要做的事不同）：它只注册一个网络工具；我的插件要维护会话状态机、
写文件桥、拉起/守护一个**进程之外**的窗口，还管 `max-tokens` 截断续写。所以我的代码是
`lib/index.js` + `lib/auto-continue.js` + `lib/bridge.js` + `lib/pet-runtime.js` 四个模块，
把「决策」和「IO」分开，纯逻辑部分可以直接单测（`test/smoke.mjs` 44 项断言）。

## 2. 同类第三方插件：社区里的奥奇主题插件

### 2.1 社区清单：Awesome DSH Plugin（4392 条）

本项目的功能设计对着社区清单筛过一遍 —— 清单是 **Awesome DSH Plugin**（[awesome-dsh-plugin.com](https://awesome-dsh-plugin.com/zh/)，
`awesome-dsh-plugin` 组织维护，本地快照 756KB / **4392 条**，每条都注明「可用 `dsh plugin add` 安装、声明了 `dsh.bundle`」）。
其中「🎨 UI 增强」分类下的**桌宠**就有 30+ 只，我按「置顶独立窗口 / 状态文件桥 / 截断续写 / 素材来源」四类挑了最贴近的逐条对照：

| 社区插件（点开可看） | 它的做法 | 我的做法 / 差异 |
|---|---|---|
| [anneheartrecord/dsh-desk-pet](https://github.com/anneheartrecord/dsh-desk-pet) | 页面**之外**的 macOS 桌宠：真置顶窗口，六种状态跟着本地 DSH 走，**跑系统自带 Python + ctypes，不用 Electron** | 结论完全一致：插件侧没有窗口能力，就得另起进程。我用 DSH 自带 Python + **tkinter**（Windows 没 AppKit），同样不引入 Electron、不下载额外运行时 |
| [qiqibabyy/dsh-pet-desktop](https://github.com/qiqibabyy/dsh-pet-desktop) | **独立置顶窗口**，导入 `~/.codex/pets`，贴边吸附拖拽 + 智能鼠标穿透 | 我也是独立置顶窗口 + 拖拽 + 位置记忆；但没有做贴边吸附/鼠标穿透那一套（见第 5 节：我试过验证穿透，结论是**没验证成功就不写**） |
| [deanzhang2026-max/dsh-lived-pet#dsh-pet-bridge](https://github.com/deanzhang2026-max/dsh-lived-pet/tree/main/packages/dsh-pet-bridge)、[wsxwj123/dsh-plugins#dsh-pet-bridge](https://github.com/wsxwj123/dsh-plugins/tree/main/packages/dsh-pet-bridge) | 「把 agent 状态（思考/干活/等待/完成/出错）写进**状态文件**，桌宠轮询该文件」 | 我的桥就是这一套（`state.json` 1s 心跳），但我多了一个反方向文件 `companion-settings.json`，并在文档里**钉死唯一真相** —— 那个「切到阿修后切不动」的 bug 根因正是两个方向的写入互相覆盖 |
| [Frog755/dsh-client-auto-retry](https://github.com/Frog755/dsh-client-auto-retry) | `turn/end` 因 error / interrupted / **max-tokens** 结束时自动发「继续」：宽限期、冷却、指数退避、连续上限、启动扫描、设置卡片、会话级停止/恢复 | 我的 `lib/auto-continue.js` 同样是「宽限 + 冷却 + 连续上限」，但**只对 max-tokens 出手**（不动 error/interrupted，避免和用户意图打架）；我**没有**指数退避和 UI 卡片 |
| [qwert702/dsh-continue-on-limit](https://github.com/qwert702/dsh-continue-on-limit) | 「**双源**截断检测 + 防死循环」 | 我的双源 = `session/event → turn/end.reason.kind === 'max-tokens'` 为主信号，「回合结束但无可见输出」为可选副信号；防死循环 = 连续上限 + `capped` 标记 + 冷却 |
| [zhou1736948757-cpu/dsh-auto-continue](https://github.com/zhou1736948757-cpu/dsh-auto-continue) | 输出到上限自动「继续」，面向自部署小上限模型 | 同一诉求；我额外把「续写了几次」写进状态文件，桌宠气泡里能看见 |
| [mengyun233/dsh-codex-pet](https://github.com/mengyun233/dsh-codex-pet) | 把 **Codex 桌宠皮肤**迁移到 DSH，右下角动画随状态变化 | 我的逐帧素材 `R.gif` 就是这一类皮；区别是我不止一套，另配了**页游官方高清立绘**（`docs/HIRES.md`） |
| [cookiesheep/whale-on-desk](https://github.com/cookiesheep/whale-on-desk) | 像素鲸鱼，**29 个逐帧状态**，等批准时贴屏 | 我用 16 帧 × 5 状态（idle/working/done/error/waiting）+ 收起图标；状态数少但每态都做了时长/透明自检 |
| [Stellum-Waq/dsh-pet-ronaldo](https://github.com/Stellum-Waq/dsh-pet-ronaldo) | spritesheet 导入、状态动画、完成时庆祝 + 提示音、多宠物管理 | 我支持五只宠物切换 + 两套素材切换；提示音在桌宠进程侧用 `winsound`（宿主侧扫不到 `winsound`，见第 4 节） |
| [ankesu/dsh-live2d-pet](https://github.com/ankesu/dsh-live2d-pet)、[Tisitan/dsh-live2d-companion](https://github.com/Tisitan/dsh-live2d-companion)、[BlackBearCC/dsh-pet-sprite](https://github.com/BlackBearCC/dsh-pet-sprite) | Live2D / 页内宠物路线 | 我选**逐帧位图 + 官方高清**而不是 Live2D：零额外运行时、零网络依赖，代价是没有物理摆动 |
| [nickkkkkk123123/dsh-whale-girl](https://github.com/nickkkkkk123123/dsh-whale-girl) | 明确宣传「**零新进程**」 | 我反过来**刻意多一个进程** —— 需求就是「宠物要跳脱到软件之外」，而进程内开不了窗口（第 4 节给了字节级证据） |

> 清单是外部资料，我只当作「同行的做法」来对照，没有照抄任何实现；
> 上表每一行的判断都对应本仓库里可读的代码或测试。

### 2.2 另一只「奥奇插件」的存在

插件管理器的安装日志里能看到一只**别人写的奥奇插件**被装过又卸掉：

```
C:\Users\big_m\.dsh\profiles\desktop\.plugin-manager\logs\operation-AW2WAJ\pnpm.log
+ aoqi-legend-theme-plugin github:fuguoqiang-bnu/aoqi-theme

C:\Users\big_m\.dsh\profiles\desktop\.plugin-manager\logs\operation-gXHNbs\pnpm.log
- aoqi-legend-theme-plugin github:fuguoqiang-bnu/aoqi-theme
```

从包名与仓库名看，它是**主题换肤向**的插件（`aoqi-theme`），和本项目的定位不冲突：
本项目做的是**应用之外的桌宠窗口 + 截断自动续写**，不是界面换肤。
它同时也证明了一件有用的事：**DSH 的插件可以直接从 GitHub 安装**（`dsh plugin add github:owner/repo`），
所以本仓库按「可被 `dsh plugin add` 消费的包」来组织（`package.json` 带 `dsh.bundle.patch`、`cordis.patch.yml`）。

## 3. 挂载机制（两边都留着证据）

1. 我在 profile 的补丁层里插了一行（`~/.dsh/profiles/desktop/cordis.patch.yml`）：

   ```yaml
   - insert:
       - id: aoqi-pet
         name: "D:/Desktop/dsh-aoqi-pet/lib/index.js"    # 直接指向桌面仓库，改完即用
         config:
           petId: shui
           companion:
             autoLaunch: true
             balloonTips: false
   ```

2. 组合结果在生成的 `~/.dsh/profiles/desktop/cordis.yml` 第 1314–1315 行可以查到：

   ```yaml
   - id: aoqi-pet
     name: file:///D:/Desktop/dsh-aoqi-pet/lib/index.js
   ```

3. **热加载的真实边界**（实测）：插件**行**的增删会热加载；
   但改动**已加载插件文件里的代码/文案**不会即时生效 —— 例如把 `PETS` 里的名字换成官方名后，
   宿主 `state.json` 里的 `petName` 仍是旧值，要下次 DSH 启动才更新（桌宠侧重启即生效）。

## 4. 为什么桌宠必须是另一个进程（原始前提的核对）

`README` 里那条架构结论不是听说的，是扫出来的。`resources\app.asar`（115.7MB，JS 在归档里是明文）
字节级统计：

| 探测项 | 次数 | 结论 |
|---|---|---|
| `from "electron"` | **1** | 只有桌面壳自己的 main 进程 bundle 里那一条 import（同一行还导入了 `BrowserWindow, Menu, Notification, Tray, WebContentsView, ipcMain, …`） |
| `require('electron')` | **0** | 插件侧没有任何 CommonJS 形式取 Electron |
| `new Tray` | **1** | 在壳的 `DesktopTray` 类里（系统托盘 + 退出入口） |
| `new Notification` | **1** | 在壳的桌面更新提醒里（`Notification.isSupported()` → `new Notification(...)`） |
| `winsound` | 0 | 宿主（Node 侧）不发声，所以提示音由 Python 侧 `winsound` 负责 |
| `PowerShell` | 347 | 宿主自己在 Windows 上大量调用 PowerShell，所以桌宠用 PowerShell 发系统气泡是同一条路 |

也就是说：**Tray / Notification / BrowserWindow 全部只存在于 Electron 主进程（桌面壳）里**，
而插件跑在 profile/agent 这一侧，拿不到 `electron` 模块 —— 插件**开不了窗口、也弹不了系统通知**。

于是才有这个拆分（这也是需求里「宠物要跳脱到软件之外」的技术必然）：

```
插件（Node 侧，无 Electron）           桌宠（独立 Python/Tk 进程，有窗口）
lib/index.js  事件→状态机、截断续写  ──▶  state.json  ──▶  置顶透明窗口、动画、气泡、提示音
lib/pet-runtime.js 拉起/守护进程     ◀──  companion-settings.json / command.json  ◀─ 右键菜单
```

副作用也一并说清楚：系统气泡（PowerShell/toast）会被 Windows 专注助手吞掉，
所以**桌宠自己的气泡 + 提示音才是可靠通道**，系统通知只是锦上添花（`balloonTips` 可关）。

## 5. 诚实边界：试过、但**没**写进卖点的事

* **鼠标穿透**：社区有插件专门做「智能鼠标穿透」（透明区域点得到桌面图标）。我用
  `WindowFromPoint` 探过桌宠窗口的四个点（左上/右下/站台/本体），结果**四个点全部返回了下层窗口** ——
  但这**不能证明**穿透，因为 `WindowFromPoint` 不做逐像素命中测试，它不看 `-transparentcolor` 抠掉的像素。
  真要证明只能往屏幕上发真实点击（会点到你自己的窗口），我**没有**这么做，所以 README 里不写这条能力。
* **系统气泡**：Windows 的 toast（PowerShell）会被专注助手吞掉，所以我把「可靠通道」定为桌宠自己的气泡 + 提示音，
  系统通知只作为可选（`balloonTips`）。这条不是推测：`docs/VERIFICATION.md` 里有对照记录。
* **热加载**：插件**行**的增删热加载（已验证），改**已加载文件的内容**不热加载（第 3 节，
  本次改宠物名时宿主 `petName` 仍是旧值，实测）。
* **素材版权**：`hires` 那套是页游官方立绘，能随仓库分发的前提是「个人学习/本地装饰」；
  真要去公开分发，删掉 `assets/pets/*/hires/` 就会自动回退到逐帧（`material: auto` 会检查文件在不在）。

## 6. 复现核对

```powershell
# 1) 看参考插件的接缝写法
notepad "$env:USERPROFILE\.dsh\profiles\desktop\plugins\image-generation\index.js"

# 2) 看我的插件行与组合结果
Select-String -Path "$env:USERPROFILE\.dsh\profiles\desktop\cordis.patch.yml" -Pattern 'aoqi-pet' -Context 0,6
Select-String -Path "$env:USERPROFILE\.dsh\profiles\desktop\cordis.yml"      -Pattern 'aoqi-pet' -Context 0,2

# 3) 复扫 Electron 前提（与本文件第 4 节的数字一致）
python - <<'PY'
from pathlib import Path
d = Path(r'D:\abc\新建文件夹 (2)\resources\app.asar').read_bytes()
for n in (b'from "electron"', b"require('electron')", b'new Tray', b'new Notification'):
    print(n.decode(), d.count(n))
PY

# 4) 扫社区清单里「桌宠」相关的条目（清单快照在仓库外，见第 2 节的出处）
Select-String -Path awesome-dsh.md -Pattern '桌宠|宠物' | Measure-Object
```

