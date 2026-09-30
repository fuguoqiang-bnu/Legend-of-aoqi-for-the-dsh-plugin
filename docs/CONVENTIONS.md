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

## 5. 复现核对

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
```
