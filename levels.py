"""档位语义 + 帧构造 —— toy_panel 和 ai_play 共用。

抽出来的原因：这两边**都需要**知道「第 5 档会自带震动」「1/2/3 是幅度」，
各自维护一份迟早会漂移（已经漂过一次：ai_play 里 `t>9` 的百分比换算
和 toy_panel 的钳位规则对不上）。

这里只放**纯数据 + 纯函数**，不碰串口、不碰网络。
"""

from __future__ import annotations

# ------------------------------------------------------------------ 上限
# 逐档实测确认：App 允许写到 9，但设备只认到 6；7/8/9 发出去毫无反应。
MAX_THRUST_LEVEL = 6
MAX_VIB_LEVEL = 9

# ------------------------------------------------------------------ 档位表
# 来源：在「档位实测台」逐档点过并逐条描述，见 capture/档位实测表-终版.md
THRUST_LEVELS: dict[int, str] = {
    0: "关",
    1: "幅度 · 弱",
    2: "幅度 · 中",
    3: "幅度 · 强",
    4: "节奏：伸缩 5 次 + 停顿，循环",
    5: "节奏：伸缩 8 次 + 停顿，循环",
    6: "节奏：伸缩 3 次 + 停顿，循环",
    7: "无反应（设备不支持）",
    8: "无反应（设备不支持）",
    9: "无反应（设备不支持）",
}

VIB_LEVELS: dict[int, str] = {
    0: "关",
    1: "幅度 · 弱",
    2: "幅度 · 中",
    3: "幅度 · 强",
    4: "节奏：3档强度 + 停一下 + 循环",
    5: "节奏：3档强度，时长中等（比 4 短），一直循环",
    6: "节奏：3档短震动反复",
    7: "节奏：一短一长的 3 档震动",
    8: "节奏：像 5 档但更短",
    9: "节奏：像 4 档但更长",
}

# 会「牵连震动」的伸缩档：震动字节是 0，设备自己还是会震。
# 不硬拦，但必须让 AI 知道 —— 她用这一档的时候要想清楚。
THRUST_ENGAGES_VIB = frozenset({5})

# 真正的强度区。4 以上是节奏程序，不是"更强的强度"。
THRUST_AMPLITUDE_LEVELS = (1, 2, 3)
VIB_AMPLITUDE_LEVELS = (1, 2, 3)

# ------------------------------------------------------------------ 内置模式
# 冒充 App 抓到的原始字节。交叉验证：App 源码 如影随形: mode:[4,6,5,0]
MODES_RAW: dict[str, tuple[int, int, int, int]] = {
    "如影随形": (4, 6, 5, 0),
    "疾风炫舞": (8, 9, 9, 0),      # ← 伸缩 8 本机无效（上限 6）
    "幻影随行": (6, 6, 8, 0),
    "水光环绕": (2, 2, 3, 0),
    "神龙摆尾": (3, 9, 3, 0),
}

# 本机修正版：超上限的档位钳到上限，让模式真的起作用。
MODES: dict[str, tuple[int, int, int, int]] = {
    name: (min(p[0], MAX_THRUST_LEVEL), min(p[1], MAX_VIB_LEVEL), p[2], p[3])
    for name, p in MODES_RAW.items()
}

MODE_NAMES = tuple(MODES.keys())

# 常见误写 → 正确名。App 内部旧名是「疾风旋舞」，最容易写错。
MODE_ALIASES = {
    "疾风旋舞": "疾风炫舞",
    "疾风": "疾风炫舞",
    "如影": "如影随形",
    "幻影": "幻影随行",
    "水光": "水光环绕",
    "神龙": "神龙摆尾",
}


# ------------------------------------------------------------------ 帧构造
def aa(cmd: int, payload: list[int]) -> bytes:
    """AA <cmd> <len> <payload…> <sum8>，sum8 = 所有前面字节求和 & 0xFF"""
    body = [0xAA, cmd & 0xFF, len(payload) & 0xFF] + [p & 0xFF for p in payload]
    body.append(sum(body) & 0xFF)
    return bytes(body)


def hexs(b: bytes) -> str:
    return " ".join(f"{x:02X}" for x in b)


def level_frame(thrust: int, vibration: int,
                ch2: int = 0, ch3: int = 0, ch4: int = 0) -> bytes:
    """档位帧 —— App 档位按钮真正发的那条路（cmd 0x06，5 字节载荷）。

        AA 06 05 <伸缩档> <震动档> <吮吸> <旋转> <备用> <sum>

    值是**档位编号 0–9**，不是百分比。
    """
    return aa(0x06, [thrust & 0xFF, vibration & 0xFF,
                     ch2 & 0xFF, ch3 & 0xFF, ch4 & 0xFF])


def pct_frame(thrust: int, vibration: int) -> bytes:
    """百分比帧 —— App 手动滑条走的那条路（cmd 0x08，6 字节载荷）。

        AA 08 06 <伸缩%> <震动%> <0> <0> <0> <0> <sum>

    **量程：0–100% 对应强度 0 到「3 档」**，够不到节奏程序。
    """
    return aa(0x08, [thrust & 0xFF, vibration & 0xFF, 0, 0, 0, 0])


STOP_FRAMES = [aa(0x06, [0, 0, 0, 0, 0]),
               aa(0x08, [0, 0, 0, 0, 0, 0]),
               aa(0x08, [0, 0, 1]),
               aa(0x08, [0])]


# ------------------------------------------------------------------ 换算
def pct_to_level(pct: float) -> float:
    """百分比 → 等效档位。**实测确认：100% = 3 档**（不是 ÷10）。"""
    return max(0.0, min(100.0, float(pct))) / 100.0 * 3.0


def level_to_pct(lv: float) -> int:
    """档位 → 百分比（0–99）。"""
    return max(0, min(99, int(round(max(0.0, min(3.0, float(lv))) / 3.0 * 100))))


def describe(thrust: int, vibration: int) -> str:
    """一句话描述这一组档位在干什么 —— 日志/回显用。"""
    t = THRUST_LEVELS.get(thrust, "?")
    v = VIB_LEVELS.get(vibration, "?")
    warn = "　⚠ 伸缩这一档自带震动" if thrust in THRUST_ENGAGES_VIB else ""
    return f"伸缩 {thrust}({t}) / 震动 {vibration}({v}){warn}"
