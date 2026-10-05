"""ai_demo.py —— AI 闭环控制演示（供应商无关）

架构：读状态（心率 + 设备状态 + 他按键）→ 交给 LLM 决策 → 下发指令 → 循环。

**跑在任何 OpenAI 兼容的接口上** —— ChatGPT / DeepSeek / Ollama / vLLM /
通义 等等都行，只要它支持 /chat/completions 和 image_url 消息格式。

  默认   https://api.deepseek.com                   模型 deepseek-chat
  ChatGPT --base-url https://api.openai.com/v1 --model gpt-4o-mini
  本地   --base-url http://127.0.0.1:11434/v1 --model qwen2.5vl
  通义   --base-url https://dashscope.aliyuncs.com/compatible-mode/v1 --model qwen-vl-max

API key 取用顺序：
  命令行 --key → $LLM_API_KEY → $OPENAI_API_KEY → $DEEPSEEK_API_KEY

用法：
    python ai_demo.py
    python ai_demo.py --base-url https://api.openai.com/v1 --model gpt-4o-mini
    python ai_demo.py --panel http://127.0.0.1:8090 --interval 25

依赖：控制台（toy_panel.py）要先跑起来。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# ⚠ 不要强制 UTF-8。
#   bat 里用的是 chcp 936 + PYTHONIOENCODING=gbk，
#   这里强制 utf-8 会让中文全乱。只设 errors，encoding 跟随环境。
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

BASE = Path(__file__).resolve().parent
PROMPT_FILE = BASE / "prompt.assistant.txt"
def _local_cfg() -> dict:
    """读 .llm.json（本地配置，含 key，已在 .gitignore 里）。

    格式：{"base_url": "...", "model": "...", "key": "sk-..."}
    命令行参数优先于它。
    """
    p = Path(__file__).resolve().parent / ".llm.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


_CFG = _local_cfg()
DEFAULT_BASE = os.environ.get("LLM_BASE_URL") or _CFG.get("base_url") or "https://api.deepseek.com"
DEFAULT_MODEL = os.environ.get("LLM_MODEL") or _CFG.get("model") or "deepseek-chat"
KEY_CANDIDATES = ("LLM_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY")

# ---- 和 ai_play 一样：这些参数是被实测逼出来的，别随手调 ----
# 1.25 / 0.55 / 0.35 那套会逼出词沙拉（高随机 + 强制换词 + 强制换话题）。
TEMPERATURE = 0.95
FREQ_PENALTY = 0.25
PRES_PENALTY = 0.15
MAX_TOKENS = 300


# ---------------------------------------------------------------- 控制台通信
def http_json(url: str, method: str = "GET", body: dict | None = None,
              timeout: float = 8.0) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


class Panel:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")

    def status(self) -> dict:
        try:
            return http_json(f"{self.base}/api/status", timeout=5)
        except Exception:
            return {}

    def level(self, t: int, v: int, sec: int) -> str:
        try:
            r = http_json(f"{self.base}/api/level", "POST",
                          {"thrust": t, "vibration": v, "seconds": sec})
            return f"档位 t={t} v={v} {sec}s" if r.get("ok") else f"失败: {r}"
        except Exception as exc:  # noqa: BLE001
            return f"失败: {exc}"

    def percent(self, tp: int, vp: int, sec: int) -> str:
        try:
            r = http_json(f"{self.base}/api/percent", "POST",
                          {"telescopic": tp, "vibration": vp, "seconds": sec})
            return f"强度 {tp}% / {vp}% {sec}s" if r.get("ok") else f"失败: {r}"
        except Exception as exc:  # noqa: BLE001
            return f"失败: {exc}"

    def mode(self, name: str, sec: int) -> str:
        try:
            r = http_json(f"{self.base}/api/mode", "POST",
                          {"mode": name, "seconds": sec})
            return f"预设「{name}」{sec}s" if r.get("ok") else f"失败: {r}"
        except Exception as exc:  # noqa: BLE001
            return f"失败: {exc}"

    def stop(self) -> str:
        try:
            http_json(f"{self.base}/api/stop", "POST", {})
            return "已停止"
        except Exception as exc:  # noqa: BLE001
            return f"失败: {exc}"


# ---------------------------------------------------------------- LLM
def _print_key_guide() -> None:
    """没配 key 的时候，直接把该去哪配打出来。"""
    print()
    print("=" * 62)
    print("   还没配置 API key")
    print("=" * 62)
    print()
    print("   两种办法，任选一种：")
    print()
    print("   【办法一】跑配置向导（推荐）")
    print()
    print("        双击      配置API.bat")
    print()
    print("      它会问你要用哪个服务、让你把 key 粘进去，")
    print("      然后自动生成 .llm.json。不用手动编辑 JSON。")
    print()
    print("   【办法二】自己填")
    print()
    print("      1. 把 .llm.json.template 复制成 .llm.json")
    print("      2. 填三样东西：base_url / model / key")
    print()
    print("      或者设环境变量也行：")
    print("        set LLM_API_KEY=sk-你的key")
    print()
    print("   详细说明（含常见服务的填法、base_url 的 /v1 坑）：")
    print()
    print("        docs\\AI_SETUP.md")
    print()
    print("=" * 62)
    print()


def get_key(cli_key: str | None = None) -> str:
    """按优先级找 API key。"""
    if cli_key:
        return cli_key
    if _CFG.get("key"):
        return _CFG["key"]
    for name in KEY_CANDIDATES:
        v = os.environ.get(name)
        if v:
            return v.strip()
    # 兼容 DSH 的凭据文件
    # 没有就留空，让调用方报错


def call_llm(messages: list[dict], key: str, base_url: str, model: str) -> str:
    """发一次请求。任何 OpenAI 兼容端点都能用。

    ⚠ 采样参数别随手调：1.25 / 0.55 / 0.35 那套（高温度 + 强制换词 +
      强制换话题）会把模型逼出"词沙拉" —— 语法像中文、语义是空的。
    """
    url = base_url.rstrip("/") + "/chat/completions"
    body = {"model": model, "messages": messages,
            "temperature": TEMPERATURE,
            "frequency_penalty": FREQ_PENALTY,
            "presence_penalty": PRES_PENALTY,
            "max_tokens": MAX_TOKENS, "stream": False}
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"})

    def _send(b: dict):
        r2 = urllib.request.Request(
            url, data=json.dumps(b).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(r2, timeout=120) as resp:
            return json.load(resp)["choices"][0]["message"]["content"]

    try:
        return _send(body).strip()
    except urllib.error.HTTPError as exc:
        if exc.code not in (400, 422):
            raise
        # 有些端点不认 penalty 参数 —— 退回不带它们的版本
        body.pop("frequency_penalty", None)
        body.pop("presence_penalty", None)
        return _send(body).strip()


# ---------------------------------------------------------------- 状态摘要
def build_state(panel: Panel, prev: dict, key_pressed: str) -> str:
    st = panel.status()
    hr = int(st.get("hr") or 0)
    hist = str(st.get("hr_history") or "").split()

    # 基线：本场见过的最低读数（相对他自己，不是绝对数字）
    base = prev.get("hr_base") or hr
    if hr and (not base or hr < base):
        base = hr
    prev["hr_base"] = base

    lines = ["[设备状态]"]
    if st.get("toy_connected"):
        lines.append(f"  连接正常　运行中={st.get('running')}")
        if st.get("level_mode"):
            lines.append(f"  当前档位：伸缩 {st.get('telescopic')} / 震动 {st.get('vibration')}")
        else:
            lines.append(f"  当前强度：伸缩 {st.get('telescopic')}% / 震动 {st.get('vibration')}%")
        lines.append(f"  剩余 {st.get('remain', 0)} 秒")
    else:
        lines.append("  设备未连接")

    lines.append("[心率]")
    if hr and st.get("hr_age", 99) < 15:
        delta = hr - base
        if delta >= 30:
            zone = "接近他惯常的高位"
        elif delta >= 18:
            zone = "明显上来了"
        elif delta >= 8:
            zone = "略有上升"
        else:
            zone = "平稳"
        lines.append(f"  当前 {hr} bpm，基线 {base}，比基线高 {delta} 点 —— {zone}")
        if hist:
            lines.append(f"  最近读数：{' '.join(hist[-12:])}")
    else:
        lines.append("  没有有效读数（看他的按键判断）")

    if key_pressed:
        meaning = {"1": "强度偏高", "2": "强度过高", "3": "已结束",
                   "4": "太强了", "5": "想要更多", "6": "想休息",
                   "7": "继续", "8": "需要降低", "9": "立即停止"}
        lines.append(f"[他的按键] {key_pressed} —— {meaning.get(key_pressed, '未知')}")

    lines.append("[你需要输出] 一句简短的话 + 一条 [[CMD ...]] 指令")
    return "\n".join(lines)


# ---------------------------------------------------------------- 指令解析
RE_TOY = re.compile(r"\[\[\s*CMD\s+([^\]]+)\]\]", re.I)


def run_command(panel: Panel, spec: str) -> str:
    spec = spec.strip()
    if spec.lower().startswith("stop"):
        return panel.stop()

    kv: dict[str, str] = {}
    for part in re.split(r"[\s,]+", spec):
        if "=" in part:
            k, _, v = part.partition("=")
            kv[k.strip().lower()] = v.strip()

    def num(k: str, d: int = 0) -> int:
        try:
            return int(float(kv.get(k, d)))
        except (TypeError, ValueError):
            return d

    sec = max(1, min(600, num("sec", 20)))

    if "mode" in kv:
        return panel.mode(kv["mode"], sec)
    if "tp" in kv or "vp" in kv:
        return panel.percent(max(0, min(99, num("tp"))),
                             max(0, min(99, num("vp"))), sec)
    return panel.level(max(0, min(6, num("t"))),
                       max(0, min(9, num("v"))), sec)


# ---------------------------------------------------------------- 主循环
def main() -> None:
    ap = argparse.ArgumentParser(description="AI 闭环控制演示")
    ap.add_argument("--panel", default=os.environ.get(
        "PANEL_URL", "http://127.0.0.1:8090"))
    ap.add_argument("--interval", type=float, default=25.0,
                    help="每轮间隔秒数（默认 25）")
    ap.add_argument("--base-url", default=DEFAULT_BASE,
                    help=f"OpenAI 兼容的接口地址（默认 {DEFAULT_BASE}）")
    ap.add_argument("--model", default=DEFAULT_MODEL,
                    help=f"模型名（默认 {DEFAULT_MODEL}）")
    ap.add_argument("--key", default=None,
                    help="API key（不给就读环境变量；本地模型随便给个非空值）")
    args = ap.parse_args()

    panel = Panel(args.panel)
    if not panel.status():
        raise SystemExit(f"连不上控制台 {args.panel} —— 先跑 toy_panel.py")

    system_prompt = PROMPT_FILE.read_text(encoding="utf-8")
    key = get_key(args.key)
    if not key:
        _print_key_guide()
        sys.exit(1)

    print("=" * 62)
    print("   AI 闭环控制演示")
    print("=" * 62)
    print(f"   控制台：{args.panel}")
    print(f"   人格：  {PROMPT_FILE.name}（{len(system_prompt)} 字，纯技术）")
    print()
    print("   按 1~9 输入反馈，直接回车跳过这一轮，q 退出")
    print("=" * 62)

    prev: dict = {}
    history: list[dict] = []

    while True:
        try:
            pressed = input("\n他按键（1-9，回车跳过，q 退出）> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if pressed.lower() in ("q", "quit", "exit"):
            break
        if pressed and pressed[0] not in "123456789":
            pressed = ""

        state = build_state(panel, prev, pressed[:1])
        msgs = ([{"role": "system", "content": system_prompt}]
                + history[-12:] + [{"role": "user", "content": state}])

        try:
            reply = call_llm(msgs, key, args.base_url, args.model)
        except Exception as exc:  # noqa: BLE001
            print(f"   [调用失败] {exc}")
            time.sleep(3)
            continue

        history.append({"role": "user", "content": state})
        history.append({"role": "assistant", "content": reply})

        # 台词部分 = 去掉指令行
        said = "\n".join(ln for ln in reply.splitlines()
                         if not RE_TOY.search(ln)).strip()
        print()
        print("─" * 62)
        if said:
            for ln in said.splitlines():
                print(f"   {ln}")
        for m in RE_TOY.finditer(reply):
            spec = m.group(1)
            result = run_command(panel, spec)
            print(f"   〔执行〕[[CMD {spec}]] → {result}")
        print("─" * 62)

        if pressed[:1] == "9":
            print("\n   收到停止信号，已全停。")
            panel.stop()
            break


if __name__ == "__main__":
    main()
