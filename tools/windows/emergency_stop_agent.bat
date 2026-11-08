@echo off
chcp 65001 >nul
setlocal
for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI"
cd /d "%PROJECT_ROOT%"
if not exist ".venv\Scripts\python.exe" (
  echo 找不到本目录的 Python 虚拟环境。
  pause
  exit /b 1
)
".venv\Scripts\python.exe" app\stop_agent.py --force --timeout 5
pause
