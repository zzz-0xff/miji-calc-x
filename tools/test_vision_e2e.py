"""端到端验证：手机拍了照 → AI 真的能看见。

流程：
  1. 造一张有明确内容的图（红方块 + 蓝圆），模拟手机上传
  2. 走 vision.vision_block() 拿多模态内容块
  3. 用和 ai_play 完全一样的参数发给模型
  4. 看它能不能说出图里有什么
"""
import os
import io
import json
import re
import sys
import time
import urllib.request

# ⚠ 不要强制 UTF-8 —— bat 用的是 chcp 936，强制 utf-8 会让中文乱码。
#   只设 errors，encoding 跟随环境。
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass
sys.path.insert(0, ".")

import vision as V  # noqa: E402

SHOTS = V.LATEST_JPG.parent
SHOTS.mkdir(exist_ok=True)


def make_png(w, h, fn):
    import struct
    import zlib
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        for x in range(w):
            raw.extend(fn(x, y))

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))


# 造图：左上红色方块，右下绿色三角，中间一行黑色横条
def px(x, y):
    if 40 <= x <= 160 and 30 <= y <= 130:
        return (220, 20, 20)
    if 280 <= x <= 400 and 150 - (x - 280) <= y <= 230:
        return (20, 180, 60)
    if 60 <= x <= 380 and 160 <= y <= 172:
        return (10, 10, 10)
    return (250, 250, 250)


V.LATEST_JPG.write_bytes(make_png(440, 260, px))
V.LATEST_JSON.write_text(json.dumps(
    {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "source": "selftest",
     "bytes": V.LATEST_JPG.stat().st_size}, ensure_ascii=False), encoding="utf-8")

print(f"  造了一张测试图 → {V.LATEST_JPG.name}  "
      f"{V.LATEST_JPG.stat().st_size} 字节")
print(f"  内容：左上红色方块 / 右下绿色三角形 / 中间一条黑色横条")
print(f"  vision.cam_age() = {V.cam_age():.1f}s")
print(f"  vision_note()    = {V.vision_note()[:70]}")

blk = V.vision_block()
print(f"  vision_block()   = {'有图块' if blk else '✗ 没有'}")
if not blk:
    raise SystemExit("  图块没生成，链路断了")

# ---- 真发一次，用和 ai_demo 一样的参数 ----
key = (os.environ.get("LLM_API_KEY")
       or os.environ.get("OPENAI_API_KEY")
       or os.environ.get("DEEPSEEK_API_KEY")
       or "")
if not key:
    sys.exit("  没有 API key —— 设一下 LLM_API_KEY 环境变量")

persona = io.open("prompt.assistant.txt",
                  encoding="utf-8").read()
system_prompt = persona + "\n\n" + V.VISION_PROMPT

state = ("[设备状态] 连接正常，当前档位 伸缩 1 / 震动 1，剩余 12 秒\n"
         "[心率] 当前 78 bpm，基线 65，比基线高 13 点 —— 略有上升\n"
         "[他按下/说] 按了 7\n"
         "[你需要输出] 一句简短的话 + 一条 [[TOY ...]] 指令")

msgs = [{"role": "system", "content": system_prompt},
        {"role": "user", "content": [{"type": "text", "text": state}, blk]}]

body = {"model": "deepseek-chat", "messages": msgs,
        "temperature": 0.95, "frequency_penalty": 0.25,
        "presence_penalty": 0.15, "max_tokens": 300, "stream": False}
req = urllib.request.Request(
    "https://api.deepseek.com/chat/completions",
    data=json.dumps(body).encode(),
    headers={"Content-Type": "application/json",
             "Authorization": f"Bearer {key}"})

print()
print("=" * 66)
print("  真发一轮（带图），看她说不说得对")
print("=" * 66)
try:
    with urllib.request.urlopen(req, timeout=120) as r:
        reply = json.load(r)["choices"][0]["message"]["content"]
    for ln in reply.splitlines():
        print("  " + ln)
    print("-" * 66)
    hit = [k for k in ("红", "绿", "方块", "三角", "黑", "横") if k in reply]
    print(f"  画面命中: {hit if hit else '✗ 一个都没提到 —— 可能没真看到'}")
    print(f"  带指令:   {'是' if '[[' + 'TOY' in reply else '✗ 没有'}")
except Exception as exc:  # noqa: BLE001
    print(f"  调用失败: {exc}")
