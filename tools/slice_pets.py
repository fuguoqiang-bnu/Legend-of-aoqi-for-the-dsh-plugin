#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""切片工具：把用户提供的 ``R.gif`` / ``R.png`` 切成桌宠插件可用的素材。

对应交付物 1 的第 1、2、4 项：

1. ``assets/pets/<id>/base/f00.png … f15.png``
   从 GIF 逐帧裁出该宠物，抠掉白底（与画布边缘连通的近白像素 alpha=0，
   紧贴主体的一圈过渡像素按亮度给半透明），再按 **16 帧并集 bbox** 统一裁切
   （避免逐帧抖动），最长边缩放到约 160px（LANCZOS）。
2. ``assets/pets/<id>/portrait.png``
   从静态立绘 PNG 自动分割出该宠物：先按列投影做 DP 最小代价切分得到 5 个粗区间，
   再在主体掩码内做多源 BFS（测地距离）把重叠部位归给正确的主体；
   去白底后最长边缩放到 512px。最终区间写进 ``index.json`` 的 ``portraitBand``。
3. ``assets/pets/contact-sheet.png``
   5 行（宠物）× 5 列（状态）的验证图。
4. ``assets/pets/index.json``
   素材索引（``generatedAt`` / ``source`` / ``pets``）。

依赖：仅 Pillow + numpy（DSH 运行时自带），可反复运行、幂等。

用法::

    python slice_pets.py
    python slice_pets.py --gif D:\\Desktop\\R.gif --png D:\\Desktop\\R.png --out assets\\pets
    python slice_pets.py --base-size 160 --portrait-size 512 --skip-portrait
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import deque
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #

#: 近白判定阈值：min(R,G,B) >= 该值即视为“背景候选”像素
THR_BG = 240
#: 抗锯齿过渡带下界：min(R,G,B) <= 该值的主体像素完全保留（alpha=255）
THR_SOFT = 200

#: 五种状态（与 make_anim.py 保持一致）
STATES = ["idle", "working", "done", "error", "waiting"]
STATES_CN = {
    "idle": "待机",
    "working": "工作",
    "done": "完成",
    "error": "出错",
    "waiting": "等待",
}

#: 宠物定义。band 是 GIF 十六帧里稳定的横向区间（含端点），
#: 区间 → 宠物的对应关系由实际图像确认：
#:   第1区间蓝色狼(shui) / 第2区间深蓝红角(an) / 第3区间绿色(mu) /
#:   第4区间黄色(jin) / 第5区间红金(huo)
PETS = [
    {"id": "jin",  "name": "诺亚", "element": "金", "elementAlt": "时空",
     "color": "#f7c948", "band": (396, 460)},
    {"id": "mu",   "name": "阿瑞斯", "element": "木",
     "color": "#4ecb8b", "band": (253, 329)},
    {"id": "shui", "name": "帝释天", "element": "水",
     "color": "#7fd4ff", "band": (15, 103)},
    {"id": "huo",  "name": "龙炎", "element": "火",
     "color": "#ff5f52", "band": (479, 567)},
    {"id": "an",   "name": "修尔", "element": "暗",
     "color": "#6c5ce7", "band": (135, 207)},
]

#: 静态立绘 PNG 里 5 只宠物从左到右的身份顺序（已用实际图像确认）
PORTRAIT_ORDER = ["an", "huo", "jin", "shui", "mu"]

DEFAULT_GIF = r"D:\Desktop\R.gif"
DEFAULT_PNG = r"D:\Desktop\R.png"


