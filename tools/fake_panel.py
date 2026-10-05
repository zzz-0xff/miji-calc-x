"""假控制台 —— 模拟一台连着设备、手环的控制台。

用途：没接真设备的时候也能看 AI 跑完整流程（截图、演示、测试提示词）。

它做三件事：
  · 装成 toy_panel 的接口，让 ai_demo.py 以为控制台在跑
  · **心率会跟着指令变** —— 档位加得越猛，心率爬得越快（这样 AI 的反应才真实）
  · 在控制台里打印每条收到的指令

真接设备的时候别用这个，用 toy_panel.py。
"""
from __future__ import annotations

import json
import random
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:  # noqa: BLE001
    pass

PORT = 8090

# ============================================================ 模拟状态
S = {
    "toy_connected": True,
    "band_connected": True,
    "running": False,
    "level_mode": True,
    "telescopic": 0,      # 档位 0-6
    "vibration": 0,       # 档位 0-9
    "percent": False,     # True 表示当前是百分比模式
    "t_remain": 0.0,
    "hr": 74,
    "hr_base": 72,
    "hr_hist": [72, 73, 74],
    "cmd_count": 0,
}

LOCK = threading.Lock()

MEANING = {"1": "强度偏高", "2": "强度过高", "3": "已结束", "4": "太强了",
           "5": "想要更多", "6": "想休息", "7": "继续", "8": "需要降低",
           "9": "立即停止"}


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def tick() -> None:
    """每秒更新一次：倒计时 + 心率漂移。

    心率逻辑：强度越高爬得越快，停手就慢慢落回来。
    刻意做得"不太听话" —— 加点噪声，这样 AI 不能靠公式硬算。
    """
    while True:
        time.sleep(1.0)
        with LOCK:
            # 倒计时
            if S["running"] and S["t_remain"] > 0:
                S["t_remain"] -= 1
                if S["t_remain"] <= 0:
                    S["running"] = False
                    S["t_remain"] = 0

            # 心率：按当前强度算目标
            if S["percent"]:
                hi = (S["telescopic"] + S["vibration"]) / 2 / 100 * 3
            else:
                hi = S["telescopic"] * 0.55 + S["vibration"] * 0.28

            if not S["running"]:
                target = S["hr_base"] + 4          # 停了就往基线回落
                rate = 0.9
            else:
                target = S["hr_base"] + 10 + hi * 11
                rate = 1.6

            S["hr"] += (target - S["hr"]) * 0.22 * rate
            S["hr"] += random.uniform(-1.6, 1.6)   # 噪声
            S["hr"] = int(clamp(S["hr"], 55, 175))

            S["hr_hist"].append(S["hr"])
            S["hr_hist"] = S["hr_hist"][-40:]


def snapshot() -> dict:
    with LOCK:
        return {
            "toy_connected": S["toy_connected"],
            "band_connected": S["band_connected"],
            "running": S["running"],
            "level_mode": not S["percent"],
            "telescopic": S["telescopic"],
            "vibration": S["vibration"],
            "remain": int(S["t_remain"]),
            "hr": S["hr"],
            "hr_age": 1,
            "hr_history": S["hr_hist"][-20:],
            "sent": S["cmd_count"],
        }


# ============================================================ HTTP
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 静音
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:  # noqa: BLE001
            return {}

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/status"):
            self._json(snapshot())
            return
        self._json({"ok": False}, 404)

    def do_POST(self):  # noqa: N802
        p = self.path
        d = self._body()

        with LOCK:
            S["cmd_count"] += 1

            if p.startswith("/api/level"):
                t = clamp(int(d.get("thrust", 0)), 0, 6)
                v = clamp(int(d.get("vibration", 0)), 0, 9)
                sec = int(d.get("seconds", 20))
                S.update({"percent": False, "telescopic": t, "vibration": v,
                          "t_remain": sec, "running": True})
                note = f"伸缩 {t} 档 / 震动 {v} 档 / {sec}s"

            elif p.startswith("/api/percent"):
                tp = clamp(int(d.get("thrust", 0)), 0, 99)
                vp = clamp(int(d.get("vibration", 0)), 0, 99)
                sec = int(d.get("seconds", 20))
                S.update({"percent": True, "telescopic": tp, "vibration": vp,
                          "t_remain": sec, "running": True})
                note = f"伸缩 {tp}% / 震动 {vp}% / {sec}s"

            elif p.startswith("/api/mode"):
                name = str(d.get("name", "?"))
                sec = int(d.get("seconds", 45))
                # 模式给一组固定的档位（实际设备就是这样）
                S.update({"percent": False, "telescopic": 3, "vibration": 6,
                          "t_remain": sec, "running": True})
                note = f"模式「{name}」→ 3 / 6 档 / {sec}s"

            elif p.startswith("/api/stop"):
                S.update({"running": False, "telescopic": 0, "vibration": 0,
                          "t_remain": 0})
                note = "全停"

            else:
                self._json({"ok": False}, 404)
                return

        print(f"    [面板] {note}")
        sys.stdout.flush()
        self._json({"ok": True})


def main() -> None:
    print()
    print("=" * 62)
    print("  模拟控制台（假设备）")
    print("=" * 62)
    print(f"  监听 http://127.0.0.1:{PORT}")
    print("  玩具=已连  手环=已连")
    print()
    print("  心率会跟着指令变 —— 强度越高爬得越快，停手就回落。")
    print("  真接设备时请用 toy_panel.py，别用这个。")
    print("=" * 62)
    print()

    threading.Thread(target=tick, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  已停止")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
