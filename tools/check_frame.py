"""看一眼手机传来的画面 —— 尺寸、质量、清晰度够不够。"""
import json
import struct
import sys
from pathlib import Path

# ⚠ 不要强制 UTF-8 —— bat 用的是 chcp 936，强制 utf-8 会让中文乱码。
#   只设 errors，encoding 跟随环境。
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

JPEG_SOI = bytes([0xFF, 0xD8])
JPEG_EOI = bytes([0xFF, 0xD9])

p = Path("captures/latest.jpg")
if not p.exists():
    print("  没有画面文件")
    raise SystemExit(0)

d = p.read_bytes()
ok_head = d[:2] == JPEG_SOI
ok_tail = d[-2:] == JPEG_EOI

print(f"  文件      {p.name}")
print(f"  大小      {len(d)} 字节 ({len(d) / 1024:.1f} KB)")
print(f"  头        {d[:2].hex()}  {'JPEG 正常' if ok_head else '不是 JPEG'}")
print(f"  尾        {d[-2:].hex()}  {'完整' if ok_tail else '截断了'}")

w = h = 0
i = 2
while i < len(d) - 9:
    if d[i] != 0xFF:
        i += 1
        continue
    m = d[i + 1]
    if m in (0xC0, 0xC1, 0xC2):
        h, w = struct.unpack(">HH", d[i + 5:i + 9])
        break
    if m in (0xD8, 0xD9) or 0xD0 <= m <= 0xD7:
        i += 2
        continue
    i += 2 + struct.unpack(">H", d[i + 2:i + 4])[0]

if w:
    dens = len(d) * 8 / (w * h)
    print(f"  分辨率    {w} x {h}")
    print(f"  信息密度  {dens:.2f} bit/像素  （<1.5 说明压得比较狠）")
else:
    print("  分辨率    读不出")

meta = Path("captures/latest.json")
if meta.exists():
    try:
        j = json.loads(meta.read_text(encoding="utf-8"))
        print(f"  拍摄时间  {j.get('at', '?')}")
        print(f"  来源      {j.get('source', '?')}")
    except Exception:  # noqa: BLE001
        pass

print()
tips = []
if w and w < 1000:
    tips.append("宽度偏小 —— 页面上把「宽度」拉到 1280 更清楚")
if len(d) < 60000:
    tips.append("文件偏小 —— 页面上把「画质」拉到 85 以上")
if not ok_tail:
    tips.append("图片没传完 —— 网络抖了一下，下一张就好了")
if tips:
    for t in tips:
        print(f"  提示: {t}")
else:
    print("  画质看起来没问题")
