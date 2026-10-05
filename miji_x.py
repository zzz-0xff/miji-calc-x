#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""miji_x.py —— 谜姬「计算者-X」BLE 驱动

协议来自 谜姬官方 APP v3.7.1（cn.intofun.mizzzee）反编译：
  assets/apps/__UNI__E392D13/www/app-service.js
  - Ur()/Ce()          -> AA 帧族组帧
  - dp{}               -> 命令表（计数 / 加热 / 模式 / 柔性控制）
  - xht.js 的 Ct/$x/Bi -> XHT 帧族（20 字节 03 12 …）
  - pg()/mP()          -> AA 帧解析 + 计数上报解码

================================ 协议速查 ================================

【A. AA 帧族】大多数谜姬 / 安可尼设备
    AA <cmd> <len> <payload…> <sum8>
    sum8 = (AA + cmd + len + payload 各字节之和) & 0xFF

    cmd  作用        payload
    0x04 加热开关    [0|1]
    0x06 模式控制    [伸缩, 震动, 吸吮, 旋转, 加热]
    0x08 柔性控制    [伸缩, 震动, 吸吮, 旋转, 时长低, 时长高]
    0x0A 计数开关    [0|1]   男用（头部计数）
    0x12 计数开关    [0|1]   女用（计数阈值 18）
    0x0D 计数重置    [1]

    上报 0x0B / 0x13，payload 8 字节：
      [0] isEnabled  [1] enterLevel  [2..3] enterCount(LE)
      [4] temperature [5] humidity   [6..7] duration(LE)
      enterLevel: 1=头部 3=半入 7=全入

【B. XHT 帧族】service 0000D34E，全部 20 字节: 03 12 <cmd> <payload…>
    0xF0 setClear     [3] = e or 7
    0xF2 setStrength  uint16LE @3
    0xF3 setStrength2 uint16LE @3
    0xF4 setModel     [3] = mode
    滑块 = 03 12 F3 | FC00 FE00 0140 v1 | FC00 FE00 0140 v2
      v = (int(rg(pct/100)*1023) << 6) | 60,  rg(x) = 0 if x==0 else 0.7x+0.3

用法:
  python miji_x.py scan
  python miji_x.py selftest
  python miji_x.py info  --name Miji
  python miji_x.py mode  --telescopic 2 --heat 1 --name Miji
  python miji_x.py flex  --telescopic 3 --duration 30 --name Miji
  python miji_x.py count start|stop|reset --name Miji
  python miji_x.py heat  on|off --name Miji
  python miji_x.py strength 60 --name Miji        (仅 XHT 设备)
  python miji_x.py raw "AA 06 05 02 00 00 00 00 B7"
  python miji_x.py listen 30 --name Miji
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
import time
from dataclasses import dataclass, field

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

AA = 0xAA

# ---- 已知服务 / 特征（来自 dev.js 的 lg.DEFAULT 与 Zx 设备表） ---- #
KNOWN_SERVICES: dict[str, dict] = {
    "0000d34e-0000-1000-8000-00805f9b34fb": {
        "proto": "xht",
        "write": "0000ff12-0000-1000-8000-00805f9b34fb",
        "notify": "0000ff11-0000-1000-8000-00805f9b34fb",
        "read": "00002a50-0000-1000-8000-00805f9b34fb",
        "name": "XHTKJ 方案",
    },
    "0000dddd-0000-1000-8000-00805f9b34fb": {
        "proto": "aa",
        "write": "0000ddd1-0000-1000-8000-00805f9b34fb",
        "notify": "0000ddd2-0000-1000-8000-00805f9b34fb",
        "read": "0000ddd3-0000-1000-8000-00805f9b34fb",
        "name": "谜姬默认设备",
    },
    "0000ffe0-0000-1000-8000-00805f9b34fb": {
        "proto": "aa",
        "write": "0000ffe1-0000-1000-8000-00805f9b34fb",
        "notify": "0000ffe1-0000-1000-8000-00805f9b34fb",
        "name": "mizzzee_0029 类",
    },
    "0000ae30-0000-1000-8000-00805f9b34fb": {
        "proto": "aa",
        "write": "0000ae01-0000-1000-8000-00805f9b34fb",
        "notify": "0000ae02-0000-1000-8000-00805f9b34fb",
        "read": "0000ae10-0000-1000-8000-00805f9b34fb",
        "name": "mizzzee_0014 类",
    },
    "0000fee0-0000-1000-8000-00805f9b34fb": {
        "proto": "aa",
        "write": "0000fee1-0000-1000-8000-00805f9b34fb",
        "notify": "0000fee1-0000-1000-8000-00805f9b34fb",
        "name": "mizzzee_0010 类",
    },
}


