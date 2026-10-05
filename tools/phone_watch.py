"""插上手机就自动跑完全流程 —— 你只管插线和拔线。

流程：
  [等] 等你插 USB
   ↓
  [1] 检测到手机 → adb tcpip 5555（切到 WiFi 模式）
   ↓
  [喊] 屏幕上大字提示「现在拔线，插回 ESP32」
   ↓
  [2] 自动 adb connect <手机IP>:5555（走 WiFi）
   ↓
  [3] 自动 adb reverse tcp:8098 tcp:8098
   ↓
  [好] 手机打开 http://localhost:8098/  → 实时连拍

之后脚本继续挂着，掉线自动重连。

用法：
    python tools\\phone_watch.py
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

# 不要强制 UTF-8 —— 用的是 bat 里的 chcp 936 控制台，
# 强制 UTF-8 会让中文变乱码。跟着控制台走就行。
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


def adb(*args: str, timeout: int = 25) -> tuple[int, str]:
    try:
        r = subprocess.run([str(ADB), *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           env=env(), timeout=timeout)
        return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
    except subprocess.TimeoutExpired:
        return -1, "超时"
    except FileNotFoundError:
        return -1, f"找不到 adb: {ADB}"


def parse() -> tuple[list[str], list[str], list[str]]:
    """返回 (usb已授权, 网络已连接, 未授权)"""
    _rc, out = adb("devices")
    usb, net, unauth = [], [], []
    for ln in out.splitlines()[1:]:
        p = ln.split()
        if len(p) < 2:
            continue
        serial, state = p[0], p[1]
        if state == "unauthorized":
            unauth.append(serial)
        elif state == "device":
            (net if ":" in serial else usb).append(serial)
    return usb, net, unauth


def big(msg: str) -> None:
    print()
    print("  " + "=" * 62)
    for ln in msg.splitlines():
        print("   " + ln)
    print("  " + "=" * 62)
    print()


def setup_now(serial: str) -> bool:
    """插线状态下：切 tcpip，拿 IP，然后提示拔线。"""
    print(f"  ✓ 检测到手机（USB）: {serial}")

    rc, out = adb("-s", serial, "shell", "ip", "-4", "addr", "show", "wlan0")
    ip = ""
    if rc == 0:
        m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", out)
        if m:
            ip = m.group(1)
            IPFILE.write_text(ip, encoding="utf-8")
            print(f"  ✓ 手机 WiFi 地址: {ip}")
    if not ip:
        print("  ⚠ 读不到手机的 WiFi 地址 —— 确认手机连着电脑那个热点")

    rc, out = adb("-s", serial, "tcpip", str(TCP_PORT))
    if rc != 0:
        print(f"  ✗ tcpip 切换失败: {out[:90]}")
        print("     （手机上「允许 USB 调试」点了吗？）")
        return False
    print(f"  ✓ 已切到网络模式（端口 {TCP_PORT}）")

    big("★ 现在把 USB 线拔掉，插到 ESP32 上 ★\n"
        "   （手机保持连着电脑的热点就行）")
    return True


def connect_wifi(tries: int = 25) -> bool:
    """拔线之后：走 WiFi 连上，做端口转发。"""
    ip = IPFILE.read_text(encoding="utf-8").strip() if IPFILE.exists() else ""
    if not ip:
        print("  ✗ 不知道手机 IP")
        return False

    for i in range(tries):
        rc, out = adb("connect", f"{ip}:{TCP_PORT}")
        _u, net, _a = parse()
        if net:
            print(f"  ✓ 已通过 WiFi 连上 {ip}:{TCP_PORT}")
            ok = True
            for p in PORTS:
                rc2, out2 = adb("reverse", f"tcp:{p}", f"tcp:{p}")
                if rc2 != 0:
                    print(f"  ✗ 转发 {p} 失败: {out2[:70]}")
                    ok = False
            if ok:
                big("手机浏览器打开这个：（不需要证书）\n"
                    "\n"
                    "      http://localhost:8098/\n"
                    "\n"
                    "   → 摄像头会自动开，每隔几秒拍一张\n"
                    "\n"
                    "   USB 口现在空着，ESP32 插回去就行。")
            return ok
        if i == 0 or i % 5 == 0:
            print(f"  … 等待 WiFi 连接（{i+1}/{tries}）")
        time.sleep(2)

    print(f"  ✗ 连不上 {ip}:{TCP_PORT}")
    print("     检查：① 手机还连着电脑热点吗 ② 手机息屏了吗（解锁一下）")
    return False


def main() -> None:
    print("  " + "=" * 62)
    print("   摄像头桥 · 插上就自动配置")
    print("  " + "=" * 62)
    print()
    print("  需要你先做两件事：")
    print("    ① 手机开 USB 调试")
    print("       设置 → 关于本机 → 版本信息 → 连点版本号 7 次")
    print("       设置 → 系统设置 → 开发者选项 → USB 调试 打开")
    print("    ② 把 USB 线插到电脑上")
    print()

    if not ADB.exists():
        print(f"  ✗ 没有 adb: {ADB}")
        return

    # 如果已经通过 WiFi 连上了，直接补一次转发
    _u, net, _a = parse()
    if net:
        print(f"  ℹ 已经有 WiFi 连接了: {net[0]}")
        for p in PORTS:
            adb("reverse", f"tcp:{p}", f"tcp:{p}")
        big("手机打开 http://localhost:8098/ 就行。")
        return

    print("  [等待插线 …]")
    handled = False
    last_state = ""

    try:
        while True:
            usb, net, unauth = parse()

            if unauth and last_state != "unauth":
                last_state = "unauth"
                big("手机上弹出了「允许 USB 调试吗？」\n"
                    "\n"
                    "   → 去手机屏幕上点【允许】\n"
                    "   → 建议勾上「一律允许」")

            if usb and not handled:
                handled = True
                last_state = "usb"
                if setup_now(usb[0]):
                    # 等线拔掉，然后走 WiFi
                    print("  [等你拔线 …]")
                    time.sleep(6)
                    connect_wifi()
                    print()
                    print("  [继续盯着，掉线会自动重连]")

            elif not usb and handled and net:
                # 已经拔线并且 WiFi 连着 —— 确认转发还在
                pass

            if handled and net:
                # 定期确认转发没丢
                for p in PORTS:
                    adb("reverse", f"tcp:{p}", f"tcp:{p}")

            if handled and not net and not usb:
                # WiFi 掉了，重连
                connect_wifi(tries=5)

            time.sleep(4)

    except KeyboardInterrupt:
        print("\n  已停止")


if __name__ == "__main__":
    main()
