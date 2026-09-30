#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""动画工具：读 ``assets/pets/<id>/base/f*.png``，程序化生成 5 种状态 GIF。

状态与参数（全部基于同一组 16 帧做变换，保持透明背景）：

===========  ========  ==========================================================
状态          帧时长     变换
===========  ========  ==========================================================
idle         110ms     16 帧原样，无限循环
working      70ms      竖直弹跳 ``dy = -round(3*abs(sin(2*pi*i/16)))`` +
                       横向摆动 ±1px + 头顶 2 个白色小光点循环淡入淡出
done         90ms      前 8 帧起跳（``dy`` 0 → -14 → 0），后 8 帧回到原样 +
                       头顶 3 颗金色四角星（多边形绘制）先放大后淡出
error        90ms      横向抖动 ``dx = round(4*sin(3*pi*i/16))`` +
                       头顶红色感叹号圆牌（红圆 + 白竖条 + 白点）
waiting      120ms     竖直轻微上下 ±2px + 头顶蓝色对话气泡（带 ``…``）
===========  ========  ==========================================================

绘制统一用 Pillow：先合成角色，再在一张独立的 ``ImageDraw`` 图层上画装饰，
最后 ``Image.alpha_composite`` 叠回去；所有坐标都按该宠物画布尺寸自适应。

GIF 导出用「全局调色板 + 保留 255 号透明索引」，尽量把单文件压到 80KB 以内。
运行结束后会刷新 ``index.json`` 的 ``states`` 字段并重建 ``contact-sheet.png``。

用法::

    python make_anim.py
    python make_anim.py --out assets\\pets --colors 96
    python make_anim.py --only jin,shui
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.dont_write_bytecode = True  # 不生成 tools/__pycache__，保持仓库干净
sys.path.insert(0, str(Path(__file__).resolve().parent))
from slice_pets import (  # noqa: E402  （同目录脚本，便于共用常量与验证图逻辑）
    PETS,
    STATES,
    _use_utf8_stdout,
    build_contact_sheet,
)

#: 透明索引（GIF 调色板保留 255 号给透明像素）
TRANSPARENT_INDEX = 255

#: 每种状态的帧时长（毫秒，均为 10ms 的整数倍，GIF 可精确表示）
STATE_DURATION = {
    "idle": 110,
    "working": 70,
    "done": 90,
    "error": 90,
    "waiting": 120,
}

#: 需要额外留出头顶空间的状态（idle 保持 base 画布“原样”）
DECORATED = ("working", "done", "error", "waiting")

#: 装饰配色
WHITE = (255, 255, 255)
GOLD = (255, 206, 66)
RED = (231, 60, 60)
BUBBLE_BLUE = (74, 150, 255)


# --------------------------------------------------------------------------- #
# 几何 / 绘制小工具
# --------------------------------------------------------------------------- #

def content_bbox(img: Image.Image) -> tuple[int, int, int, int]:
    """返回图片中 alpha>0 内容的 (x0, y0, x1, y1)，空图返回 (-1,-1,-1,-1)。"""
    a = np.asarray(img.getchannel("A"))
    ys, xs = np.nonzero(a > 16)
    if len(ys) == 0:
        return (-1, -1, -1, -1)
    return (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))


def star_points(cx: float, cy: float, r_out: float, r_in: float,
                points: int = 4, rot: float = 0.0) -> list[tuple[float, float]]:
    """四角（或 n 角）星的多边形顶点：外顶点/内顶点交替，默认第一尖朝正上。"""
    pts = []
    for k in range(points * 2):
        ang = rot - math.pi / 2 + k * math.pi / points
        r = r_out if k % 2 == 0 else r_in
        pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    return pts


def draw_sparkle(draw: ImageDraw.ImageDraw, cx: float, cy: float, r: float, alpha: int) -> None:
    """白色小光点（外圈柔光 + 内芯）。"""
    if alpha <= 0 or r <= 0:
        return
    a_out = int(alpha * 0.35)
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=WHITE + (a_out,))
    ri = r * 0.55
    draw.ellipse([cx - ri, cy - ri, cx + ri, cy + ri], fill=WHITE + (alpha,))


def draw_star(draw: ImageDraw.ImageDraw, cx: float, cy: float, r: float, alpha: int,
              color=GOLD) -> None:
    """金色四角星（多边形绘制 + 一层描边增强可读性）。"""
    if alpha <= 0 or r < 1:
        return
    pts = star_points(cx, cy, r, r * 0.34, points=4)
    draw.polygon(pts, fill=color + (alpha,))
    dark = (168, 108, 16, int(alpha * 0.55))
    draw.line(pts + [pts[0]], fill=dark, width=max(1, int(r * 0.14)))


