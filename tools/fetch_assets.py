#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""网络素材抓取：抓取《奥奇传说》（百田 / 百奥）官方公开图片，供 README 与主题装饰使用。

设计要点
--------
* **纯标准库**：``urllib.request`` 为主，失败时回退到系统 ``curl``（``subprocess``）；
  Pillow 只在抓取后做一次「能不能打开」的额外校验，缺了也不影响主流程。
* **内置真实可用的 URL 清单**（默认 12 条，全部实测 200）：**只取页游（百田《奥奇传说》页游
  ``aoqi.100bt.com``）与其静态资源域 ``resource.a0bi.com/marketnew/aoqi/``**，另有官方资讯图床
  ``img4.a0bi.com`` 与发行商百田网 ``www.100bt.com``。
  **不含手游（aqsy / ``/aoqi/m/``）资源** —— 本项目只针对页游口径。
* 每条记录最终 URL、HTTP 状态、Content-Type、字节数、sha256、保存路径与 ``note``。
* **合法性校验**：按 magic bytes 判断 PNG/JPEG/GIF/WEBP，非法文件会删除并在 manifest 里标 ``invalid``。
* **礼貌抓取**：串行 + 每条间隔 0.3s + 15s 超时 + 普通浏览器 UA + 失败重试 1 次；
  403/404 等直接记进 manifest 后跳过，不抛栈退出。
* 输出：``assets/fetched/<host>/<slug>.<ext>`` 与 ``assets/fetched/manifest.json``。

用法::

    python fetch_assets.py                      # 抓内置清单
    python fetch_assets.py --dry-run            # 只打印计划
    python fetch_assets.py --urls my.txt        # 用文件里的 URL 覆盖（每行一个，可写 "url,note"）
    python fetch_assets.py --urls https://a/x.png,https://b/y.jpg
    python fetch_assets.py --no-pillow          # 跳过 Pillow 校验

⚠️ 版权声明：奥奇传说及其角色形象的版权归百田信息科技（百奥家庭互动）所有。
本脚本抓取的图片仅用于个人学习与桌宠装饰用途，仓库不主张任何所有权，
如需商用请自行联系版权方获得授权。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
TIMEOUT = 15          # 单次请求超时（秒）
SLEEP_BETWEEN = 0.3   # 每条之间的礼貌间隔（秒）
RETRY = 1             # 失败重试次数

COPYRIGHT_NOTE = "仅供个人学习与桌宠装饰，版权归百田信息科技（百奥家庭互动）所有"

#: magic bytes → 扩展名
MAGIC = [
    (b"\x89PNG\r\n\x1a\n", "png", "PNG"),
    (b"\xff\xd8\xff", "jpg", "JPEG"),
    (b"GIF87a", "gif", "GIF"),
    (b"GIF89a", "gif", "GIF"),
    (b"RIFF", "webp", "WEBP"),  # 需再看 8..12 字节是否为 WEBP
]

