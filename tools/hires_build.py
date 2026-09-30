#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从《奥奇传说》页游官方高清立绘生成「精细素材」（hires）。

为什么需要它
------------
`assets/pets/<id>/base/fNN.png` 是从用户给的 R.gif 里抠的：源帧只有约 **122px** 高，
再放大到 160px 显示在 1.25 倍缩放的屏幕上（物理 200px）—— 实打实的「放大就糊」。
本脚本换一条路：拿**页游官网精灵图鉴的官方立绘**（370×344 透明 PNG）当底，
**降采样**到显示尺寸（默认 190px 高，LANCZOS），再做程序化动作，
于是每一帧都是「缩小」而不是「放大」，边缘干净。

产物（与 base/ 并存，互不覆盖）
-------------------------------
    assets/pets/<id>/hires/source.png        官方原图（留档，便于复现与核对）
    assets/pets/<id>/hires/f00.png           静态高清帧（给静态图标/文档用）
    assets/pets/<id>/hires/{idle,working,done,error,waiting}.gif   16 帧动画

素材偏好由桌宠的 companion-settings.json 的 ``material`` 决定：
    auto（默认，有 hires 就用 hires）/ frames（原味 16 帧逐帧动画）/ hires（官方高清）

用法::

    python tools/hires_build.py                 # 下载（如缺）+ 生成
    python tools/hires_build.py --offline       # 只用本地已下载的 source.png
    python tools/hires_build.py --height 220    # 换生成高度
    python tools/hires_build.py --only huo,an   # 只做部分宠物

⚠️ 官方立绘版权归百田信息科技（百奥家庭互动）所有，仅限个人学习/本地装饰用途。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_anim  # noqa: E402  —— 复用它的状态变换、装饰绘制与 GIF 编码器

ROOT = Path(__file__).resolve().parent.parent
UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"),
    "Accept-Language": "zh-CN,zh;q=0.9",
}

#: 五只小五王 ←→ 页游官方「传说五王」对应关系（考证见 docs/NAMES.md）
#: 每条都记录了官方图鉴详情页与立绘原图 URL，可复现、可核对。
SOURCES = {
    "huo": {
        "official": "传说王者·龙炎",
        "element": "火",
        "detail": "http://aoqi.100bt.com/jingling/294874.html",
        "image": "http://img4.a0bi.com/upload/articleResource/20190126/1548494939099.png",
    },
    "an": {
        "official": "传说王者·修尔",
        "element": "暗/魔神",
        "detail": "http://aoqi.100bt.com/jingling/291858.html",
        "image": "http://img4.a0bi.com/upload/articleResource/20180827/1535363199494.png",
    },
    "mu": {
        "official": "传说王者·阿瑞斯",
        "element": "木/生命",
        "detail": "http://aoqi.100bt.com/jingling/296843.html",
        "image": "http://img4.a0bi.com/upload/articleResource/20190603/1559552204681.png",
    },
    "shui": {
        "official": "传说王者·帝释天",
        "element": "水/冰",
        "detail": "http://aoqi.100bt.com/jingling/295815.html",
        "image": "http://img4.a0bi.com/upload/articleResource/20190318/1552911544803.png",
    },
    "jin": {
        "official": "传说王者·诺亚",
        "element": "时空/光",
        "detail": "http://aoqi.100bt.com/jingling/293589.html",
        "image": "http://img4.a0bi.com/upload/articleResource/20181119/1542622829659.png",
    },
}

#: 与 make_anim 保持一致的状态时长（ms）
STATE_DURATION = dict(make_anim.STATE_DURATION)


def download(url: str, target: Path, log=print) -> bool:
    target.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(request, timeout=30) as response:
                data = response.read()
            if not data.startswith(b"\x89PNG"):
                log(f"    [bad] 不是 PNG：{url}")
                return False
            target.write_bytes(data)
            log(f"    [ok ] {len(data) // 1024}KB ← {url}")
            return True
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            log(f"    [retry {attempt + 1}] {type(error).__name__}: {error}")
            time.sleep(0.8 * (attempt + 1))
    log(f"    [fail] {url}")
    return False


