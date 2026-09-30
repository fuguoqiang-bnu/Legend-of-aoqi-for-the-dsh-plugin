#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
桌宠物理体的单测（不需要 GUI，也不需要 DSH）。

跑法：
    python test/pet-physics.py
退出码 0 = 全过；非 0 = 有失败项（并打印失败详情）。
"""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "companion"))

import petphysics  # noqa: E402
from petphysics import Body, work_area  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

BOUNDS = (0.0, 0.0, 1920.0, 1080.0)
W, H = 216.0, 312.0

CASES = []


def case(name):
    def wrapper(fn):
        CASES.append((name, fn))
        return fn
    return wrapper


def sim(body, seconds, dt=1 / 60):
    """按固定 dt 跑一段时间，返回所有碰撞事件。"""
    events = []
    steps = int(round(seconds / dt))
    for _ in range(steps):
        events.extend(body.step(dt))
    return events


# ── 重力模式 ───────────────────────────────────────────────────────────────────
@case("重力模式：从顶上掉下来，撞到底边会弹，最后睡在底边")
def t_gravity_fall():
    body = Body(W, H, BOUNDS, mode="gravity")
    body.place(800, 0)
    assert body.max_y == 1080 - H
    body.wake()
    events = sim(body, 6.0)
    edges = [edge for edge, _ in events]
    assert "bottom" in edges, "没撞到底边：%r" % edges
    assert body.bounces >= 1, "撞墙计数没增加"
    assert body.awake is False, "应该已经睡着（停止每帧移动窗口）"
    assert abs(body.y - body.max_y) < 0.5, "睡着后没停在底边：y=%r" % body.y


@case("重力模式：弹跳速度逐次衰减（能量不会变大）")
def t_gravity_decay():
    body = Body(W, H, BOUNDS, mode="gravity")
    body.place(800, 0)
    body.wake()
    speeds = []
    for _ in range(int(6.0 * 60)):
        for edge, speed in body.step(1 / 60):
            if edge == "bottom":
                speeds.append(speed)
    assert len(speeds) >= 2, "至少该弹两次才能比衰减：%r" % speeds
    for before, after in zip(speeds, speeds[1:]):
        assert after < before, "第 n 次弹跳速度没有变小：%r" % speeds
    assert speeds[0] <= math.sqrt(2 * petphysics.GRAVITY * body.max_y) + 1, "首次撞击速度超过自由落体上限"


@case("重力模式：落到底边后不会无限抖动（没有再发射事件）")
def t_gravity_settles():
    body = Body(W, H, BOUNDS, mode="gravity")
    body.place(800, 200)
    body.wake()
    sim(body, 8.0)
    assert body.awake is False
    late = sim(body, 2.0)
    assert late == [], "睡着后还在产生碰撞事件：%r" % late


# ── 反重力模式 ─────────────────────────────────────────────────────────────────
@case("反重力模式：从底面往上飘，撞到顶边，最后贴顶悬停")
def t_antigravity_rise():
    body = Body(W, H, BOUNDS, mode="antigravity")
    body.place(800, BOUNDS[3] - H)
    body.wake()
    ys = []
    events = []
    for _ in range(int(10.0 * 60)):
        events.extend(body.step(1 / 60))
        ys.append(body.y)
    assert min(ys) <= body.min_y + 1, "没有飘到顶部：min(y)=%r" % min(ys)
    assert any(edge == "top" for edge, _ in events), "没有记录到撞顶：%r" % events
    assert ys[-1] <= ys[0], "反重力模式下整体应该是在上升"
    assert body.awake is False, "贴顶后应该睡着"
    assert abs(body.y - body.min_y) < 0.5, "睡着后没贴住顶边：y=%r" % body.y


@case("反重力与重力：同样时长里反重力是向上、重力是向下（方向相反）")
def t_modes_opposite():
    up = Body(W, H, BOUNDS, mode="antigravity")
    up.place(800, 400)
    up.wake()
    sim(up, 0.25, dt=1 / 240)
    down = Body(W, H, BOUNDS, mode="gravity")
    down.place(800, 400)
    down.wake()
    sim(down, 0.25, dt=1 / 240)
    assert up.y < 400 < down.y, "方向不对：up.y=%r down.y=%r" % (up.y, down.y)


# ── 甩动 / 碰撞 ────────────────────────────────────────────────────────────────
@case("甩动：throw 赋予速度；水平甩出去会撞到左右边框")
def t_throw_hits_sides():
    left = Body(W, H, BOUNDS, mode="none")
    left.place(900, 300)
    left.set_mode("gravity")
    left.throw(-1400, -200)
    events = sim(left, 4.0)
    assert any(edge == "left" for edge, _ in events), "没撞到左边框：%r" % events
    assert left.x >= left.min_x - 0.001

    right = Body(W, H, BOUNDS, mode="none")
    right.place(600, 300)
    right.set_mode("gravity")
    right.throw(1500, -150)
    events = sim(right, 4.0)
    assert any(edge == "right" for edge, _ in events), "没撞到右边框：%r" % events
    assert right.x <= right.max_x + 0.001


@case("甩动：撞墙后法向速度按 RESTITUTION 衰减（不是镜面全反射）")
def t_restitution():
    body = Body(W, H, BOUNDS, mode="none")
    body.place(1000, 500)
    body.set_mode("gravity")
    body.throw(900, 0)
    first = None
    for _ in range(int(6.0 * 240)):
        for edge, speed in body.step(1 / 240):
            if edge == "right":
                first = speed
                break
        if first is not None:
            break
    assert first is not None, "没撞到右边框"
    assert abs(body.vx) < first, "反弹速度应该小于入射速度：%r vs %r" % (body.vx, first)
    assert abs(abs(body.vx) - first * petphysics.RESTITUTION) < first * 0.25, "反弹系数偏离预期太多"


@case("不会穿墙：超大 dt 也不能跑到工作区外面")
def t_no_tunneling():
    body = Body(W, H, BOUNDS, mode="gravity")
    body.place(960, 540)
    body.throw(3000, 3000)
    for _ in range(30):
        body.step(1.0)                      # 一次给 1 秒，故意刁难
        assert body.min_x <= body.x <= body.max_x, "x 跑出边界：%r" % body.x
        assert body.min_y <= body.y <= body.max_y, "y 跑出边界：%r" % body.y


@case("物理关闭（none）：不移动、不产生事件")
def t_mode_none():
    body = Body(W, H, BOUNDS, mode="none")
    body.place(500, 500)
    body.throw(500, -500)
    assert body.step(0.5) == []
    assert (body.x, body.y) == (500.0, 500.0)


@case("place 越界会被夹回工作区；resize 之后仍在区内")
def t_clamp_and_resize():
    body = Body(W, H, BOUNDS, mode="gravity")
    body.place(-9999, 99999)
    assert body.x == body.min_x and body.y == body.max_y
    body.resize(400, 500)
    assert body.x <= body.max_x and body.y <= body.max_y
    assert body.max_x == 1920 - 400 and body.max_y == 1080 - 500


@case("dt 无关性：30fps 与 480fps 结果一致（都睡在底边同一位置）")
def t_dt_independence():
    coarse = Body(W, H, BOUNDS, mode="gravity")
    coarse.place(800, 0)
    coarse.wake()
    sim(coarse, 6.0, dt=1 / 30)
    fine = Body(W, H, BOUNDS, mode="gravity")
    fine.place(800, 0)
    fine.wake()
    sim(fine, 6.0, dt=1 / 480)
    assert coarse.awake is False and fine.awake is False, "两次都该睡着了"
    assert abs(coarse.y - fine.y) < 2.0, "不同帧率落点差太多：%r vs %r" % (coarse.y, fine.y)
    assert abs(coarse.y - coarse.max_y) < 2.0


@case("反重力模式：切换模式立刻醒来，但初速度为 0（不会切换即弹飞）")
def t_switch_mode():
    body = Body(W, H, BOUNDS, mode="gravity")
    body.place(800, 400)
    body.sleep()
    assert body.awake is False
    assert body.set_mode("antigravity") is True
    assert body.awake is True
    assert (body.vx, body.vy) == (0.0, 0.0)
    assert body.set_mode("antigravity") is False, "同模式重复设置应返回 False"


@case("工作区：拿到的是合法的矩形，且窗口能放进去")
def t_work_area():
    rect = work_area(1920, 1080)
    left, top, right, bottom = rect
    assert right > left and bottom > top, "工作区不合法：%r" % (rect,)
    body = Body(W, H, rect, mode="gravity")
    assert body.max_x >= body.min_x and body.max_y >= body.min_y


def main():
    failed = []
    for name, fn in CASES:
        try:
            fn()
            print("  [✓] %s" % name)
        except AssertionError as error:
            failed.append((name, "断言失败：%s" % error))
            print("  [✗] %s\n        %s" % (name, error))
        except Exception as error:  # noqa: BLE001
            failed.append((name, "%s: %s" % (type(error).__name__, error)))
            print("  [✗] %s\n        %s: %s" % (name, type(error).__name__, error))
    print("")
    if failed:
        print("失败 %d 项，通过 %d 项" % (len(failed), len(CASES) - len(failed)))
        for name, why in failed:
            print("  - %s → %s" % (name, why))
        return 1
    print("通过 %d 项，失败 0 项" % len(CASES))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
