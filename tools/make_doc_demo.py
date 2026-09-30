#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 README 用的**动态演示图** `docs/demo.gif`。

它证明两件事，而且完全不碰真机：
  1. 桌宠的「动态」是素材层面的真逐帧，不是截图摆拍 —— 演示用的就是仓库里那 16 帧 × 5 状态；
  2. 生图产物（assets/theme/aurora-sky-raw.png）确实进了成品 —— 它就是这张演示图的背景。

做法：读 assets/pets/<pet>/{base,hires} 的 5 个状态 GIF → 拆帧 → 贴到生图极光背景上 →
逐帧写状态标签 → 输出成一张循环 GIF。素材帧与桌宠窗口里跑的是同一批文件，所以看到的就是它实际的样子。

    python tools/make_doc_demo.py --pet huo --max-kb 1400
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageSequence

REPO = Path(__file__).resolve().parent.parent
STATES = ("idle", "working", "done", "error", "waiting")
STATE_LABEL = {
    "idle": "待机 idle",
    "working": "干活 working",
    "done": "完成 done",
    "error": "出错 error",
    "waiting": "等你 waiting",
}
FONT_CANDIDATES = (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf")


def load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def backdrop(width: int, height: int, blur: float = 7.0) -> Image.Image:
    """生图极光原图 → 裁成演示图背景（没有就用纯深色兜底）。

    背景会被刻意**模糊**：GIF 每帧都要重画背景，细节越多体积越大；
    模糊之后既好看又压得下来（这张图只是衬托，主角是素材帧）。
    """
    raw = REPO / "assets" / "theme" / "aurora-sky-raw.png"
    if raw.exists():
        sky = Image.open(raw).convert("RGB")
        scale = max(width / sky.width, height / sky.height) * 1.1
        sky = sky.resize((max(width, int(sky.width * scale)), max(height, int(sky.height * scale))), Image.LANCZOS)
        left = (sky.width - width) // 2
        top = (sky.height - height) // 2
        sky = sky.crop((left, top, left + width, top + height))
        if blur > 0:
            sky = sky.filter(ImageFilter.GaussianBlur(blur))
        shade = Image.new("L", (width, height), 0)
        draw = ImageDraw.Draw(shade)
        for y in range(height):
            draw.line([(0, y), (width, y)], fill=int(120 + 90 * (y / height)))
        return Image.composite(Image.new("RGB", (width, height), (8, 10, 24)), sky, shade)
    return Image.new("RGB", (width, height), (10, 12, 28))


def state_frames(pet: str, material: str) -> dict[str, list[Image.Image]]:
    """按桌宠自己的规则找素材：
       frames → assets/pets/<id>/<state>.gif
       hires  → assets/pets/<id>/hires/<state>.gif
    """
    out: dict[str, list[Image.Image]] = {}
    for state in STATES:
        if material == "hires":
            path = REPO / "assets" / "pets" / pet / "hires" / ("%s.gif" % state)
        else:
            path = REPO / "assets" / "pets" / pet / ("%s.gif" % state)
        if not path.exists():
            raise SystemExit("缺素材：%s（先跑 tools/hires_build.py 或 tools/make_anim.py）" % path)
        frames = [frame.convert("RGBA").copy() for frame in ImageSequence.Iterator(Image.open(path))]
        out[state] = frames
    return out


def compose(pet: str, materials: dict[str, dict[str, list[Image.Image]]], width: int, height: int,
            scale: float, per_state: int) -> list[Image.Image]:
    bg = backdrop(width, height).convert("RGBA")
    title = load_font(26)
    label = load_font(22)
    small = load_font(18)

    # 左边放「原味逐帧」，右边放「官方高清」——一张图同时展示两套素材
    columns = [("frames", "原味逐帧（16 帧）"), ("hires", "官方高清立绘")]
    out: list[Image.Image] = []
    for state in STATES:
        longest = max(len(materials[key][state]) for key, _ in columns)
        for index in range(per_state):
            frame = bg.copy()
            draw = ImageDraw.Draw(frame)
            draw.text((26, 18), "奥奇传说 · 小五王桌宠", font=title, fill=(190, 230, 255, 255))
            draw.text((26, 52), "状态：%s" % STATE_LABEL[state], font=label, fill=(255, 226, 160, 255))
            for slot, (key, caption) in enumerate(columns):
                frames = materials[key][state]
                sprite = frames[(index * longest // per_state) % len(frames)]
                sprite = sprite.resize((int(sprite.width * scale), int(sprite.height * scale)), Image.LANCZOS)
                x = int(width * (0.28 + 0.42 * slot)) - sprite.width // 2
                y = height - sprite.height - 54
                frame.alpha_composite(sprite, (max(0, x), max(0, y)))
                draw.text((x, height - 40), caption, font=small, fill=(210, 225, 240, 255))
            out.append(frame.convert("RGB"))
    return out


def save_gif(frames: list[Image.Image], path: Path, duration: int, max_kb: int) -> tuple[int, int]:
    """按颜色阶梯逐步降色，保证体积不超标（与 tools/make_anim.py 同一思路）。"""
    best: tuple[int, int] | None = None
    for colors in (128, 96, 64, 48, 32):
        quantized = [f.convert("P", palette=Image.ADAPTIVE, colors=colors) for f in frames]
        tmp = path.with_suffix(".tmp.gif")
        quantized[0].save(tmp, save_all=True, append_images=quantized[1:], duration=duration, loop=0,
                          optimize=True, disposal=2)
        size = tmp.stat().st_size
        best = (colors, size)
        if not max_kb or size <= max_kb * 1024:
            tmp.replace(path)
            return colors, size
    if best is not None:
        path.with_suffix(".tmp.gif").replace(path)
    return best if best else (0, 0)


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="生成 README 动态演示 docs/demo.gif")
    parser.add_argument("--pet", default="huo")
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=320)
    parser.add_argument("--scale", type=float, default=0.9)
    parser.add_argument("--per-state", type=int, default=5, help="每个状态取几帧（×5 状态 = 总帧数）")
    parser.add_argument("--duration", type=int, default=110)
    parser.add_argument("--max-kb", type=int, default=1200)
    parser.add_argument("--out", default=str(REPO / "docs" / "demo.gif"))
    args = parser.parse_args(argv)

    materials = {key: state_frames(args.pet, key) for key in ("frames", "hires")}
    frames = compose(args.pet, materials, args.width, args.height, args.scale, args.per_state)
    out = Path(args.out)
    colors, size = save_gif(frames, out, args.duration, args.max_kb)
    print("演示图：%s  %d 帧  %dx%d  %d 色  %.0fKB" % (
        out, len(frames), frames[0].width, frames[0].height, colors, size / 1024))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