def normalize(image: Image.Image, height: int, width: int, margin: int = 4) -> Image.Image:
    """裁到内容 bbox → 等比缩放**放进 (width × height) 的内接框**（这里是降采样）→ 底对齐居中。

    为什么要限宽：官方立绘是「张开翅膀」的横构图（帝释天 370×344 缩放后仍有 257px 宽），
    而桌宠窗口只有 216px 宽 —— 不限宽就会被窗口裁掉两翼。
    """
    rgba = image.convert("RGBA")
    alpha = np.asarray(rgba)[..., 3]
    rows = np.where(alpha.max(axis=1) > 8)[0]
    cols = np.where(alpha.max(axis=0) > 8)[0]
    if len(rows) and len(cols):
        rgba = rgba.crop((int(cols.min()), int(rows.min()), int(cols.max()) + 1, int(rows.max()) + 1))
    scale = min(height / rgba.height, width / rgba.width)
    resized = rgba.resize((max(1, round(rgba.width * scale)), max(1, round(rgba.height * scale))), Image.LANCZOS)
    canvas = Image.new("RGBA", (width + margin * 2, height + margin), (0, 0, 0, 0))
    canvas.alpha_composite(resized, ((canvas.width - resized.width) // 2, margin + (height - resized.height)))
    return canvas


def idle_frames(base: Image.Image) -> list[Image.Image]:
    """官方立绘只有一张静态图，所以 idle 用程序化「呼吸」：上下 3px + 1px 横向微摆。"""
    import math
    w, h = base.size
    top = 4                                  # 与 normalize 的 margin 对齐，给起伏留位置
    canvas = Image.new("RGBA", (w, h + top), (0, 0, 0, 0))
    frames = []
    for index in range(16):
        phase = 2 * math.pi * index / 16.0
        dy = -int(round(2.5 * abs(math.sin(phase))))
        dx = int(round(1.0 * math.sin(phase)))
        layer = canvas.copy()
        layer.alpha_composite(base, (max(0, dx), top + dy))
        frames.append(layer)
    return frames


def build_pet(pet: str, out_root: Path, height: int, width: int, offline: bool, colors: int, max_kb: int) -> dict:
    info = SOURCES[pet]
    pet_dir = out_root / "pets" / pet / "hires"
    source = pet_dir / "source.png"
    print(f"  {pet} ← {info['official']}（{info['element']}）")

    if not source.is_file() and not offline:
        download(info["image"], source)
    if not source.is_file():
        print("    [skip] 没有 source.png（--offline？）")
        return {}

    base = normalize(Image.open(source), height, width)
    base.save(pet_dir / "f00.png")

    frames = idle_frames(base)
    budget = max_kb * 1024
    make_anim.save_state_gif(frames, pet_dir / "idle.gif", STATE_DURATION["idle"], colors, budget)
    for state in ("working", "done", "error", "waiting"):
        state_frames = make_anim.make_state_frames([base] * 16, state)
        make_anim.save_state_gif(state_frames, pet_dir / f"{state}.gif", STATE_DURATION[state], colors, budget)

    return {
        "official": info["official"],
        "element": info["element"],
        "detail": info["detail"],
        "sourceImage": info["image"],
        "sourceSize": list(Image.open(source).size),
        "frameHeight": base.height,
        "canvasWidth": base.width,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="用页游官方高清立绘生成精细素材")
    parser.add_argument("--root", default=str(ROOT), help="仓库根目录")
    parser.add_argument("--height", type=int, default=190, help="生成帧高度上限（像素，降采样）")
    parser.add_argument("--width", type=int, default=196, help="生成帧宽度上限（像素，防止立绘超出窗口）")
    parser.add_argument("--colors", type=int, default=200, help="调色板颜色上限")
    parser.add_argument("--max-kb", type=int, default=200, help="单个 GIF 体积预算（KB），0=不限")
    parser.add_argument("--only", default="", help="只处理这些宠物 id，逗号分隔")
    parser.add_argument("--offline", action="store_true", help="不联网，只用本地 source.png")
    args = parser.parse_args(argv)

    root = Path(args.root)
    pets = [p.strip() for p in args.only.split(",") if p.strip()] or list(SOURCES)
    report = {}
    print(f"生成精细素材：内接 {args.width}x{args.height}px，调色板 {args.colors} 色，单文件 ≤{args.max_kb}KB")
    for pet in pets:
        if pet not in SOURCES:
            print(f"  [skip] 未知宠物 {pet}")
            continue
        built = build_pet(pet, root / "assets", args.height, args.width, args.offline, args.colors, args.max_kb)
        if built:
            report[pet] = built

    index_path = root / "assets" / "pets" / "hires-index.json"
    index_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    total = 0
    print("\n产物：")
    for pet in report:
        pet_dir = root / "assets" / "pets" / pet / "hires"
        for path in sorted(pet_dir.glob("*.gif")):
            size = path.stat().st_size
            total += size
            with Image.open(path) as gif:
                print(f"  {pet}/hires/{path.name:12s} {gif.size} {gif.n_frames} 帧 "
                      f"{size // 1024}KB 时长={gif.info.get('duration')}ms")
    print(f"合计 GIF {total / 1024:.0f}KB；索引 → {index_path.relative_to(root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