#: 内置 URL 清单（全部实测 HTTP 200；``slug`` 决定保存文件名）
BUILTIN_URLS: list[dict] = [
    {
        "url": "https://resource.a0bi.com/marketnew/aoqi/dest/scss/img_s/xinshoubg.jpg",
        "slug": "aoqi-site-kv-xinshou",
        "note": "奥奇传说官网（aoqi.100bt.com）首页新手引导横幅 KV，1920x574。" + COPYRIGHT_NOTE,
    },
    {
        "url": "https://resource.a0bi.com/marketnew/aoqi/dest/scss/img_s/indexBg.jpg",
        "slug": "aoqi-site-index-bg",
        "note": "奥奇传说官网首页主背景（含官方角色立绘），1920x899。" + COPYRIGHT_NOTE,
    },
    {
        "url": "https://resource.a0bi.com/marketnew/aoqi/dest/scss/img_s/jinglingBg.jpg",
        "slug": "aoqi-site-jingling-bg",
        "note": "奥奇传说官网「精灵图鉴」区背景，1000x320。" + COPYRIGHT_NOTE,
    },
    {
        "url": "https://resource.a0bi.com/marketnew/aoqi/dest/scss/img_s/tujianBg.jpg",
        "slug": "aoqi-site-tujian-bg",
        "note": "奥奇传说官网「图鉴」区背景横幅，1000x370。" + COPYRIGHT_NOTE,
    },
    {
        "url": "https://resource.a0bi.com/marketnew/aoqi/dest/scss/img_s/liteindexbg.jpg",
        "slug": "aoqi-site-lite-bg",
        "note": "奥奇传说官网轻量版首页背景，1920x1280。" + COPYRIGHT_NOTE,
    },
    {
        "url": "https://resource.a0bi.com/marketnew/aoqi/dest/scss/img_s/subBg.jpg",
        "slug": "aoqi-site-sub-bg",
        "note": "奥奇传说官网子页面背景（星空 + 角色），1920x349。" + COPYRIGHT_NOTE,
    },
    {
        "url": "https://resource.a0bi.com/marketnew/aoqi/dest/scss/img_s/zhenxingShare.jpg",
        "slug": "aoqi-site-zhenxing-share",
        "note": "奥奇传说官网「真形」分享图，官方角色立绘，450x450，适合 README 插图。" + COPYRIGHT_NOTE,
    },
    {
        "url": "https://resource.a0bi.com/marketnew/aoqi/dest/scss/spritedest/index.png",
        "slug": "aoqi-site-ui-sprite",
        "note": "奥奇传说官网首页 UI 雪碧图（左上角含官方『奥奇传说』logo 字样），1285x510。" + COPYRIGHT_NOTE,
    },
    {
        "url": "https://resource.a0bi.com/marketnew/aoqi/dest/scss/img_s/logo.png",
        "slug": "aoqi-site-logo-small",
        "note": "奥奇传说官网页头小 logo（透明 PNG），46x24。" + COPYRIGHT_NOTE,
    },
    {
        "url": "https://img4.a0bi.com/upload/articleResource/20240102/1704181670976.png",
        "slug": "aoqi-news-pet-render",
        "note": "百田官方资讯栏目里的奥奇传说精灵立绘（270x270）。" + COPYRIGHT_NOTE,
    },
    {
        "url": "https://www.100bt.com/resource/zl/index_images/zl_btlogo.png",
        "slug": "baioo-publisher-logo",
        "note": "发行商百田网（www.100bt.com）官方 logo，147x78。" + COPYRIGHT_NOTE,
    },
]


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #

def _use_utf8_stdout() -> None:
    """Windows 控制台默认 GBK，改成 UTF-8 以免中文日志乱码（失败则忽略）。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


def sniff_image(data: bytes) -> tuple[str | None, str | None]:
    """按 magic bytes 判断图片类型，返回 ``(扩展名, 格式名)``，非图片返回 ``(None, None)``。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "PNG"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg", "JPEG"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif", "GIF"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", "WEBP"
    return None, None


def _http_get_urllib(url: str) -> tuple[int, dict, bytes]:
    """用标准库拉一次，返回 (状态码, 响应头, 内容)。"""
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "image/avif,image/webp,image/png,image/jpeg,image/gif,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://aoqi.100bt.com/",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.status, dict(resp.headers), resp.read()


def _http_get_curl(url: str) -> tuple[int, dict, bytes]:
    """回退方案：调系统 curl（部分站点对 urllib 的 TLS 指纹不友好）。"""
    curl = shutil.which("curl") or "curl"
    proc = subprocess.run(
        [curl, "-sSL", "--max-time", str(TIMEOUT), "-A", UA, "-D", "-", url],
        capture_output=True, timeout=TIMEOUT + 10,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"curl 退出码 {proc.returncode}: {proc.stderr.decode('utf-8', 'replace')[:200]}")
    raw = proc.stdout
    # 头/体分隔（兼容 CRLF / LF；重定向时会有多段响应头，取最后一段）
    sep = raw.find(b"\r\n\r\n")
    if sep < 0:
        sep = raw.find(b"\n\n")
        head, body = raw[:sep], raw[sep + 2:]
    else:
        head, body = raw[:sep], raw[sep + 4:]
    text = head.decode("utf-8", "replace")
    last = text.strip().split("\r\n\r\n")[-1]
    status = 0
    headers = {}
    for i, line in enumerate(last.splitlines()):
        if i == 0:
            parts = line.split()
            status = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        elif ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return status, headers, body


