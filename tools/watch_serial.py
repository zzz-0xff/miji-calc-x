"""盯着串口 —— 插上 ESP32 就报出来，顺便看面板有没有连上。

用法：
    python tools\\watch_serial.py
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request

sys.stdout.reconfigure(errors="replace")

PANEL = "http://127.0.0.1:8090"


def ports() -> list[str]:
    try:
        import serial.tools.list_ports as lp
        return [p.device for p in lp.comports()]
    except ImportError:
        # 没 pyserial 就用 Windows 的
        import subprocess
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_SerialPort).DeviceID"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=15).stdout
            return [x.strip() for x in out.splitlines() if x.strip()]
        except Exception:  # noqa: BLE001
            return []


def panel() -> dict:
    try:
        with urllib.request.urlopen(PANEL + "/api/status", timeout=4) as r:
            return json.loads(r.read().decode())
    except Exception:  # noqa: BLE001
        return {}


def line(tag: str, msg: str) -> None:
    print(f"  [{time.strftime('%H:%M:%S')}] {tag} {msg}")


print("=" * 62)
print("  盯着串口 —— 插上 ESP32 会自动报出来")
print("=" * 62)
print()

last = None
try:
    while True:
        cur = ports()
        key = ",".join(cur)

        if key != last:
            last = key
            print()
            if not cur:
                line("·", "没有串口。把 ESP32 插到电脑上。")
            else:
                line("★", f"发现串口: {', '.join(cur)}")

            p = panel()
            if p:
                toy = p.get("toy_connected")
                band = p.get("band_connected")
                hr = p.get("hr")
                line(" ", f"面板: 玩具={'已连' if toy else '未连'}  "
                          f"手环={'已连' if band else '未连'}  "
                          f"心率={hr if hr else '无'}")
                if cur and not toy:
                    line(" ", "有串口但玩具没连 —— ESP32 可能还在启动，等几秒")
                if toy and band:
                    line("✓", "两个都连上了，可以开玩了")
            else:
                line(" ", "面板 8090 没响应 —— 先跑 点我-控制台.bat")

        time.sleep(2)
except KeyboardInterrupt:
    print("\n  已停止")
