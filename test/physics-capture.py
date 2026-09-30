#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
真机截图：把桌宠物理互动**真实窗口位图**抓下来，做成两张证据图。

抓的是隔离实例（自己起进程、自己的 state/settings），用 Win32 `PrintWindow` 抓窗口位图，
位置用 `GetWindowRect` 实测，撞击记录从桌宠自己的日志里读。所以图上每个点、每个数字都来自真机，
不是画出来的示意。

输出：
    docs/physics-live.png    轨迹图：重力下落 + 甩出去撞边框的实测轨迹、窗口缩略图、撞击记录
    docs/physics-frames.png  胶片条：6 帧真机窗口位图（带时刻/位置/速度），证明「真的抓到了不同的帧」

自检（拍不出东西就直接失败，不产出假证据）：
    * 抓到的窗口位图里非透明像素占比 ≥ 4%（否则说明 PrintWindow 拍了个空白）
    * 选中的帧两两像素差 ≥ 2.0（否则说明是同一张图复制了 6 份）
    * 甩动幅度 ≥ 250px、最终停在下方、日志里确有撞边框记录

跑法：python test/physics-capture.py
"""
import ctypes
import ctypes.wintypes as wintypes
import importlib.util
import json
import os
import sys
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "companion"))
import petphysics  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

# 复用 physics-live.py 的隔离实例启动器（文件名带连字符，没法直接 import）
_spec = importlib.util.spec_from_file_location("physics_live", os.path.join(HERE, "physics-live.py"))
live = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(live)

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

DOCS = os.path.join(ROOT, "docs")
FONT_PATH = r"C:\Windows\Fonts\msyh.ttc"


def font(size):
    try:
        return ImageFont.truetype(FONT_PATH, size)
    except OSError:
        return ImageFont.load_default()


def key_color():
    """从桌宠源码里读出 -transparentcolor 的键色，免得两边写死不一致。"""
    with open(os.path.join(ROOT, "companion", "aoqi_pet.py"), "r", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("KEY_COLOR"):
                value = line.split("=", 1)[1].strip().strip('"').strip("'").lstrip("#")
                return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
    return (255, 0, 254)


# ── Win32：抓窗口位图 ─────────────────────────────────────────────────────────
class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


def capture_window(hwnd):
    """PrintWindow 抓窗口自身的位图（不受 DPI 虚拟化影响，也不会拍到你桌面上的别的内容）。"""
    left, top, width, height = live.rect_of(hwnd)
    screen_dc = user32.GetDC(0)
    mem_dc = gdi32.CreateCompatibleDC(screen_dc)
    info = BITMAPINFO()
    info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    info.bmiHeader.biWidth = width
    info.bmiHeader.biHeight = -height          # 负数 = 自上而下
    info.bmiHeader.biPlanes = 1
    info.bmiHeader.biBitCount = 32
    info.bmiHeader.biCompression = 0           # BI_RGB
    bits = ctypes.c_void_p()
    dib = gdi32.CreateDIBSection(screen_dc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
    old = gdi32.SelectObject(mem_dc, dib)
    ok = user32.PrintWindow(hwnd, mem_dc, 2)   # 2 = PW_RENDERFULLCONTENT
    buffer = ctypes.string_at(bits, width * height * 4)
    gdi32.SelectObject(mem_dc, old)
    gdi32.DeleteObject(dib)
    gdi32.DeleteDC(mem_dc)
    user32.ReleaseDC(0, screen_dc)
    if not ok:
        raise RuntimeError("PrintWindow 失败")
    image = Image.frombuffer("RGBA", (width, height), buffer, "raw", "BGRA", 0, 1).copy()
    return image, (left, top, width, height)


SENTINELS = [(1, 2, 3), (255, 0, 255), (0, 255, 0), (255, 255, 0), (7, 200, 90)]


def keyed(image, key, tol=60):
    """抠掉窗口背景。

    实测两件事：
    1. `PrintWindow(..., PW_RENDERFULLCONTENT)` 把 `-transparentcolor` 的透明区拍成**黑色**
       （早先用 PW_CLIENTONLY 的脚本拍到的是键色本身），所以不能只按键色匹配；
    2. Pillow 的 `floodfill` 有个坑：当 `_color_diff(填充色, 种子色) <= thresh` 时它会**直接返回**
       （认为「已经是填充色了」什么都不做）。所以哨兵色必须离种子色足够远，否则静默失效。

    于是：从四角泛洪（每个角用不同的哨兵色，避免第二个角把已填的像素当种子跳过），
    这样不管背景是黑还是键色都能连根抠掉，而被精灵包住的深色（眼睛/描边）泛洪进不去，不会被误伤。
    """
    img = image.convert("RGBA")
    width, height = img.size
    work = img.convert("RGB").copy()
    used = []
    corners = [(0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)]
    for index, corner in enumerate(corners):
        seed = work.getpixel(corner)
        if seed in used:
            continue
        pick = None
        for offset in range(len(SENTINELS)):
            candidate = SENTINELS[(index + offset) % len(SENTINELS)]
            if sum(abs(a - b) for a, b in zip(candidate, seed)) > tol + 1:
                pick = candidate
                break
        if pick is None:
            continue
        try:
            ImageDraw.floodfill(work, corner, pick, thresh=tol)
            used.append(pick)
        except Exception:  # noqa: BLE001
            pass
    array = np.array(work)
    background = np.zeros(array.shape[:2], dtype=bool)
    for sentinel in used:
        background |= ((array[..., 0] == sentinel[0]) & (array[..., 1] == sentinel[1])
                       & (array[..., 2] == sentinel[2]))
    out = np.array(img)
    out[..., 3] = np.where(background, 0, 255)
    return Image.fromarray(out, "RGBA"), float(1.0 - background.mean())


# ── 采样 ─────────────────────────────────────────────────────────────────────
def record(phase, limit, quiet=0.7, interval=0.09, eps=2.0):
    """采样窗口位置 + 抓位图，直到窗口安静下来或超时。返回 (样本列表, 是否停稳)。"""
    samples = []
    started = time.time()
    last_move = started
    prev = None
    while time.time() - started < limit:
        try:
            image, rect = capture_window(phase.hwnd)
        except Exception as error:  # noqa: BLE001
            print("    抓图失败：%s" % error)
            break
        samples.append({"t": time.time() - started, "rect": rect, "img": image})
        if prev is not None and (abs(rect[0] - prev[0]) > eps or abs(rect[1] - prev[1]) > eps):
            last_move = time.time()
        prev = rect
        if time.time() - last_move >= quiet:
            return samples, True
        time.sleep(interval)
    return samples, False


def speeds(samples):
    out = []
    for a, b in zip(samples, samples[1:]):
        dt = b["t"] - a["t"]
        out.append(0.0 if dt <= 0 else (((b["rect"][0] - a["rect"][0]) ** 2
                                         + (b["rect"][1] - a["rect"][1]) ** 2) ** 0.5) / dt)
    return out or [0.0]


def find_frame_near(samples, target_t):
    return min(range(len(samples)), key=lambda i: abs(samples[i]["t"] - target_t))


# ── 出图 ─────────────────────────────────────────────────────────────────────
def draw_map(fall, throw, collisions, device, work, out_path):
    """轨迹图：工作区示意 + 实测轨迹 + 真实窗口缩略图 + 撞击记录。"""
    device_w, device_h = device
    left, top, right, bottom = work
    scale = 900.0 / device_w
    map_w, map_h = int(device_w * scale), int(device_h * scale)
    pad = 20
    panel_w = 250                      # 右侧留给「撞击记录」，免得压住轨迹
    canvas = Image.new("RGB", (map_w + pad * 3 + panel_w, map_h + pad * 2 + 92), (12, 15, 22))
    draw = ImageDraw.Draw(canvas)
    origin = (pad, pad)

    work_h = int((bottom - top) * scale)
    draw.rectangle([pad, pad, pad + map_w, pad + work_h], fill=(22, 27, 38))
    draw.rectangle([pad, pad + work_h, pad + map_w, pad + map_h], fill=(35, 42, 58))
    draw.text((pad + 8, pad + work_h + 6), "任务栏（工作区之外，宠物不会压到它）", font=font(13), fill=(150, 160, 180))
    for gx in range(0, device_w + 1, 240):
        x = pad + int(gx * scale)
        draw.line([x, pad, x, pad + map_h], fill=(32, 39, 54))
    for gy in range(0, device_h + 1, 240):
        y = pad + int(gy * scale)
        draw.line([pad, y, pad + map_w, y], fill=(32, 39, 54))
    draw.line([pad, pad + work_h, pad + map_w, pad + work_h], fill=(70, 200, 130), width=2)
    draw.text((pad + 8, pad + work_h - 20), "工作区底边（宠物常态停在这条线上）", font=font(13), fill=(70, 200, 130))

    def center(sample):
        left_, top_, width_, height_ = sample["rect"]
        return (pad + int((left_ + width_ / 2) * scale), pad + int((top_ + height_ / 2) * scale))

    if len(fall) > 1:
        draw.line([center(s) for s in fall], fill=(110, 125, 150), width=2)
    if len(throw) > 1:
        draw.line([center(s) for s in throw], fill=(255, 159, 67), width=2)

    thumb_scale = 0.42
    marked = []
    if throw:
        picks = sorted({find_frame_near(throw, t) for t in np.linspace(throw[0]["t"], throw[-1]["t"], 5)})
        for index in picks:
            sample = throw[index]
            image, _ = keyed(sample["img"], KEY)
            image = image.resize((int(image.width * scale * thumb_scale), int(image.height * scale * thumb_scale)), Image.LANCZOS)
            cx, cy = center(sample)
            canvas.paste(image, (cx - image.width // 2, cy - image.height // 2), image)
            draw.ellipse([cx - 3, cy - 3, cx + 3, cy + 3], fill=(255, 200, 90))
            marked.append(sample)

    if collisions:
        lines = []
        for edge, speed in collisions[-8:]:
            lines.append("%s %s px/s" % ({"right": "右边框", "left": "左边框", "bottom": "底边", "top": "顶边"}.get(edge, edge), speed))
            if edge == "right":
                draw.rectangle([pad + map_w - 4, pad, pad + map_w, pad + 26], fill=(255, 120, 70))
            elif edge == "left":
                draw.rectangle([pad, pad, pad + 4, pad + 26], fill=(255, 120, 70))
            elif edge == "bottom":
                draw.rectangle([pad + map_w // 2 - 30, pad + work_h - 4, pad + map_w // 2 + 30, pad + work_h], fill=(255, 120, 70))
            elif edge == "top":
                draw.rectangle([pad + map_w // 2 - 30, pad, pad + map_w // 2 + 30, pad + 4], fill=(255, 120, 70))
        box_x = pad + map_w + pad
        box_h = 30 + 22 * len(lines)
        draw.rectangle([box_x, pad, box_x + panel_w - 10, pad + box_h], fill=(28, 34, 48), outline=(70, 80, 100))
        draw.text((box_x + 12, pad + 8), "撞击记录（桌宠日志）", font=font(15), fill=(255, 190, 120))
        for i, line in enumerate(lines):
            draw.text((box_x + 12, pad + 34 + i * 22), line, font=font(14), fill=(215, 224, 238))
        draw.text((box_x + 12, pad + box_h + 16), "橙块＝撞到的那条边", font=font(13), fill=(255, 150, 100))
        draw.text((box_x + 12, pad + box_h + 38), "（位置取自日志时刻）", font=font(12), fill=(150, 160, 180))

    legend_y = pad + map_h + 26
    draw.text((pad, legend_y), "灰线＝① 重力下落　橙线＝② 甩出去之后的轨迹　缩略图＝真机 PrintWindow 抓的窗口位图（按实测坐标放置）",
              font=font(14), fill=(200, 210, 225))
    draw.text((pad, legend_y + 22), "屏幕 %dx%d，工作区 (%d,%d)-(%d,%d)：所有位置都是 GetWindowRect 实测，不是画出来的示意"
              % (device_w, device_h, left, top, right, bottom), font=font(13), fill=(140, 152, 172))
    canvas.save(out_path)
    return out_path


def draw_frames(frames, out_path, work):
    """胶片条：6 帧真机窗口位图 + 时刻/位置/速度说明。"""
    scale = 0.34
    cell_w = int(frames[0]["rect"][2] * scale)
    cell_h = int(frames[0]["rect"][3] * scale)
    gap = 10
    top_pad = 66          # 表头两行 + 每格标题，别互相压（第一版 top_pad=46 时标题压在表头上）
    canvas = Image.new("RGB", (len(frames) * (cell_w + gap) + gap, cell_h + top_pad + 30), (16, 19, 27))
    draw = ImageDraw.Draw(canvas)
    draw.text((gap, 10), "真机窗口位图（PrintWindow 抓的窗口本身）：%d 帧，不是示意图" % len(frames),
              font=font(14), fill=(228, 234, 244))
    draw.text((gap, 32), "位图里只有精灵、不含位置，所以「时刻/位置/速度」写在标注里；姿势相同则位图看起来会一样",
              font=font(12), fill=(150, 160, 180))
    for i, sample in enumerate(frames):
        image, ratio = keyed(sample["img"], KEY)
        image = image.resize((cell_w, cell_h), Image.LANCZOS)
        x = gap + i * (cell_w + gap)
        canvas.paste(image.convert("RGB"), (x, top_pad), image)
        draw.rectangle([x - 1, top_pad - 1, x + cell_w, top_pad + cell_h], outline=(60, 70, 90))
        left, top, _, _ = sample["rect"]
        draw.text((x, top_pad - 22), "%.1fs (%d,%d)" % (sample["t"], left, top), font=font(12), fill=(190, 200, 215))
        draw.text((x, top_pad + cell_h + 6), "速度 %4.0f px/s" % sample.get("speed", 0.0), font=font(12), fill=(150, 200, 160))
    canvas.save(out_path)
    return out_path


# ── 主流程 ───────────────────────────────────────────────────────────────────
def main():
    global KEY
    KEY = key_color()
    device_w = user32.GetSystemMetrics(0)
    device_h = user32.GetSystemMetrics(1)
    work = petphysics.work_area(device_w, device_h)
    print("屏幕 %dx%d；工作区 %s；透明键色 #%02x%02x%02x" % (device_w, device_h, work, *KEY))

    workdir = os.path.join(os.environ.get("TEMP", "."), "aoqi-capture")
    # 从屏幕中上方起手（x=800）：重力那一段是一条竖直线，甩动那一段全程留在屏幕内
    # （x=240 起手时，往左甩会把窗口挤出左边界，头几帧就不具代表性了）
    phase = live.Phase(workdir, live.base_settings(x=800, y=0))
    checks = []
    if not phase.start():
        print("✗ 桌宠窗口没起来")
        for line in phase.log_tail(10):
            print("   " + line)
        return 2

    print("\n① 重力下落：从 (800,0) 起手，采样 + 抓图直到停稳")
    fall, settled_fall = record(phase, 8.0)
    fx = [s["rect"][0] for s in fall]
    print("   起点 (%d,%d) → 落点 (%d,%d)；x 范围 [%d,%d]"
          % (fall[0]["rect"][0], fall[0]["rect"][1], fall[-1]["rect"][0], fall[-1]["rect"][1],
             min(fx), max(fx)))
    print("   %d 帧，停稳=%s，落点 y=%d（工作区底边 - 窗口高 = %d）"
          % (len(fall), settled_fall, fall[-1]["rect"][1], work[3] - fall[-1]["rect"][3]))
    checks.append(("重力：掉下来并停在工作区底边", settled_fall
                   and abs((fall[-1]["rect"][1] + fall[-1]["rect"][3]) - work[3]) < 12))

    print("\n② 甩动：合成鼠标抓宠物身体快速往左上甩，采样 + 抓图直到停稳")
    # 往「左上」甩：手拖时会跟随鼠标，往下拖会把窗口拖到底边外面（下一拍才被物理拉回来），
    # 那样抓到的头几帧就没有代表性了；往左上甩既自然又始终在边界内。
    #
    # 合成鼠标偶尔会「点空」——桌宠窗口是 -transparentcolor 的，透明区会把点击透给下层，
    # 所以抓取点必须落在精灵身上。判定标准不看像素，而是看**桌宠自己的日志**：
    # 它只在真的收到拖动并甩出时写「甩出：vx=...」。没这条就换个抓取点重试。
    def synthetic_throw(ratio, dx):
        """合成一次拖拽甩出。返回 False 表示这一下没抓住（透明区把点击透给了下层窗口）。"""
        rect = live.rect_of(phase.hwnd)
        grab_x = rect[0] + rect[2] // 2 + dx
        grab_y = rect[1] + int(rect[3] * ratio)
        live.set_cursor(grab_x, grab_y)
        time.sleep(0.18)
        live.mouse(0x0002)
        time.sleep(0.05)
        live.set_cursor(grab_x - 40, grab_y - 8)        # 先小挪一下，验证点击真的落在精灵身上
        time.sleep(0.06)
        if abs(live.rect_of(phase.hwnd)[0] - rect[0]) < 15:
            live.mouse(0x0004)                          # 没抓住：立刻松手，别把窗口留在半路
            return False
        for step in range(2, 20):
            live.set_cursor(grab_x - step * 34, grab_y - step * 15)   # 左上斜甩：弧线更明显
            time.sleep(0.007)
        live.mouse(0x0004)
        return True

    throw, settled_throw = [], False
    attempts = [(0.62, 0), (0.70, 0), (0.55, 0), (0.62, -30), (0.62, 30), (0.70, -20), (0.55, 20), (0.78, 0)]
    for attempt, (ratio, dx) in enumerate(attempts, start=1):
        if not synthetic_throw(ratio, dx):
            print("   第 %d 次没抓住（抓取点在 %d%%、偏移 %+d），立刻换点重试" % (attempt, ratio * 100, dx))
            time.sleep(0.25)
            continue
        print("   第 %d 次抓住了（抓取点在 %d%%、偏移 %+d），开始采样" % (attempt, ratio * 100, dx))
        throw, settled_throw = record(phase, 15.0)
        if phase.log_has("甩出：") and len(throw) > 3:
            break
        print("   但日志里没有「甩出：」（手速不够没到甩动阈值？），再试一次")

    xs = [s["rect"][0] for s in throw]
    ys = [s["rect"][1] for s in throw]
    moved = max(max(xs) - min(xs), max(ys) - min(ys)) if throw else 0
    print("   %d 帧，停稳=%s，落点 y=%d" % (len(throw), settled_throw, throw[-1]["rect"][1] if throw else -1))
    print("   x 范围 [%d,%d]，y 范围 [%d,%d]，幅度 %dpx，峰值速度 %.0f px/s"
          % (min(xs), max(xs), min(ys), max(ys), moved, max(speeds(throw))))
    checks.append(("甩动：合成鼠标的甩动被桌宠确认（日志里有「甩出：vx=...」）", phase.log_has("甩出：")))
    checks.append(("甩动：运动幅度 ≥ 250px（实际 %d）" % moved, moved >= 250))
    checks.append(("甩动：日志里确有 ≥2 条不同边的撞边框记录", len(set(edge for edge, _ in parse_collisions(phase))) >= 2))
    checks.append(("甩动之后：回到下方停稳", bool(throw) and settled_throw
                   and abs((throw[-1]["rect"][1] + throw[-1]["rect"][3]) - work[3]) < 12))

    collisions = parse_collisions(phase)
    phase.stop()

    # 选 6 帧：等间隔，但保证首尾在内
    speed_list = speeds(throw)
    for i, sample in enumerate(throw):
        sample["speed"] = speed_list[i - 1] if i > 0 else speed_list[0]
    if throw:
        indexes = sorted({find_frame_near(throw, t) for t in np.linspace(throw[0]["t"], throw[-1]["t"], 6)})
        frames = [throw[i] for i in indexes]
    else:
        frames = []

    print("\n③ 自检（拍不出东西就失败，不产出假证据）")
    if not frames:
        checks.append(("抓到了可用的窗口位图（至少 1 帧）", False))
    else:
        ratios = [keyed(s["img"], KEY)[1] for s in frames]
        checks.append(("每帧非透明像素 ≥ 4%%（实际 %s）" % ", ".join("%.0f%%" % (r * 100) for r in ratios),
                       all(r >= 0.04 for r in ratios)))
        diffs = []
        base = np.asarray(frames[0]["img"].convert("L"), dtype=float)
        for sample in frames[1:]:
            other = np.asarray(sample["img"].convert("L"), dtype=float)
            if other.shape == base.shape:
                diffs.append(float(np.abs(other - base).mean()))
        # 注意：PrintWindow 抓的是**窗口自身**的位图，里面只有精灵、不含位置，
        # 所以姿势相同的两帧本来就完全一样（差 0.0 是正常的，不是抓失败）。
        # 因此分两条断言：① 至少有两帧不同（说明动画确实在动、不是抓了个死图）
        #              ② 6 帧的**实测位置**各不相同（说明是飞行不同时刻的真实采样）
        checks.append(("位图至少两帧不同（最大平均像素差 %.1f ≥ 2.0）" % (max(diffs) if diffs else 0.0),
                       bool(diffs) and max(diffs) >= 2.0))
        centers = [(s["rect"][0], s["rect"][1]) for s in frames]
        spread = max(max(p[0] for p in centers) - min(p[0] for p in centers),
                     max(p[1] for p in centers) - min(p[1] for p in centers))
        checks.append(("6 帧的实测位置各不相同（跨度 %dpx）" % spread, spread >= 250))

    # ★ 自检全过才落盘：失败的运行绝不能用坏图覆盖 docs/ 里的好图（第一次写这脚本时踩过）
    map_path = os.path.join(DOCS, "physics-live.png")
    frames_path = os.path.join(DOCS, "physics-frames.png")
    pre_ok = all(ok for _, ok in checks)
    if pre_ok:
        os.makedirs(DOCS, exist_ok=True)
        draw_map(fall, throw, collisions, (device_w, device_h), work, map_path)
        draw_frames(frames, frames_path, work)
        sizes = [(p, os.path.getsize(p)) for p in (map_path, frames_path)]
        checks.append(("两张证据图已更新且不是空文件", all(size > 25000 for _, size in sizes)))
        for path, size in sizes:
            print("   输出 %s（%.1f KB）" % (os.path.relpath(path, ROOT), size / 1024.0))
    else:
        print("   ✗ 自检未全过 ⇒ **不落盘**：docs/ 里原有的图保持不动（不用坏证据覆盖好证据）")
        checks.append(("自检未全过时不覆盖已有证据图", False))

    print("\n=== 判定 ===")
    failed = 0
    for name, ok in checks:
        print("  [%s] %s" % ("✓" if ok else "✗", name))
        failed += 0 if ok else 1
    print("\n真机截图：%s（%d 项）" % ("全部通过 ✅" if not failed else "失败 %d 项" % failed, len(checks)))
    return 1 if failed else 0


def parse_collisions(phase):
    out = []
    try:
        with open(phase.log_path, "r", encoding="utf-8") as handle:
            for line in handle:
                if "撞边框：" not in line:
                    continue
                tail = line.split("撞边框：", 1)[1]
                edge = tail.split()[0]
                speed = tail.split("速度=")[1].split("px")[0]
                out.append((edge, speed))
    except OSError:
        pass
    return out


if __name__ == "__main__":
    raise SystemExit(main())
