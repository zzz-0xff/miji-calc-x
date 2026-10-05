@echo off
chcp 936 >nul
rem 和 chcp 配对，保证中文不乱码
set PYTHONIOENCODING=gbk
title AI 演示（模拟设备）
cd /d "%~dp0"
set PYTHONUTF8=
echo.
echo ============================================================
echo    AI 演示模式
echo ============================================================
echo.
echo   没接设备也能看 AI 跑完整流程。
echo.
echo   假控制台会装成「玩具已连、手环已连」，
echo   心率会跟着指令变 —— 强度越高爬得越快。
echo.
echo   剧本会自动喂按键，你不用动，看着就行。
echo   全程大约 3 分半。
echo.
echo ============================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo   [错误] 找不到 python，先装 Python 并勾上 Add to PATH
  echo.
  pause
  exit /b 1
)

python -u tools\demo_mode.py %*

echo.
pause
