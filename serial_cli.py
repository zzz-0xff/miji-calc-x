#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""serial_cli.py —— 通过 USB 串口控制 ESP32 蓝牙网关

架构： [PC / 本脚本] --USB串口--> [ESP32-S3] --BLE--> [计算者-X]

用法:
    python serial_cli.py monitor [秒]            看 ESP32 输出（默认 10 秒）
    python serial_cli.py state
    python serial_cli.py scan
    python serial_cli.py stop
    python serial_cli.py set --cmd 8 --payload 0,20,1 [--hz 10] [--seconds 10]
    python serial_cli.py raw AA0803010A00C3
    python serial_cli.py seq --steps "8:0,20,1:10;8:20,0,1:10"

ESP32 串口协议（每行一条）：
    SET <cmd> <payload逗号分隔> [hz]
    STOP / STATE / SCAN / RAW <hex>
"""
from __future__ import annotations

import argparse
import sys
import time

try:
    import serial  # pyserial
except ImportError:
    sys.exit("缺少 pyserial：python -m pip install pyserial")

import findport

PORT = None          # None = 自动识别
BAUD = 115200


def open_port(port: str, baud: int) -> "serial.Serial":
    return serial.Serial(port, baud, timeout=0.3)


def read_lines(ser: "serial.Serial", seconds: float, echo: bool = True) -> list[str]:
    out: list[str] = []
    end = time.time() + seconds
    buf = b""
    while time.time() < end:
        chunk = ser.read(256)
        if chunk:
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                text = line.decode("utf-8", "replace").strip("\r")
                if text:
                    out.append(text)
                    if echo:
                        print(text)
        else:
            time.sleep(0.02)
    return out


def send_line(ser: "serial.Serial", line: str) -> None:
    ser.write((line + "\n").encode())
    ser.flush()


def parse_payload(text: str) -> list[int]:
    return [int(x, 0) & 0xFF for x in text.replace(" ", "").split(",") if x != ""]


def main() -> None:
    ap = argparse.ArgumentParser(description="ESP32 蓝牙网关串口客户端")
    ap.add_argument("--port", default=PORT)
    ap.add_argument("--baud", type=int, default=BAUD)
    sub = ap.add_subparsers(dest="action", required=True)

    p = sub.add_parser("monitor")
    p.add_argument("seconds", type=float, nargs="?", default=10.0)

    sub.add_parser("state")
    sub.add_parser("scan")
    sub.add_parser("stop")

    p = sub.add_parser("set")
    p.add_argument("--cmd", type=lambda v: int(v, 0), required=True)
    p.add_argument("--payload", default="0")
    p.add_argument("--hz", type=float, default=10.0)
    p.add_argument("--seconds", type=float, default=0.0, help="跑多少秒后自动 stop")

    p = sub.add_parser("raw")
    p.add_argument("hex")

    p = sub.add_parser("seq")
    p.add_argument("--steps", required=True, help="cmd:payload:秒;cmd:payload:秒")
    p.add_argument("--gap", type=float, default=3.0)

    args = ap.parse_args()

    port, why = findport.find_port(args.port)
    if not port:
        sys.exit(f"没找到网关串口：{why}")
    args.port = port
    try:
        ser = open_port(port, args.baud)
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"打不开串口 {port}: {exc}（是不是 PlatformIO 监视器 / 控制台占着？）")

    if args.action == "monitor":
        print(f"[*] 监听 {args.port} {args.seconds}s ...")
        read_lines(ser, args.seconds)
    elif args.action == "state":
        send_line(ser, "STATE")
        read_lines(ser, 1.5)
    elif args.action == "scan":
        send_line(ser, "SCAN")
        read_lines(ser, 12)
    elif args.action == "stop":
        send_line(ser, "STOP")
        read_lines(ser, 2)
    elif args.action == "raw":
        send_line(ser, "RAW " + args.hex)
        read_lines(ser, 2)
    elif args.action == "set":
        payload = ",".join(str(x) for x in parse_payload(args.payload))
        send_line(ser, f"SET {args.cmd} {payload} {args.hz:g}")
        read_lines(ser, 2)
        if args.seconds > 0:
            time.sleep(args.seconds)
            send_line(ser, "STOP")
            read_lines(ser, 2)
            print(f"{args.seconds}s 后已停")
    elif args.action == "seq":
        for step in args.steps.split(";"):
            if not step.strip():
                continue
            cmd_s, payload_s, sec_s = step.split(":")
            payload = ",".join(str(x) for x in parse_payload(payload_s))
            secs = float(sec_s)
            print(f">>> SET {cmd_s} {payload}  跑 {secs}s")
            send_line(ser, f"SET {int(cmd_s, 0)} {payload} 10")
            read_lines(ser, 1.5)
            time.sleep(secs)
            send_line(ser, "STOP")
            read_lines(ser, 1.5)
            time.sleep(args.gap)
        print(">>> 序列结束")

    ser.close()


if __name__ == "__main__":
    main()