def _use_utf8_stdout() -> None:
    """Windows 控制台默认 GBK，改成 UTF-8 以免中文日志乱码（失败则忽略）。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# 基础图像工具
# --------------------------------------------------------------------------- #

def _shift(mask: np.ndarray, dy: int, dx: int) -> np.ndarray:
    """把布尔掩码整体平移 (dy, dx)，空出的位置补 False。"""
    out = np.zeros_like(mask)
    h, w = mask.shape
    ys0, ys1 = max(0, dy), min(h, h + dy)
    xs0, xs1 = max(0, dx), min(w, w + dx)
    out[ys0:ys1, xs0:xs1] = mask[ys0 - dy:ys1 - dy, xs0 - dx:xs1 - dx]
    return out


def dilate8(mask: np.ndarray) -> np.ndarray:
    """8 邻域膨胀一格（不依赖 scipy）。"""
    out = mask.copy()
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dy or dx:
                out |= _shift(mask, dy, dx)
    return out


def content_mask(rgb: np.ndarray) -> np.ndarray:
    """主体掩码：非近白像素。"""
    return rgb.min(axis=2) < THR_BG


def near_white_alpha(rgb: np.ndarray, thr: int = THR_BG, soft: int = THR_SOFT) -> np.ndarray:
    """抠白底，返回 alpha 通道（uint8）。

    * 与画布四边连通（4 邻域）的近白像素判为背景 → ``alpha = 0``；
    * 主体内部被轮廓包住的白色（例如浅色毛发的白色）不会被误删；
    * 紧贴主体的一圈过渡像素（8 邻域相邻、且比 pure white 暗）按亮度给
      0..255 的半透明值，保留抗锯齿边缘。
    """
    rgb = rgb.astype(np.int16)
    hard = rgb.min(axis=2) >= thr
    h, w = hard.shape

    bg = np.zeros_like(hard)
    stack: deque[tuple[int, int]] = deque()
    for x in range(w):
        for y in (0, h - 1):
            if hard[y, x] and not bg[y, x]:
                bg[y, x] = True
                stack.append((y, x))
    for y in range(h):
        for x in (0, w - 1):
            if hard[y, x] and not bg[y, x]:
                bg[y, x] = True
                stack.append((y, x))
    while stack:
        y, x = stack.pop()
        for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
            if 0 <= ny < h and 0 <= nx < w and hard[ny, nx] and not bg[ny, nx]:
                bg[ny, nx] = True
                stack.append((ny, nx))

    alpha = np.where(bg, 0, 255).astype(np.uint8)
    mn = rgb.min(axis=2)
    rim = (~bg) & dilate8(bg) & (mn > soft)
    if rim.any():
        a = np.clip(np.round(255.0 * (255.0 - mn) / float(255 - soft)), 0, 255)
        alpha[rim] = a[rim].astype(np.uint8)
    return alpha


def bbox_of(mask: np.ndarray) -> tuple[int, int, int, int]:
    """返回掩码的 (x0, y0, x1, y1)，全空时返回 (-1, -1, -1, -1)。"""
    ys, xs = np.nonzero(mask)
    if len(ys) == 0:
        return (-1, -1, -1, -1)
    return (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))


def resize_longest(img: Image.Image, size: int) -> Image.Image:
    """把图片的最长边缩放到 ``size``（LANCZOS），保持比例，保留 alpha。"""
    w, h = img.size
    scale = size / float(max(w, h))
    if abs(scale - 1.0) < 1e-9:
        return img
    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
    return img.resize((nw, nh), Image.LANCZOS)


def connected_components(mask: np.ndarray, min_area: int = 1):
    """行程编码 + 并查集的 8 邻域连通域标记（不依赖 scipy）。

    返回 ``(labels, comps)``；``comps`` 是 ``[(area, (x0,y0,x1,y1), label)]``，
    按面积从大到小排序。
    """
    h, w = mask.shape
    parent: dict[int, int] = {}

    def find(a: int) -> int:
        root = a
        while parent[root] != root:
            root = parent[root]
        while parent[a] != root:
            parent[a], a = root, parent[a]
        return root

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    labels = np.zeros((h, w), dtype=np.int32)
    prev_runs: list[tuple[int, int, int]] = []
    nxt = 1
    for y in range(h):
        row = mask[y]
        runs: list[tuple[int, int]] = []
        x = 0
        while x < w:
            if row[x]:
                x0 = x
                while x < w and row[x]:
                    x += 1
                runs.append((x0, x - 1))
            else:
                x += 1
        cur: list[tuple[int, int, int]] = []
        for x0, x1 in runs:
            lab = nxt
            nxt += 1
            parent[lab] = lab
            for py0, py1, plab in prev_runs:
                if x1 >= py0 - 1 and py1 >= x0 - 1:
                    union(lab, plab)
            cur.append((x0, x1, lab))
        for x0, x1, lab in cur:
            labels[y, x0:x1 + 1] = lab
        prev_runs = cur

    groups: dict[int, list[int]] = {}
    for lab in parent:
        groups.setdefault(find(lab), []).append(lab)

    comps = []
    for root_lab, members in groups.items():
        sel = np.isin(labels, members)
        area = int(sel.sum())
        if area < min_area:
            continue
        comps.append((area, bbox_of(sel), root_lab, sel))
    comps.sort(key=lambda t: t[0], reverse=True)
    return labels, comps


# --------------------------------------------------------------------------- #
# GIF：逐帧切分
# --------------------------------------------------------------------------- #

def load_gif_frames(path: Path) -> tuple[list[np.ndarray], dict]:
    """读取 GIF 全部帧（转 RGB 数组），返回 (frames, info)。"""
    im = Image.open(path)
    info = dict(im.info)
    n = getattr(im, "n_frames", 1)
    frames = []
    for i in range(n):
        im.seek(i)
        frames.append(np.asarray(im.convert("RGB")))
    return frames, {"size": im.size, "frames": n, "info_keys": sorted(info)}


def slice_gif_pet(frames: list[np.ndarray], band: tuple[int, int]) -> tuple[list[Image.Image], tuple[int, int, int, int]]:
    """裁出某只宠物的 16 帧（统一画布 + 抠白底）。

    返回 ``(frames_rgba, union_bbox)``，``union_bbox`` 是 16 帧内容并集 bbox。
    """
    x0, x1 = band
    subs = [f[:, x0:x1 + 1] for f in frames]
    union = np.zeros(subs[0].shape[:2], dtype=bool)
    for s in subs:
        union |= content_mask(s)
    bx0, by0, bx1, by1 = bbox_of(union)
    if bx0 < 0:  # 兜底：区间内没有内容时退回整块
        bx0, by0, bx1, by1 = 0, 0, subs[0].shape[1] - 1, subs[0].shape[0] - 1

    out = []
    for s in subs:
        crop = s[by0:by1 + 1, bx0:bx1 + 1]
        alpha = near_white_alpha(crop)
        out.append(Image.fromarray(np.dstack([crop, alpha]), "RGBA"))
    return out, (bx0 + x0, by0, bx1 + x0, by1)


# --------------------------------------------------------------------------- #
# PNG：静态立绘分割
# --------------------------------------------------------------------------- #

def detect_bands_by_projection(mask: np.ndarray, n: int = 5, min_w_ratio: float = 0.14) -> list[tuple[int, int]]:
    """列投影 + DP：找 n-1 条“代价最小”的切分线（代价 = 该列主体像素数）。

    立绘里的宠物横向重叠，投影不会有 0 谷底，所以用动态规划在所有满足
    “每段宽度 >= min_w”的切分方案里取总代价最小的一组。
    """
    colsum = mask.sum(axis=0).astype(np.int64)
    w = int(colsum.shape[0])
    min_w = max(1, int(w * min_w_ratio))
    inf = 1 << 60

    # dp[k][j]：第 k 条切分线落在 j 时的最小累计代价（k 从 1 开始）
    dp = np.full((n, w), inf, dtype=np.int64)
    prev = np.full((n, w), -1, dtype=np.int64)
    dp[0, :] = 0
    for k in range(1, n):
        # 第 k 条切分线 j：左边 k 段各 >= min_w，右边还剩 (n-k) 段各 >= min_w
        for j in range(min_w * k, w - min_w * (n - k) + 1):
            best, bi = inf, -1
            for i in range(min_w * (k - 1), j - min_w + 1):
                if dp[k - 1, i] < best:
                    best, bi = int(dp[k - 1, i]), i
            if bi >= 0:
                dp[k, j] = best + int(colsum[j])
                prev[k, j] = bi

    last = int(np.argmin(dp[n - 1]))
    cuts = []
    j = last
    for k in range(n - 1, 0, -1):
        cuts.append(j)
        j = int(prev[k, j])
    cuts.reverse()

    bounds = [0] + cuts + [w]
    return [(bounds[i], bounds[i + 1] - 1) for i in range(n)]


def geodesic_split(mask: np.ndarray, seeds: list[tuple[str, np.ndarray]]) -> np.ndarray:
    """多源 BFS：把掩码内每个像素分配给测地距离最近的种子。

    这样横向重叠的部位（例如 A 的尾巴伸进 B 的区间）会沿着“离谁更近”
    归属，而不是被直线切断。
    """
    h, w = mask.shape
    owner = np.zeros((h, w), dtype=np.int32)
    index = {pid: i + 1 for i, (pid, _) in enumerate(seeds)}
    dq: deque[tuple[int, int]] = deque()
    for pid, seed in seeds:
        ys, xs = np.nonzero(seed & mask)
        for y, x in zip(ys.tolist(), xs.tolist()):
            if owner[y, x] == 0:
                owner[y, x] = index[pid]
                dq.append((y, x))
    neigh = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))
    while dq:
        y, x = dq.popleft()
        lab = owner[y, x]
        for dy, dx in neigh:
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and owner[ny, nx] == 0:
                owner[ny, nx] = lab
                dq.append((ny, nx))
    return owner


def split_portraits(rgb: np.ndarray, n: int = 5):
    """把整张立绘分割成 n 只宠物。

    返回 ``(bands, labels_id, label_masks)``：
    ``bands`` 是按 x 的粗切分区间，``label_masks`` 是 {pet_id: 掩码}。
    """
    mask = content_mask(rgb)
    bands = detect_bands_by_projection(mask, n=n)

    seeds: list[tuple[str, np.ndarray]] = []
    for pid, (x0, x1) in zip(PORTRAIT_ORDER, bands):
        _, comps = connected_components(mask[:, x0:x1 + 1], min_area=200)
        if comps:
            core = comps[0][3]
            # 腐蚀 3 次得到更“内核”的种子，避免把重叠部位算进种子
            for _ in range(3):
                core = core & _shift(core, 1, 0) & _shift(core, -1, 0) & _shift(core, 0, 1) & _shift(core, 0, -1)
                if not core.any():
                    core = comps[0][3]
                    break
        else:
            core = np.zeros_like(mask[:, x0:x1 + 1])
        full = np.zeros_like(mask)
        full[:, x0:x1 + 1] = core
        seeds.append((pid, full))

    owner = geodesic_split(mask, seeds)
    label_masks = {}
    for i, (pid, _) in enumerate(seeds):
        label_masks[pid] = (owner == i + 1) & mask
    return bands, label_masks


def portrait_rgba(rgb: np.ndarray, pet_mask: np.ndarray, size: int) -> Image.Image:
    """按主体掩码裁出立绘并抠白底（主体 alpha=255，边缘一圈过渡半透明）。"""
    x0, y0, x1, y1 = bbox_of(pet_mask)
    if x0 < 0:
        raise RuntimeError("立绘分割失败：掩码为空")
    x0, y0 = max(0, x0 - 2), max(0, y0 - 2)
    x1, y1 = min(rgb.shape[1] - 1, x1 + 2), min(rgb.shape[0] - 1, y1 + 2)
    crop = rgb[y0:y1 + 1, x0:x1 + 1].astype(np.int16)
    sub = pet_mask[y0:y1 + 1, x0:x1 + 1]

    alpha = np.where(sub, 255, 0).astype(np.uint8)
    mn = crop.min(axis=2)
    rim = (~sub) & dilate8(sub) & (mn > THR_SOFT)
    if rim.any():
        a = np.clip(np.round(255.0 * (255.0 - mn) / float(255 - THR_SOFT)), 0, 255)
        alpha[rim] = a[rim].astype(np.uint8)
    img = Image.fromarray(np.dstack([crop.astype(np.uint8), alpha]), "RGBA")
    return resize_longest(img, size)


# --------------------------------------------------------------------------- #
# 验证图
# --------------------------------------------------------------------------- #

def _checker(w: int, h: int, cell: int = 8, light=(240, 240, 240), dark=(222, 222, 222)) -> Image.Image:
    """浅灰棋盘底。"""
    arr = np.zeros((h, w, 3), np.uint8)
    yy, xx = np.mgrid[0:h, 0:w]
    odd = ((yy // cell + xx // cell) % 2).astype(bool)
    arr[...] = light
    arr[odd] = dark
    return Image.fromarray(arr, "RGB")


def _font(size: int = 14):
    """尽量用系统无衬线字体，失败则回落到 Pillow 内置位图字体。"""
    for name in ("arial.ttf", "msyh.ttc", "simhei.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=size)
    except Exception:
        return ImageFont.load_default()


def build_contact_sheet(out_dir: Path, pets: list[dict], states: list[str],
                        frame_index: dict[str, int] | None = None,
                        max_width: int = 1400) -> Path:
    """生成 ``contact-sheet.png``：5 行（宠物）× 5 列（状态）。

    每格优先用 ``<state>.gif`` 的指定帧（默认 idle 取第 0 帧、其余取第 4 帧），
    GIF 还不存在时回落到 ``base/f00.png``（这样 make_anim.py 之前也能出图）。
    """
    frame_index = frame_index or {"idle": 0, "working": 4, "done": 4, "error": 4, "waiting": 4}
    tiles: list[tuple[Image.Image, str]] = []
    for pet in pets:
        pdir = out_dir / pet["id"]
        for state in states:
            gif = pdir / f"{state}.gif"
            img = None
            if gif.exists():
                im = Image.open(gif)
                idx = min(frame_index.get(state, 0), getattr(im, "n_frames", 1) - 1)
                im.seek(idx)
                img = im.convert("RGBA")
            if img is None:
                base = pdir / "base" / "f00.png"
                if base.exists():
                    img = Image.open(base).convert("RGBA")
            if img is None:
                img = Image.new("RGBA", (64, 64), (255, 0, 255, 255))
            tiles.append((img, f"{pet['id']} / {state}"))

    pad_x, pad_top, pad_bottom = 6, 18, 6
    cw = max(t.size[0] for t, _ in tiles) + pad_x * 2
    ch = max(t.size[1] for t, _ in tiles) + pad_top + pad_bottom
    ncols, nrows = len(states), len(pets)
    sheet = Image.new("RGB", (cw * ncols, ch * nrows), (255, 255, 255))
    draw = ImageDraw.Draw(sheet)
    font = _font(14)

    for row, pet in enumerate(pets):
        for col, state in enumerate(states):
            idx = row * ncols + col
            img, label = tiles[idx]
            cell = _checker(cw, ch)
            cell.paste(img, ((cw - img.size[0]) // 2, pad_top + (ch - pad_top - pad_bottom - img.size[1]) // 2), img)
            cd = ImageDraw.Draw(cell)
            cd.text((6, 3), label, fill=(70, 70, 70), font=font)
            cd.rectangle([0, 0, cw - 1, ch - 1], outline=(205, 205, 205))
            sheet.paste(cell, (col * cw, row * ch))

    if sheet.size[0] > max_width:  # 控制宽度上限
        scale = max_width / sheet.size[0]
        sheet = sheet.resize((max_width, max(1, int(sheet.size[1] * scale))), Image.LANCZOS)

    path = out_dir / "contact-sheet.png"
    sheet.save(path, optimize=True)
    return path


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #

def run(gif_path: Path, png_path: Path, out_dir: Path, base_size: int,
        portrait_size: int, skip_portrait: bool, skip_sheet: bool) -> dict:
    frames, gif_info = load_gif_frames(gif_path)
    print(f"[gif] {gif_path} 尺寸={gif_info['size']} 帧数={gif_info['frames']}")

    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- 1. 逐帧切分 ----
    pet_frames: dict[str, list[Image.Image]] = {}
    for pet in PETS:
        pid = pet["id"]
        fdir = out_dir / pid / "base"
        fdir.mkdir(parents=True, exist_ok=True)
        imgs, ubox = slice_gif_pet(frames, pet["band"])
        scaled = [resize_longest(im, base_size) for im in imgs]
        expected = {f"f{i:02d}.png" for i in range(len(scaled))}
        for stale in fdir.glob("*.png"):  # 清掉上一次运行留下的、命名不符的帧
            if stale.name not in expected:
                stale.unlink()
        for i, im in enumerate(scaled):
            im.save(fdir / f"f{i:02d}.png", optimize=True)
        pet_frames[pid] = scaled
        print(f"[base] {pid:5s} band={pet['band']} 并集bbox={ubox} "
              f"导出={len(scaled)}帧 画布={scaled[0].size}")

    # ---- 2. 静态立绘 ----
    portrait_bands: dict[str, tuple[int, int]] = {}
    if not skip_portrait:
        rgb = np.asarray(Image.open(png_path).convert("RGB"))
        bands, label_masks = split_portraits(rgb, n=len(PORTRAIT_ORDER))
        for pid, (x0, x1) in zip(PORTRAIT_ORDER, bands):
            portrait_bands[pid] = (x0, x1)
            img = portrait_rgba(rgb, label_masks[pid], portrait_size)
            img.save(out_dir / pid / "portrait.png", optimize=True)
            area = int(label_masks[pid].sum())
            print(f"[portrait] {pid:5s} 区间=({x0},{x1}) 主体像素={area} 输出={img.size}")
    else:
        for pid in PORTRAIT_ORDER:
            portrait_bands[pid] = (0, 0)

    # ---- 3. index.json ----
    index = {
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": {"gif": str(gif_path), "png": str(png_path)},
        "pets": [],
    }
    for pet in PETS:
        pid = pet["id"]
        entry = {
            "id": pid,
            "name": pet["name"],
            "element": pet["element"],
            "color": pet["color"],
            "band": [pet["band"][0], pet["band"][1]],
            "portraitBand": [portrait_bands[pid][0], portrait_bands[pid][1]],
            "frames": len(pet_frames[pid]),
            "size": list(pet_frames[pid][0].size),
            "states": list(STATES),
        }
        if "elementAlt" in pet:
            entry["elementAlt"] = pet["elementAlt"]
        index["pets"].append(entry)

    def _default(o):
        if isinstance(o, (np.integer,)):
            return int(o)
        raise TypeError(str(type(o)))

    (out_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2, default=_default) + "\n",
        encoding="utf-8")
    print(f"[index] {out_dir / 'index.json'}")

    # ---- 4. 验证图 ----
    if not skip_sheet:
        sheet = build_contact_sheet(out_dir, PETS, STATES)
        print(f"[sheet] {sheet} 尺寸={Image.open(sheet).size}")
    return index


def main(argv: list[str] | None = None) -> int:
    _use_utf8_stdout()
    ap = argparse.ArgumentParser(
        description="把 R.gif / R.png 切成桌宠插件素材（帧、立绘、验证图、索引）。")
    ap.add_argument("--gif", default=DEFAULT_GIF, help="源 GIF（默认 %(default)s）")
    ap.add_argument("--png", default=DEFAULT_PNG, help="源立绘 PNG（默认 %(default)s）")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "assets" / "pets"),
                    help="输出目录（默认 <repo>/assets/pets）")
    ap.add_argument("--base-size", type=int, default=160, help="帧最长边像素（默认 160）")
    ap.add_argument("--portrait-size", type=int, default=512, help="立绘最长边像素（默认 512）")
    ap.add_argument("--skip-portrait", action="store_true", help="跳过静态立绘")
    ap.add_argument("--skip-sheet", action="store_true", help="跳过 contact-sheet.png")
    args = ap.parse_args(argv)

    gif_path, png_path = Path(args.gif), Path(args.png)
    for p in (gif_path, png_path):
        if not p.exists() and not (args.skip_portrait and p == png_path):
            print(f"找不到源文件：{p}", file=sys.stderr)
            return 2
    run(gif_path, png_path, Path(args.out), args.base_size, args.portrait_size,
        args.skip_portrait, args.skip_sheet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