# --------------------------------------------------------------------------- #
# 组帧 / 解析
# --------------------------------------------------------------------------- #
def sum8(values: list[int]) -> int:
    return sum(values) & 0xFF


def aa_frame(cmd: int, payload: list[int] | None = None) -> bytes:
    """AA <cmd> <len> <payload> <sum8>"""
    payload = payload or []
    if not 0 <= cmd <= 0xFF:
        raise ValueError("cmd 必须在 0-255")
    if len(payload) > 16:
        raise ValueError("payload 最长 16 字节")
    body = [AA, cmd, len(payload), *payload]
    body.append(sum8(body))
    return bytes(body)


def parse_aa_frame(data: bytes) -> dict | None:
    """解析 AA 帧，格式不符返回 None"""
    if len(data) < 4 or data[0] != AA:
        return None
    cmd, length = data[1], data[2]
    if len(data) != 4 + length:
        return None
    content = list(data[3 : 3 + length])
    checksum = data[3 + length]
    return {
        "header": data[0],
        "cmd": cmd,
        "length": length,
        "content": content,
        "checksum": checksum,
        "valid": sum8(list(data[: 3 + length])) == checksum,
        "bytes": bytes(data),
    }


def rg(x: float) -> float:
    """APP 里的强度曲线 rg(e) = e==0 ? 0 : 0.7e+0.3"""
    return 0.0 if x == 0 else 0.7 * x + 0.3


def xht_slider_value(pct: float) -> int:
    """百分比 -> 10 位强度字段（低 6 位固定 0x3C）"""
    pct = max(0.0, min(100.0, float(pct)))
    return (int(rg(pct / 100.0) * 1023) << 6) | 0x3C


def xht_frame(cmd: int, payload: bytes = b"", total: int = 20) -> bytes:
    buf = bytearray(total)
    buf[0], buf[1], buf[2] = 0x03, 0x12, cmd & 0xFF
    buf[3 : 3 + len(payload)] = payload
    return bytes(buf)


def xht_set_model(mode: int) -> bytes:
    return xht_frame(0xF4, bytes([mode & 0xFF]))


def xht_set_clear(value: int = 7) -> bytes:
    return xht_frame(0xF0, bytes([value & 0xFF]))


def xht_set_strength(pct: float, strength2: bool = False) -> bytes:
    value = xht_slider_value(pct) if pct > 1 else int(pct)
    return xht_frame(0xF3 if strength2 else 0xF2, value.to_bytes(2, "little"))


def xht_dual_slider(a_pct: float, b_pct: float) -> bytes:
    buf = bytearray(20)
    buf[0], buf[1], buf[2] = 0x03, 0x12, 0xF3
    for offset, pct in ((3, a_pct), (11, b_pct)):
        buf[offset : offset + 2] = (0xFC00).to_bytes(2, "little")
        buf[offset + 2 : offset + 4] = (0xFE00).to_bytes(2, "little")
        buf[offset + 4 : offset + 6] = (0x0140).to_bytes(2, "little")
        buf[offset + 6 : offset + 8] = xht_slider_value(pct).to_bytes(2, "little")
    return bytes(buf)


# ---- AA 命令构造（对应 APP 的 dp{}） ---- #
def frame_heat(on: bool) -> bytes:
    return aa_frame(0x04, [1 if on else 0])


def frame_mode(telescopic=0, vibration=0, suction=0, rotation=0, heat=0) -> bytes:
    return aa_frame(0x06, [telescopic, vibration, suction, rotation, heat])


def frame_flex(telescopic=0, vibration=0, suction=0, rotation=0, duration=0) -> bytes:
    d = int(duration) & 0xFFFF
    return aa_frame(0x08, [telescopic, vibration, suction, rotation, d & 0xFF, (d >> 8) & 0xFF])


