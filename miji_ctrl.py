#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""miji_ctrl.py —— 计算者-X BLE 驱动（协议表驱动，逆向出结果后只改 frames.json）

协议表 frames.json 结构:
{
  "address":      "AA:BB:CC:DD:EE:FF",         // 或设备名，如 "Miji"
  "write_char":   "0000fff2-....",             // 命令行 dump 出来的可写特征
  "notify_char":  "0000fff1-....",             // 可省
  "write_with_response": false,
  "checksum":     "sum8",                      // none|sum8|xor8|crc16le|crc16be
  "limits":       {"mode": 3, "speed": 3, "max_seconds": 300},
  "frames": {
    "stop": [170, 0, 0, 0, 85],
    "run":  [170, "{mode}", "{speed}", 0, "{checksum}", 85]
  }
}
模板里 {mode}/{speed} 之类的占位符由命令行动态填；{checksum} 按 checksum 字段
在校验位上展开（sum8/xor8 占 1 字节，crc16 占 2 字节，默认小端）。

命令:
  list                          打印协议表里的帧名
  run    --mode M --speed S [--seconds N]
  send   <帧名> [--var k=v ...]
  raw    "AA 01 01 00 00 55"
  subscribe [SEC]               连上去只订阅通知，边操作边看回传
  stop

示例:
  python miji_ctrl.py -c frames.json run --mode 1 --speed 2 --seconds 20
  python miji_ctrl.py -c frames.json raw "AA 01 02 00 03 55"
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

try:
    from bleak import BleakClient, BleakScanner

    BLEAK_OK = True
    BLEAK_ERR = ""
except Exception as exc:  # noqa: BLE001
    BleakClient = None  # type: ignore[assignment]
    BleakScanner = None  # type: ignore[assignment]
    BLEAK_OK = False
    BLEAK_ERR = f"{exc}（先执行 pip install bleak）"

MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")


# --------------------------------------------------------------------------- #
# 协议表
# --------------------------------------------------------------------------- #
def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


class FrameSet:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.frames: dict[str, list] = cfg.get("frames", {})
        self.checksum = str(cfg.get("checksum", "none")).lower()
        if not self.frames:
            raise SystemExit("frames.json 里没有 frames 段")

    def _checksum_bytes(self, body: bytes) -> bytes:
        if self.checksum in ("none", "", "off"):
            return b""
        if self.checksum == "sum8":
            return bytes([sum(body) & 0xFF])
        if self.checksum == "xor8":
            acc = 0
            for b in body:
                acc ^= b
            return bytes([acc])
        if self.checksum in ("crc16le", "crc16be"):
            value = crc16_modbus(body)
            return value.to_bytes(2, "little" if self.checksum.endswith("le") else "big")
        raise SystemExit(f"不认识的 checksum 类型: {self.checksum}")

    def build(self, name: str, **variables: int) -> bytes:
        if name not in self.frames:
            raise SystemExit(f"协议表里没有帧 {name!r}；现有: {', '.join(self.frames)}")
        out = bytearray()
        for item in self.frames[name]:
            if isinstance(item, int):
                out.append(item & 0xFF)
                continue
            text = str(item).strip()
            if text == "{checksum}":
                out.extend(self._checksum_bytes(bytes(out)))
            elif text.startswith("{") and text.endswith("}"):
                key = text[1:-1]
                if key not in variables:
                    raise SystemExit(f"帧 {name!r} 需要变量 --var {key}=N")
                out.append(int(variables[key]) & 0xFF)
            else:
                out.append(int(text, 0) & 0xFF)
        return bytes(out)


