@echo off
chcp 936 >nul
rem 和 chcp 配对，保证中文不乱码
set PYTHONIOENCODING=gbk
title Miji Calc-X 安装
cd /d "%~dp0"
set PYTHONUTF8=
echo.
echo ============================================================
echo    Miji Calc-X  安装
echo ============================================================
echo.
echo    这个 bat 干什么：
echo.
echo      1. 检查你电脑上有没有 Python（要 3.10 以上）
echo      2. 检查缺哪些依赖库，列出来
echo      3. **问过你之后**才装，不会偷偷下东西
echo      4. 装完复查一遍
echo.
echo    用的是 pip --user，不需要管理员权限。
echo.
echo ============================================================
echo.

rem ---- 先看有没有 python ----
where python >nul 2>nul
if errorlevel 1 goto :nopython

python -u tools\setup_env.py
goto :done

:nopython
echo   !! 找不到 Python
echo.
echo   ────────────────────────────────────────────────
echo   要先去装 Python：
echo.
echo       https://www.python.org/downloads/
echo.
echo   选 3.11 或更高版本，下载 Windows 安装包。
echo.
echo   ? 装的时候一定要勾上「Add Python to PATH」
echo      不勾的话命令行里找不到 python，这项目跑不起来。
echo.
echo   装完之后重新双击本文件。
echo   ────────────────────────────────────────────────
echo.
pause
exit /b 1

:done
echo.
pause
