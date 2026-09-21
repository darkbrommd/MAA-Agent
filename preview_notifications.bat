@echo off
setlocal
pushd "%~dp0"
title MAA Agent - Notification Preview
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" notification_drill.py --dry-run
) else (
    py -3 notification_drill.py --dry-run
)
echo.
pause
popd
