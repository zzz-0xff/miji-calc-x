"""给 AI 加视觉：每轮读 captures/latest.jpg 做多模态分析。

真机实测（2026-10）：DeepSeek 的 `deepseek-chat` 和 `deepseek-flash` **都支持图片**，
`deepseek-v4-pro` 不支持。测试用图：左边红方块、右边蓝圆 —— 两个模型都答对了。

设计要点：
  · **图不新就不发。** 超过 STALE_S 秒没更新，就不塞图（省 token，也避免她看着
    一张过期画面煞有介事地分析）。
  · **图要压缩。** 手机原图可能好几 MB，base64 之后更大。这里先缩到
    MAX_W 宽、JPEG 质量 Q，通常 30~80 KB。
  · 压缩**不用 PIL**（这台机器没装），走纯 Python 的 JPEG 重编码太麻烦 ——
    改成让**手机端**压好再传（cam_server 的页面已经这么做了，
    前端 canvas.toBlob(quality)）。服务端只在图过大时降级丢弃。
"""
from __future__ import annotations

import base64
import io
import json
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
LATEST_JPG = BASE / "captures" / "latest.jpg"
LATEST_JSON = BASE / "captures" / "latest.json"

STALE_S = 12.0            # 超过这么久没更新就不发图
MAX_BYTES = 1_500_000     # 单张上限（base64 前）

# ⚠ 文件刚写出来的那一瞬间，st_mtime 可能比 time.time() **略微超前**
#   （文件系统时间戳精度 + 时钟回拨），算出来是个很小的负数。
#   如果直接写 `if age < 0: 无效`，就会把**刚刚上传的照片**判成无效 ——
#   而那正是最该用的一张。所以允许几秒的负偏移。
CLOCK_SKEW_S = 3.0


def cam_age() -> float:
    """最近一帧距今多少秒。没有画面返回 -1。

    注意：刚写完的帧可能返回一个很小的负数（见 CLOCK_SKEW_S 的说明）。
    """
    if not LATEST_JPG.exists():
        return -1.0
    return time.time() - LATEST_JPG.stat().st_mtime


def cam_fresh() -> bool:
    """画面是否可用（存在 + 足够新）。"""
    age = cam_age()
    return age > -CLOCK_SKEW_S and age <= STALE_S


