@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 找不到本目录的 Python 虚拟环境。
  pause
  exit /b 1
)
".venv\Scripts\python.exe" stop_agent.py --force --timeout 5
pause
