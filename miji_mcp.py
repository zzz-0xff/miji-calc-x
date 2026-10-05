#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""miji_mcp.py —— 把「计算者-X」的 BLE 控制暴露成 MCP(stdio) 工具，让 AI 直接调用。

协议来自 谜姬官方 APP v3.7.1 反编译，详见同目录 miji_x.py 顶部注释 / PROTOCOL.md。

由 MCP 客户端拉起（DSH 的 mcpServers 里 command 必须是绝对路径，按自己的实际位置改）:
{
  "mcpServers": [{
    "name": "miji",
    "type": "stdio",
    "command": "<python 的绝对路径>",
    "args": ["<项目目录>/miji_mcp.py", "--name", "Miji"],
    "env": {}
  }]
}

工具:
  miji_status     连接状态 / MAC / 协议族 / 写特征
  miji_mode       模式控制 cmd 0x06 [伸缩,震动,吸吮,旋转,加热]，可定时自动停
  miji_flex       柔性控制 cmd 0x08，带时长（秒）
  miji_count      计数开关 / 重置（0x0A 男用，0x12 女用，0x0D 重置）
  miji_heat       加热开关 cmd 0x04
  miji_strength   0-100% 强度（XHT 设备走 0xF2，AA 设备走 0x08）
  miji_raw        直接发一帧 HEX，逆向调试用
  miji_stop       立即停