def frame_count(on: bool, female: bool = False) -> bytes:
    return aa_frame(0x12 if female else 0x0A, [1 if on else 0])


def frame_count_reset() -> bytes:
    return aa_frame(0x0D, [1])


def decode_count(payload: list[int]) -> dict:
    is_enabled, level = payload[0], payload[1]
    bucket = {1: "headEnterCount", 3: "halfEnterCount", 7: "fullEnterCount"}.get(level, f"level{level}")
    return {
        "isEnabled": is_enabled == 1,
        "enterLevel": level,
        "enterCount": payload[2] | (payload[3] << 8),
        "temperature": payload[4],
        "humidity": payload[5],
        "duration": payload[6] | (payload[7] << 8),
        "bucket": bucket,
    }


# --------------------------------------------------------------------------- #
# 设备
# --------------------------------------------------------------------------- #
@dataclass
class Link:
    address: str
    name: str = ""
    proto: str = "aa"
    write: str = ""
    notify: str = ""
    service: str = ""
    profile: str = "未知"
    services: list[str] = field(default_factory=list)


async def resolve(target: str, timeout: float = 15.0) -> tuple[str, str]:
    if MAC_RE.match(target):
        return target, ""
    if not BLEAK_OK:
        raise SystemExit(BLEAK_ERR)
    for dev in await BleakScanner.discover(timeout=timeout):
        if target.lower() in (dev.name or "").lower():
            return dev.address, dev.name or ""
    raise SystemExit(f"没扫到名字含 {target!r} 的设备（玩具要先开机）")


class Device:
    def __init__(self, target: str, timeout: float = 20.0):
        self.target = target
        self.timeout = timeout
        self.link: Link | None = None
        self.client: BleakClient | None = None

    async def connect(self) -> Link:
        if not BLEAK_OK:
            raise SystemExit(BLEAK_ERR)
        address, name = await resolve(self.target, timeout=min(self.timeout, 15.0))
        self.client = BleakClient(address, timeout=self.timeout)
        await self.client.connect()

        link = Link(address=address, name=name, services=[s.uuid.lower() for s in self.client.services])

        for svc in self.client.services:
            cfg = KNOWN_SERVICES.get(svc.uuid.lower())
            if not cfg:
                continue
            props = {c.uuid.lower(): c.properties for c in svc.characteristics}
            link.proto = cfg["proto"]
            link.service = svc.uuid.lower()
            link.profile = cfg.get("name", "")
            want_write, want_notify = cfg["write"].lower(), cfg.get("notify", "").lower()
            link.write = want_write if want_write in props else ""
            link.notify = want_notify if want_notify in props else ""
            if not link.write:
                for uuid, pr in props.items():
                    if any("write" in p for p in pr):
                        link.write = uuid
                        break
            break
        else:
            link.proto, link.profile = "aa", "未知服务（按 AA 协议试）"
            for svc in self.client.services:
                for ch in svc.characteristics:
                    if any("write" in p for p in ch.properties):
                        link.write, link.service = ch.uuid.lower(), svc.uuid.lower()
                        break
                if link.write:
                    break

        if not link.write:
            raise SystemExit("这个设备没有可写特征，可能不是 BLE 控制型号")

        self.link = link
        return link

    async def notify_on(self, handler) -> None:
        assert self.client and self.link
        if self.link.notify:
            try:
                await self.client.start_notify(self.link.notify, handler)
            except Exception as exc:  # noqa: BLE001
                print(f"[!] 订阅 {self.link.notify} 失败: {exc}", file=sys.stderr)

    async def send(self, payload: bytes) -> None:
        assert self.client and self.link
        try:
            await self.client.write_gatt_char(self.link.write, payload, response=False)
        except Exception:
            await self.client.write_gatt_char(self.link.write, payload, response=True)
        print(f"[>] {payload.hex(' ').upper()}", flush=True)

    async def send_hex(self, hexstr: str) -> None:
        cleaned = re.sub(r"[^0-9A-Fa-f]", "", hexstr)
        await self.send(bytes.fromhex(cleaned))

    async def close(self) -> None:
        if self.client:
            try:
                await self.client.disconnect()
            except Exception:
                pass


