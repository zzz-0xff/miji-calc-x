"""安装助手 —— 检查 Python 和依赖，缺什么就告诉用户、问要不要装。

设计原则：
  · **每一步都要用户确认**，不偷偷装东西
  · 拒绝时要**明确说清楚后果**（玩不了），不是含糊带过
  · 装完自动复查，确认真的能跑
  · 全程不用管理员权限（用 --user 装）

用法：由 安装.bat 调用，也可以直接 python tools\\setup_env.py
"""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:  # noqa: BLE001
    pass

BASE = Path(__file__).resolve().parent.parent

# 需要的最低 Python 版本
MIN_PY = (3, 10)

# (import 名, pip 名, 干什么用的, 是否必需)
DEPS = [
    ("serial", "pyserial", "串口通信（连 ESP32 网关）", True),
    ("PIL", "Pillow", "画面质量分析（摄像头模块）", False),
    ("cryptography", "cryptography", "生成 HTTPS 证书（手机连拍）", False),
]


def rule(ch="=", n=66):
    print(ch * n)


def head(title: str):
    print()
    rule()
    print(f"   {title}")
    rule()


def ask(prompt: str, default: str = "n") -> bool:
    """问一个是非题。默认否。"""
    hint = "[y/N]" if default == "n" else "[Y/n]"
    try:
        a = input(f"   {prompt} {hint} ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if not a:
        return default == "y"
    return a in ("y", "yes", "是", "好", "1")


def have(mod: str) -> str | None:
    """有就返回版本号，没有返回 None。"""
    try:
        m = importlib.import_module(mod)
        return str(getattr(m, "__version__", "已安装"))
    except ImportError:
        return None


# ============================================================ 1. Python
def check_python() -> bool:
    head("第 1 步 / 检查 Python")

    v = sys.version_info
    print(f"   当前 Python：{v.major}.{v.minor}.{v.micro}")
    print(f"   位置：      {sys.executable}")
    print()

    if v >= MIN_PY:
        print(f"   OK 版本够（需要 {MIN_PY[0]}.{MIN_PY[1]} 以上）")
        return True

    print(f"   !! 版本太低 —— 需要 {MIN_PY[0]}.{MIN_PY[1]} 或更高")
    print()
    print("   ────────────────────────────────────────────────")
    print("   要去装吗？")
    print()
    print("   下载地址（官方，选 3.11 或更高）：")
    print("       https://www.python.org/downloads/")
    print()
    print("   ⚠ 装的时候一定要勾上：「Add Python to PATH」")
    print("      不勾的话命令行里找不到 python，项目跑不起来。")
    print("   ────────────────────────────────────────────────")
    print()
    print("   装完重新双击 安装.bat。")
    return False


# ============================================================ 2. 依赖
def check_deps() -> bool:
    head("第 2 步 / 检查依赖库")

    missing_need = []
    missing_opt = []
    ok = []

    for mod, pipname, why, need in DEPS:
        ver = have(mod)
        if ver:
            ok.append((pipname, ver, why))
        elif need:
            missing_need.append((pipname, why))
        else:
            missing_opt.append((pipname, why))

    if ok:
        print("   已装：")
        for name, ver, why in ok:
            print(f"     OK  {name:<14} {ver:<12} {why}")
        print()

    if not missing_need and not missing_opt:
        print("   全部齐了。")
        return True

    if missing_need:
        print("   !! 缺（必需）：")
        for name, why in missing_need:
            print(f"     --  {name:<14} {why}")
        print()

    if missing_opt:
        print("   缺（可选，不装也能用基础功能）：")
        for name, why in missing_opt:
            print(f"     --  {name:<14} {why}")
        print()

    # ---- 必需的发问 ----
    if missing_need:
        print("   ────────────────────────────────────────────────")
        print("   必需的库不装，**控制台和 AI 都跑不起来**：")
        print()
        print("     · 玩具连不上（没有串口通信）")
        print("     · 网页控制台打不开")
        print("     · ai.bat / 演示.bat 会直接退出")
        print()
        print("   要现在装吗？")
        print("   ────────────────────────────────────────────────")
        print()

        if not ask("装（会联网下载，大约几十秒）"):
            print()
            print("   ────────────────────────────────────────────────")
            print("   好，不装。")
            print()
            print("   ⚠ 但这样**这个项目玩不了** —— 必需依赖没装。")
            print()
            print("   什么时候想装了，重新双击 安装.bat 就行。")
            print("   ────────────────────────────────────────────────")
            return False

        print()
        print("   装中 ...")
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install",
             "--user", "-r", str(BASE / "requirements.txt")],
            cwd=str(BASE))
        if r.returncode != 0:
            print()
            print("   !! 装失败了。常见原因：")
            print("      · 网络不通（国内可以加个镜像，见下面）")
            print("      · 权限不够（试试用管理员身份跑）")
            print()
            print("   手动装（国内的镜像，快）：")
            print(f"      {Path(sys.executable).name} -m pip install -r requirements.txt "
                  "-i https://pypi.tuna.tsinghua.edu.cn/simple")
            return False
        print()
        print("   OK 装完了")
        # 清掉导入缓存再查
        importlib.invalidate_caches()

    # ---- 可选的也问一下 ----
    if missing_opt:
        print()
        print("   ────────────────────────────────────────────────")
        print("   可选的那几个要一起装吗？")
        print()
        for name, why in missing_opt:
            print(f"     · {name:<14} {why}")
        print()
        print("   不装的话：")
        print("     · 接不了手机摄像头（画面分析要用）")
        print("     · 手机只能用「拍一张」模式，不能自动连拍")
        print("   ────────────────────────────────────────────────")
        print()

        if ask("装可选的"):
            names = " ".join(n for n, _ in missing_opt)
            print()
            print(f"   装中（{names}）...")
            r = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--user", *names.split()],
                cwd=str(BASE))
            if r.returncode == 0:
                print()
                print("   OK 装完了")
                importlib.invalidate_caches()
            else:
                print()
                print("   !! 没装上。不影响基础功能，先跳过也行。")

    return True


