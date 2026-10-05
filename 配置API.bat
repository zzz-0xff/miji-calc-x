@echo off
chcp 936 >nul
rem 和 chcp 配对，保证中文不乱码
set PYTHONIOENCODING=gbk
title 配置 API key
cd /d "%~dp0"
set PYTHONUTF8=

where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo   [错误] 找不到 python，先装 Python 并勾上 Add to PATH
  echo.
  pause
  exit /b 1
)

python -u tools\setup_key.py

pause