async def with_device(target: str, action) -> None:
    dev = Device(target)
    try:
        link = await dev.connect()
        print(f"[+] {link.address}  {link.name}  协议={link.proto}  方案={link.profile}")
        print(f"    service={link.service}  write={link.write}  notify={link.notify}")
        await action(dev)
    finally:
        await dev.close()


def make_handler():
    def handler(sender, data: bytearray) -> None:
        raw = bytes(data)
        stamp = time.strftime("%H:%M:%S")
        frame = parse_aa_frame(raw)
        if frame:
            extra = ""
            if frame["cmd"] in (0x0B, 0x13) and frame["length"] == 8:
                extra = "  计数: " + str(decode_count(frame["content"]))
            print(
                f"[{stamp}] {raw.hex(' ').upper()}  cmd=0x{frame['cmd']:02X} len={frame['length']} "
                f"checksum={'OK' if frame['valid'] else 'BAD'}{extra}",
                flush=True,
            )
        else:
            print(f"[{stamp}] {raw.hex(' ').upper()}  (非 AA 帧)", flush=True)

    return handler


# --------------------------------------------------------------------------- #
# 子命令
# --------------------------------------------------------------------------- #
async def run_scan(args) -> None:
    try:
        found = await BleakScanner.discover(timeout=args.timeout, return_adv=True)
        pairs = list(found.values())
    except TypeError:
        pairs = [(d, None) for d in await BleakScanner.discover(timeout=args.timeout)]

    rows = []
    for dev, adv in pairs:
        name = dev.name or (adv.local_name if adv else None) or "-"
        rows.append((getattr(adv, "rssi", None) or dev.rssi or -127, name, dev.address))
    rows.sort(reverse=True)
    for rssi, name, address in rows:
        print(f"{rssi:>5}  {name:<26} {address}")
    print(f"\n共 {len(rows)} 个。玩具要开机才会广播。")

    if rows and args.probe > 0:
        print("\n=== 逐个试连，自动识别协议 ===")
        for _, name, address in rows[: args.probe]:
            dev = Device(address)
            try:
                link = await dev.connect()
                print(f"  {address}  {name}  ->  协议={link.proto}  方案={link.profile}  write={link.write}")
            except Exception as exc:  # noqa: BLE001
                print(f"  {address}  {name}  连接失败: {exc}")
            finally:
                await dev.close()


async def run_info(args) -> None:
    async def action(dev: Device) -> None:
        assert dev.client
        for svc in dev.client.services:
            print(f"\nSERVICE {svc.uuid}  ({svc.description})")
            for ch in svc.characteristics:
                mark = ""
                if dev.link and ch.uuid.lower() == dev.link.write:
                    mark = "   <== 写这里"
                if dev.link and ch.uuid.lower() == dev.link.notify:
                    mark += "   <== 订阅这里"
                print(f"  CHAR {ch.uuid}  [{','.join(ch.properties)}]{mark}")

    await with_device(args.address or args.name, action)


async def run_mode(args) -> None:
    async def action(dev: Device) -> None:
        if dev.link and dev.link.proto == "xht":
            level = max(args.telescopic, args.vibration, args.suction, args.rotation)
            await dev.send(xht_set_model(args.telescopic))
            await dev.send(xht_set_strength(min(100.0, level * 33.0)))
        else:
            await dev.send(frame_mode(args.telescopic, args.vibration, args.suction, args.rotation, args.heat))
            print(f"    (cmd=0x06 payload=[{args.telescopic},{args.vibration},{args.suction},{args.rotation},{args.heat}])")
        if args.duration:
            print(f"[*] {args.duration}s 后自动停")
            await asyncio.sleep(args.duration)
            await dev.send(frame_mode())

    await with_device(args.address or args.name, action)


async def run_flex(args) -> None:
    async def action(dev: Device) -> None:
        await dev.send(frame_flex(args.telescopic, args.vibration, args.suction, args.rotation, args.duration))
        print(f"    (cmd=0x08 payload=[{args.telescopic},{args.vibration},{args.suction},{args.rotation},{args.duration}] LE)")

    await with_device(args.address or args.name, action)