def fetch(url: str) -> tuple[int, dict, bytes, str]:
    """抓取一个 URL，失败时换一条通道重试 1 次。

    返回 ``(状态码, 响应头, 内容, 实际用的通道)``；HTTP 错误也会返回状态码而不是抛异常。
    """
    last_err = ""
    for attempt in range(RETRY + 1):
        for channel, fn in (("urllib", _http_get_urllib), ("curl", _http_get_curl)):
            try:
                status, headers, body = fn(url)
                if 200 <= status < 300:
                    return status, headers, body, channel
                last_err = f"HTTP {status}"
                # 4xx 没必要换通道重试，直接返回错误状态
                if 400 <= status < 500:
                    return status, headers, body, channel
            except urllib.error.HTTPError as e:  # 4xx/5xx 也会走这里
                return e.code, dict(e.headers or {}), e.read() or b"", channel
            except Exception as e:  # 网络/TLS 问题 → 下一条通道
                last_err = f"{type(e).__name__}: {e}"
        if attempt < RETRY:
            time.sleep(SLEEP_BETWEEN)
    raise RuntimeError(last_err or "未知错误")


def _slug_from_url(url: str) -> str:
    """从 URL 推一个可读的文件名（不含扩展名）。"""
    path = urllib.parse.urlparse(url).path
    name = Path(path).stem or "index"
    return "".join(c if c.isalnum() or c in "-_." else "-" for c in name)[:60] or "asset"


def _ext_from_url(url: str) -> str:
    suffix = Path(urllib.parse.urlparse(url).path).suffix.lower().lstrip(".")
    return suffix if suffix in ("png", "jpg", "jpeg", "gif", "webp") else ""


def load_urls(spec: str | None) -> list[dict]:
    """解析 ``--urls`` 覆盖参数。

    * 含 ``://`` 时视为**命令行内联清单**：条目用 ``;`` 分隔，单条可写 ``url,note``；
    * 否则视为**文件路径**：每行一个 ``url`` 或 ``url,note``，``#`` 开头是注释。

    只保留看起来像 URL 的条目，其余（例如说明文字）会打印警告后跳过。
    """
    if not spec:
        return [dict(item) for item in BUILTIN_URLS]
    items: list[dict] = []
    skipped: list[str] = []
    if "://" in spec:
        raw = [s.strip() for s in spec.split(";") if s.strip()]
    else:
        text = Path(spec).read_text(encoding="utf-8")
        raw = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    for line in raw:
        url, note = (line.split(",", 1) + [""])[:2] if "," in line else (line, "")
        url = url.strip()
        if "://" not in url:
            skipped.append(line)
            continue
        items.append({"url": url, "slug": _slug_from_url(url),
                      "note": note.strip() or COPYRIGHT_NOTE})
    for s in skipped:
        print(f"[warn] --urls 里这一条不像 URL，已跳过：{s}")
    return items


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #

