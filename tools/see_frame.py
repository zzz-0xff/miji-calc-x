"""看 AI 能不能真的认出手机传来的画面。

不打印图片本身（隐私），只让模型描述它「看到了什么结构」——
这也正是 vision.py 里那条提示词要求的：只依据真正看得见的东西判断。

问的是中性问题：
  · 画面里有没有人手
  · 手在做什么（扶着东西 / 抓床单 / 按键盘 / 抓身体 / 空着）
  · 光够不够、清不清楚
"""
import io
import json
import sys
import urllib.request
from pathlib import Path

# ⚠ 不要强制 UTF-8 —— bat 用的是 chcp 936，强制 utf-8 会让中文乱码。
#   只设 errors，encoding 跟随环境。
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass
sys.path.insert(0, ".")

import vision as V  # noqa: E402

print("=" * 68)
print("  让 AI 看一张手机传来的画面")
print("=" * 68)

age = V.cam_age()
print(f"  画面年龄: {age:.1f} 秒" if age is not None else "  没有画面")
if not V.cam_fresh():
    print("  ✗ 画面过期了 —— 手机上刷新页面重新拍")
    raise SystemExit(1)

blk = V.vision_block()
if not blk:
    print("  ✗ 生成不了图块")
    raise SystemExit(1)
print(f"  图块: 有（base64 {len(blk['image_url']['url']) // 1024} KB）")
print()

cfg = json.loads(io.open(".llm.json", encoding="utf-8").read())
BASE, KEY = cfg["base_url"], cfg["key"]
MODEL = cfg["model"]

ASK = """描述这张画面的**结构**，不要描述任何人体部位：
1. 光线够不够？画面清楚还是模糊？
2. 画面里能看到人手吗？如果有，手的姿态是什么？
3. 画面里有没有一个柱状/筒状的物体？
4. 手和那个物体是什么空间关系（扶着、离开、压在上面）？
5. 除此之外还能认出什么东西（床单、键盘、桌面、墙、毯子）？

每项一两句话，看不清就说看不清。"""

body = {
    "model": MODEL,
    "reasoning_effort": "none",
    "max_tokens": 500,
    "messages": [{"role": "user", "content": [
        {"type": "text", "text": ASK}, blk]}],
}
req = urllib.request.Request(
    BASE.rstrip("/") + "/chat/completions",
    data=json.dumps(body).encode(),
    headers={"Content-Type": "application/json",
             "Authorization": f"Bearer {KEY}"})

try:
    with urllib.request.urlopen(req, timeout=150) as r:
        ans = json.load(r)["choices"][0]["message"]["content"]
except Exception as exc:  # noqa: BLE001
    print(f"  ✗ 调用失败: {type(exc).__name__}: {exc}")
    raise SystemExit(1)

print("  ── AI 看到的 ──")
for ln in ans.strip().splitlines():
    print("   " + ln)
print()
