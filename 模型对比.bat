@echo off
chcp 936 >nul
rem 和 chcp 配对，保证中文不乱码
set PYTHONIOENCODING=gbk
title 模型对比
cd /d "%~dp0"
set PYTHONUTF8=

echo.
echo ============================================================
echo    模型对比
echo ============================================================
echo.
echo   同一份设备状态，分别发给几个模型，看各自怎么回。
echo   比哪几个由 tools\models.json 决定。
echo.
echo ============================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo   [错误] 找不到 python
  pause
  exit /b 1
)

python -u tools\compare_models.py %*

echo.
echo   想多跑几轮：模型对比.bat --rounds 3
echo.
pause
