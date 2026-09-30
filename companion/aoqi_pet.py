#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
奥奇桌宠 · 小五王 —— DSH 之外的置顶动画宠物窗口。

由宿主插件（lib/pet-runtime.js）以独立进程拉起，两边通过文件桥通信：

    读  <DSH_HOME>/aoqi-pet/state.json              宿主写的运行状态（动画/气泡/统计）
    读写 <DSH_HOME>/aoqi-pet/companion-settings.json 宠物选择、位置、静音、暂停自动续写
    写  <DSH_HOME>/aoqi-pet/command.json            一次性指令（点击 / 恢复自动续写）

只依赖 Python 标准库（tkinter）。没有素材时也能用：会用矢量占位宠物顶上，
所以可以先跑起来看效果，再去补 assets/pets 里的 GIF。

用法（一般由插件自动调用）：
    python aoqi_pet.py --state ... --settings ... --command ... --assets ... --pet shui
"""
import argparse
import json
import os
import sys
import time
from collections import OrderedDict

try:
    import tkinter as tk
except ImportError:  # pragma: no cover - 没有 Tk 的发行版
    sys.stderr.write("aoqi-pet: 这个 Python 没有 tkinter，无法开窗\n")
    raise SystemExit(3)

# ── 视觉常量 ────────────────────────────────────────────────────────────────────
# 透明色：窗口里所有等于这个颜色的像素都会被 Windows 抠掉，并让鼠标穿透。
# 选品红是因为小五王素材里不可能出现它，不会误抠到宠物身上。
KEY_COLOR = "#ff00fe"
SPRITE_BOX = 200          # 精灵绘制区（宽 = SPRITE_BOX，高 = SPRITE_BOX）
BUBBLE_BOX = 96           # 气泡预留高度（没有气泡时是透明区）
ICON_BOX = 78             # 收起成图标时的窗口边长
PAD = 8
TICK_MS = 200             # 轮询状态文件的间隔
STALE_GRACE_MS = 30000    # 启动后允许 state.json 缺席的宽限

# 收起成图标时，右上角那个小圆点表示宿主这会儿在干什么（静止不动的状态灯）
STATE_DOTS = {
    "idle": "#9aa6bd",
    "working": "#37c7ff",
    "done": "#3ddc84",
    "error": "#ff5c4d",
    "waiting": "#ffb020",
}

# 传说五王（页游官方名）—— 不是自创名，出处见 docs/NAMES.md。
# baby = 初始形态名（桌宠默认帧素材就是这个形态），顺序沿用官方宣传的「龙炎/诺亚/帝释天/修尔/阿瑞斯」。
PETS = OrderedDict([
    ("huo", {"name": "龙炎", "baby": "小炎", "color": "#ff8a6b", "accent": "#b53f24"}),
    ("jin", {"name": "诺亚", "baby": "小诺", "color": "#ffd166", "accent": "#b58324"}),
    ("shui", {"name": "帝释天", "baby": "小天", "color": "#7fd4ff", "accent": "#2f7fb5"}),
    ("an", {"name": "修尔", "baby": "阿修", "color": "#8b93e8", "accent": "#3a2f7f"}),
    ("mu", {"name": "阿瑞斯", "baby": "阿瑞", "color": "#7fe08a", "accent": "#2f7f45"}),
])

# 非循环状态播完一轮回到 idle 的动画名映射
LOOPING_STATES = ("idle", "working", "waiting")


def parse_args():
    parser = argparse.ArgumentParser(description="奥奇桌宠伴生窗口")
    parser.add_argument("--state", required=True, help="宿主写的 state.json")
    parser.add_argument("--settings", required=True, help="本窗口读写的设置文件")
    parser.add_argument("--command", required=True, help="写一次性指令的文件")
    parser.add_argument("--assets", required=True, help="assets 目录")
    parser.add_argument("--log", default="", help="统一日志文件（和宿主共用）")
    parser.add_argument("--pet", default="shui", help="初始宠物 id")
    parser.add_argument("--material", default="auto", choices=("auto", "hires", "frames"),
                        help="素材偏好：auto=有官方高清就用；hires=只用官方高清；frames=只用原味 16 帧")
    parser.add_argument("--scale", type=float, default=1.0)
    parser.add_argument("--opacity", type=float, default=0.97)
    parser.add_argument("--topmost", type=int, default=1)
    parser.add_argument("--sound", type=int, default=1)
    parser.add_argument("--stale-exit-ms", type=int, default=120000)
    parser.add_argument("--selftest", action="store_true", help="只做初始化自检后退出（CI 用）")
    parser.add_argument("--selftest-materials", action="store_true",
                        help="三种素材偏好各加载一遍并打日志（CI 用）")
    return parser.parse_args()


class Logger:
    """宿主与桌宠共用一个日志文件，前缀区分。文件写不了就退回 stderr。"""

    def __init__(self, path):
        self.path = path
        self.enabled = bool(path)

    def __call__(self, message):
        line = "[%s] [companion] %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), message)
        if self.enabled:
            try:
                with open(self.path, "a", encoding="utf-8") as handle:
                    handle.write(line)
                return
            except OSError:
                self.enabled = False
        sys.stderr.write(line)


class Settings:
    """companion-settings.json：本窗口是唯一写者，宿主只读。"""

    DEFAULTS = {
        "pet": "shui",
        "x": None,
        "y": None,
        "scale": 1.0,
        "opacity": 0.97,
        "topmost": True,
        "muted": False,
        "hidden": False,
        "collapsed": False,
        "autoContinuePaused": False,
        "material": "auto",
        "seq": 0,
    }

    def __init__(self, path, log):
        self.path = path
        self.log = log
        self.data = dict(self.DEFAULTS)
        self.mtime = 0.0
        self.load()
        self.mtime = self._stat_mtime()

    def _stat_mtime(self):
        try:
            return os.path.getmtime(self.path)
        except OSError:
            return 0.0

    def reload_if_changed(self):
        """文件被别处改过（宿主工具换宠物、手改配置、测试脚本）时重新读进来。"""
        mtime = self._stat_mtime()
        if mtime and mtime != self.mtime:
            self.mtime = mtime
            self.load()
            return True
        return False

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
            if isinstance(raw, dict):
                for key in self.DEFAULTS:
                    if key in raw and raw[key] is not None:
                        self.data[key] = raw[key]
        except (OSError, ValueError):
            pass
        return self.data

    def save(self):
        """写设置：临时文件 + 原子替换，**失败重试**。

        为什么要重试：宿主工具、外部脚本、本窗口可能同时读写这个文件，
        偶尔会撞上 Windows 的文件锁（实测出现过一次 `WinError 5`）。
        一次失败就等于「用户刚点的选择没保存」，所以这里重试 3 次（50ms 间隔）。
        另外每次写入都会刷新 mtime，因此重试成功后本窗口自身的 mtime 也要同步，
        否则会把「自己写的」误判成「外面改的」而多走一次热重载。
        """
        tmp = self.path + ".tmp"
        last_error = None
        for attempt in range(3):
            try:
                with open(tmp, "w", encoding="utf-8") as handle:
                    json.dump(self.data, handle, ensure_ascii=False, indent=2)
                os.replace(tmp, self.path)
                self.mtime = self._stat_mtime()
                return True
            except OSError as error:
                last_error = error
                time.sleep(0.05 * (attempt + 1))
        self.log("设置写入失败（已重试 3 次）：%s" % last_error)
        return False

    def set(self, **patch):
        changed = False
        for key, value in patch.items():
            if self.data.get(key) != value:
                self.data[key] = value
                changed = True
        if changed:
            self.save()
        return changed

    def next_seq(self):
        self.data["seq"] = int(self.data.get("seq", 0)) + 1
        self.save()
        return self.data["seq"]


class SpriteLibrary:
    """按 (宠物, 状态) 找动画，三种素材按偏好挑：

    * ``hires``  —— ``<assets>/pets/<id>/hires/<state>.gif``：页游官方高清立绘（370×344 降采样），
      静态底图 + 程序化动作，**最清晰**（tools/hires_build.py 生成）。
    * ``frames`` —— ``<assets>/pets/<id>/<state>.gif``：用户给的 16 帧逐帧动画（低清但真有逐帧姿势）。
    * ``auto``   —— 有 hires 就用 hires，否则用 frames。

    再往下才是 ``base/fNN.png`` 逐帧 PNG，最后是矢量占位。
    """

    MAX_GIF_FRAMES = 64
    MATERIALS = ("auto", "hires", "frames")

    def __init__(self, assets_dir, log, material="auto"):
        self.assets_dir = assets_dir
        self.log = log
        self.material = material if material in self.MATERIALS else "auto"
        self.cache = {}
        self.warned = set()

    def set_material(self, material):
        """换素材偏好：清缓存，下一次 tick 会重新按新素材加载。"""
        if material not in self.MATERIALS or material == self.material:
            return False
        self.material = material
        self.cache.clear()
        self.log("素材偏好切换为 %s" % material)
        return True

    def _hires_path(self, pet, state):
        return os.path.join(self.assets_dir, "pets", pet, "hires", "%s.gif" % state)

    def _gif_path(self, pet, state):
        return os.path.join(self.assets_dir, "pets", pet, "%s.gif" % state)

    def resolve_material(self, pet, state):
        """实际用上了哪种素材（auto 需要看文件在不在）。"""
        if self.material == "hires":
            return "hires"
        if self.material == "frames":
            return "frames"
        return "hires" if os.path.isfile(self._hires_path(pet, state)) else "frames"

    def _base_dir(self, pet):
        return os.path.join(self.assets_dir, "pets", pet, "base")

    def load(self, root, pet, state):
        """返回 (frames, delay_ms)：frames 是 PhotoImage 列表，可能为空（用占位）。"""
        used = self.resolve_material(pet, state)
        key = (pet, state, used)
        if key in self.cache:
            return self.cache[key]

        frames = []
        delay = 110

        candidates = [self._hires_path(pet, state)] if used == "hires" else []
        candidates.append(self._gif_path(pet, state))
        for gif in candidates:
            if not os.path.isfile(gif):
                continue
            for index in range(self.MAX_GIF_FRAMES):
                try:
                    frames.append(tk.PhotoImage(file=gif, format="gif -index %d" % index))
                except tk.TclError:
                    break
            if frames:
                delay = self._gif_delay(gif, state)
                break

        if not frames:
            base = self._base_dir(pet)
            if os.path.isdir(base):
                names = sorted(name for name in os.listdir(base) if name.lower().endswith(".png"))
                for name in names:
                    try:
                        frames.append(tk.PhotoImage(file=os.path.join(base, name)))
                    except tk.TclError:
                        continue

        if not frames:
            if (pet, state, used) not in self.warned:
                self.warned.add((pet, state, used))
                self.log("缺少素材，使用矢量占位：pets/%s/%s（%s）" % (pet, state, used))

        self.cache[key] = (frames, delay)
        return self.cache[key]

    @staticmethod
    def _gif_delay(path, state):
        """GIF 帧延时：文件里写的是 1/100 秒；很多导出工具写 0，那就按状态给默认值。"""
        defaults = {"idle": 110, "working": 70, "done": 90, "error": 90, "waiting": 120}
        try:
            with open(path, "rb") as handle:
                data = handle.read()
            index = data.find(b"\x21\xf9\x04")
            if index != -1 and len(data) > index + 6:
                delay = int.from_bytes(data[index + 4:index + 6], "little")
                if delay > 0:
                    return max(40, min(400, delay * 10))
        except OSError:
            pass
        return defaults.get(state, 110)


class Bubble:
    """宠物头顶的气泡：一个圆角框 + 文本 + 小尖角。"""

    KINDS = {
        "info": ("#ffffff", "#2b3a55", "#2b3a55"),
        "done": ("#fff8e1", "#8a5a00", "#e6a700"),
        "error": ("#ffe9e6", "#8a2b1a", "#e2573a"),
        "warn": ("#fff4e0", "#8a5200", "#e69138"),
        "say": ("#ffffff", "#2b3a55", "#2b3a55"),
    }

    def __init__(self, canvas):
        self.canvas = canvas
        self.items = []
        self.text = ""
        self.kind_id = "info"

    def clear(self):
        for item in self.items:
            self.canvas.delete(item)
        self.items = []

    def show(self, text, kind, x, y, width):
        self.clear()
        fill, ink, border = self.KINDS.get(kind, self.KINDS["info"])
        text_id = self.canvas.create_text(
            x, y - 40, text=text, width=width - 28, fill=ink,
            font=("Microsoft YaHei UI", 9), justify="left", anchor="nw",
        )
        left, top, right, bottom = self.canvas.bbox(text_id)
        box = self.canvas.create_rectangle(
            left - 10, top - 8, right + 10, bottom + 8,
            fill=fill, outline=border, width=1,
        )
        tail_left = max(left + 12, min(x - 6, right - 12))
        tail = self.canvas.create_polygon(
            tail_left, bottom + 7, tail_left + 14, bottom + 7, tail_left + 6, bottom + 18,
            fill=fill, outline=border, width=1,
        )
        self.canvas.tag_lower(box, text_id)
        self.canvas.tag_lower(tail, text_id)
        self.items = [box, tail, text_id]
        self.text = text
        self.kind_id = kind


class PetWindow:
    def __init__(self, args, log):
        self.args = args
        self.log = log
        self.settings = Settings(args.settings, log)
        initial_material = self.settings.data.get("material") or args.material
        self.library = SpriteLibrary(args.assets, log, initial_material)

        self.pet = self.settings.data.get("pet") or args.pet
        if self.pet not in PETS:
            self.pet = "shui"
        self.animation = "idle"
        self.frames = []
        self.frame_index = 0
        self.frame_delay = 110
        self.bubble_signature = None
        self.bubble_until = 0.0
        self.state = {}
        self.state_missing_since = time.time()
        self.last_host_seen = 0.0
        self.press = None
        self.moved = False
        self.dragging = False
        self.placeholder_items = []
        self.running = True

        self.root = tk.Tk()
        self.root.title("奥奇桌宠")
        self.root.overrideredirect(True)
        try:
            self.root.attributes("-transparentcolor", KEY_COLOR)
        except tk.TclError as error:
            self.log("系统不支持 -transparentcolor：%s" % error)
        try:
            self.root.attributes("-topmost", bool(self.settings.data.get("topmost", True)))
        except tk.TclError:
            pass
        try:
            self.root.attributes("-alpha", float(self.settings.data.get("opacity", args.opacity)))
        except tk.TclError:
            pass
        self.root.configure(bg=KEY_COLOR)

        self.expanded_width = SPRITE_BOX + PAD * 2
        self.expanded_height = SPRITE_BOX + BUBBLE_BOX + PAD * 2
        self.width = self.expanded_width
        self.height = self.expanded_height
        self.canvas = tk.Canvas(
            self.root, width=self.width, height=self.height,
            bg=KEY_COLOR, highlightthickness=0, bd=0,
        )
        self.canvas.pack()

        self.bubble = Bubble(self.canvas)
        self.sprite_item = None
        self.scaled_cache = {}
        self.last_frame_at = time.monotonic()
        # 宠物脚下的站台：有 assets/theme/platform.png 就用它，没有就退回一个灰椭圆。
        self.platform_photo = self.load_platform(args.assets)
        # 收起成图标时显示的官方图标（assets/theme/aoqi-icon.png，静态不动）
        self.icon_photo = self.load_icon(args.assets)
        self.shadow_item = None
        self.placeholder_items = []
        self.dot_items = []
        self.ground_y = self.height - PAD - 12

        self.menu = self.build_menu()
        self.bind_events()
        self.collapsed = bool(self.settings.data.get("collapsed", False)) and self.icon_photo is not None
        self.width, self.height = (
            (ICON_BOX, ICON_BOX) if self.collapsed else (self.expanded_width, self.expanded_height)
        )
        self.canvas.config(width=self.width, height=self.height)
        self.place_window()
        self.write_pid()
        self.build_stage()
        self.switch_animation(self.animation, force=True)
        self.root.after(120, self.tick)

    # ── 窗口与菜单 ─────────────────────────────────────────────────────────────
    @staticmethod
    def load_platform(assets_dir):
        """站台底图。加载失败/文件不存在都返回 None（调用方退回灰椭圆）。"""
        path = os.path.join(assets_dir, "theme", "platform.png")
        if not os.path.isfile(path):
            return None
        try:
            return tk.PhotoImage(file=path)
        except tk.TclError:
            return None

    @staticmethod
    def load_icon(assets_dir):
        """收起模式用的官方图标。没有就返回 None（这时不允许收起）。"""
        path = os.path.join(assets_dir, "theme", "aoqi-icon.png")
        if not os.path.isfile(path):
            return None
        try:
            return tk.PhotoImage(file=path)
        except tk.TclError:
            return None

    def build_stage(self):
        """按当前模式（展开/收起）重建画布内容。"""
        self.canvas.delete("all")
        self.sprite_item = None
        self.placeholder_items = []
        self.shadow_item = None
        self.dot_items = []
        if self.collapsed:
            if self.icon_photo is not None:
                self.canvas.create_image(self.width / 2, self.height / 2, image=self.icon_photo, anchor="center")
            self.ground_y = self.height
            self.update_dot()
            return
        if self.platform_photo is not None:
            self.canvas.create_image(self.width / 2, self.height - PAD, image=self.platform_photo, anchor="s")
        else:
            self.shadow_item = self.canvas.create_oval(
                self.width / 2 - 42, self.height - 26, self.width / 2 + 42, self.height - 10,
                fill="#d9dee8", outline="",
            )
        # 宠物脚底所在的 y：踩在站台上就抬高一点，免得陷进底盘。
        self.ground_y = self.height - PAD - (26 if self.platform_photo is not None else 12)
        self.redraw_sprite()

    def set_collapsed(self, value):
        """收起成桌面图标（静态）／展开成动画桌宠。右下角保持不动，视觉上是缩到角上。"""
        value = bool(value)
        if value == self.collapsed:
            return
        if value and self.icon_photo is None:
            self.show_bubble("没有图标素材，收不起来～", "warn", 3000)
            self.var_collapsed.set(False)
            return
        old_width, old_height = self.width, self.height
        self.collapsed = value
        self.width, self.height = (ICON_BOX, ICON_BOX) if value else (self.expanded_width, self.expanded_height)
        x = self.root.winfo_x() + (old_width - self.width)
        y = self.root.winfo_y() + (old_height - self.height)
        self.settings.set(collapsed=value, x=int(x), y=int(y))
        self.canvas.config(width=self.width, height=self.height)
        self.root.geometry("%dx%d+%d+%d" % (self.width, self.height, int(x), int(y)))
        self.var_collapsed.set(value)
        self.build_stage()
        self.log("收起成图标（静态）" if value else "展开桌宠")

    def place_window(self):
        x = self.settings.data.get("x")
        y = self.settings.data.get("y")
        if x is None or y is None:
            x = self.root.winfo_screenwidth() - self.width - 48
            y = self.root.winfo_screenheight() - self.height - 96
            self.settings.set(x=int(x), y=int(y))
        self.root.geometry("%dx%d+%d+%d" % (self.width, self.height, int(x), int(y)))

    def write_pid(self):
        try:
            pid_path = os.path.join(os.path.dirname(os.path.abspath(self.args.state)), "companion.pid")
            with open(pid_path, "w", encoding="utf-8") as handle:
                handle.write(str(os.getpid()))
        except OSError as error:
            self.log("pid 写入失败：%s" % error)

    def build_menu(self):
        menu = tk.Menu(self.root, tearoff=0)
        pet_menu = tk.Menu(menu, tearoff=0)
        for pet_id, info in PETS.items():
            pet_menu.add_command(
                label="%s（初始形态 %s · %s）" % (info["name"], info.get("baby", ""), pet_id),
                command=lambda value=pet_id: self.choose_pet(value),
            )
        menu.add_cascade(label="换一只小五王", menu=pet_menu)
        menu.add_separator()
        self.var_collapsed = tk.BooleanVar(value=bool(self.settings.data.get("collapsed", False)))
        menu.add_checkbutton(label="收起为图标（静态）", variable=self.var_collapsed,
                             command=lambda: self.set_collapsed(self.var_collapsed.get()))
        # 素材：勾上=页游官方高清立绘（清晰），不勾=你给的原味 16 帧逐帧动画
        self.var_hires = tk.BooleanVar(value=self.material_choice() == "hires")
        menu.add_checkbutton(label="素材用官方高清立绘", variable=self.var_hires,
                             command=self.toggle_material)
        self.var_muted = tk.BooleanVar(value=bool(self.settings.data.get("muted", False)))
        self.var_paused = tk.BooleanVar(value=bool(self.settings.data.get("autoContinuePaused", False)))
        self.var_top = tk.BooleanVar(value=bool(self.settings.data.get("topmost", True)))
        menu.add_checkbutton(label="静音（不出提示音）", variable=self.var_muted, command=self.toggle_muted)
        menu.add_checkbutton(label="暂停自动续写", variable=self.var_paused, command=self.toggle_paused)
        menu.add_checkbutton(label="窗口置顶", variable=self.var_top, command=self.toggle_topmost)
        menu.add_separator()
        menu.add_command(label="让宠物回到默认位置", command=self.reset_position)
        menu.add_command(label="立即恢复自动续写", command=self.resume_auto_continue)
        menu.add_separator()
        menu.add_command(label="退出桌宠", command=self.quit)
        return menu

    def bind_events(self):
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Button-3>", self.on_context)
        self.canvas.bind("<Double-Button-1>", self.on_double_click)
        self.root.bind("<Escape>", lambda _event: self.quit())

    # ── 交互 ───────────────────────────────────────────────────────────────────
    def on_press(self, event):
        self.press = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())
        self.moved = False

    def on_drag(self, event):
        if self.press is None:
            return
        self.dragging = True
        x = event.x_root - self.press[0]
        y = event.y_root - self.press[1]
        if abs(x - self.root.winfo_x()) > 2 or abs(y - self.root.winfo_y()) > 2:
            self.moved = True
        self.root.geometry("+%d+%d" % (x, y))

    def on_release(self, _event):
        if self.dragging and self.moved:
            self.settings.set(x=self.root.winfo_x(), y=self.root.winfo_y())
        elif not self.moved:
            # 图标模式点一下 = 展开；展开态点一下 = 告诉宿主「我看见了」（清零连击）
            if self.collapsed:
                self.set_collapsed(False)
            else:
                self.send_command("poke")
        self.dragging = False
        self.press = None

    def on_context(self, event):
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def on_double_click(self, _event):
        if self.collapsed:
            self.set_collapsed(False)
        else:
            self.send_command("poke")

    def choose_pet(self, pet_id):
        if self.collapsed:
            self.set_collapsed(False)   # 图标模式看不见宠物，换人先展开
        self.pet = pet_id
        self.settings.set(pet=pet_id)
        self.library.cache.clear()
        self.switch_animation(self.animation, force=True)
        self.show_bubble("%s 上场！" % PETS[pet_id]["name"], "say", 3200)

    def toggle_muted(self):
        self.settings.set(muted=bool(self.var_muted.get()))

    def material_choice(self):
        """当前该按哪种素材显示：auto 时看 hires 文件在不在。"""
        return self.library.resolve_material(self.pet, self.animation)

    def toggle_material(self):
        """勾上=官方高清立绘（静态底图 + 程序化动作），不勾=原味 16 帧逐帧动画。"""
        choice = "hires" if self.var_hires.get() else "frames"
        self.settings.set(material=choice)
        self.apply_material(choice)
        self.show_bubble("素材：官方高清立绘" if choice == "hires" else "素材：原味逐帧动画", "say", 3200)

    def apply_material(self, material):
        if self.library.set_material(material):
            self.switch_animation(self.animation, force=True)

    def resume_auto_continue(self):
        """解除宿主的连击封顶，同时把本窗口的「暂停」开关也掰回来（否则宿主下一拍又会暂停）。"""
        self.var_paused.set(False)
        self.settings.set(autoContinuePaused=False)
        self.send_command("resume-auto-continue")

    def toggle_paused(self):
        paused = bool(self.var_paused.get())
        self.settings.set(autoContinuePaused=paused)
        self.show_bubble("自动续写已暂停" if paused else "自动续写已恢复", "say", 3000)

    def toggle_topmost(self):
        topmost = bool(self.var_top.get())
        self.settings.set(topmost=topmost)
        try:
            self.root.attributes("-topmost", topmost)
        except tk.TclError:
            pass

    def reset_position(self):
        x = self.root.winfo_screenwidth() - self.width - 48
        y = self.root.winfo_screenheight() - self.height - 96
        self.settings.set(x=int(x), y=int(y))
        self.root.geometry("+%d+%d" % (int(x), int(y)))

    def send_command(self, action, **payload):
        command = {"seq": self.settings.next_seq(), "action": action, "at": int(time.time() * 1000)}
        command.update(payload)
        try:
            tmp = self.args.command + ".tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(command, handle, ensure_ascii=False)
            os.replace(tmp, self.args.command)
            self.log("发送指令 %s" % action)
        except OSError as error:
            self.log("指令写入失败：%s" % error)
        self.show_bubble("我在这儿！" if action == "poke" else "收到～", "say", 2600)

    # ── 状态与动画 ─────────────────────────────────────────────────────────────
    def read_state(self):
        try:
            with open(self.args.state, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, ValueError):
            return None

    def switch_animation(self, animation, force=False):
        if animation not in ("idle", "working", "done", "error", "waiting"):
            animation = "idle"
        if not force and animation == self.animation and self.frames:
            return
        self.animation = animation
        frames, delay = self.library.load(self.root, self.pet, animation)
        self.frames = frames
        self.frame_delay = delay
        self.last_frame_at = time.monotonic()
        if self.frame_index >= len(frames):
            self.frame_index = 0
        self.redraw_sprite()
        self.update_dot()

    def redraw_sprite(self):
        """把当前帧画到画布；没有素材就画矢量占位宠物。"""
        if self.collapsed:
            return
        for item in self.placeholder_items:
            self.canvas.delete(item)
        self.placeholder_items = []
        if self.sprite_item is not None:
            self.canvas.delete(self.sprite_item)
            self.sprite_item = None

        if self.frames:
            photo = self.frames[self.frame_index % len(self.frames)]
            scale = float(self.settings.data.get("scale", self.args.scale) or 1.0)
            if scale < 0.95:
                factor = max(2, int(round(1.0 / scale)))
                cache_key = (self.pet, self.animation, factor)
                if cache_key not in self.scaled_cache:
                    self.scaled_cache[cache_key] = photo.subsample(factor, factor)
                photo = self.scaled_cache[cache_key]
            self.sprite_item = self.canvas.create_image(
                self.width / 2, self.ground_y, image=photo, anchor="s",
            )
            return
        self.draw_placeholder()

    def draw_placeholder(self):
        """矢量占位：一只圆滚滚的小家伙，保证没素材时窗口也有内容。"""
        info = PETS.get(self.pet, PETS["shui"])
        center_x = self.width / 2
        base_y = self.ground_y
        body_r = 34
        self.placeholder_items.append(self.canvas.create_oval(
            center_x - body_r, base_y - body_r * 2, center_x + body_r, base_y,
            fill=info["color"], outline=info["accent"], width=3,
        ))
        self.placeholder_items.append(self.canvas.create_oval(
            center_x - 22, base_y - body_r * 2 - 26, center_x - 6, base_y - body_r * 2 - 6,
            fill=info["color"], outline=info["accent"], width=3,
        ))
        self.placeholder_items.append(self.canvas.create_oval(
            center_x + 6, base_y - body_r * 2 - 26, center_x + 22, base_y - body_r * 2 - 6,
            fill=info["color"], outline=info["accent"], width=3,
        ))
        for offset in (-12, 12):
            self.placeholder_items.append(self.canvas.create_oval(
                center_x + offset - 5, base_y - body_r - 12, center_x + offset + 5, base_y - body_r - 2,
                fill="#22304a", outline="",
            ))
        self.placeholder_items.append(self.canvas.create_text(
            center_x, base_y - body_r - 34, text=PETS.get(self.pet, PETS["shui"])["name"].split("·")[-1],
            fill="#22304a", font=("Microsoft YaHei UI", 9, "bold"),
        ))

    def update_dot(self):
        """收起模式下右上角的状态灯：静止，只换颜色。"""
        for item in self.dot_items:
            self.canvas.delete(item)
        self.dot_items = []
        if not self.collapsed:
            return
        color = STATE_DOTS.get(self.animation, STATE_DOTS["idle"])
        center_x = self.width - 17
        center_y = 17
        radius = 9
        self.dot_items.append(self.canvas.create_oval(
            center_x - radius - 2, center_y - radius - 2, center_x + radius + 2, center_y + radius + 2,
            fill="#ffffff", outline="",
        ))
        self.dot_items.append(self.canvas.create_oval(
            center_x - radius, center_y - radius, center_x + radius, center_y + radius,
            fill=color, outline="",
        ))

    def show_bubble(self, text, kind, ttl_ms):
        if self.collapsed:
            # 图标模式没有地方画气泡：只记过期时间，别浪费一次绘制
            self.bubble_until = time.time() + ttl_ms / 1000.0
            self.bubble_signature = (text, kind)
            return
        self.bubble.show(text, kind, self.width / 2 + 6, self.height - SPRITE_BOX - PAD, self.width)
        self.bubble_until = time.time() + ttl_ms / 1000.0
        self.bubble_signature = (text, kind)

    def hide_bubble(self):
        self.bubble.clear()
        self.bubble_until = 0.0
        self.bubble_signature = None

    def chime(self, kind):
        if int(self.args.sound) != 1:
            return
        if self.settings.data.get("muted", False):
            return
        if kind not in ("done", "error"):
            return
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK if kind == "done" else winsound.MB_ICONHAND)
        except Exception:
            pass

    # ── 主循环 ─────────────────────────────────────────────────────────────────
    def apply_settings(self):
        """把「外面改过的设置」落到窗口上：宿主工具换宠物、手改配置、测试脚本都走这里。"""
        pet = self.settings.data.get("pet")
        if isinstance(pet, str) and pet in PETS and pet != self.pet:
            self.pet = pet
            self.library.cache.clear()
            self.switch_animation(self.animation, force=True)
        topmost = bool(self.settings.data.get("topmost", True))
        if topmost != bool(self.var_top.get()):
            self.var_top.set(topmost)
            self.toggle_topmost()
        muted = bool(self.settings.data.get("muted", False))
        if muted != bool(self.var_muted.get()):
            self.var_muted.set(muted)
        paused = bool(self.settings.data.get("autoContinuePaused", False))
        if paused != bool(self.var_paused.get()):
            self.var_paused.set(paused)
        collapsed = bool(self.settings.data.get("collapsed", False))
        if collapsed != self.collapsed:
            self.set_collapsed(collapsed)
        material = self.settings.data.get("material") or "auto"
        if material != self.library.material:
            self.library.set_material(material)
            self.switch_animation(self.animation, force=True)
            if hasattr(self, "var_hires"):
                self.var_hires.set(self.library.resolve_material(self.pet, self.animation) == "hires")
        opacity = float(self.settings.data.get("opacity", self.args.opacity) or self.args.opacity)
        try:
            self.root.attributes("-alpha", max(0.2, min(1.0, opacity)))
        except tk.TclError:
            pass
        self.log("设置热重载：pet=%s collapsed=%s topmost=%s muted=%s material=%s"
                 % (self.pet, self.collapsed, topmost, muted, self.library.material))

    def tick(self):
        """一拍一拍的轮询。**单拍异常绝不能让循环停掉**——Tk 的 after 回调里抛异常
        只会把这一拍吞掉，循环就不会再排下一拍，桌宠会「活着但永远不动、也切不了宠物」。"""
        if not self.running:
            return
        try:
            self.tick_once()
        except Exception as error:            # noqa: BLE001 —— 兜底，保证循环不死
            self.log("tick 异常（已忽略，继续跑）：%s: %s" % (type(error).__name__, error))
        if self.running:
            self.root.after(TICK_MS, self.tick)

    def tick_once(self):
        now = time.time()
        if self.settings.reload_if_changed():
            self.apply_settings()
        state = self.read_state()
        if state is None:
            if self.state and now - self.last_host_seen > self.args.stale_exit_ms / 1000.0:
                self.log("宿主状态文件长时间未更新，桌宠退出")
                self.quit()
                return
            if not self.state and (now - self.state_missing_since) * 1000 > STALE_GRACE_MS:
                self.log("等不到宿主状态文件，桌宠退出")
                self.quit()
                return
        else:
            self.state = state
            self.last_host_seen = now
            self.apply_state(state, now)

        self.advance_frame()

    def apply_state(self, state, now):
        # 宠物选择的唯一真相是 companion-settings.json：右键菜单和宿主工具都写它。
        # 这里**绝对不能用** state.json 里的 petId 反写设置——宿主心跳 1 秒一次，
        # 而本进程 200ms 一拍，反写永远比宿主更早生效，结果就是「刚切走就被弹回来」。
        # 只有在设置文件里没有合法 pet 时，才用宿主状态兜一次底。
        setting_pet = self.settings.data.get("pet")
        if not (isinstance(setting_pet, str) and setting_pet in PETS):
            pet_id = state.get("petId")
            if isinstance(pet_id, str) and pet_id in PETS and pet_id != self.pet:
                self.pet = pet_id
                self.library.cache.clear()
                self.settings.set(pet=pet_id)
                self.switch_animation(state.get("animation", "idle"), force=True)
        animation = state.get("animation", "idle")
        if animation != self.animation:
            self.switch_animation(animation)

        bubble = state.get("bubble")
        if isinstance(bubble, dict) and bubble.get("text"):
            signature = (bubble.get("text"), bubble.get("kind", "info"))
            age_ms = now * 1000 - float(bubble.get("at", 0))
            ttl = float(bubble.get("ttlMs", 6000))
            if signature != self.bubble_signature and age_ms <= ttl:
                self.show_bubble(bubble.get("text"), bubble.get("kind", "info"), ttl - age_ms)
                self.chime(bubble.get("kind", "info"))
        elif self.bubble_until and now > self.bubble_until:
            self.hide_bubble()

        if self.bubble_until and now > self.bubble_until:
            self.hide_bubble()

    def advance_frame(self):
        """按 GIF 自己的帧延时推进（tick 只是轮询节奏，不是帧率）。图标模式静止不动。"""
        if self.collapsed or not self.frames or len(self.frames) <= 1:
            return
        now = time.monotonic()
        if (now - self.last_frame_at) * 1000.0 < max(40, self.frame_delay):
            return
        self.last_frame_at = now
        self.frame_index = (self.frame_index + 1) % len(self.frames)
        self.redraw_sprite()

    def run(self):
        self.log("桌宠窗口启动：pet=%s size=%dx%d pid=%d" % (self.pet, self.width, self.height, os.getpid()))
        self.root.mainloop()

    def quit(self):
        self.running = False
        try:
            self.root.destroy()
        except tk.TclError:
            pass


def main():
    args = parse_args()
    log = Logger(args.log)
    try:
        window = PetWindow(args, log)
    except tk.TclError as error:
        log("初始化窗口失败：%s" % error)
        return 4
    if args.selftest:
        size = "empty"
        if window.frames:
            size = "%dx%d" % (window.frames[0].width(), window.frames[0].height())
        window.log("selftest ok：pet=%s frames=%d material=%s resolved=%s 首帧=%s delay=%dms"
                   % (window.pet, len(window.frames), window.library.material,
                      window.library.resolve_material(window.pet, window.animation), size,
                      window.frame_delay))
        window.quit()
        return 0
    if args.selftest_materials:
        for material in ("auto", "hires", "frames"):
            window.library.set_material(material)
            frames, delay = window.library.load(window.root, window.pet, window.animation)
            size = "%dx%d" % (frames[0].width(), frames[0].height()) if frames else "empty"
            window.log("素材自检：偏好=%-6s 实际=%-6s 帧数=%d 首帧=%s delay=%dms"
                       % (material, window.library.resolve_material(window.pet, window.animation),
                          len(frames), size, delay))
        window.quit()
        return 0
    window.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