async def run_count(args) -> None:
    async def action(dev: Device) -> None:
        await dev.notify_on(make_handler())
        if args.action == "start":
            await dev.send(frame_count(True, args.female))
        elif args.action == "stop":
            await dev.send(frame_count(False, args.female))
        else:
            await dev.send(frame_count_reset())
        if args.listen:
            print(f"[*] 监听计数上报 {args.listen}s …")
            await asyncio.sleep(args.listen)

    await with_device(args.address or args.name, action)


async def run_heat(args) -> None:
    async def action(dev: Device) -> None:
        await dev.send(frame_heat(args.action == "on"))

    await with_device(args.address or args.name, action)


async def run_strength(args) -> None:
    async def action(dev: Device) -> None:
        if dev.link and dev.link.proto == "xht":
            await dev.send(xht_set_strength(args.percent))
        else:
            base = max(0, min(255, int(args.percent / 100 * 255)))
            await dev.send(aa_frame(0x08, [base, 0, 0, 0, 0, 0]))

    await with_device(args.address or args.name, action)


async def run_raw(args) -> None:
    async def action(dev: Device) -> None:
        await dev.send_hex(args.hex)

    await with_device(args.address or args.name, action)


async def run_listen(args) -> None:
    async def action(dev: Device) -> None:
        await dev.notify_on(make_handler())
        print(f"[*] 监听 {args.seconds}s（同时打印解码后的计数）")
        await asyncio.sleep(args.seconds)

    await with_device(args.address or args.name, action)


PROBE_SEQUENCE: list[tuple[str, bytes]] = [
    ("计数开关 (cmd 0x0A)", frame_count(True)),
    ("单马达 50%  (prefix=08, 1 路)", aa_frame(0x08, [50])),
    ("单马达 100% (prefix=08, 1 路)", aa_frame(0x08, [100])),
    ("单马达 50 + level (prefix=08, 2 字节)", aa_frame(0x08, [50, 100])),
    ("prefix=06 单马达 50%", aa_frame(0x06, [50])),
    ("五路模式 [伸缩1,0,0,0,0]", aa_frame(0x06, [1, 0, 0, 0, 0])),
    ("五路模式 [伸缩2,0,0,0,0]", aa_frame(0x06, [2, 0, 0, 0, 0])),
    ("五路模式 [伸缩3,0,0,0,0]", aa_frame(0x06, [3, 0, 0, 0, 0])),
    ("柔性控制 [伸缩3, 时长10s]", frame_flex(3, 0, 0, 0, 10)),
    ("加热开 (cmd 0x04)", frame_heat(True)),
    ("全停 (cmd 0x06 五路全 0)", frame_mode()),
    ("全停 (cmd 0x08 单马达 0)", aa_frame(0x08, [0])),
]


async def run_probe(args) -> None:
    if args.dry_run:
        print("=== 试探序列（可直接粘进 nRF Connect 的 Byte Array 输入框）===")
        for i, (label, payload) in enumerate(PROBE_SEQUENCE, 1):
            print(f"{i:>2}. {label:<38} {payload.hex(' ').upper()}")
        print("\n一帧一帧发，记住哪一帧让设备动了。")
        return

    async def action(dev: Device) -> None:
        await dev.notify_on(make_handler())
        for i, (label, payload) in enumerate(PROBE_SEQUENCE, 1):
            print(f"\n>>> [{i}/{len(PROBE_SEQUENCE)}] {label}")
            await dev.send(payload)
            await asyncio.sleep(args.gap)
            if args.stop_between and payload != frame_mode():
                await dev.send(frame_mode())
                await asyncio.sleep(0.8)
        print("\n[*] 序列跑完。告诉我第几帧设备有反应。")

    await with_device(args.address or args.name, action)