# ============================================================ 3. 复查
def verify() -> bool:
    head("第 3 步 / 复查")

    all_ok = True
    for mod, pipname, why, need in DEPS:
        ver = have(mod)
        if ver:
            print(f"   OK  {pipname:<14} {ver}")
        elif need:
            print(f"   !!  {pipname:<14} 还是没有 —— 这是个必需库")
            all_ok = False
        else:
            print(f"   --  {pipname:<14} 没装（可选，跳过）")

    # 看关键文件在不在（不 import —— 作为脚本运行时 __file__ 路径会飘，
    # 直接 import 会误报"找不到模块"）
    print()
    print("   检查项目文件：")
    need_files = [
        ("levels.py", "档位定义"),
        ("toy_panel.py", "网页控制台"),
        ("ai_demo.py", "AI 闭环"),
        ("tools/fake_panel.py", "演示用假面板"),
    ]
    for rel, what in need_files:
        f = BASE / rel
        if f.exists():
            print(f"     OK  {rel:<24} {what}")
        else:
            print(f"     !!  {rel:<24} 缺了（{what}）")
            all_ok = False

    return all_ok


def main() -> None:
    print()
    rule()
    print("   Miji Calc-X —— 安装")
    rule()

    if not check_python():
        print()
        rule()
        print("   先装 Python，装完再回来双击这个。")
        rule()
        print()
        return

    print()
    print("   ────────────────────────────────────────────────")
    print("   下一步会检查依赖库。")
    print()
    print("   缺的会**先问你**再装，不会偷偷下东西。")
    print("   用的是 pip --user，不需要管理员权限。")
    print("   ────────────────────────────────────────────────")

    if not check_deps():
        print()
        rule()
        print("   没装完。必需依赖不齐的话，项目跑不起来。")
        print("   想装了随时重新双击 安装.bat。")
        rule()
        print()
        return

    ok = verify()

    print()
    rule()
    if ok:
        print("   装好了")
        rule()
        print()
        print("   接下来：")
        print()
        print("     1. 烧 ESP32 固件（看 README 的快速开始）")
        print("     2. 双击  点我-控制台.bat")
        print("     3. 想用 AI 的话，先跑一次  配置API.bat")
        print()
        print("   没接设备想先看效果：双击  演示.bat")
    else:
        print("   有缺的，但基础功能可能还能用")
        rule()
        print()
        print("   跑不起来的模块会提示缺什么。")
    print()


if __name__ == "__main__":
    main()