def draw_badge(draw: ImageDraw.ImageDraw, cx: float, cy: float, r: float) -> None:
    """红色感叹号圆牌：红圆 + 白竖条 + 白点。"""
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=RED + (240,))
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(150, 24, 24, 255),
                 width=max(1, int(r * 0.18)))
    bar_w = max(2.0, r * 0.30)
    draw.rounded_rectangle([cx - bar_w / 2, cy - r * 0.52, cx + bar_w / 2, cy + r * 0.16],
                           radius=bar_w / 2, fill=WHITE + (255,))
    dot_r = max(1.5, r * 0.17)
    draw.ellipse([cx - dot_r, cy + r * 0.34 - dot_r, cx + dot_r, cy + r * 0.34 + dot_r],
                 fill=WHITE + (255,))


def draw_bubble(draw: ImageDraw.ImageDraw, cx: float, top: float, w: float, h: float) -> None:
    """蓝色对话气泡（圆角矩形 + 小尾巴 + 三个白点，即 ``…``）。"""
    left, right = cx - w / 2, cx + w / 2
    bottom = top + h
    draw.rounded_rectangle([left, top, right, bottom], radius=h * 0.42,
                           fill=BUBBLE_BLUE + (235,), outline=(36, 92, 190, 255),
                           width=max(1, int(h * 0.10)))
    tail = h * 0.30
    tw = w * 0.16
    draw.polygon([(cx - tw * 0.7, bottom - 1), (cx + tw * 0.7, bottom - 1),
                  (cx, bottom + tail)], fill=BUBBLE_BLUE + (235,))
    dot_r = max(1.5, h * 0.10)
    for k, fx in enumerate((0.28, 0.5, 0.72)):
        dx = (k - 1) * dot_r * 1.9
        cy = top + h * 0.5
        draw.ellipse([cx + dx - dot_r, cy - dot_r, cx + dx + dot_r, cy + dot_r],
                     fill=WHITE + (255,))


# --------------------------------------------------------------------------- #
# 逐状态生成
# --------------------------------------------------------------------------- #

def _offsets(base_w: int, base_h: int, state: str) -> list[tuple[int, int]]:
    """返回 16 帧的 (dx, dy) 位移。"""
    out = []
    for i in range(16):
        if state == "idle":
            out.append((0, 0))
        elif state == "working":
            out.append((int(round(1.0 * math.sin(2 * math.pi * i / 16))),
                        -int(round(3 * abs(math.sin(2 * math.pi * i / 16))))))
        elif state == "done":
            dy = -int(round(14 * math.sin(math.pi * i / 7))) if i < 8 else 0
            out.append((0, dy))
        elif state == "error":
            out.append((int(round(4 * math.sin(3 * math.pi * i / 16))), 0))
        elif state == "waiting":
            out.append((0, int(round(2 * math.sin(2 * math.pi * i / 16)))))
        else:
            raise ValueError(f"未知状态：{state}")
    return out


def _painter(state: str, base_w: int, base_h: int):
    """返回 painter(overlay, index, sprite_layer)，在独立图层上画头顶装饰。"""
    if state == "idle":
        return None
    r_dot = max(2.0, base_h * 0.030)
    r_star = max(4.0, base_h * 0.055)
    r_badge = max(7.0, base_h * 0.090)
    bub_w = max(26.0, base_w * 0.62)
    bub_h = max(15.0, base_h * 0.17)

    def painter(overlay: Image.Image, i: int, sprite_layer: Image.Image) -> None:
        x0, y0, x1, y1 = content_bbox(sprite_layer)
        if x0 < 0:
            return
        draw = ImageDraw.Draw(overlay, "RGBA")
        cx = (x0 + x1) / 2.0
        top = float(y0)
        gap = base_h * 0.045

        if state == "working":
            # 两个白色小光点：相位错开，循环淡入淡出
            for k, (fx, phase) in enumerate(((0.34, 0.0), (0.66, 0.5))):
                t = (i / 16.0 + phase) % 1.0
                alpha = int(255 * max(0.0, math.sin(math.pi * t)))
                sx = x0 + (x1 - x0) * fx
                sy = top - gap - r_dot - (r_dot * 1.6 if k else 0)
                draw_sparkle(draw, sx, sy, r_dot, alpha)
        elif state == "done":
            # 三颗四角星：整体先放大后淡出
            for k, fx in enumerate((0.24, 0.5, 0.76)):
                grow = min(1.0, (i - k * 0.6) / 5.0)
                if grow <= 0:
                    continue
                scale = 0.45 + 0.75 * grow
                fade = max(0.0, 1.0 - max(0, i - 7 - k * 0.5) / 6.0)
                alpha = int(255 * min(1.0, grow * 2.2) * fade)
                sx = x0 + (x1 - x0) * fx
                sy = top - gap - r_star * (1.0 + 0.5 * (1 - grow)) - (k % 2) * r_star * 0.8
                draw_star(draw, sx, sy, r_star * scale, alpha)
        elif state == "error":
            draw_badge(draw, cx, top - gap - r_badge, r_badge)
        elif state == "waiting":
            draw_bubble(draw, cx, top - gap - bub_h, bub_w, bub_h)

    return painter


