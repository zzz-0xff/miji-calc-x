@echo off
rem level-test.bat -- open the level/byte mapping test bench
rem All logic (and all Chinese text) lives in the .ps1; keep this file ASCII-only.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\level-test.ps1"
