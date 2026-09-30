#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
真机验证：桌宠的物理互动是不是**真的在动**。

不依赖 DSH：自己在临时目录里造 state.json / settings / command，
拉起一个隔离的桌宠进程，然后用 Win32 `GetWindowRect` 直接量它的真实窗口矩形。
所以它证明的是「屏幕上的窗口确实在掉、被甩出去、撞了边框、又回到下面」，
而不是「物理函数返回值对不对」（那是 test/pet-physics.py 的活）。

验证的目标按用户口径来：**重力常开、宠物常态待在屏幕下方**；
甩动只是给它碰撞能力（撞边框会弹，然后落回下方）。

    python test/physics-live.py                    # 重力落底 + 甩动撞框并回到底部
    python test/physics-live.py --with-antigravity # 额外验一下失重实验开关（默认关的功能）
    python test/physics-live.py --no-drag          # 不合成鼠标（不动你的鼠标）

坐标口径：本进程声明 DPI 感知后，Win32 返回**设备像素**；而 Tk 窗口自己用的是
**逻辑像素**（125% 缩放时 1920 设备 = 1536 逻辑）。所以下面一律用
`GetDpiForWindow/96` 把测量值换算回逻辑像素再比，否则会得出「没落到底」的假失败
（第一版就踩了：1536x960 被当成屏幕尺寸）。
"""
import argparse
import ctypes
import ctypes.wintypes as wintypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "companion"))
import petphysics  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "companion", "aoqi_pet.py")
ASSETS = os.path.join(ROOT, "assets")
TITLE = "奥奇桌宠"
user32 = ctypes.windll.user32

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)   # PROCESS_PER_MONITOR_DPI_AWARE
except Exception:  # noqa: BLE001
    try:
        user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001
        pass


def find_windows(pid):
    """按 pid 找出所有名为「奥奇桌宠」的窗口。"""
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value != pid:
            return True
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        if buf.value == TITLE:
            found.append(hwnd)
        return True

    user32.EnumWindows(callback, 0)
    return found


def rect_of(hwnd):
    r = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    return (r.left, r.top, r.right - r.left, r.bottom - r.top)


def scale_of(hwnd, default=1.0):
    try:
        dpi = user32.GetDpiForWindow(hwnd)
        if dpi:
            return dpi / 96.0
    except Exception:  # noqa: BLE001
        pass
    return default


def set_cursor(x, y):
    user32.SetCursorPos(int(x), int(y))


def mouse(flag):
    user32.mouse_event(flag, 0, 0, 0, 0)


class Phase:
    """一次隔离实例：写设置 → 起进程 → 找窗口 → 采样 → 收尸。"""

    def __init__(self, workdir, settings, pet="shui"):
        self.dir = workdir
        self.settings = settings
        self.pet = pet
        os.makedirs(workdir, exist_ok=True)
        self.state = os.path.join(workdir, "state.json")
        self.settings_path = os.path.join(workdir, "companion-settings.json")
        self.command = os.path.join(workdir, "command.json")
        self.log_path = os.path.join(workdir, "aoqi-pet.log")
        json.dump({
            "version": 1, "updatedAt": int(time.time() * 1000), "petId": pet,
            "petName": pet, "animation": "idle", "headline": "验证中", "bubble": None,
            "muted": True, "paused": False, "sessions": [], "stats": {},
        }, open(self.state, "w", encoding="utf-8"), ensure_ascii=False)
        json.dump(settings, open(self.settings_path, "w", encoding="utf-8"), ensure_ascii=False)

    def start(self):
        args = [
            sys.executable, SCRIPT,
            "--state", self.state, "--settings", self.settings_path,
            "--command", self.command, "--assets", ASSETS, "--log", self.log_path,
            "--pet", self.pet, "--sound", "0", "--stale-exit-ms", "600000",
        ]
        self.proc = subprocess.Popen(args, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 15
        self.hwnd = None
        while time.time() < deadline:
            windows = find_windows(self.proc.pid)
            if windows:
                self.hwnd = windows[0]
                self.window_count = len(windows)
                time.sleep(0.5)      # 等物理跑起来
                return True
            if self.proc.poll() is not None:
                return False
            time.sleep(0.15)
        return False

    def sample(self, seconds, interval=0.08):
        out = []
        end = time.time() + seconds
        while time.time() < end:
            rect = rect_of(self.hwnd)
            if rect is not None:
                out.append((time.time(),) + rect)
            time.sleep(interval)
        return out

    def wait_settled(self, timeout, quiet=0.6, eps=2.0, interval=0.06):
        """采样直到窗口「安静」quiet 秒（相邻采样位移 < eps 像素），或超时。

        比固定采样窗口靠谱：甩得越猛飞得越久，固定 9 秒会误判「没停稳」。
        返回 (采样列表, 是否停稳, 停稳耗时)。
        """
        samples = []
        started = time.time()
        end = started + timeout
        last_move = started
        prev = None
        while time.time() < end:
            rect = rect_of(self.hwnd)
            if rect is not None:
                samples.append((time.time(),) + rect)
                if prev is not None and (abs(rect[0] - prev[0]) > eps or abs(rect[1] - prev[1]) > eps):
                    last_move = time.time()
                prev = rect
            time.sleep(interval)
            if prev is not None and time.time() - last_move >= quiet:
                return samples, True, time.time() - started
        return samples, False, time.time() - started

    def stop(self):
        try:
            self.proc.terminate()
            self.proc.wait(timeout=6)
        except Exception:  # noqa: BLE001
            try:
                subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:  # noqa: BLE001
                pass

    def log_tail(self, lines=8):
        try:
            with open(self.log_path, "r", encoding="utf-8") as handle:
                return [line.rstrip() for line in handle.readlines()[-lines:]]
        except OSError:
            return []

    def log_has(self, needle):
        try:
            with open(self.log_path, "r", encoding="utf-8") as handle:
                return needle in handle.read()
        except OSError:
            return False


def describe(samples, label, scale):
    """把设备像素换算成逻辑像素后汇报，便于和 Tk 自己的坐标对比。"""
    if not samples:
        print("  %s：没有采到窗口位置" % label)
        return None
    pts = [(s[0], s[1] / scale, s[2] / scale) for s in samples]
    xs = [p[1] for p in pts]
    ys = [p[2] for p in pts]
    speeds = []
    for a, b in zip(pts, pts[1:]):
        dt = b[0] - a[0]
        if dt > 0.001:
            speeds.append(((b[1] - a[1]) ** 2 + (b[2] - a[2]) ** 2) ** 0.5 / dt)
    info = {
        "n": len(pts), "x0": xs[0], "y0": ys[0], "x1": xs[-1], "y1": ys[-1],
        "dy": ys[-1] - ys[0], "dx": xs[-1] - xs[0],
        "ymin": min(ys), "ymax": max(ys), "xmin": min(xs), "xmax": max(xs),
        "vmax": max(speeds) if speeds else 0.0,
    }
    print("  %s：采样 %d 次（逻辑px）  y: %.0f → %.0f（Δ%+.0f）  x: %.0f → %.0f  "
          "y范围[%.0f,%.0f]  峰值速度≈%.0fpx/s"
          % (label, info["n"], info["y0"], info["y1"], info["dy"], info["x0"], info["x1"],
             info["ymin"], info["ymax"], info["vmax"]))
    return info


def base_settings(**overrides):
    data = {
        "pet": "shui", "scale": 1.0, "opacity": 0.97, "topmost": True, "muted": True,
        "collapsed": False, "physics": True, "antigravity": False, "material": "frames",
        "x": 240, "y": 0,
    }
    data.update(overrides)
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--with-antigravity", action="store_true",
                        help="额外验证失重实验开关（默认关闭的彩蛋功能）")
    parser.add_argument("--no-drag", action="store_true", help="不做合成鼠标甩动（不动你的鼠标）")
    parser.add_argument("--keep", action="store_true", help="保留临时目录便于看日志")
    args = parser.parse_args()

    workdir = tempfile.mkdtemp(prefix="aoqi-physics-")
    device_w = user32.GetSystemMetrics(0)
    device_h = user32.GetSystemMetrics(1)
    d_left, d_top, d_right, d_bottom = petphysics.work_area(device_w, device_h)
    print("屏幕 %dx%d（设备像素）；工作区 (%.0f,%.0f)-(%.0f,%.0f)"
          % (device_w, device_h, d_left, d_top, d_right, d_bottom))

    checks = []

    # ── 相位 A：重力常开 + 常态待在下方 ────────────────────────────────────────
    print("\n[相位 A] 重力：从屏幕顶部 (240,0) 起手，应该自己掉下来并停在**下方**")
    phase_a = Phase(os.path.join(workdir, "a"), base_settings(x=240, y=0))
    if not phase_a.start():
        print("  ✗ 没能找到桌宠窗口（进程起不来？）")
        for line in phase_a.log_tail(10):
            print("    " + line)
        return 2
    scale = scale_of(phase_a.hwnd)
    print("  DPI 缩放 = %.2f（设备→逻辑要除以它）；该进程 Tk 窗口数 = %d" % (scale, phase_a.window_count))
    samples_a, settled_a, took_a = phase_a.wait_settled(8.0)
    info_a = describe(samples_a, "重力模式", scale)
    win_h_logical = rect_of(phase_a.hwnd)[3] / scale
    work_bottom_logical = d_bottom / scale
    print("  窗口逻辑尺寸 216x%.0f；工作区逻辑底边=%.0f → 理论落点 y=%.0f（%.1f 秒后停稳=%s）"
          % (win_h_logical, work_bottom_logical, work_bottom_logical - win_h_logical, took_a, settled_a))
    # 起点是设置里的 y=0，所以「最终 y 比起点低 ≥200」不受采样时机影响（比拿首个采样当起点稳）
    checks.append(("重力：从顶部掉下来了（最终 y ≥ 200 逻辑px）", info_a["y1"] >= 200))
    checks.append(("重力：真的停稳了（%s）" % ("是" if settled_a else "8 秒内一直在动"), settled_a))
    checks.append(("重力：停在工作区底边、即「待在下方」（误差 < 8 逻辑px）",
                   abs((info_a["y1"] + win_h_logical) - work_bottom_logical) < 8))
    checks.append(("重力：日志里有落底撞击记录（证明是掉下来撞的，不是摆上去的）",
                   phase_a.log_has("撞边框：bottom")))
    print("  桌宠日志尾部：")
    for line in phase_a.log_tail(4):
        print("    " + line)
    phase_a.stop()

    # ── 相位 B（可选）：失重实验开关 ───────────────────────────────────────────
    if args.with_antigravity:
        print("\n[相位 B] 失重实验（默认关）：从下方起手，应该往上飘并贴顶")
        phase_b = Phase(os.path.join(workdir, "b"),
                        base_settings(x=240, y=int(d_bottom / 1.25) - 320, antigravity=True))
        if phase_b.start():
            info_b = describe(phase_b.sample(6.0), "失重模式", scale_of(phase_b.hwnd))
            checks.append(("失重实验：往上飘了（Δy ≤ -200 逻辑px）", info_b["dy"] <= -200))
            phase_b.stop()
        else:
            print("  ✗ 起不来")
            checks.append(("失重实验：窗口起不来", False))

    # ── 相位 C：甩动撞框，然后被重力拉回下方 ──────────────────────────────────
    if not args.no_drag:
        print("\n[相位 C] 甩动：合成一次快速鼠标拖拽后松手 → 应该飞出去、撞边框、再落回下方")
        phase_c = Phase(os.path.join(workdir, "c"),
                        base_settings(x=int(device_w / 1.25 / 2), y=int(device_h / 1.25 / 2 - 200)))
        if not phase_c.start():
            print("  ✗ 没能找到桌宠窗口")
            return 2
        scale_c = scale_of(phase_c.hwnd)
        rect = rect_of(phase_c.hwnd)
        grab_x = rect[0] + rect[2] // 2
        grab_y = rect[1] + int(rect[3] * 0.62)      # 抓宠物身体（不是透明区）
        set_cursor(grab_x, grab_y)
        time.sleep(0.2)
        mouse(0x0002)                                # LEFTDOWN
        for i in range(1, 14):                       # 快速往左下甩
            set_cursor(grab_x - i * 28, grab_y + i * 8)
            time.sleep(0.011)
        mouse(0x0004)                                # LEFTUP
        # 甩出去之后会被重力拉回底部，所以「起终点差」会互相抵消；
        # 要看的是**运动幅度**（相对起点最远跑到哪）。等它自己停稳再判（最长 15 秒）。
        samples_c, settled_c, took_c = phase_c.wait_settled(15.0)
        info_c = describe(samples_c, "甩出后（等停稳）", scale_c)
        c_bottom_logical = d_bottom / scale_c
        c_win_h = rect_of(phase_c.hwnd)[3] / scale_c
        moved = max(abs(info_c["xmax"] - info_c["x0"]), abs(info_c["xmin"] - info_c["x0"]),
                    abs(info_c["ymax"] - info_c["y0"]), abs(info_c["ymin"] - info_c["y0"]))
        print("  停稳耗时 %.1f 秒，最终落点 y=%.0f（理论 %.0f）"
              % (took_c, info_c["y1"], c_bottom_logical - c_win_h))
        checks.append(("甩动：运动幅度 ≥ 250 逻辑px（实际 %.0f）" % moved, moved >= 250))
        checks.append(("甩动：飞行峰值速度 > 400px/s", info_c["vmax"] > 400))
        checks.append(("甩动：撞到了边框（日志有「撞边框」记录）", phase_c.log_has("撞边框")))
        checks.append(("甩动之后：真的停稳了（%s）" % ("是，%.1fs" % took_c if settled_c else "15 秒内一直在动"), settled_c))
        checks.append(("甩动之后：重力把它拉回下方并停稳（误差 < 12 逻辑px）",
                       abs((info_c["y1"] + c_win_h) - c_bottom_logical) < 12))
        print("  桌宠日志尾部：")
        for line in phase_c.log_tail(10):
            print("    " + line)
        phase_c.stop()

    print("\n=== 判定 ===")
    failed = 0
    for name, ok in checks:
        print("  [%s] %s" % ("✓" if ok else "✗", name))
        failed += 0 if ok else 1

    if args.keep:
        print("\n临时目录保留在：%s" % workdir)
    else:
        shutil.rmtree(workdir, ignore_errors=True)

    if failed:
        print("\n真机验证：失败 %d 项" % failed)
        return 1
    print("\n真机验证：全部通过 ✅（%d 项）" % len(checks))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
