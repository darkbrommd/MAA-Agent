@echo off
chcp 65001 >nul
setlocal
for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI"
cd /d "%PROJECT_ROOT%"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" app\schedule_control.py
) else (
  python app\schedule_control.py
)
if errorlevel 1 pause
