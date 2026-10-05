#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ble_probe.py —— BLE 侦察 / 最小验证工具（逆向第一步）

命令:
  scan                        扫描周边 BLE 设备（名称 / 地址 / RSSI / 广播数据）
  dump   <ADDR|NAME>          连接并枚举全部 服务 / 特征 / 属性
  watch  <ADDR|NAME> [SEC]    订阅所有 notify/indicate 特征并打印（边按键边看）
  poke   <ADDR|NAME> <UUID> <HEX>   向指定特征写一帧十六进制（单帧试探）

示例:
  python ble_probe.py scan -t 20
  python ble_probe.py dump AA:BB:CC:DD:EE:FF
  python ble_probe.py watch "Miji" 30
  python ble_probe.py poke AA:BB:CC:DD:EE:FF 0000fff2-0000-1000-8000-00805f9b34fb "AA 01 01 00 00 55"

注意：一次只改一个字节。异常帧可能让设备重启或掉线，属正常现象，重连即可。
依赖：pip install bleak
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
import time

try:
    from bleak import BleakClient, BleakScanner
except ImportError:
    sys.exit("缺少依赖，先执行: pip install bleak")

MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")


def to_bytes(text: str) -> bytes:
    s = re.sub(r"[^0-9A-Fa-f]", "", text)
    if len(s) % 2:
        raise SystemExit("HEX 长度必须为偶数")
    return bytes.fromhex(s)


async def resolve(target: str, timeout: float = 15.0) -> str:
    """把 MAC 或设备名解析成可连接的地址。"""
    if MAC_RE.match(target):
        return target
    print(f"[*] 未给 MAC，按名称扫描 {target!r} ...", file=sys.stderr)
    devices = await BleakScanner.discover(timeout=timeout)
    for d in devices:
        if target.lower() in (d.name or "").lower():
            print(f"[+] 命中 {d.name}  {d.address}", file=sys.stderr)
            return d.address
    raise SystemExit(f"没扫到名字含 {target!r} 的设备，先跑 scan 看实际广播名")


async def cmd_scan(args: argparse.Namespace) -> None:
    try:
        found = await BleakScanner.discover(timeout=args.timeout, return_adv=True)
        pairs = list(found.values())
    except TypeError:  # 老版本 bleak 没有 return_adv
        pairs = [(d, None) for d in await BleakScanner.discover(timeout=args.timeout)]

    rows = []
    for dev, adv in pairs:
        name = dev.name or (adv.local_name if adv else None) or "-"
        rssi = getattr(adv, "rssi", None) or dev.rssi
        svc = ""
        if adv is not None:
            svc = ",".join(sorted(adv.service_uuids or [])[:4])
        rows.append((rssi or -127, name, dev.address, svc))

    rows.sort(reverse=True)
    print(f"{'RSSI':>5}  {'NAME':<24} {'ADDRESS':<18} SERVICES")
    for rssi, name, address, svc in rows:
        print(f"{rssi:>5}  {name:<24} {address:<18} {svc}")
    print(f"\n共 {len(rows)} 个设备。找功率最高、名字像玩具/App 品牌的那个。")


async def cmd_dump(args: argparse.Namespace) -> None:
    address = await resolve(args.target)
    async with BleakClient(address, timeout=args.timeout) as client:
        print(f"[+] 已连接 {address}")
        for service in client.services:
            print(f"\nSERVICE {service.uuid}  ({service.description})")
            for ch in service.characteristics:
                props = ",".join(ch.properties)
                print(f"  CHAR {ch.uuid}  [{props}]  ({ch.description})")
                for d in ch.descriptors:
                    print(f"       DESC {d.uuid} ({d.description})")
        writable = [c.uuid for s in client.services for c in s.characteristics
                    if "write" in c.properties or "write-without-response" in c.properties]
        notifiable = [c.uuid for s in client.services for c in s.characteristics
                      if "notify" in c.properties or "indicate" in c.properties]
        print("\n---")
        print("可写特征:", writable or "无")
        print("可订阅特征:", notifiable or "无")
        print("把上面这两行填进 frames.json 的 write_char / notify_char。")


async def cmd_watch(args: argparse.Namespace) -> None:
    address = await resolve(args.target)

    def on_data(sender, data: bytearray) -> None:
        stamp = time.strftime("%H:%M:%S")
        try:
            ascii_repr = data.decode("ascii")
            ascii_repr = "".join(c if 32 <= ord(c) < 127 else "." for c in ascii_repr)
        except Exception:
            ascii_repr = "." * len(data)
        print(f"[{stamp}] {sender.uuid}  {data.hex(' ')}   |{ascii_repr}|", flush=True)

    async with BleakClient(address, timeout=args.timeout) as client:
        targets = [c for s in client.services for c in s.characteristics
                   if "notify" in c.properties or "indicate" in c.properties]
        if not targets:
            print("该设备没有可订阅特征。")
            return
        for ch in targets:
            try:
                await client.start_notify(ch, on_data)
                print(f"[+] 已订阅 {ch.uuid}")
            except Exception as exc:
                print(f"[!] 订阅 {ch.uuid} 失败: {exc}")
        print(f"[*] 监听 {args.seconds}s —— 现在去按设备上的按键 / 用官方 App 操作，观察回传。")
        await asyncio.sleep(args.seconds)
        for ch in targets:
            try:
                await client.stop_notify(ch)
            except Exception:
                pass


async def cmd_poke(args: argparse.Namespace) -> None:
    address = await resolve(args.target)
    payload = to_bytes(args.hex)
    async with BleakClient(address, timeout=args.timeout) as client:
        print(f"[+] 已连接 {address}，写入 {args.uuid} <- {payload.hex(' ')}")
        try:
            await client.write_gatt_char(args.uuid, payload, response=False)
        except Exception as exc:
            print(f"[!] 无响应写失败({exc})，改用需要响应的方式")
            await client.write_gatt_char(args.uuid, payload, response=True)
        print("[+] 已发送。观察设备是否动作；若是，把这一帧记进 frames.json。")


def main() -> None:
    ap = argparse.ArgumentParser(description="BLE 侦察工具")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scan", help="扫描周边 BLE 设备")
    p.add_argument("-t", "--timeout", type=float, default=12.0)
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("dump", help="枚举服务/特征/属性")
    p.add_argument("target")
    p.add_argument("-t", "--timeout", type=float, default=20.0)
    p.set_defaults(func=cmd_dump)

    p = sub.add_parser("watch", help="订阅 notify 并打印")
    p.add_argument("target")
    p.add_argument("seconds", nargs="?", type=float, default=30.0)
    p.add_argument("-t", "--timeout", type=float, default=20.0)
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("poke", help="向特征写一帧 HEX")
    p.add_argument("target")
    p.add_argument("uuid")
    p.add_argument("hex")
    p.add_argument("-t", "--timeout", type=float, default=20.0)
    p.set_defaults(func=cmd_poke)

    args = ap.parse_args()
    try:
        asyncio.run(args.func(args))
    except KeyboardInterrupt:
        print("\n[!] 中断")


if __name__ == "__main__":
    main()
