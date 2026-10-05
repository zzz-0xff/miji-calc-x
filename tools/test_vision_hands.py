"""用「扶着设备」的场景测视觉 —— 验证她分得清扶/按/推。

澄清一下：那个"杯子"就是设备本身，手大概率是**扶着它**，
也可能抓身体/床单/敲键盘。所以要验的是**她能不能分清这几种**。
"""
import os
import io
import json
import re
import struct
import sys
import time
import urllib.request
import zlib

# ⚠ 不要强制 UTF-8 —— bat 用的是 chcp 936，强制 utf-8 会让中文乱码。
#   只设 errors，encoding 跟随环境。
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass
sys.path.insert(0, ".")

import vision as V  # noqa: E402


def make_png(w: int, h: int, fn) -> bytes:
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        for x in range(w):
            raw.extend(fn(x, y))

    def ch(t, d):
        return (struct.pack(">I", len(d)) + t + d
                + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF))
    return (b"\x89PNG\r\n\x1a\n"
            + ch(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + ch(b"IDAT", zlib.compress(bytes(raw), 9))
            + ch(b"IEND", b""))


SKIN = (226, 184, 152)
DARK = (38, 40, 48)
SHEET = (206, 202, 196)
DEV = (58, 60, 72)
DEV_HI = (96, 100, 116)

W, H = 480, 300


def scene_hold(x, y):
    """场景：他手扶着设备本体（手掌扣在设备侧面）。"""
    # 床单背景
    if y > 210:
        return SHEET
    # 设备本体（中央偏右，圆柱侧视）
    if 210 <= x <= 320 and 70 <= y <= 250:
        return DEV_HI if x in (210, 320) else DEV
    # 手指扣在设备左侧
    if 170 <= x <= 212 and 120 <= y <= 200:
        return SKIN
    # 小臂往左下延伸
    if 60 <= x <= 172 and 150 <= y <= 186:
        return SKIN
    return DARK


def scene_sheet(x, y):
    """场景：手在抓床单，指节发白。"""
    if y > 190:
        # 床单上有褶皱（几条深色线）
        for fold in (120, 180, 240, 300, 360):
            if abs(x - fold - (y - 190) * 0.25) < 5:
                return (172, 168, 162)
        return SHEET
    if 150 <= x <= 330 and 110 <= y <= 195:
        return SKIN                      # 手背
    if 150 <= x <= 330 and 186 <= y <= 200:
        return (240, 240, 245)           # 指节发白
    if 200 <= x <= 280 and 60 <= y <= 112:
        return DEV                       # 设备在后方
    return DARK


def scene_keys(x, y):
    """场景：手搭在键盘上。"""
    if 100 <= x <= 380 and 160 <= y <= 240:
        return (52, 54, 64)              # 键盘底
    # 键帽
    for r in range(3):
        for c in range(6):
            kx, ky = 115 + c * 45, 172 + r * 24
            if kx <= x <= kx + 36 and ky <= y <= ky + 18:
                return (78, 82, 96)
    if 140 <= x <= 340 and 120 <= y <= 175:
        return SKIN                      # 手掌
    if 250 <= x <= 330 and 60 <= y <= 122:
        return DEV
    return DARK


CASES = [
    ("手扶着设备本体", scene_hold,
     "他右手掌扣在设备侧面，手臂往下延伸。没有抓床单，也没在键盘上。"),
    ("手抓床单（指节发白）", scene_sheet,
     "手背在上方，指节压在床单上，床单有几道褶皱。设备在后方。"),
    ("手搭在键盘上", scene_keys,
     "手掌平放，下面是方格键帽。设备在右上角。"),
]


def ask(scene_fn, label, truth, key, persona):
    V.LATEST_JPG.write_bytes(make_png(W, H, scene_fn))
    V.LATEST_JSON.write_text(json.dumps(
        {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "source": "selftest"},
        ensure_ascii=False), encoding="utf-8")

    state = ("[设备状态] 连接正常，当前档位 伸缩 2 / 震动 1，剩余 15 秒\n"
             "[心率] 当前 84 bpm，基线 66，比基线高 18 点 —— 明显上来了\n"
             "[他按下/说] 按了 7\n"
             "[你需要输出] 一句简短的话 + 一条 [[TOY ...]] 指令")

    msgs = [{"role": "system", "content": persona + "\n\n" + V.VISION_PROMPT},
            {"role": "user",
             "content": [{"type": "text", "text": state}, V.vision_block()]}]
    body = {"model": "deepseek-chat", "messages": msgs, "temperature": 0.95,
            "frequency_penalty": 0.25, "presence_penalty": 0.15,
            "max_tokens": 300, "stream": False}
    req = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        reply = json.load(r)["choices"][0]["message"]["content"]

    print()
    print("=" * 68)
    print(f"  【{label}】真相：{truth}")
    print("=" * 68)
    for ln in reply.splitlines():
        print("  " + ln)
    print("-" * 68)
    return reply


key = (os.environ.get("LLM_API_KEY")
       or os.environ.get("OPENAI_API_KEY")
       or os.environ.get("DEEPSEEK_API_KEY")
       or "")
if not key:
    sys.exit("  没有 API key —— 设一下 LLM_API_KEY 环境变量")
persona = io.open("prompt.assistant.txt",
                  encoding="utf-8").read()

for label, fn, truth in CASES:
    try:
        rep = ask(fn, label, truth, key, persona)
        bad = [k for k in ("那里", "下面", "私密", "那个地方", "某处")
               if k in rep]
        print(f"  乱编隐私部位: {'✗ ' + str(bad) if bad else '✓ 没有'}")
        print(f"  带指令: {'是' if '[[' + 'TOY' in rep else '✗ 没有'}")
    except Exception as exc:  # noqa: BLE001
        print(f"\n  【{label}】调用失败: {exc}")