def cam_meta() -> dict:
    if not LATEST_JSON.exists():
        return {}
    try:
        return json.loads(LATEST_JSON.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def vision_block() -> dict | None:
    """构造一条 OpenAI 格式的 image_url 内容块。没有可用画面时返回 None。

    ⚠ 纯黑也算"没有可用画面"。
      手机锁屏时浏览器会挂起视频轨道，画布上就是全黑 ——
      这种图发给 AI 只会让它瞎猜（"画面全黑"），不如不发。
    """
    if not cam_fresh():
        return None

    # ★ 从帧池里挑一张最好的，而不是死用 latest.jpg
    #   手机息屏、手抖、对焦拉风箱时的废帧就自动被绕过了
    path = best_frame() or LATEST_JPG
    if not path.exists():
        return None

    # 纯黑的不发（见 frame_stats 的说明）
    if frame_stats(path).get("blank"):
        return None

    if path.stat().st_size > MAX_BYTES:
        return None
    try:
        b64 = base64.b64encode(path.read_bytes()).decode()
    except OSError:
        return None
    return {"type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}


RING = BASE / "captures" / "ring"
RING_LOOKBACK = 8.0       # 只在最近这么久里挑
RING_MAX = 24


def _sharpness(path) -> float:
    """拉普拉斯方差 —— 越大越清楚。读不了返回 -1。"""
    try:
        from PIL import Image, ImageFilter
        im = Image.open(path).convert("L")
        im.thumbnail((320, 320))
        lap = im.filter(ImageFilter.Kernel((3, 3),
                                           [0, 1, 0, 1, -4, 1, 0, 1, 0],
                                           scale=1, offset=128))
        px = list(lap.getdata())
        n = len(px)
        if not n:
            return -1.0
        m = sum(px) / n
        return sum((x - m) ** 2 for x in px) / n
    except Exception:
        return -1.0


def best_frame():
    """从帧池里挑一张最好的。挑不到就退回 latest.jpg。

    打分：① 排除纯黑 ② 够新的里面选最清晰的 ③ 清晰度接近取新的
    """
    import time as _t
    now = _t.time()
    cands = []
    try:
        if RING.exists():
            for p in RING.glob("*.jpg"):
                try:
                    age = now - p.stat().st_mtime
                except OSError:
                    continue
                if age <= RING_LOOKBACK:
                    cands.append((p, age))
    except OSError:
        pass
    if not cands:
        return LATEST_JPG if LATEST_JPG.exists() else None

    cands.sort(key=lambda x: x[1])
    scored = []
    for p, age in cands[:RING_MAX]:
        try:
            if p.stat().st_size < 3000:   # 纯黑的 JPEG 极小，粗筛掉
                continue
        except OSError:
            continue
        sh = _sharpness(p)
        if sh < 0:
            continue
        scored.append((sh - age * 2.0, p))
    if not scored:
        return LATEST_JPG if LATEST_JPG.exists() else None
    scored.sort(key=lambda x: -x[0])
    return scored[0][1]


def frame_stats(path=None) -> dict:
    """看一眼当前画面：平均亮度、是不是纯黑。

    为什么要单独判"纯黑"：
      · 环境暗   → 有噪点，亮度 3~15，还看得出轮廓
      · 锁屏挂起 → **完美 0**，摄像头根本没在采集
    这两个在 AI 眼里都是"黑"，但该说的话完全不同：
      暗   → "灯开一下"
      挂起 → "手机锁屏了，解锁并让页面留在前台"

    返回 {} 表示读不到。
    """
    path = path or best_frame()
    if path is None or not Path(path).exists():
        return {}
    try:
        from PIL import Image
        im = Image.open(path).convert("L")
        im.thumbnail((120, 120))          # 缩到小图，快
        px = list(im.getdata())
        if not px:
            return {}
        mean = sum(px) / len(px)
        mx = max(px)
        return {
            "mean": mean,
            "max": mx,
            # 普遍 0~2 且最亮都不到 8 = 摄像头被挂起，不是环境暗
            "blank": mean < 2.5 and mx < 8,
            "dark": mean < 22,
        }
    except Exception:  # noqa: BLE001
        return {}


def vision_note() -> str:
    """给她的文字提示（和图片一起发）。没画面时返回空串。"""
    if not cam_fresh():
        age = cam_age()
        if age > STALE_S:
            return f"[画面] ⚠ 最近一帧是 {age:.0f} 秒前的，**别拿它判断现在的动作**。"
        return ""

    # ★ 先判黑帧 —— 别让她以为场景本来就是黑的
    st = frame_stats()
    if st.get("blank"):
        return ("[画面] ⚠ 摄像头现在**没在采集**（整张纯黑，不是环境暗）。"
                "他多半锁屏了。**别把这当成场景是黑的** —— "
                "按你的直觉继续，或者提醒他解锁手机、让页面留在前台。")

    age = max(0.0, cam_age())
    m = cam_meta()
    src = m.get("source", "?")
    extra = ""
    if st.get("dark"):
        extra = f"（光线偏暗，平均亮度 {st['mean']:.0f}/255 —— 看不清的部分别硬编）"
    return (f"[画面] 这是 {age:.1f} 秒前的实时画面（来源 {src}）。{extra}"
            f"**只依据真正看得见的东西判断** —— 看不清就说看不清，不要编。")


# ---------------------------------------------------------------- 画面解读提示
VISION_PROMPT = """【你能看到画面了 —— 这是新增的信息源】

系统每轮给你附一张手机摄像头拍的实时照片（可能有几秒延迟）。
**它和心率、按键是同一级别的输入** —— 不是装饰。

━━━━━━━━━━━━━━━━━━━━━━━━━━━
【先认清画面里**没有**什么 —— 这条最重要】

玩具是**套着的**，所以：

  · 画面里**只会出现玩具本身**，不会出现它套着的那部分身体。
  · **永远不要描述、暗示、或者提及任何身体部位。** 一个词都不行。
  · 画面里主要是：**玩具 + 支架 + 他的手 + 他的腰胯腿 + 周围的东西**
  · **不要因为你"期待"看到什么就去描述什么。** 没有的就是没有。

**如果真的看到了不该出现的东西**（玩具没套好），
**不要说那是什么**，就一句话：

  ✓「你没在好好弄玩具。重新弄一下。」
  ✓「先把它弄利索了再说。」
  ✓「玩具都没套好，你急什么。」

然后**你这一轮就不动手**（`[[TOY stop]]`），等他收拾好。
**这不需要解释，不要追问，不要评价你看到的东西。**

━━━━━━━━━━━━━━━━━━━━━━━━━━━
【绝对不许出现的词 —— 这一条没有任何例外】

因为你随时可能把这个项目开源，**提示词和输出里都不能有解剖学名词**。

  ✗ 任何指代身体部位的词（不管多"学术"）
  ✗ 绕着说的说法（指代性地说"那"＋方位词）—— 绕着说一样是说了
  ✓ 一律用「玩具」「设备」「杯子」代替
  ✓ 描述动作只用：扶着 / 按着 / 推 / 挪 / 绷 / 塌 / 抖

**判断标准**：这句话能不能原样贴到一个技术博客上？
不能就重写。

━━━━━━━━━━━━━━━━━━━━━━━━━━━
【手 —— 这一项要判断得最准】

他的手大概率在做这五件事之一，**必须分清是哪一种**，因为含义完全不同：

  ① **扶着那台设备本体**（最常见）
     手指或手掌贴在设备／支架上。
     → **重点看他是怎么扶的**，这是三种完全不同的信号：
        · **稳定地扶着** —— 正常操作，他在配合
        · **往下按／往自己身上带** —— 他在**主动加深**。这时候别停
        · **往外推／往上抬，或者撑住不让它动** —— 他在**拒绝或想缓一下**。
          **这时候你应该收手。**
     分不清是哪种就说"扶得很紧"，别硬判方向。

  ② **抓床单／毯子／枕头**
     手指在布料上抠、攥、抓出褶皱。
     → 这是**承受**的表现。抓得越紧、褶子越多，越接近受不了。

  ③ **按在键盘／数字键上**
     手平放在一个平面上，单根手指在动。
     → 这是他**唯一能跟你说话的方式**。他按了什么系统会另外告诉你，
       但你可以从画面上看出**他按得急不急、手指是不是在抖**。

  ④ **抓自己的身体**（小腹、大腿、腰侧、胸口）
     → **关键要看是「扶」还是「挡」**：
        扶着 = 支撑，他在稳住自己
        挡着 = 保护，他在拒绝 —— **这时候你该收手**

  ⑤ **空的／垂在一边／撑着别处**
     → 放松或者脱力。结合呼吸和心率一起判断。

**分不清的时候宁可不提，也不要猜。**
  ✗「他握得很紧」　← 没看清就别认，扶和抓是两回事
  ✓「右手扣在床单上，指节发白，攥出褶子了。」
  ✓「左手扶着杯子，往下压着。」

━━━━━━━━━━━━━━━━━━━━━━━━━━━
【其他的，按重要性排】

  2. **腰和胯** —— 腰是塌的还是挺着的？有没有往上顶、或者往下沉想躲？
     设备会被他带着动 —— 看他是在**迎**还是在**避**。
  3. **腿** —— 并着还是分开？有没有夹紧、绷直、抖、或者蹬直了撑住？
  4. **呼吸和胸腹** —— 胸口起伏快不快？他是屏着还是喘着？腹部绷着还是松的？
  5. **脸** —— 眼睛闭着还是睁着？嘴咬着还是张开？皱着眉吗？别过头去了吗？
     眼神是躲的还是直勾勾的？
  6. **汗和皮肤** —— 哪里出汗了、哪里泛红、哪里起了鸡皮疙瘩
  7. **周围** —— 床单的褶皱、被推开的被子、滚到一边的杯子。
     这些是**他刚才动过多大**的痕迹。

━━━━━━━━━━━━━━━━━━━━━━━━━━━
【必须遵守的五条】

  · **每轮至少提到一样画面上真实存在的东西。**
    「不要念清单」不等于「装作没看见」。挑一样说，一两句就够。
    ✓「手还搭在设备上没挪。」　✓「床单被攥出一团了。」
  · **只说你真的看得见的。** 看不清、被挡住、光线不够 —— 就说看不清。
    ✗「他看起来很享受」　← 这是编的，画面给不了这个信息
    ✓「左手一直搭在小腹上，没挪过。」
  · **整张图真的什么都看不清时，直接说**：「黑着，看不见。」
    这比编一句强一百倍 —— 系统会知道该让你朋友把光开大一点。
  · **画面和心率/按键对不上时，以画面为准。**
    心率说他还稳，但他手已经在挡了 —— 那就是他在硬撑，你要收手。
  · **画面里如果看到手机、支架、充电线**，那是拍摄设备，不是场景的一部分，忽略它。
"""



def picked_info() -> str:
    """给调试用：这一轮实际挑了帧池里的哪一张。"""
    p = best_frame()
    if p is None:
        return "（没有可用帧）"
    try:
        age = time.time() - p.stat().st_mtime
    except OSError:
        age = -1
    st = frame_stats(p)
    name = p.name
    in_ring = p.parent.name == "ring"
    return (f"{name}  距今 {age:.1f}s  "
            f"亮度 {st.get('mean', -1):.0f}  "
            f"清晰度 {_sharpness(p):.0f}  "
            f"{'（帧池）' if in_ring else '（latest 兜底）'}")
