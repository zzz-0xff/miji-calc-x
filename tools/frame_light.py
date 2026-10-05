"""分析画面到底有多黑 —— 判断是「摄像头被挡住」还是「环境太暗」。"""
import sys
from pathlib import Path

# ⚠ 不要强制 UTF-8 —— bat 用的是 chcp 936，强制 utf-8 会让中文乱码。
#   只设 errors，encoding 跟随环境。
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

try:
    from PIL import Image
except ImportError:
    print("  没有 Pillow，装一下：")
    print("    python -m pip install Pillow")
    raise SystemExit(1)

p = Path("captures/latest.jpg")
if not p.exists():
    print("  没有画面文件")
    raise SystemExit(1)

im = Image.open(p).convert("L")
w, h = im.size
px = list(im.getdata())
n = len(px)

mean = sum(px) / n
mx = max(px)
mn = min(px)

# 分块亮度：看是不是「整体黑」还是「局部有内容」
COLS, ROWS = 6, 8
blocks = []
for r in range(ROWS):
    row = []
    for c in range(COLS):
        x0, x1 = c * w // COLS, (c + 1) * w // COLS
        y0, y1 = r * h // ROWS, (r + 1) * h // ROWS
        s = 0
        cnt = 0
        for y in range(y0, y1, 4):
            base = y * w
            for x in range(x0, x1, 4):
                s += px[base + x]
                cnt += 1
        row.append(s / max(1, cnt))
    blocks.append(row)

print(f"  分辨率     {w} x {h}")
print(f"  平均亮度   {mean:.1f} / 255")
print(f"  最亮像素   {mx}")
print(f"  最暗像素   {mn}")
print()

# 直方图
hist = [0] * 8
for v in px:
    hist[min(7, v // 32)] += 1
print("  亮度分布：")
for i, cnt in enumerate(hist):
    lo, hi = i * 32, i * 32 + 31
    bar = "█" * int(cnt / n * 50)
    print(f"    {lo:>3}-{hi:>3}  {cnt/n*100:>5.1f}%  {bar}")
print()

print("  分块亮度图（数字=该区域平均亮度）：")
for row in blocks:
    print("    " + "  ".join(f"{v:>3.0f}" for v in row))
print()

# 判定
top = max(max(r) for r in blocks)
print("  判定：")
if mean < 12 and top < 40:
    print("    ✗ 画面几乎全黑 —— **摄像头被挡住了**")
    print("      → 检查手机是不是扣着放／镜头贴在什么东西上")
elif mean < 45:
    print("    ⚠ 很暗 —— 环境光线不够")
    print("      → 开一盏灯，或者把灯对着拍摄方向")
elif top / max(1, mean) > 6:
    print("    ⚠ 有大片黑区 —— 可能是遮挡，也可能只是背景暗")
else:
    print("    ✓ 亮度正常")