"""
from __future__ import annotations

import argparse
import asyncio
import atexit
import json
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from miji_x import (  # noqa: E402
    Device,
    frame_count,
    frame_count_reset,
    frame_flex,
    frame_heat,
    frame_mode,
    aa_frame,
    xht_set_strength,
)

PROTOCOL_VERSION = "2024-11-05"
MAX_SECONDS = 300

TOOLS = [
    {
        "name": "miji_status",
        "description": "查看玩具连接状态、MAC、识别到的协议族与写特征。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "miji_mode",
        "description": "模式控制(cmd 0x06)：伸缩/震动/吸吮/旋转/加热 五路档位，可指定 duration 秒后自动停。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "telescopic": {"type": "integer", "description": "伸缩档位 0-3"},
                "vibration": {"type": "integer", "description": "震动档位 0-3"},
                "suction": {"type": "integer", "description": "吸吮档位 0-3"},
                "rotation": {"type": "integer", "description": "旋转档位 0-3"},
                "heat": {"type": "integer", "description": "加热 0/1"},
                "duration": {"type": "integer", "description": "持续秒数，0=不停"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "miji_flex",
        "description": "柔性控制(cmd 0x08)：四路档位 + 时长(秒)，设备到时间自己停。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "telescopic": {"type": "integer"},
                "vibration": {"type": "integer"},
                "suction": {"type": "integer"},
                "rotation": {"type": "integer"},
                "duration": {"type": "integer", "description": "秒"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "miji_count",
        "description": "计数功能：start 开启 / stop 关闭 / reset 清零。返回设备上报的进入次数、温度、湿度、时长。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["start", "stop", "reset"]},
                "female": {"type": "boolean", "description": "女用计数命令(0x12)，默认 false(0x0A)"},
                "listen": {"type": "integer", "description": "开启后监听的秒数，0=不听"},
            },
            "required": ["action"],
            "additionalProperties": False,
        },
    },
    {
        "name": "miji_heat",
        "description": "加热开关(cmd 0x04)。",
        "inputSchema": {
            "type": "object",
            "properties": {"on": {"type": "boolean"}},
            "required": ["on"],
            "additionalProperties": False,
        },
    },
    {
        "name": "miji_strength",
        "description": "按百分比设强度 0-100。XHT 设备走 0xF2，AA 设备走 0x08。",
        "inputSchema": {
            "type": "object",
            "properties": {"percent": {"type": "number"}},
            "required": ["percent"],
            "additionalProperties": False,
        },
    },
    {
        "name": "miji_raw",
        "description": "直接发送一帧十六进制（逆向调试用），如 'AA 06 05 02 00 00 00 00 B7'。",
        "inputSchema": {
            "type": "object",
            "properties": {"hex": {"type": "string"}},
            "required": ["hex"],
            "additionalProperties": False,
        },
    },
    {
        "name": "miji_stop",
        "description": "立即停止全部输出（发送全 0 模式帧）。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]

_write_lock = threading.Lock()


def send(message: dict) -> None:
    with _write_lock:
        sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
        sys.stdout.flush()


class Bridge:
    def __init__(self, target: str):
        self.target = target
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        self.dev = Device(target)
        self.timer: asyncio.TimerHandle | None = None
        self.last_error = ""
        atexit.register(self.shutdown)

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def run(self, coro, timeout: float = 60.0):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def shutdown(self) -> None:
        try:
            if self.timer:
                self.timer.cancel()
        except Exception:
            pass
        try:
            self.loop.call_soon_threadsafe(self.loop.stop)
        except Exception:
            pass

    def ensure(self):
        client = self.dev.client
        if client is not None and client.is_connected:
            return self.dev.link
        return self.run(self.dev.connect())

    def _schedule_stop(self, seconds: int) -> None:
        if self.timer:
            self.timer.cancel()
            self.timer = None
        if seconds > 0:
            def fire() -> None:
                self.loop.create_task(self.dev.send(frame_mode()))
                self.timer = None

            self.timer = self.loop.call_later(seconds, fire)

    # ---------------- 工具实现 ---------------- #
    def status(self, _args: dict) -> str:
        link = self.dev.link
        remain = None
        if self.timer is not None:
            remain = round(max(0.0, self.timer.when() - self.loop.time()), 1)
        return json.dumps(
            {
                "target": self.target,
                "connected": bool(link),
                "address": link.address if link else None,
                "name": link.name if link else None,
                "protocol": link.proto if link else None,
                "profile": link.profile if link else None,
                "write_char": link.write if link else None,
                "notify_char": link.notify if link else None,
                "auto_stop_remaining_s": remain,
                "last_error": self.last_error or None,
            },
            ensure_ascii=False,
        )

    def mode(self, args: dict) -> str:
        self.ensure()
        link = self.dev.link
        t = int(args.get("telescopic", 0))
        v = int(args.get("vibration", 0))
        s = int(args.get("suction", 0))
        r = int(args.get("rotation", 0))
        h = 1 if int(args.get("heat", 0)) else 0
        duration = max(0, min(int(args.get("duration", 0)), MAX_SECONDS))

        if link and link.proto == "xht":
            level = max(t, v, s, r)
            self.run(self.dev.send(xht_set_strength(min(100.0, level * 33.0))))
            sent = ["xht 0xF2 strength=%d" % level]
        else:
            payload = frame_mode(t, v, s, r, h)
            self.run(self.dev.send(payload))
            sent = [payload.hex(" ").upper()]

        self._schedule_stop(duration)
        return json.dumps({"sent": sent, "duration": duration}, ensure_ascii=False)

    def flex(self, args: dict) -> str:
        self.ensure()
        payload = frame_flex(
            int(args.get("telescopic", 0)),
            int(args.get("vibration", 0)),
            int(args.get("suction", 0)),
            int(args.get("rotation", 0)),
            int(args.get("duration", 0)),
        )
        self.run(self.dev.send(payload))
        return json.dumps({"sent": payload.hex(" ").upper()}, ensure_ascii=False)

    def count(self, args: dict) -> str:
        self.ensure()
        action = str(args["action"])
        female = bool(args.get("female", False))
        listen = max(0, min(int(args.get("listen", 0)), 60))

        if action == "start":
            payload = frame_count(True, female)
        elif action == "stop":
            payload = frame_count(False, female)
        else:
            payload = frame_count_reset()

        self.run(self.dev.send(payload))
        result = {"sent": payload.hex(" ").upper(), "action": action}
        if listen:
            self.run(self.dev.notify_on(lambda s, d: None))
            self.run(asyncio.sleep(listen), timeout=listen + 30)
            result["listened_s"] = listen
        return json.dumps(result, ensure_ascii=False)

    def heat(self, args: dict) -> str:
        self.ensure()
        payload = frame_heat(bool(args["on"]))
        self.run(self.dev.send(payload))
        return json.dumps({"sent": payload.hex(" ").upper()}, ensure_ascii=False)

    def strength(self, args: dict) -> str:
        self.ensure()
        percent = max(0.0, min(100.0, float(args["percent"])))
        link = self.dev.link
        if link and link.proto == "xht":
            payload = xht_set_strength(percent)
        else:
            payload = aa_frame(0x08, [int(percent / 100 * 255), 0, 0, 0, 0, 0])
        self.run(self.dev.send(payload))
        return json.dumps({"percent": percent, "sent": payload.hex(" ").upper()}, ensure_ascii=False)

    def raw(self, args: dict) -> str:
        self.ensure()
        cleaned = "".join(ch for ch in str(args["hex"]) if ch in "0123456789abcdefABCDEF")
        if len(cleaned) % 2:
            raise RuntimeError("HEX 长度必须为偶数")
        payload = bytes.fromhex(cleaned)
        self.run(self.dev.send(payload))
        return json.dumps({"sent": payload.hex(" ").upper()}, ensure_ascii=False)

    def stop(self, _args: dict) -> str:
        if self.timer:
            self.timer.cancel()
            self.timer = None
        self.ensure()
        payload = frame_mode()
        self.run(self.dev.send(payload))
        return json.dumps({"sent": payload.hex(" ").upper(), "stopped": True}, ensure_ascii=False)

    HANDLERS = {
        "miji_status": status,
        "miji_mode": mode,
        "miji_flex": flex,
        "miji_count": count,
        "miji_heat": heat,
        "miji_strength": strength,
        "miji_raw": raw,
        "miji_stop": stop,
    }


def handle(bridge: Bridge, message: dict) -> dict | None:
    method = message.get("method")
    msg_id = message.get("id")

    if method == "initialize":
        params = message.get("params") or {}
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "miji-x-bridge", "version": "1.0.0"},
            },
        }

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}

    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        handler = Bridge.HANDLERS.get(name)
        if handler is None:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"content": [{"type": "text", "text": f"未知工具: {name}"}], "isError": True},
            }
        try:
            text = handler(bridge, params.get("arguments") or {})
            is_error = False
        except Exception as exc:  # noqa: BLE001
            bridge.last_error = str(exc)
            text, is_error = f"执行失败: {exc}", True
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"content": [{"type": "text", "text": text}], "isError": is_error},
        }

    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

    if msg_id is None:
        return None

    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"未实现的方法: {method}"}}


def main() -> None:
    ap = argparse.ArgumentParser(description="计算者-X MCP(stdio) 桥")
    ap.add_argument("--name", help="设备广播名（子串匹配）")
    ap.add_argument("--address", help="设备 MAC")
    args = ap.parse_args()
    target = args.address or args.name
    if not target:
        target = "Miji"

    bridge = Bridge(target)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        reply = handle(bridge, message)
        if reply is not None:
            send(reply)


if __name__ == "__main__":
    main()
