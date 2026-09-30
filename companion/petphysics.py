#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
桌宠的物理体 —— 反重力、甩动惯性、四边碰撞反弹。

**刻意不依赖 tkinter**：这里只有数学，所以可以脱离 GUI 单测
（见 test/pet-physics.py）。窗口那边每 33ms 调一次 step()，把返回的
位移写进 root.geometry()，把返回的碰撞事件变成火花/震动。

坐标系：屏幕像素，**y 向下为正**（和 Tk 的 window geometry 一致）。
Body 只跟踪窗口**左上角**的 (x, y)：桌宠的脚在窗口内部，不影响边界语义 ——
「撞边框」= 窗口贴到工作区边缘。

三个模式：
    gravity      正常重力：往下掉，落到底边弹几下，然后睡着
    antigravity  反重力：往上飘，顶到上边弹，缓慢左右漂，最后贴顶悬停
    none         物理关闭：body 不动，位置完全由拖动/菜单决定
"""
import math
import sys

# ── 手感参数（都是可直接在单测里断言的量）────────────────────────────────────
GRAVITY = 2200.0        # px/s²，重力模式向下
ANTIGRAVITY = 900.0     # px/s²，反重力模式向上（比重力小，飘得更久更像失重）
AIR_DRAG = 0.55         # 1/s，空气阻尼（指数衰减，和帧率无关）
RESTITUTION = 0.55      # 撞墙：法向速度保留比例
WALL_FRICTION = 0.88    # 撞墙：切向速度保留比例
MIN_BOUNCE = 45.0       # px/s，法向速度低于此值就不再弹，避免无限小抖动
SLEEP_SPEED = 18.0      # px/s，两个轴都低于此值且贴边 → 睡（停止每帧移动窗口）
MOVE_EPS = 0.4          # px，位移小于它就不调 geometry（省一次窗口重绘）
MAX_SUBSTEP = 1 / 240.0  # 最大积分步长：dt 再大也要切成 ≤1/240s 的子步
MAX_SUBSTEPS = 64       # 子步上限，防止某次极长 dt 把一帧算爆

MODES = ("gravity", "antigravity", "none")


def work_area(fallback_width, fallback_height):
    """Windows 工作区（排除任务栏）。拿不到就退回整屏。

    用 ctypes 调 SystemParametersInfoW(SPI_GETWORKAREA)，纯标准库；
    非 Windows / 调用失败一律退回 (0, 0, fallback_width, fallback_height)。
    """
    if sys.platform == "win32":
        try:
            import ctypes
            import ctypes.wintypes as wintypes

            rect = wintypes.RECT()
            ok = ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
            if ok and rect.right > rect.left and rect.bottom > rect.top:
                return (float(rect.left), float(rect.top), float(rect.right), float(rect.bottom))
        except Exception:  # noqa: BLE001 —— 任何失败都退回整屏，不能因此开不了宠物
            pass
    return (0.0, 0.0, float(fallback_width), float(fallback_height))


class Body:
    """一个只有位移/速度状态的矩形（窗口）物理体。"""

    def __init__(self, width, height, bounds, mode="gravity"):
        self.width = float(width)
        self.height = float(height)
        self.x = 0.0
        self.y = 0.0
        self.vx = 0.0
        self.vy = 0.0
        self.mode = mode if mode in MODES else "gravity"
        self.awake = False
        self.grounded = False   # 贴底（重力模式）
        self.ceiling = False    # 贴顶（反重力模式）
        self.bounces = 0        # 累计撞墙次数（日志/测试用）
        self.set_bounds(bounds)

    # ── 几何 ───────────────────────────────────────────────────────────────────
    def set_bounds(self, bounds):
        """bounds = (left, top, right, bottom)：**屏幕物理像素**的工作区。"""
        left, top, right, bottom = bounds
        self.bounds = (float(left), float(top), float(right), float(bottom))
        self.min_x = float(left)
        self.min_y = float(top)
        self.max_x = max(float(left), float(right) - self.width)
        self.max_y = max(float(top), float(bottom) - self.height)

    def resize(self, width, height):
        """窗口尺寸变了（展开↔收起）：保持当前左上角，但重新夹进工作区。"""
        self.width, self.height = float(width), float(height)
        self.set_bounds(self.bounds)
        self.clamp()

    def place(self, x, y, keep_velocity=False):
        self.x, self.y = float(x), float(y)
        if not keep_velocity:
            self.vx = self.vy = 0.0
        self.clamp()

    def clamp(self):
        self.x = min(max(self.x, self.min_x), self.max_x)
        self.y = min(max(self.y, self.min_y), self.max_y)

    # ── 状态切换 ───────────────────────────────────────────────────────────────
    def set_mode(self, mode):
        if mode not in MODES:
            mode = "gravity"
        changed = mode != self.mode
        self.mode = mode
        if mode == "none":
            self.awake = False
            self.vx = self.vy = 0.0
        elif changed:
            # 换模式立刻醒过来，但不给初速度（避免「切换即弹飞」的突兀感）
            self.wake(settle=False)
        return changed

    def wake(self, settle=True):
        self.awake = True
        if settle:
            self.grounded = False
            self.ceiling = False

    def sleep(self):
        self.awake = False
        self.vx = self.vy = 0.0

    def throw(self, vx, vy):
        """甩出去：把速度塞给它并唤醒。"""
        self.vx, self.vy = float(vx), float(vy)
        self.awake = True
        self.grounded = self.ceiling = False

    def speed(self):
        return math.hypot(self.vx, self.vy)

    # ── 积分 ───────────────────────────────────────────────────────────────────
    def step(self, dt):
        """推进 dt 秒。返回本次撞到的边：[(edge, impact_speed), ...]。

        edge ∈ {"left", "right", "top", "bottom"}。dt 再大也会切成子步，
        所以慢 tick / 系统卡顿都不会「穿墙」。
        """
        if dt <= 0 or not self.awake or self.mode == "none":
            return []
        count = min(MAX_SUBSTEPS, max(1, int(math.ceil(dt / MAX_SUBSTEP))))
        sub = dt / count
        events = []
        for _ in range(count):
            events.extend(self._substep(sub))
            if not self.awake:
                break
        return events

    def _substep(self, dt):
        if self.mode == "gravity":
            self.vy += GRAVITY * dt
        elif self.mode == "antigravity":
            self.vy -= ANTIGRAVITY * dt

        drag = math.exp(-AIR_DRAG * dt)
        self.vx *= drag
        self.vy *= drag

        self.x += self.vx * dt
        self.y += self.vy * dt

        events = []
        # 左右
        if self.x < self.min_x:
            self.x = self.min_x
            impact = abs(self.vx)
            if impact >= MIN_BOUNCE:
                events.append(("left", impact))
                self.vx = impact * RESTITUTION
            else:
                self.vx = 0.0
            self.vy *= WALL_FRICTION
            self.bounces += 1
        elif self.x > self.max_x:
            self.x = self.max_x
            impact = abs(self.vx)
            if impact >= MIN_BOUNCE:
                events.append(("right", impact))
                self.vx = -impact * RESTITUTION
            else:
                self.vx = 0.0
            self.vy *= WALL_FRICTION
            self.bounces += 1
        # 上下
        if self.y < self.min_y:
            self.y = self.min_y
            impact = abs(self.vy)
            if impact >= MIN_BOUNCE:
                events.append(("top", impact))
                self.vy = impact * RESTITUTION
            else:
                self.vy = 0.0
            self.vx *= WALL_FRICTION
            self.bounces += 1
        elif self.y > self.max_y:
            self.y = self.max_y
            impact = abs(self.vy)
            if impact >= MIN_BOUNCE:
                events.append(("bottom", impact))
                self.vy = -impact * RESTITUTION
            else:
                self.vy = 0.0
            self.vx *= WALL_FRICTION
            self.bounces += 1

        # 睡眠判定：贴边 + 几乎不动
        on_floor = self.y >= self.max_y - 0.5
        on_ceiling = self.y <= self.min_y + 0.5
        self.grounded = on_floor
        self.ceiling = on_ceiling
        slow_v = abs(self.vy) < MIN_BOUNCE
        slow_h = abs(self.vx) < SLEEP_SPEED
        if slow_h and slow_v and abs(self.vy) < SLEEP_SPEED:
            if self.mode == "gravity" and on_floor:
                self.y = self.max_y
                self.sleep()
            elif self.mode == "antigravity" and on_ceiling:
                self.y = self.min_y
                self.sleep()
        return events

    def moved_since(self, x, y):
        return abs(self.x - x) > MOVE_EPS or abs(self.y - y) > MOVE_EPS

    def rect(self):
        return (int(round(self.x)), int(round(self.y)))
