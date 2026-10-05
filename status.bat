@echo off
chcp 65001 >nul
title Miji-X Console - Status
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\status.ps1"
echo.
pause >nul