def load_config(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"找不到配置文件 {p}；先复制 frames.example.json 改名为 frames.json")
    return json.loads(p.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# 设备
# --------------------------------------------------------------------------- #
async def resolve_target(cfg: dict, timeout: float = 15.0) -> str:
    target = str(cfg.get("address") or cfg.get("name_hint") or "").strip()
    if not target:
        raise SystemExit("frames.json 里必须填 address（MAC 或设备名）")
    if MAC_RE.match(target):
        return target
    if not BLEAK_OK:
        raise SystemExit(BLEAK_ERR)
    for dev in await BleakScanner.discover(timeout=timeout):
        if target.lower() in (dev.name or "").lower():
            return dev.address
    raise SystemExit(f"按名字 {target!r} 没扫到设备，用 ble_probe.py scan 看实际广播名")


class Toy:
    """极简 BLE 控制封装；每次命令连一次、发完即断，避免长时间占用连接。"""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.frames = FrameSet(cfg)

    async def _with_client(self, action):
        if not BLEAK_OK:
            raise SystemExit(BLEAK_ERR)
        address = await resolve_target(self.cfg)
        client = BleakClient(address, timeout=float(self.cfg.get("timeout", 20.0)))
        await client.connect()
        try:
            return await action(client)
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass

    async def _write(self, client, payload: bytes) -> None:
        char = self.cfg.get("write_char")
        if not char:
            raise SystemExit("frames.json 里没填 write_char（先跑 ble_probe.py dump）")
        response = bool(self.cfg.get("write_with_response", False))
        try:
            await client.write_gatt_char(char, payload, response=response)
        except Exception:
            await client.write_gatt_char(char, payload, response=not response)
        print(f"[>] {payload.hex(' ')}", flush=True)

    async def send(self, frame_name: str, **variables: int) -> bytes:
        payload = self.frames.build(frame_name, **variables)

        async def action(client):
            await self._write(client, payload)

        await self._with_client(action)
        return payload

    async def raw(self, payload: bytes) -> None:
        async def action(client):
            await self._write(client, payload)

        await self._with_client(action)

    async def subscribe(self, seconds: float) -> None:
        def on_data(sender, data: bytearray) -> None:
            print(f"[{time.strftime('%H:%M:%S')}] {sender.uuid} {data.hex(' ')}", flush=True)

        async def action(client):
            targets = [c for s in client.services for c in s.characteristics
                       if "notify" in c.properties or "indicate" in c.properties]
            if not targets:
                print("无可订阅特征")
                return
            for ch in targets:
                try:
                    await client.start_notify(ch, on_data)
                    print(f"[+] 订阅 {ch.uuid}")
                except Exception as exc:  # noqa: BLE001
                    print(f"[!] {ch.uuid} 订阅失败: {exc}")
            await asyncio.sleep(seconds)

        await self._with_client(action)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def parse_vars(pairs: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise SystemExit(f"--var 需要 key=value 形式，收到 {pair!r}")
        key, value = pair.split("=", 1)
        out[key.strip()] = int(value, 0)
    return out


async def amain(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)

    if args.cmd == "list":
        for name, body in cfg.get("frames", {}).items():
            print(f"{name:<12} {body}")
        return

    toy = Toy(cfg)

    if args.cmd == "stop":
        await toy.send("stop")
    elif args.cmd == "run":
        limits = cfg.get("limits", {})
        mode = max(1, min(int(args.mode), int(limits.get("mode", 3))))
        speed = max(1, min(int(args.speed), int(limits.get("speed", 3))))
        max_seconds = int(limits.get("max_seconds", 300))
        seconds = max(0, min(int(args.seconds), max_seconds))
        await toy.send("run", mode=mode, speed=speed)
        print(f"[+] mode={mode} speed={speed}" + (f"，{seconds}s 后自动停" if seconds else ""))
        if seconds:
            await asyncio.sleep(seconds)
            await toy.send("stop")
            print("[+] 已到时自动停")
    elif args.cmd == "send":
        payload = await toy.send(args.frame, **parse_vars(args.var))
        print(f"[+] 已发送 {args.frame}: {payload.hex(' ')}")
    elif args.cmd == "raw":
        cleaned = re.sub(r"[^0-9A-Fa-f]", "", args.hex)
        if len(cleaned) % 2:
            raise SystemExit("HEX 长度必须为偶数")
        await toy.raw(bytes.fromhex(cleaned))
    elif args.cmd == "subscribe":
        await toy.subscribe(float(args.seconds))


def main() -> None:
    ap = argparse.ArgumentParser(description="计算者-X BLE 控制")
    ap.add_argument("-c", "--config", default="frames.json")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="列出协议表里的帧")

    p = sub.add_parser("run", help="按模式/速度运行")
    p.add_argument("--mode", type=int, default=1)
    p.add_argument("--speed", type=int, default=1)
    p.add_argument("--seconds", type=int, default=0)
    p.set_defaults(func=amain)

    p = sub.add_parser("send", help="发送协议表里的命名帧")
    p.add_argument("frame")
    p.add_argument("--var", action="append", default=[])
    p.set_defaults(func=amain)

    p = sub.add_parser("raw", help="直接发十六进制")
    p.add_argument("hex")
    p.set_defaults(func=amain)

    p = sub.add_parser("subscribe", help="只订阅通知")
    p.add_argument("seconds", nargs="?", type=float, default=30.0)
    p.set_defaults(func=amain)

    sub.add_parser("stop", help="发送 stop 帧")

    args = ap.parse_args()
    args.func = getattr(args, "func", None)
    if args.cmd not in ("list", "run", "send", "raw", "subscribe", "stop"):
        ap.error("未知命令")
    try:
        asyncio.run(amain(args))
    except KeyboardInterrupt:
        print("\n[!] 中断")


if __name__ == "__main__":
    main()