def make_state_frames(base: list[Image.Image], state: str) -> list[Image.Image]:
    """把 16 帧 base 变换成某状态的 16 帧（RGBA，统一画布）。

    先按“够用”的余量铺一层画布（给位移和头顶装饰留位置），
    再把 16 帧 **共用的内容并集 bbox** 裁掉，得到尽量紧凑又对齐的画布。
    """
    w, h = base[0].size
    if state == "idle":  # 原样输出，不加内边距
        return [im.copy() for im in base]

    pad_x = max(12, round(0.10 * w))
    pad_top = max(30, round(0.42 * h))
    pad_bottom = max(10, round(0.08 * h))
    cw, ch = w + pad_x * 2, h + pad_top + pad_bottom

    offsets = _offsets(w, h, state)
    painter = _painter(state, w, h)
    frames = []
    for i, im in enumerate(base):
        dx, dy = offsets[i]
        layer = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        layer.alpha_composite(im, (pad_x + dx, pad_top + dy))
        if painter is not None:
            overlay = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
            painter(overlay, i, layer)
            layer = Image.alpha_composite(layer, overlay)
        frames.append(layer)

    # 16 帧共用的内容并集 bbox（保留 2px 余量）→ 统一裁切，避免逐帧抖动
    x0, y0 = cw, ch
    x1 = y1 = -1
    for f in frames:
        bx0, by0, bx1, by1 = content_bbox(f)
        if bx0 < 0:
            continue
        x0, y0 = min(x0, bx0), min(y0, by0)
        x1, y1 = max(x1, bx1), max(y1, by1)
    if x1 >= 0:
        box = (max(0, x0 - 2), max(0, y0 - 2), min(cw, x1 + 3), min(ch, y1 + 3))
        frames = [f.crop(box) for f in frames]
    return frames


# --------------------------------------------------------------------------- #
# GIF 导出（全局调色板 + 透明索引 + 自写 GIF89a 编码器）
# --------------------------------------------------------------------------- #
#
# 为什么不用 Pillow 存多帧 GIF：Pillow 的编码器会把「与上一帧像素完全相同」的帧
# 合并到上一帧并累加时长（GifImagePlugin._write_multiple_frames），而源素材 16 帧里
# 本来就有成对的重复姿势 —— 结果 16 帧会被写成 9 帧、110ms 变成 220ms。
# 这里自己写 GIF89a（含 LZW 编码），保证「16 帧 / 精确时长 / 透明索引」原样落地。


def _lzw_encode(indices: bytes, min_code_size: int) -> bytes:
    """GIF 变体的 LZW 压缩（返回未分块的字节流）。"""
    clear = 1 << min_code_size
    end = clear + 1
    code_size = min_code_size + 1
    table: dict[int, int] = {}
    next_code = end + 1
    out = bytearray()
    bitbuf = bitcnt = 0

    def emit(code: int, size: int) -> None:
        nonlocal bitbuf, bitcnt
        bitbuf |= code << bitcnt
        bitcnt += size
        while bitcnt >= 8:
            out.append(bitbuf & 0xFF)
            bitbuf >>= 8
            bitcnt -= 8

    emit(clear, code_size)
    prev = -1
    for b in indices:
        if prev < 0:
            prev = b
            continue
        key = (prev << 8) | b
        nxt = table.get(key)
        if nxt is not None:
            prev = nxt
            continue
        emit(prev, code_size)
        if next_code < 4096:
            table[key] = next_code
            next_code += 1
            # 码长增长点：next_code 超过 2^code_size 时才加宽（与 GIF 解码器同步，
            # 实测用 (2^code_size - 1) 会得到 broken data stream）
            if next_code > (1 << code_size) and code_size < 12:
                code_size += 1
        else:  # 码表满：发 clear 重来
            emit(clear, code_size)
            table.clear()
            next_code = end + 1
            code_size = min_code_size + 1
        prev = b
    if prev >= 0:
        emit(prev, code_size)
    emit(end, code_size)
    if bitcnt:
        out.append(bitbuf & 0xFF)
    return bytes(out)


