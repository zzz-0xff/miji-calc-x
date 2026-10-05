"""无线 adb 端口转发 —— 插一次 USB，之后全程走 WiFi，USB 口还给 ESP32。

为什么这样做：
  如果只有一根能传数据的线，而 ESP32 网关要常年占用 USB。
  所以不能靠 USB 常连着手机。

  解法：用 `adb tcpip 5555` 把手机的 adb 切到网络模式。
  插一次线设好，拔掉之后 adb 走 WiFi，`adb reverse` 也跟着走 WiFi ——
  手机就有了 localhost，摄像头能开，**USB 口空出来给 ESP32**。

流程：
  ① 插 USB  →  adb tcpip 5555  →  拔 USB
  ② adb connect <手机IP>:5555     （走 WiFi）
  ③ adb reverse tcp:8098 tcp:8098 （也走 WiFi）
  ④ 手机浏览器开 http://localhost:8098/  → 自动连拍

用法：
    python tools\\usb_cam_wifi.py              # 一步步来
    python tools\\usb_cam_wifi.py --connect    # 已切过 tcpip，直接连
    python tools\\usb_cam_wifi.py --watch      # 盯着，掉线自动重连
"""
from __future__ import annotations

import argparse
import os
import re
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
PORTS = [8098, 8099]
TCP_PORT = 5555
IPFILE = BASE / "tools" / ".phone_ip"


def env() -> dict:
    e = dict(os.environ)
    tmp = BASE / "tools" / "adbtmp"
    tmp.mkdir(parents=True, exist_ok=True)
    e["ANDROID_TMP"] = str(tmp)
    e["TMP"] = str(tmp)
    e["TEMP"] = str(tmp)
    return e


def run(*args: str, timeout: int = 25) -> tuple[int, str]:
    try:
        r = subprocess.run([str(ADB), *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           env=env(), timeout=timeout)
        return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
    except subprocess.TimeoutExpired:
        return -1, "超时"
    except FileNotFoundError:
        return -1, f"找不到 adb：{ADB}"


def lines_of(out: str) -> list[str]:
    return [ln for ln in out.splitlines() if ln.strip()]


def usb_devices() -> list[str]:
    _rc, out = run("devices")
    res = []
    for ln in lines_of(out)[1:]:
        p = ln.split()
        if len(p) >= 2 and p[1] == "device" and ":" not in p[0]:
            res.append(p[0])
    return res


def net_devices() -> list[str]:
    _rc, out = run("devices")
    res = []
    for ln in lines_of(out)[1:]:
        p = ln.split()
        if len(p) >= 2 and p[1] == "device" and ":" in p[0]:
            res.append(p[0])
    return res


def phone_ip() -> str:
    """拿手机的 WiFi IP。"""
    if IPFILE.exists():
        ip = IPFILE.read_text(encoding="utf-8").strip()
        if ip:
            return ip
    for serial in (net_devices() + usb_devices()):
        rc, out = run("-s", serial, "shell", "ip", "-4", "addr", "show", "wlan0")
        if rc == 0:
            m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", out)
            if m:
                IPFILE.write_text(m.group(1), encoding="utf-8")
                return m.group(1)
    return ""


def do_reverse(tag: str = "") -> bool:
    ok = True
    for p in PORTS:
        rc, out = run("reverse", f"tcp:{p}", f"tcp:{p}")
        if rc != 0:
            print(f"  ✗ tcp:{p} 转发失败: {out[:70]}")
            ok = False
    if ok:
        print(f"  ✓ 端口转发就绪{tag}（8098 / 8099）")
    return ok


def banner(ip: str = "") -> None:
    print()
    print("  " + "=" * 58)
    print("   手机浏览器打开这个：(不需要证书)")
    print()
    print("       http://localhost:8098/")
    print()
    if ip:
        print(f"   （也可以用自己的 WiFi 地址 http://{ip} 但那要证书）")
    print("   USB 口现在空出来了，把 ESP32 插回去就行。")
    print("  " + "=" * 58)


def step1_switch_to_tcpip() -> bool:
    """插线的时候跑一次：把 adb 切到网络模式。"""
    devs = usb_devices()
    if not devs:
        print("  ✗ 没看到 USB 连接的手机。")
        print("     这一步需要插一次线 —— 就这一次，几秒钟。")
        print("     插上后如果手机弹「允许 USB 调试」，点【允许】。")
        return False

    serial = devs[0]
    print(f"  ✓ 找到手机（USB）: {serial}")

    rc, out = run("-s", serial, "shell", "ip", "-4", "addr", "show", "wlan0")
    ip = ""
    if rc == 0:
        m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", out)
        if m:
            ip = m.group(1)
            IPFILE.write_text(ip, encoding="utf-8")
            print(f"  ✓ 手机 WiFi 地址: {ip}")

    rc, out = run("-s", serial, "tcpip", str(TCP_PORT))
    if rc != 0:
        print(f"  ✗ 切换失败: {out[:80]}")
        return False
    print(f"  ✓ 已切到网络模式（端口 {TCP_PORT}）")

    if not ip:
        print("  ⚠ 没读到手机 WiFi 地址 —— 连线时确认手机连着和电脑同一个网")
        return False

    print()
    print("  " + "-" * 58)
    print("   ★ 现在可以把 USB 线拔掉了，插到 ESP32 上。")
    print("  " + "-" * 58)
    time.sleep(3)

    rc, out = run("connect", f"{ip}:{TCP_PORT}")
    print(f"  → adb connect {ip}:{TCP_PORT}")
    for ln in lines_of(out):
        print(f"     {ln}")

    if not net_devices():
        print("  ✗ WiFi 连接没成功")
        return False

    print(f"  ✓ 已通过 WiFi 连上手机")
    if do_reverse("（走 WiFi）"):
        banner(ip)
        return True
    return False


def reconnect() -> bool:
    ip = phone_ip()
    if not ip:
        print("  ✗ 不知道手机 IP —— 先跑一次 --switch")
        return False
    rc, out = run("connect", f"{ip}:{TCP_PORT}")
    if not net_devices():
        print(f"  ✗ 连不上 {ip}:{TCP_PORT}")
        return False
    print(f"  ✓ 已连上 {ip}:{TCP_PORT}")
    return do_reverse()


def main() -> None:
    ap = argparse.ArgumentParser(description="无线 adb 转发（USB 只占用一次）")
    ap.add_argument("--switch", action="store_true",
                    help="插线时跑这个：切到 WiFi 模式")
    ap.add_argument("--connect", action="store_true",
                    help="已经切过了，直接连")
    ap.add_argument("--watch", action="store_true",
                    help="盯着，掉线自动重连")
    args = ap.parse_args()

    print("  " + "=" * 58)
    print("   无线摄像头桥 —— USB 口留给你 ESP32")
    print("  " + "=" * 58)

    if not ADB.exists():
        print(f"  ✗ 没有 adb: {ADB}")
        return

    if args.switch or not (args.connect or args.watch):
        step1_switch_to_tcpip()
        if not args.watch:
            return

    if args.connect:
        reconnect()
        if not args.watch:
            return

    if args.watch:
        print("\n  （盯着连接，掉线自动重连，Ctrl+C 退出）")
        try:
            while True:
                if not net_devices():
                    print(f"\n  [{time.strftime('%H:%M:%S')}] 掉线，重连中 …")
                    reconnect()
                time.sleep(3)
        except KeyboardInterrupt:
            print("\n  已停止")
