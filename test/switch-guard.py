#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归测试：宠物切换不会被宿主状态「弹回去」。

背景（真实 bug）
----------------
桌宠 200ms 一拍，宿主状态（state.json）1 秒一次。旧版桌宠会把 state.json 里的
``petId`` 反写进 companion-settings.json，于是用户刚在右键菜单里切到别的宠物，
不到 200ms 就被过期的宿主状态覆盖回来 —— 表现为「切到某只之后就再也切不动了」。

这个测试把两边的时间差**钉死**：自己起一个独立的桌宠实例，宿主状态里永远写着
``petId = an``，然后连续改设置文件，断言设置**不会被弹回 an**。

用法::

    python test/switch-guard.py            # 用仓库自带运行时（自动探测）
    python test/switch-guard.py --python <python.exe>

退出码 0 = 全部通过；1 = 有切换被弹回（回归）。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.dont_write_bytecode = True
if hasattr(sys.stdout, "reconfigure"):          # Windows 控制台默认 GBK，打不出 ✓/✗
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
SEQUENCE = ["jin", "shui", "mu", "huo", "an", "jin"]
PINNED_STATE_PET = "an"          # 宿主状态永远说 an —— 旧版就是被它弹回去的
SETTLE_SECONDS = 2.5             # 远超桌宠的 200ms 一拍


def find_python(explicit: str | None) -> str:
    if explicit:
        return explicit
    env = os.environ.get("DSH_AOQI_PYTHON")
    if env and Path(env).is_file():
        return env
    home = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".dsh" / "dsh-runtimes"
    if home.is_dir():
        for candidate in sorted(home.glob("*/dependencies/python/python.exe")):
            return str(candidate)
    for name in ("python", "python3"):
        found = shutil.which(name)
        if found:
            return found
    raise SystemExit("找不到 Python 解释器，请用 --python 指定")


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> dict:
    for _ in range(6):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            time.sleep(0.2)
    return {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="宠物切换不被弹回的回归测试")
    parser.add_argument("--python", default="", help="Python 解释器路径")
    parser.add_argument("--keep", action="store_true", help="保留临时目录便于排查")
    args = parser.parse_args(argv)

    python = find_python(args.python)
    workdir = Path(tempfile.mkdtemp(prefix="aoqi-switch-guard-"))
    state_path = workdir / "state.json"
    settings_path = workdir / "companion-settings.json"
    command_path = workdir / "command.json"
    log_path = workdir / "companion.log"

    now_ms = int(time.time() * 1000)
    write_json(state_path, {
        "petId": PINNED_STATE_PET,
        "animation": "idle",
        "bubble": None,
        "updatedAt": now_ms,
        "sessions": [],
        "stats": {},
    })
    write_json(settings_path, {"pet": PINNED_STATE_PET, "collapsed": False, "x": 120, "y": 120})
    write_json(command_path, {"seq": 0, "action": ""})

    print(f"解释器：{python}")
    print(f"临时目录：{workdir}")
    print(f"宿主状态里固定 petId={PINNED_STATE_PET}（旧版会用它把用户选择弹回去）\n")

    process = subprocess.Popen(
        [python, str(ROOT / "companion" / "aoqi_pet.py"),
         "--state", str(state_path), "--settings", str(settings_path),
         "--command", str(command_path), "--assets", str(ROOT / "assets"),
         "--log", str(log_path), "--pet", PINNED_STATE_PET,
         "--stale-exit-ms", "600000", "--opacity", "0.0"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    try:
        # 等窗口起来（日志里出现「桌宠窗口启动」）
        started = False
        deadline = time.time() + 25
        while time.time() < deadline:
            if log_path.exists() and "桌宠窗口启动" in log_path.read_text(encoding="utf-8", errors="ignore"):
                started = True
                break
            if process.poll() is not None:
                break
            time.sleep(0.3)
        if not started:
            print("✗ 桌宠实例没起来")
            if process.poll() is not None:
                print((process.stderr.read() or b"").decode("utf-8", "ignore")[-2000:])
            return 1

        failures = []
        for index, pet in enumerate(SEQUENCE, 1):
            data = read_json(settings_path)
            data["pet"] = pet
            write_json(settings_path, data)
            time.sleep(SETTLE_SECONDS)
            after = read_json(settings_path).get("pet")
            ok = after == pet
            if not ok:
                failures.append((index, pet, after))
            print(f"{'✓' if ok else '✗'} {index}. 设置切到 {pet:4s} → {SETTLE_SECONDS}s 后设置里是 {after!r}"
                  f"{'' if ok else '   ← 被弹回去了！'}")

        log_tail = log_path.read_text(encoding="utf-8", errors="ignore") if log_path.exists() else ""
        reloads = log_tail.count("设置热重载")
        print(f"\n日志里「设置热重载」共 {reloads} 次（说明每次改动都被桌宠读到了）")
        if failures:
            print(f"失败 {len(failures)} 次：{failures}")
            print("→ 宠物选择被宿主状态覆盖了，检查 companion/aoqi_pet.py 的 apply_state()")
            return 1
        print(f"全部通过 ✅ {len(SEQUENCE)}/{len(SEQUENCE)} 次切换稳定，没有被弹回")
        return 0
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        if not args.keep:
            shutil.rmtree(workdir, ignore_errors=True)
        else:
            print(f"临时目录保留在：{workdir}")


if __name__ == "__main__":
    raise SystemExit(main())