def write_gif(path: Path, index_frames: list[np.ndarray], palette: list[int],
              transparent_index: int, duration_ms: int, loop: int = 0) -> int:
    """手写 GIF89a：全局调色板 + 每帧图形控制扩展（disposal=2 / 透明）+ LZW 数据。"""
    h, w = index_frames[0].shape
    need = max(transparent_index + 1, 2)
    bits = 2
    while (1 << bits) < need:
        bits += 1
    gct_size = 1 << bits

    pal = list(palette[:transparent_index * 3])
    pal += [0] * max(0, gct_size * 3 - len(pal))

    out = bytearray()
    out += b"GIF89a"
    out += struct.pack("<HH", w, h)
    out += bytes([0xF0 | (bits - 1), 0, 0])  # 有全局调色板 / 颜色深度 8 / 表大小 2^bits
    out += bytes(pal)
    out += b"\x21\xff\x0bNETSCAPE2.0\x03\x01" + struct.pack("<H", loop) + b"\x00"  # 无限循环

    delay = max(1, round(duration_ms / 10.0))
    for arr in index_frames:
        out += (b"\x21\xf9\x04" + bytes([(2 << 2) | 1]) + struct.pack("<H", delay)
                + bytes([transparent_index]) + b"\x00")
        out += b"\x2c" + struct.pack("<HHHH", 0, 0, w, h) + b"\x00"
        lzw = _lzw_encode(arr.astype(np.uint8).tobytes(), bits)
        out += bytes([bits])
        for i in range(0, len(lzw), 255):
            chunk = lzw[i:i + 255]
            out += bytes([len(chunk)]) + chunk
        out += b"\x00"
    out += b"\x3b"
    path.write_bytes(bytes(out))
    return len(out)


def to_indexed_frames(frames: list[Image.Image], colors: int):
    """把 RGBA 帧量化成共用一张全局调色板的索引图。

    调色板用 FASTOCTREE 从「所有帧拼接成的长条图」上一次量化得到：
    全局调色板 + 少而稳的色阶，实测比逐帧 MEDIANCUT 小 30% 左右且观感更好。

    返回 ``(索引帧列表, 调色板 RGB 列表, 透明索引)``。
    """
    n = max(2, min(colors, 255))
    w = sum(f.size[0] for f in frames)
    strip = Image.new("RGB", (w, frames[0].size[1]))
    x = 0
    for f in frames:
        strip.paste(f.convert("RGB"), (x, 0))
        x += f.size[0]
    pal_img = strip.quantize(colors=n, method=Image.Quantize.FASTOCTREE,
                             dither=Image.Dither.NONE)

    idx_frames: list[np.ndarray] = []
    max_used = 0
    alphas = []
    for f in frames:
        q = np.array(f.convert("RGB").quantize(palette=pal_img, dither=Image.Dither.NONE))
        a = np.asarray(f.getchannel("A"))
        opaque = q[a >= 128]  # GIF 不支持半透明：<128 视为全透明
        if opaque.size:
            max_used = max(max_used, int(opaque.max()))
        idx_frames.append(q)
        alphas.append(a)

    transparent_index = max_used + 1  # 紧接着最后一个用到的颜色
    for q, a in zip(idx_frames, alphas):
        q[a < 128] = transparent_index  # 阈值化：透明像素统一映射到透明索引

    pal = ((pal_img.getpalette() or []) + [0] * 768)[:max(768, (transparent_index + 1) * 3)]
    return idx_frames, pal, transparent_index


