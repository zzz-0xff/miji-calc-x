#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""web_server.py —— 计算者-X 蓝牙桥的服务端（PC 上跑）

架构（因为这台电脑的蓝牙收不到广播，改用手机当 BLE 网关）：

    [AI / DSH] --HTTP--> [本服务 :8080] <--200ms 轮询-- [手机的 Edge + Web Bluetooth] --BLE--> [玩具]

用法:
    python web_server.py            # 监听 127.0.0.1:8080
    adb reverse tcp:8080 tcp:8080   # 把手机的 localhost:8080 打到 PC
    手机 Edge 打开 http://localhost:8080

对外接口（AI 用这个控制设备）:
    GET  /                桥页面
    GET  /api/state       当前目标状态
    POST /api/set         设置状态 {"running":true,"cmd":8,"payload":[50],"hz":10}
    GET  /api/log         读设备侧日志
    POST /api/log         设备侧上报日志
    POST /api/notify      设备侧上报通知帧
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = Path(__file__).resolve().parent
LOG_FILE = BASE / "capture" / "bridge.log"

STATE_LOCK = threading.Lock()
STATE = {
    "running": False,
    "cmd": 0x08,
    "payload": [0],
    "hz": 10,
    "updated": time.time(),
}
LOGS: list[str] = []
NOTIFIES: list[str] = []


def add_log(text: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {text}"
    LOGS.append(line)
    del LOGS[:-500]
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    print(line, flush=True)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def log_message(self, fmt, *args):  # 静音默认访问日志
        return

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            page = (BASE / "web" / "index.html").read_bytes()
            self._send(200, page, "text/html")
        elif path == "/api/state":
            with STATE_LOCK:
                body = json.dumps(STATE).encode()
            self._send(200, body)
        elif path == "/api/log":
            tail = 60
            if "?" in self.path:
                query = self.path.split("?", 1)[1]
                for part in query.split("&"):
                    if part.startswith("tail="):
                        try:
                            tail = max(1, min(500, int(part[5:])))
                        except ValueError:
                            pass
            self._send(200, json.dumps({"logs": LOGS[-tail:], "notifies": NOTIFIES[-20:]}, ensure_ascii=False).encode())
        else:
            self._send(404, b'{"error":"not found"}')

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        raw = self._read_body()
        if path == "/api/set":
            try:
                data = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                self._send(400, b'{"error":"bad json"}')
                return
            with STATE_LOCK:
                if "running" in data:
                    STATE["running"] = bool(data["running"])
                if "cmd" in data:
                    STATE["cmd"] = int(data["cmd"])
                if "payload" in data:
                    STATE["payload"] = [int(x) & 0xFF for x in data["payload"]]
                if "hz" in data:
                    STATE["hz"] = max(1, min(50, int(data["hz"])))
                STATE["updated"] = time.time()
                body = json.dumps(STATE).encode()
            add_log(f"SET {data}")
            self._send(200, body)
        elif path == "/api/log":
            add_log(raw.decode("utf-8", "replace")[:300])
            self._send(200, b'{"ok":true}')
        elif path == "/api/notify":
            text = raw.decode("utf-8", "replace")[:200]
            if not NOTIFIES or NOTIFIES[-1] != text:
                NOTIFIES.append(text)
                del NOTIFIES[:-50]
                add_log(f"NOTIFY {text} (变化)")
            self._send(200, b'{"ok":true}')
        else:
            self._send(404, b'{"error":"not found"}')


def main() -> None:
    addr = ("127.0.0.1", 8080)
    httpd = ThreadingHTTPServer(addr, Handler)
    add_log(f"bridge server on http://{addr[0]}:{addr[1]}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
