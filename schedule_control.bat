@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" schedule_control.py
) else (
  python schedule_control.py
)
if errorlevel 1 pause
