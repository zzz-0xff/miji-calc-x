"""算画面的清晰度（拉普拉斯方差）—— 判断是真模糊还是模型看不准。

拉普拉斯方差是标准的对焦度量：
  < 50    很糊（失焦或严重抖动）
  50~150  偏软
  150~500 正常
  > 500   很锐利

手机拍近距离物体时，如果没切到微距，很容易糊。
"""
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
    from PIL import Image, ImageFilter
except ImportError:
    print("  缺 Pillow")
    raise SystemExit(1)

p = Path("captures/latest.jpg")
if not p.exists():
    print("  没有画面")
    raise SystemExit(1)

im = Image.open(p).convert("L")
w, h = im.size

# 缩小到统一尺寸再算，避免分辨率影响
im2 = im.copy()
im2.thumbnail((640, 640))
lap = im2.filter(ImageFilter.Kernel((3, 3),
                                    [0, 1, 0, 1, -4, 1, 0, 1, 0],
                                    scale=1, offset=128))
px = list(lap.getdata())
n = len(px)
mean = sum(px) / n
var = sum((v - mean) ** 2 for v in px) / n

# 亮度
bpx = list(im2.getdata())
bmean = sum(bpx) / len(bpx)
bmax = max(bpx)

print(f"  分辨率      {w} x {h}")
print(f"  平均亮度    {bmean:.1f} / 255    最亮 {bmax}")
print(f"  清晰度      {var:.0f}   （拉普拉斯方差）")
print()

if var < 50:
    verdict = "很糊 —— 失焦或者镜头脏了"
    tips = [
        "手机离太近了 —— 后退到 50~80cm",
        "点一下屏幕对焦（大部分相机 App 支持）",
        "擦一下镜头（指纹是糊的头号原因）",
        "别在弱光下手持 —— 会拖慢快门导致抖动模糊",
    ]
elif var < 150:
    verdict = "偏软 —— 能看但细节不清"
    tips = [
        "点屏幕对焦一下",
        "光线再加一点",
    ]
elif var < 500:
    verdict = "正常"
    tips = []
else:
    verdict = "很锐利"
    tips = []

print(f"  判定        {verdict}")
for t in tips:
    print(f"    · {t}")
