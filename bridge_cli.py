#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bridge_cli.py —— 通过蓝牙桥控制设备（AI/命令行都用它）

拓扑：
    [AI] --HTTP--> [web_server.py :8080] <--轮询-- [手机 Edge + Web Bluetooth] --BLE--> [玩具]

用法:
    python bridge_cli.py state
    python bridge_cli.py stop
    python bridge_cli.py set --cmd 6 --payload 3,0,0,0,0 --seconds 6
    python bridge_cli.py set --cmd 6 --payload 1,0,0,0,0 --hz 10          # 一直跑
    python bridge_cli.py seq --steps "6:1,0,0,0,0:6;6:0,1,0,0,0:6"        # 顺序测试
    python bridge_cli.py log --tail 30
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8080"


def post(path: str, payload: dict) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(BASE + path, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode() or "{}")


def get(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=10) as resp:
        raw = resp.read().decode()
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {"raw": raw}


def parse_payload(text: str) -> list[int]:
    return [int(x, 0) & 0xFF for x in text.replace(" ", "").split(",") if x != ""]


def main() -> None:
    global BASE
    ap = argparse.ArgumentParser(description="计算者-X 蓝牙桥 CLI")
    ap.add_argument("--base", default=BASE,
                    help="桥的地址。手机方案: http://127.0.0.1:8080（配合 adb reverse）；"
                         "ESP32 方案: http://<ESP32的IP>:8080")
    sub = ap.add_subparsers(dest="action", required=True)

    sub.add_parser("state")
    sub.add_parser("stop")

    p = sub.add_parser("set")
    p.add_argument("--cmd", type=lambda v: int(v, 0), default=6, help="命令字节，如 6 / 0x06")
    p.add_argument("--payload", default="0", help="逗号分隔，如 3,0,0,0,0")
    p.add_argument("--hz", type=float, default=10.0)
    p.add_argument("--seconds", type=float, default=0.0, help="跑多少秒后自动停，0=不停")

    p = sub.add_parser("seq", help="顺序执行多段，格式 cmd:payload:秒;cmd:payload:秒")
    p.add_argument("--steps", required=True)
    p.add_argument("--gap", type=float, default=2.0, help="两段之间的停止秒数")

    p = sub.add_parser("log")
    p.add_argument("--tail", type=int, default=30)

    args = ap.parse_args()

    BASE = args.base.rstrip("/")

    if args.action == "state":
        print(json.dumps(get("/api/state"), ensure_ascii=False, indent=2))
    elif args.action == "stop":
        # 设备会锁存最后一条指令：光"停止发送"不会让它停，必须显式下发全 0 帧
        post("/api/set", {"running": True, "cmd": 6, "payload": [0, 0, 0, 0, 0], "hz": 10})
        time.sleep(1.2)
        post("/api/set", {"running": True, "cmd": 8, "payload": [0], "hz": 10})
        time.sleep(1.2)
        post("/api/set", {"running": False})
        print("已下发全 0 帧（cmd 0x06 全 0 + cmd 0x08 [0]），然后停止发送")
    elif args.action == "set":
        payload = parse_payload(args.payload)
        st = post("/api/set", {"running": True, "cmd": args.cmd, "payload": payload, "hz": args.hz})
        print(f"已下发 cmd=0x{args.cmd:02X} payload={payload} hz={args.hz}  ->  {st}")
        if args.seconds > 0:
            time.sleep(args.seconds)
            post("/api/set", {"running": False})
            print(f"{args.seconds}s 后已停")
    elif args.action == "seq":
        for step in args.steps.split(";"):
            if not step.strip():
                continue
            cmd_s, payload_s, sec_s = step.split(":")
            cmd = int(cmd_s, 0)
            payload = parse_payload(payload_s)
            secs = float(sec_s)
            print(f">>> cmd=0x{cmd:02X} payload={payload} 跑 {secs}s")
            post("/api/set", {"running": True, "cmd": cmd, "payload": payload, "hz": 10})
            time.sleep(secs)
            post("/api/set", {"running": False})
            time.sleep(args.gap)
        print(">>> 序列结束，设备已停")
    elif args.action == "log":
        data = get(f"/api/log?tail={args.tail}")
        for line in data.get("logs", []):
            print(line)
        notifies = data.get("notifies", [])
        if notifies:
            print("\n-- 最近去重后的通知 --")
            for n in notifies[-8:]:
                print("  " + n)


if __name__ == "__main__":
    try:
        main()
    except urllib.error.URLError as exc:
        sys.exit(f"连不上蓝牙桥（{BASE}）：{exc} —— 先确认 web_server.py 在跑、手机页面已连接")