def selftest() -> None:
    print("=== AA 帧样例（可与 APP 日志对照） ===")
    cases = [
        ("开始计数(男用)", frame_count(True)),
        ("停止计数", frame_count(False)),
        ("计数重置", frame_count_reset()),
        ("加热 开", frame_heat(True)),
        ("加热 关", frame_heat(False)),
        ("模式 [伸缩2,0,0,0,0]", frame_mode(telescopic=2)),
        ("柔性 [伸缩3, 时长30s]", frame_flex(telescopic=3, duration=30)),
        ("关闭全部", frame_mode()),
    ]
    for label, payload in cases:
        parsed = parse_aa_frame(payload)
        assert parsed and parsed["valid"], label
        print(f"  {label:<24} {payload.hex(' ').upper()}")

    print("\n=== XHT 帧样例 ===")
    print(f"  setModel(1)        {xht_set_model(1).hex(' ').upper()}")
    print(f"  setClear()         {xht_set_clear().hex(' ').upper()}")
    print(f"  setStrength(60%)   {xht_set_strength(60).hex(' ').upper()}")
    print(f"  双滑块 60%/80%     {xht_dual_slider(60, 80).hex(' ').upper()}")

    print("\n=== 计数上报解码样例 ===")
    demo = aa_frame(0x0B, [1, 1, 0x2A, 0x00, 0x24, 0x32, 0x3C, 0x00])
    parsed = parse_aa_frame(demo)
    assert parsed
    print(f"  {demo.hex(' ').upper()}  ->  {decode_count(parsed['content'])}")
    print("\n全部校验和通过。")


def main() -> None:
    ap = argparse.ArgumentParser(description="谜姬 计算者-X BLE 驱动")
    ap.add_argument("--address", help="MAC 地址")
    ap.add_argument("--name", help="设备广播名（子串匹配）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scan")
    p.add_argument("-t", "--timeout", type=float, default=12.0)
    p.add_argument("--probe", type=int, default=0, help="对前 N 个设备试连并识别协议")
    p.set_defaults(func=run_scan)

    sub.add_parser("selftest").set_defaults(func=None)

    sub.add_parser("info").set_defaults(func=run_info)

    p = sub.add_parser("mode")
    p.add_argument("--telescopic", type=int, default=0)
    p.add_argument("--vibration", type=int, default=0)
    p.add_argument("--suction", type=int, default=0)
    p.add_argument("--rotation", type=int, default=0)
    p.add_argument("--heat", type=int, default=0)
    p.add_argument("--duration", type=int, default=0, help="秒，0 表示不停")
    p.set_defaults(func=run_mode)

    p = sub.add_parser("flex")
    p.add_argument("--telescopic", type=int, default=0)
    p.add_argument("--vibration", type=int, default=0)
    p.add_argument("--suction", type=int, default=0)
    p.add_argument("--rotation", type=int, default=0)
    p.add_argument("--duration", type=int, default=0)
    p.set_defaults(func=run_flex)

    p = sub.add_parser("count")
    p.add_argument("action", choices=["start", "stop", "reset"])
    p.add_argument("--female", action="store_true", help="女用计数命令(0x12)")
    p.add_argument("--listen", type=int, default=0)
    p.set_defaults(func=run_count)

    p = sub.add_parser("heat")
    p.add_argument("action", choices=["on", "off"])
    p.set_defaults(func=run_heat)

    p = sub.add_parser("strength")
    p.add_argument("percent", type=float)
    p.set_defaults(func=run_strength)

    p = sub.add_parser("raw")
    p.add_argument("hex")
    p.set_defaults(func=run_raw)

    p = sub.add_parser("listen")
    p.add_argument("seconds", type=float, nargs="?", default=30.0)
    p.set_defaults(func=run_listen)

    p = sub.add_parser("probe", help="按顺序试探全部候选帧，人肉看哪一帧有反应")
    p.add_argument("--gap", type=float, default=4.0, help="每帧之间的观察秒数")
    p.add_argument("--no-stop-between", dest="stop_between", action="store_false")
    p.add_argument("--dry-run", action="store_true", help="只打印帧，不连蓝牙")
    p.set_defaults(stop_between=True, func=run_probe)

    args = ap.parse_args()
    if args.cmd == "selftest":
        selftest()
        return
    if args.cmd == "scan":
        asyncio.run(run_scan(args))
        return
    if args.cmd == "probe" and args.dry_run:
        asyncio.run(run_probe(args))
        return
    if not args.address and not args.name:
        ap.error("需要 --address 或 --name")
    try:
        asyncio.run(args.func(args))
    except KeyboardInterrupt:
        print("\n[!] 中断")


if __name__ == "__main__":
    main()
