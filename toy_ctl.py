#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""toy_ctl.py —— 计算者-X 控制台（走 ESP32 串口）

链路： [本脚本] --USB COM3--> [ESP32-S3] --BLE--> [玩具]

用法:
    python toy_ctl.py run --telescopic 80 --vibration 0 --seconds 15
    python toy_ctl.py run --telescopic 100 --vibration 100            # 不停，直到 stop
    python toy_ctl.py stop
    python toy_ctl.py status
    python toy_ctl.py count start|stop|reset
    python toy_ctl.py heat on|off
    python toy_ctl.py raw AA08036464017E
    python toy_ctl.py reconnect toy|band|both   # 玩具掉线了让 ESP32 重连
    python toy_ctl.py demo --seconds 60        # 自动跑一段伸缩+震动的起伏

协议（实测锁定，详见 PROTOCOL.md）:
    运行   AA 08 03 <伸缩0-100> <震动0-100> 01 <sum8>   持续 10Hz 下发
    全停   AA 06 05 00 00 00 00 00 B5  +  AA 08 03 00 00 01 B6
"""
from __future__ import annotations

import argparse
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("缺少 pyserial：python -m pip install pyserial")

import findport

PORT = None          # None = 自动识别（--port COM5 或环境变量 TOY_PORT 可指定）
BAUD = 115200
HZ = 10.0


# ------------------------------------------------------------------ 帧
def aa(cmd: int, payload: list[int]) -> bytes:
    body = [0xAA, cmd & 0xFF, len(payload) & 0xFF] + [p & 0xFF for p in payload]
    body.append(sum(body) & 0xFF)
    return bytes(body)


def motor_frame(telescopic: int, vibration: int, t3: int = 1) -> bytes:
    return aa(0x08, [telescopic & 0xFF, vibration & 0xFF, t3 & 0xFF])


STOP_FRAMES = [aa(0x06, [0, 0, 0, 0, 0]), aa(0x08, [0, 0, 1]), aa(0x08, [0])]


def hexs(b: bytes) -> str:
    return " ".join(f"{x:02X}" for x in b)


# ------------------------------------------------------------------ 端口
class Toy:
    def __init__(self, port: str = PORT):
        self.port = port
        self.ser: serial.Serial | None = None

    def open(self) -> None:
        if self.ser:
            return
        cands = findport.candidates(self.port)
        if not cands:
            sys.exit("系统里没有任何串口 —— ESP32 插好了吗？")
        last = ""
        for port, score, desc in cands:
            try:
                s = serial.Serial(port, BAUD, timeout=0.2)
                try:
                    s.dtr = False
                    s.rts = False
                except Exception:
                    pass
                self.ser = s
                self.port = port
                break
            except Exception as exc:  # noqa: BLE001
                last = f"{port}: {exc}"
        if self.ser is None:
            sys.exit(f"所有候选端口都打不开（{last}）—— 被 PlatformIO 监视器 / 控制台占着？")
        time.sleep(0.3)
        self.drain(0.3)

    def close(self) -> None:
        if self.ser:
            self.ser.close()
            self.ser = None

    def send_line(self, line: str) -> None:
        assert self.ser
        self.ser.write((line + "\n").encode())
        self.ser.flush()

    def send_frame(self, frame: bytes) -> None:
        self.send_line("RAW " + hexs(frame).replace(" ", ""))

    def drain(self, seconds: float) -> list[str]:
        out, buf, end = [], b"", time.time() + seconds
        while time.time() < end:
            chunk = self.ser.read(256) if self.ser else b""
            if chunk:
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    t = line.decode("utf-8", "replace").strip("\r")
                    if t:
                        out.append(t)
            else:
                time.sleep(0.02)
        return out

    # ------------- 高层动作 -------------
    def run(self, telescopic: int, vibration: int, seconds: float, hz: float = HZ,
            verbose: bool = False) -> None:
        self.open()
        f = motor_frame(telescopic, vibration)
        print(f"[>] 伸缩={telescopic:3d}  震动={vibration:3d}  帧={hexs(f)}  持续 {seconds:g}s")
        end = time.time() + seconds
        period = 1.0 / hz
        nxt = 0.0
        while time.time() < end:
            now = time.time()
            if now >= nxt:
                self.send_frame(f)
                nxt = now + period
            self.drain(0.05)
        self.stop()

    def stop(self) -> None:
        self.open()
        for f in STOP_FRAMES:
            self.send_frame(f)
            self.drain(0.2)
        self.drain(0.5)
        print("[x] 已停（发过全 0 帧）")

    def raw(self, hexstr: str) -> None:
        self.open()
        self.send_frame(bytes.fromhex(hexstr.replace(" ", "")))
        for line in self.drain(1.5):
            print("   " + line)

    def status(self) -> None:
        self.open()
        self.send_line("STATE")
        for line in self.drain(1.5):
            print("   " + line)


# ------------------------------------------------------------------ CLI
def main() -> None:
    ap = argparse.ArgumentParser(description="计算者-X 控制台")
    ap.add_argument("--port", default=PORT)
    sub = ap.add_subparsers(dest="action", required=True)

    p = sub.add_parser("run")
    p.add_argument("--telescopic", type=int, default=0, help="伸缩 0-100")
    p.add_argument("--vibration", type=int, default=0, help="震动 0-100")
    p.add_argument("--seconds", type=float, default=15.0, help="持续秒数")
    p.add_argument("--hz", type=float, default=HZ)

    sub.add_parser("stop")
    sub.add_parser("status")

    p = sub.add_parser("count")
    p.add_argument("what", choices=["start", "stop", "reset"])
    p.add_argument("--female", action="store_true")

    p = sub.add_parser("heat")
    p.add_argument("what", choices=["on", "off"])

    p = sub.add_parser("raw")
    p.add_argument("hex")

    p = sub.add_parser("reconnect")
    p.add_argument("what", nargs="?", default="toy",
                   choices=["toy", "band", "both"],
                   help="让 ESP32 重新扫/连（默认玩具）")

    p = sub.add_parser("demo")
    p.add_argument("--seconds", type=float, default=60.0)

    args = ap.parse_args()
    toy = Toy(args.port)
    try:
        if args.action == "run":
            t = max(0, min(100, args.telescopic))
            v = max(0, min(100, args.vibration))
            toy.run(t, v, args.seconds, args.hz)
        elif args.action == "stop":
            toy.stop()
        elif args.action == "status":
            toy.status()
        elif args.action == "count":
            cmd = {"start": 0x12 if args.female else 0x0A,
                   "stop": 0x12 if args.female else 0x0A,
                   "reset": 0x0D}[args.what]
            payload = [1] if args.what != "stop" else [0]
            if args.what == "reset":
                payload = [1]
            toy.open()
            toy.send_frame(aa(cmd, payload))
            for line in toy.drain(2.0):
                print("   " + line)
        elif args.action == "heat":
            toy.open()
            toy.send_frame(aa(0x04, [1 if args.what == "on" else 0]))
            for line in toy.drain(1.5):
                print("   " + line)
        elif args.action == "raw":
            toy.raw(args.hex)
        elif args.action == "reconnect":
            toy.open()
            for c in {"toy": ["SCAN"], "band": ["BSCAN"],
                      "both": ["SCAN", "BSCAN"]}[args.what]:
                toy.send_line(c)
                toy.drain(0.3)
            print(f"[~] 已让 ESP32 重连 {args.what}（connectToy 可能阻塞几秒）")
            for line in toy.drain(6.0):
                print("   " + line)
        elif args.action == "demo":
            toy.open()
            total = args.seconds
            step = 6.0
            t = 0.0
            up = True
            while t < total:
                level = int(100 * (t / total)) if up else int(100 * (1 - t / total))
                level = max(10, min(100, level))
                toy.run(level, int(level * 0.6), step)
                t += step
            toy.stop()
    except KeyboardInterrupt:
        print("\n[!] 中断")
        toy.stop()
    finally:
        toy.close()


if __name__ == "__main__":
    main()