def run(urls: list[dict], out_root: Path, dry_run: bool, use_pillow: bool) -> dict:
    manifest = {
        "fetchedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "script": "tools/fetch_assets.py",
        "copyright": COPYRIGHT_NOTE,
        "outDir": str(out_root),
        "results": [],
        "stats": {"total": len(urls), "ok": 0, "failed": 0, "invalid": 0},
    }
    results = manifest["results"]

    for i, item in enumerate(urls, 1):
        url = item["url"]
        slug = item.get("slug") or _slug_from_url(url)
        note = item.get("note") or COPYRIGHT_NOTE
        host = urllib.parse.urlparse(url).netloc or "unknown"

        if dry_run:
            print(f"[dry-run] {i:2d}/{len(urls)} {url}\n          -> {out_root / host / (slug + '.<ext>')}")
            results.append({"url": url, "status": None, "bytes": 0, "path": None,
                            "note": note, "state": "dry-run"})
            continue

        entry = {"url": url, "slug": slug, "host": host, "note": note,
                 "status": None, "finalUrl": url, "contentType": None, "bytes": 0,
                 "sha256": None, "path": None, "format": None, "state": "failed",
                 "reason": ""}
        try:
            status, headers, body, channel = fetch(url)
            entry["status"] = status
            entry["channel"] = channel
            entry["contentType"] = headers.get("Content-Type") or headers.get("content-type")
            entry["bytes"] = len(body)
            if not (200 <= status < 300):
                entry["state"] = "failed"
                entry["reason"] = f"HTTP {status}（已优雅跳过）"
                results.append(entry)
                manifest["stats"]["failed"] += 1
                print(f"[skip] {i:2d}/{len(urls)} HTTP {status} {url}")
                time.sleep(SLEEP_BETWEEN)
                continue

            ext, fmt = sniff_image(body)
            if ext is None:
                entry["state"] = "invalid"
                entry["reason"] = f"magic bytes 不是图片（Content-Type={entry['contentType']}）"
                results.append(entry)
                manifest["stats"]["invalid"] += 1
                print(f"[bad ] {i:2d}/{len(urls)} 非图片内容 {url}")
                time.sleep(SLEEP_BETWEEN)
                continue

            target_dir = out_root / host
            target_dir.mkdir(parents=True, exist_ok=True)
            path = target_dir / f"{slug}.{ext}"
            path.write_bytes(body)
            entry["path"] = str(path)
            entry["format"] = fmt
            entry["sha256"] = hashlib.sha256(body).hexdigest()
            entry["state"] = "ok"

            if use_pillow:
                try:
                    from PIL import Image
                    with Image.open(path) as im:
                        im.verify()
                    with Image.open(path) as im:
                        entry["pillow"] = {"format": im.format, "size": list(im.size), "mode": im.mode}
                except ImportError:
                    pass
                except Exception as e:
                    entry["state"] = "invalid"
                    entry["reason"] = f"Pillow 校验失败：{type(e).__name__}: {e}"
                    path.unlink(missing_ok=True)
                    entry["path"] = None
                    results.append(entry)
                    manifest["stats"]["invalid"] += 1
                    print(f"[bad ] {i:2d}/{len(urls)} Pillow 打不开 {url}")
                    time.sleep(SLEEP_BETWEEN)
                    continue

            manifest["stats"]["ok"] += 1
            size_kb = len(body) / 1024
            print(f"[ok  ] {i:2d}/{len(urls)} {status} {entry['contentType']} "
                  f"{size_kb:7.1f}KB sha256={entry['sha256'][:12]}… -> {path}")
        except Exception as e:  # 兜底：任何异常都只记录，不中断整个流程
            entry["state"] = "failed"
            entry["reason"] = str(e)
            manifest["stats"]["failed"] += 1
            print(f"[fail] {i:2d}/{len(urls)} {url} -> {entry['reason']}")
        results.append(entry)
        time.sleep(SLEEP_BETWEEN)

    if not dry_run:
        out_root.mkdir(parents=True, exist_ok=True)
        (out_root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[manifest] {out_root / 'manifest.json'}")
    print(f"[stats] 成功={manifest['stats']['ok']} 失败={manifest['stats']['failed']} "
          f"非法={manifest['stats']['invalid']} 总计={manifest['stats']['total']}")
    return manifest


def main(argv: list[str] | None = None) -> int:
    _use_utf8_stdout()
    ap = argparse.ArgumentParser(description="抓取奥奇传说官方公开图片（供 README / 主题装饰使用）。")
    ap.add_argument("--urls", default=None,
                    help="覆盖内置清单：逗号分隔的 URL，或一个每行一个 URL 的文件路径（可写 url,note）")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "assets" / "fetched"),
                    help="输出根目录（默认 <repo>/assets/fetched）")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不下载")
    ap.add_argument("--no-pillow", action="store_true", help="跳过 Pillow 额外校验")
    args = ap.parse_args(argv)

    urls = load_urls(args.urls)
    if not urls:
        print("URL 清单为空", file=sys.stderr)
        return 2
    run(urls, Path(args.out), args.dry_run, not args.no_pillow)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
