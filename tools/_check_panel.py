import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def check_panel() -> bool:
    """控制台（8090）在跑吗。给 ai.bat 用，退出码 0=在跑。"""
    try:
        with urllib.request.urlopen("http://127.0.0.1:8090/api/status",
                                    timeout=3) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def check_key() -> bool:
    """配好 API key 了吗。退出码 0=配好了。"""
    import os
    cfg = Path(__file__).resolve().parent.parent / ".llm.json"
    key = ""
    if cfg.exists():
        try:
            key = str(json.loads(cfg.read_text(encoding="utf-8")).get("key") or "")
        except Exception:  # noqa: BLE001
            key = ""
    if not key:
        for e in ("LLM_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY"):
            if os.environ.get(e):
                key = os.environ[e]
                break
    key = key.strip()
    # 模板里的占位符不算
    return bool(key) and "YOUR" not in key.upper() and "在这里填" not in key


if __name__ == "__main__":
    sys.exit(0 if check_panel() else 1)
