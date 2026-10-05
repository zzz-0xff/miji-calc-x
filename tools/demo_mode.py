"""演示模式 —— 没接设备也能看 AI 跑完整流程。

**单进程跑的**，所以：
  · 不用管道喂 stdin（那种在受限环境里会 EPERM）
  · 所有输出都在同一个窗口，截图方便

做三件事：
  · 后台线程起一个假控制台（假装设备已连上）
  · 内联跑 AI 闭环
  · 按剧本自动喂按键

真接设备时别用这个，用 ai.bat。
"""
from __future__ import annotations

import io
import json
import sys
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:  # noqa: BLE001
    pass

BASE = Path(__file__).resolve().parent.parent
PANEL = "http://127.0.0.1:8090"

# 剧本：(第几秒按, 按什么, 给看的人看的说明)
SCRIPT = [
    (0,   "",  "开始"),
    (26,  "5", "想要更多"),
    (52,  "5", "又按了 5"),
    (78,  "",  "（没出声）"),
    (104, "1", "强度偏高了"),
    (130, "",  "（缓一缓）"),
    (156, "5", "还想要"),
    (182, "3", "结束了"),
]

INTERVAL = 24          # 每轮间隔秒数


def hr_bar(hr: int) -> str:
    """心率画成一条小条，截图好看。"""
    n = max(1, min(30, (hr - 55) // 3))
    return "#" * n


def start_fake_panel() -> None:
    """后台起假控制台。"""
    import subprocess
    subprocess.Popen(
        [sys.executable, "-u",
         str(BASE / "tools" / "fake_panel.py")],
        cwd=str(BASE),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL)


def wait_panel(timeout: float = 15.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(PANEL + "/api/status",
                                        timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            time.sleep(0.4)
    return False


def main() -> None:
    print()
    print("=" * 70)
    print("   演示模式 —— 没接设备，用模拟的跑一遍")
    print("=" * 70)
    print()
    print("   假控制台会装成「玩具已连、手环已连」，")
    print("   心率会跟着 AI 的指令变化 —— 强度越高爬得越快。")
    print()
    print("   真接设备请用 ai.bat。")
    print("=" * 70)
    print()

    # ---- 检查 key ----
    sys.argv = [sys.argv[0]]           # 清掉参数，ai_demo 自己解析
    import ai_demo

    key = ai_demo.get_key(None)
    if not key:
        ai_demo._print_key_guide()
        sys.exit(1)

    # ---- 起假面板 ----
    print("   启动模拟控制台 ...")
    start_fake_panel()
    if not wait_panel():
        print("   [错误] 假控制台没起来")
        sys.exit(1)
    print("   控制台就绪（玩具=已连  手环=已连）")
    print()
    time.sleep(1)

    panel = ai_demo.Panel(PANEL)
    system_prompt = ai_demo.PROMPT_FILE.read_text(encoding="utf-8")

    print("=" * 70)
    print(f"   模型：  {ai_demo.DEFAULT_MODEL}")
    print(f"   接口：  {ai_demo.DEFAULT_BASE}")
    print(f"   每轮 {INTERVAL} 秒。剧本会自动喂按键，你不用动。")
    print("=" * 70)
    print()

    prev: dict = {}
    history: list[dict] = []
    t0 = time.time()
    n = 0

    try:
        for at, key_press, note in SCRIPT:
            # 等到该按的时候
            target = t0 + at
            while time.time() < target:
                time.sleep(0.2)

            n += 1
            el = int(time.time() - t0)

            # ---- 到点就先按（这一轮的状态里能带上按键）----
            if key_press:
                st = panel.status()
                hr = int(st.get("hr") or 0)
                print(f"  [{el:>3}s] >>> 他按了 {key_press}  （{note}）")
            else:
                st = panel.status()
                hr = int(st.get("hr") or 0)
                print(f"  [{el:>3}s] --- 第 {n} 轮 ---")

            # ---- 组装状态 ----
            state = ai_demo.build_state(panel, prev, key_press)

            # ---- 调模型 ----
            history.append({"role": "user", "content": state})
            msgs = [{"role": "system", "content": system_prompt}] + history[-8:]

            try:
                reply = ai_demo.call_llm(msgs, key,
                                         ai_demo.DEFAULT_BASE,
                                         ai_demo.DEFAULT_MODEL)
            except Exception as exc:  # noqa: BLE001
                print(f"         [接口错误] {type(exc).__name__}: {str(exc)[:60]}")
                continue

            history.append({"role": "assistant", "content": reply})

            # ---- 打印 ----
            print(f"         心率 {hr:>3} {hr_bar(hr)}")
            for ln in reply.strip().splitlines():
                print(f"         {ln}")

            # ---- 下发 ----
            n_cmd = len(ai_demo.RE_TOY.findall(reply))
            if n_cmd:
                ai_demo.run_command(panel, reply)
            else:
                print("         （这轮没给指令）")
            print()

    except KeyboardInterrupt:
        print("\n   中断")
        return

    time.sleep(2)
    st = panel.status()
    print("=" * 70)
    print("   演示结束")
    print("=" * 70)
    print(f"   最终心率：{st.get('hr')} bpm")
    print(f"   一共 {n} 轮")


if __name__ == "__main__":
    main()
