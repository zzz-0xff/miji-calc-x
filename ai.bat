@echo off
chcp 936 >nul
rem 和 chcp 配对，保证中文不乱码
set PYTHONIOENCODING=gbk
title AI 闭环控制
cd /d "%~dp0"
set PYTHONUTF8=

echo.
echo ============================================================
echo    AI 闭环控制
echo ============================================================
echo.

rem ---- 1. python ----
where python >nul 2>nul
if errorlevel 1 (
  echo   [错误] 找不到 python
  echo          先装 Python，装的时候勾上 Add Python to PATH
  echo.
  pause
  exit /b 1
)

rem ---- 2. 控制台 ----
echo   检查控制台 ...
python tools\_check_panel.py >nul 2>nul
if errorlevel 1 (
  echo.
  echo   [提示] 控制台（8090）没在跑，帮你起一个 ...
  start "控制台" cmd /c "python -u toy_panel.py"
  echo          等 5 秒 ...
  ping -n 6 127.0.0.1 >nul
)
echo          OK
echo.

rem ---- 3. key ----
echo   检查 API key ...
python tools\_check_key.py >nul 2>nul
if errorlevel 1 (
  echo.
  echo ============================================================
  echo    还没配置 API key
  echo ============================================================
  echo.
  echo    跑一次配置向导就行：
  echo.
  echo        配置API.bat
  echo.
  echo    或者看 docs\AI_SETUP.md
  echo.
  echo ============================================================
  echo.
  pause
  exit /b 1
)
echo          OK
echo.

rem ---- 4. 起 ----
echo ============================================================
echo    连上了。按 1~9 是他那边的反馈按键，q 退出。
echo ============================================================
echo.

python -u ai_demo.py %*

echo.
echo [已退出]
pause
