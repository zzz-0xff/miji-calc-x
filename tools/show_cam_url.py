"""show_cam_url.py —— 告诉你手机上该打开哪个地址。

痛点：电脑换了网络之后 IP 就变了，每次都要重新找。
这个脚本列出**所有可能能用的地址**，并标出哪个最可能是对的。

用法：
    python show_cam_url.py           # 看一次
    python show_cam_url.py --watch   # 一直盯着，网络一变就重打
"""
from __future__ import annotations

import argparse
import socket
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

PORT_HTTPS = 8099
PORT_HTTP = 8098


def adapters() -> list[dict]:
    """拿所有已连接的网卡（名字 + IP + 描述）。"""
    out = []
    try:
        raw = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-NetIPAddress -AddressFamily IPv4 | "
             "Select-Object IPAddress,InterfaceAlias,PrefixLength | "
             "ConvertTo-Csv -NoTypeInformation"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=20).stdout
    except Exception:  # noqa: BLE001
        return out

    for ln in raw.splitlines()[1:]:
        parts = [p.strip('"') for p in ln.split('","')]
        if len(parts) < 3:
            continue
        ip, alias, plen = parts[0], parts[1], parts[2]
        if ip.startswith(("127.", "169.254.")):
            continue
        out.append({"ip": ip, "alias": alias, "plen": plen})
    return out


def guess_kind(ip: str) -> str:
    """按网段猜这是哪种网络。"""
    if ip.startswith("192.168.137."):
        return "Windows 移动热点（电脑开的）"
    if ip.startswith("192.168.43.") or ip.startswith("192.168.42."):
        return "手机热点 / USB 共享"
    if ip.startswith("192.168.100."):
        return "当前 WiFi（公共热点，可能有隔离）"
    if ip.startswith("192.168.1.") or ip.startswith("192.168.0."):
        return "家用路由"
    return "未知网络"


def show() -> str:
    ads = adapters()
    print("=" * 68)
    print("  手机上打开哪个地址")
    print("=" * 68)
    if not ads:
        print("  没找到可用网卡")
        return ""
    best = ""
    for a in ads:
        kind = guess_kind(a["ip"])
        # 热点/自组网最可能没有隔离 —— 优先推荐
        good = ("热点" in kind or "共享" in kind)
        mark = "★" if good else " "
        url = f"https://{a['ip']}:{PORT_HTTPS}/"
        print(f"  {mark} {url}")
        print(f"      {a['alias']}　{kind}")
        if good and not best:
            best = url
    if not best:
        best = f"https://{ads[0]['ip']}:{PORT_HTTPS}/"
    print()
    print(f"  → 优先试：{best}")
    print()
    if all("公共热点" in guess_kind(a["ip"]) for a in ads):
        print("  ⚠ 你现在只有公共热点。这类网络开了客户端隔离，")
        print("    手机够不到电脑 —— 换手机热点或电脑热点。")
    print()
    print("  电脑本机（不用证书、不弹警告，但用的是电脑摄像头）：")
    print(f"     http://127.0.0.1:{PORT_HTTP}/")
    print("=" * 68)
    return best


def main() -> None:
    ap = argparse.ArgumentParser(description="显示手机上该打开的地址")
    ap.add_argument("--watch", action="store_true", help="一直盯着，网络变了就重打")
    args = ap.parse_args()

    last = show()
    if not args.watch:
        return

    print("\n  （盯着网络变化，Ctrl+C 退出）")
    try:
        while True:
            time.sleep(3)
            now = adapters()
            key = ",".join(sorted(a["ip"] for a in now))
            if key != ",".join(sorted(a["ip"] for a in adapters())) or not last:
                pass
            cur = {a["ip"] for a in now}
            prev = getattr(main, "_prev", None)
            if prev != cur:
                main._prev = cur
                print()
                last = show()
    except KeyboardInterrupt:
        print("\n  已停止")


if __name__ == "__main__":
    main()
