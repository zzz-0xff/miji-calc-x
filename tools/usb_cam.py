"""一键 USB 端口转发 —— 让手机访问 http://localhost:8098

原理：
  浏览器把 localhost 视为「安全源」，所以 **HTTP 也能开摄像头**。
  用 adb reverse 把手机的 localhost:8098 映射到电脑的 8098，
  手机打开 http://localhost:8098/ 就等于访问电脑上的摄像头页 ——
  **不需要证书、不需要装 CA、不违法任何浏览器策略。**

这解决了：
  · OPPO ColorOS 没有「安装 CA 证书」入口
  · HTTPS 自签证书被手机浏览器硬拒
  · Chrome/Edge flags 开关不生效

用法：
    python tools\\usb_cam.py            # 连一次（插着线的时候跑）
    python tools\\usb_cam.py --watch    # 一直盯着，掉线自动重连
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

# ⚠ 不要强制 UTF-8 —— bat 用的是 chcp 936，强制 utf-8 会让中文乱码。
#   只设 errors，encoding 跟随环境。
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

BASE = Path(__file__).resolve().parent.parent
ADB = BASE / "tools" / "platform-tools" / "adb.exe"
PORTS = [8098, 8099]        # HTTP 给手机用；HTTPS 一起转过去也无害


def env() -> dict:
    """adb 写日志的目录必须是可写的 —— 否则 daemon 起不来。"""
    e = dict(os.environ)
    tmp = BASE / "tools" / "adbtmp"
    tmp.mkdir(parents=True, exist_ok=True)
    e["ANDROID_TMP"] = str(tmp)
    e["TMP"] = str(tmp)
    e["TEMP"] = str(tmp)
    return e


def run(*args: str, timeout: int = 20) -> tuple[int, str]:
    try:
        r = subprocess.run([str(ADB), *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           env=env(), timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return -1, "超时"
    except FileNotFoundError:
        return -1, f"找不到 adb：{ADB}"


def devices() -> list[str]:
    _rc, out = run("devices")
    found = []
    for ln in out.splitlines()[1:]:
        parts = ln.split()
        if len(parts) >= 2 and parts[1] == "device":
            found.append(parts[0])
    return found


def unauthorized() -> list[str]:
    _rc, out = run("devices")
    return [ln.split()[0] for ln in out.splitlines()[1:]
            if len(ln.split()) >= 2 and ln.split()[1] == "unauthorized"]


def setup() -> bool:
    ser = devices()
    unauth = unauthorized()

    if unauth:
        print()
        print("  ⚠ 手机上弹出了「允许 USB 调试吗？」—— 去点【允许】")
        print(f"     （设备号 {unauth[0]} 还没授权）")
        return False

    if not ser:
        print("  ✗ 没检测到手机。检查：")
        print("     ① USB 线插好了吗（要能传数据的线，不是只能充电的）")
        print("     ② 手机「设置 → 关于手机 → 连点版本号 7 次」开开发者模式")
        print("     ③ 「设置 → 系统设置 → 开发者选项 → USB 调试」打开")
        print("     ④ 手机弹「允许 USB 调试」时点了【允许】")
        return False

    ok = True
    for p in PORTS:
        rc, out = run("reverse", f"tcp:{p}", f"tcp:{p}")
        if rc == 0:
            print(f"  ✓ 转发 tcp:{p}  →  手机 localhost:{p} 就是电脑的 {p}")
        else:
            print(f"  ✗ tcp:{p} 转发失败: {out.strip()[:80]}")
            ok = False

    if ok:
        print()
        print("  " + "=" * 56)
        print("   现在在手机浏览器里打开：")
        print()
        print("      http://localhost:8098/")
        print()
        print("   localhost 是安全源 → 摄像头会直接自动开 → 自动连拍")
        print("   **不需要证书、不需要装 CA**")
        print("  " + "=" * 56)
    return ok


def main() -> None:
    ap = argparse.ArgumentParser(description="USB 端口转发，让手机走 localhost")
    ap.add_argument("--watch", action="store_true", help="一直盯着，掉线自动重连")
    args = ap.parse_args()

    if not ADB.exists():
        print(f"  ✗ 没有 adb：{ADB}")
        print("     先跑一次下载，或者手动放 platform-tools 到 tools/ 下")
        return

    print("  " + "=" * 56)
    print("   USB 摄像头桥 —— 让手机用 localhost 访问电脑")
    print("  " + "=" * 56)

    if not args.watch:
        sys.exit(0 if setup() else 1)

    last = None
    print("  （盯着 USB，插拔自动处理，Ctrl+C 退出）")
    try:
        while True:
            cur = tuple(devices())
            if cur and cur != last:
                print()
                print(f"  [{time.strftime('%H:%M:%S')}] 检测到设备 {cur[0]}")
                setup()
                last = cur
            elif not cur and last:
                print(f"\n  [{time.strftime('%H:%M:%S')}] 设备断开了")
                last = None
            time.sleep(2)
    except KeyboardInterrupt:
        print("\n  已停止")


if __name__ == "__main__":
    main()
