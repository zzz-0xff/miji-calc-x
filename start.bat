@echo off
chcp 65001 >nul
title Miji-X Console - Start
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1"
echo.
pause >nul
