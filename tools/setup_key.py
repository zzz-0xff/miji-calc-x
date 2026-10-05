"""交互式配置 API key —— 生成 .llm.json。

直接跑，跟着提示走就行。不用手动编辑 JSON（那是很多人出错的地方）。
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:  # noqa: BLE001
    pass

BASE = Path(__file__).resolve().parent.parent
CFG = BASE / ".llm.json"

# 常见端点预设
PRESETS = {
    "1": ("DeepSeek（推荐，便宜稳定）", "https://api.deepseek.com", "deepseek-chat",
          "https://platform.deepseek.com/api_keys"),
    "2": ("OpenAI", "https://api.openai.com/v1", "gpt-4o-mini",
          "https://platform.openai.com/api-keys"),
    "3": ("本地模型（Ollama / LM Studio 等）", "http://127.0.0.1:11434/v1",
          "qwen2.5", "（本地不用 key，随便填个非空的）"),
    "4": ("其他（自己填地址和模型名）", "", "", ""),
}


def ask(prompt: str, default: str = "") -> str:
    hint = f" [{default}]" if default else ""
    try:
        v = input(f"  {prompt}{hint}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)
    return v or default


def main() -> None:
    print()
    print("=" * 62)
    print("  配置 API key")
    print("=" * 62)
    print()

    if CFG.exists():
        print(f"  {CFG.name} 已经存在了。")
        try:
            old = json.loads(CFG.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            old = {}
        cur = old.get("model", "?")
        print(f"  现在用的是: {cur}")
        print()
        if ask("要覆盖它吗？(y/N)", "n").lower() not in ("y", "yes"):
            print()
            print("  没改。")
            return
        print()

    print("  你的模型走哪个服务？")
    print()
    for k, (name, base, model, _) in PRESETS.items():
        print(f"    {k}. {name}")
    print()

    pick = ask("选一个 (1-4)", "1")
    preset = PRESETS.get(pick, PRESETS["1"])
    name, base, model, help_url = preset

    if pick == "4":
        base = ask("接口地址（要带 /v1 之类，完整到能接 /chat/completions）")
        model = ask("模型名")
        help_url = ""

    print()
    print("  ── API key ──")
    if help_url:
        print(f"     在哪拿: {help_url}")
    if pick == "3":
        print("     本地模型不用真的 key，随便填个字符就行")
    print()
    key = ask("粘贴你的 key")
    if not key:
        print()
        print("  没填 key，没改。")
        return

    cfg = {
        "_说明": "本地配置，已在 .gitignore 里，不会被提交。",
        "base_url": base,
        "model": model,
        "key": key,
    }
    CFG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                   encoding="utf-8")

    print()
    print("=" * 62)
    print(f"  写好了: {CFG}")
    print("=" * 62)
    print()
    print(f"  服务   {name}")
    print(f"  地址   {base}")
    print(f"  模型   {model}")
    print(f"  key    {key[:6]}…{key[-4:]}  （{len(key)} 位）")
    print()
    print("  现在可以直接跑：")
    print()
    print("      python ai_demo.py")
    print()


if __name__ == "__main__":
    main()