def save_state_gif(frames: list[Image.Image], path: Path, duration: int, colors: int,
                   max_bytes: int = 0) -> tuple[int, int]:
    """写出单文件 GIF（无限循环），返回 ``(字节数, 实际用的颜色数)``。

    ``max_bytes > 0`` 时会沿颜色阶梯（``colors → 64 → 48 → 40 → 32 → 24``）逐步降色，
    尽量把单文件压进预算内；已经在预算内就用最高画质。
    写完后会用 Pillow 重新读一遍，确认帧数与单帧时长都对得上。
    """
    ladder = []
    for c in (colors, 64, 48, 40, 32, 24):
        c = min(c, colors)
        if c >= 2 and c not in ladder:
            ladder.append(c)
    ladder.sort(reverse=True)

    size, used = 0, ladder[0]
    for c in ladder:
        idx_frames, pal, trans = to_indexed_frames(frames, c)
        size = write_gif(path, idx_frames, pal, trans, duration, loop=0)
        used = c
        if not max_bytes or size <= max_bytes:
            break

    # 自检：帧数 / 时长 / 透明像素（任何一项不对都要立刻暴露，而不是交出去才发现）
    with Image.open(path) as im:
        n = getattr(im, "n_frames", 1)
        durs = []
        min_alpha = 10 ** 9
        for i in range(n):
            im.seek(i)
            durs.append(im.info.get("duration"))
            a = np.asarray(im.convert("RGBA").getchannel("A"))
            min_alpha = min(min_alpha, int((a < 20).sum()))
    if n != len(frames) or any(d != duration for d in durs) or min_alpha <= 0:
        raise RuntimeError(f"GIF 自检失败 {path.name}: 帧={n}/{len(frames)} 时长={set(durs)} "
                           f"透明像素={min_alpha}")
    return size, used


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #

def load_base(pet_dir: Path) -> list[Image.Image]:
    files = sorted((pet_dir / "base").glob("f*.png"))
    if not files:
        raise FileNotFoundError(f"{pet_dir / 'base'} 下没有 f*.png，请先运行 slice_pets.py")
    return [Image.open(p).convert("RGBA") for p in files]


def run(out_dir: Path, colors: int, only: list[str] | None, skip_sheet: bool,
        max_kb: float = 80.0) -> dict:
    index_path = out_dir / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": {"gif": "", "png": ""},
        "pets": [{"id": p["id"], "name": p["name"], "element": p["element"],
                  "color": p["color"], "band": list(p["band"]), "portraitBand": [0, 0],
                  "frames": 16, "size": [0, 0], "states": list(STATES)} for p in PETS],
    }

    for entry in index["pets"]:
        pid = entry["id"]
        if only and pid not in only:
            continue
        pet_dir = out_dir / pid
        base = load_base(pet_dir)
        meta = {}
        for state in STATES:
            frames = make_state_frames(base, state)
            path = pet_dir / f"{state}.gif"
            size, used = save_state_gif(frames, path, STATE_DURATION[state], colors,
                                        max_bytes=int(max_kb * 1024))
            meta[state] = {
                "frames": len(frames),
                "duration": STATE_DURATION[state],
                "size": list(frames[0].size),
                "bytes": size,
                "colors": used,
            }
            print(f"[gif] {pid:5s} {state:8s} 帧={len(frames):2d} 时长={STATE_DURATION[state]}ms "
                  f"画布={frames[0].size} 配色={used:3d} 大小={size / 1024:5.1f}KB"
                  f"{'  > 预算' if max_kb and size > max_kb * 1024 else ''}")
        entry["states"] = list(STATES)
        entry["animation"] = meta
        entry["size"] = list(base[0].size)

    index["animGeneratedAt"] = datetime.now().astimezone().isoformat(timespec="seconds")
    def _default(o):
        if isinstance(o, np.integer):
            return int(o)
        raise TypeError(str(type(o)))

    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2, default=_default) + "\n",
                          encoding="utf-8")
    print(f"[index] 已更新 states/animation -> {index_path}")

    if not skip_sheet:
        sheet = build_contact_sheet(out_dir, PETS, STATES)
        print(f"[sheet] {sheet} 尺寸={Image.open(sheet).size}")
    return index


def main(argv: list[str] | None = None) -> int:
    _use_utf8_stdout()
    ap = argparse.ArgumentParser(description="由 base 帧生成 5 种状态 GIF，并更新 index.json。")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "assets" / "pets"),
                    help="素材目录（默认 <repo>/assets/pets）")
    ap.add_argument("--colors", type=int, default=96,
                    help="GIF 调色板颜色数上限（默认 96；超预算时自动沿阶梯降色）")
    ap.add_argument("--max-kb", type=float, default=80.0,
                    help="单文件大小预算 KB，0 表示不限制（默认 80）")
    ap.add_argument("--only", default="", help="只处理这些宠物 id，逗号分隔")
    ap.add_argument("--skip-sheet", action="store_true", help="不重建 contact-sheet.png")
    args = ap.parse_args(argv)

    out_dir = Path(args.out)
    if not out_dir.exists():
        print(f"素材目录不存在：{out_dir}", file=sys.stderr)
        return 2
    only = [s.strip() for s in args.only.split(",") if s.strip()] or None
    run(out_dir, args.colors, only, args.skip_sheet, args.max_kb)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
