@echo off
setlocal
for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI"
pushd "%PROJECT_ROOT%"
title MAA Agent - Notification Preview
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" app\notification_drill.py --dry-run
) else (
    py -3 app\notification_drill.py --dry-run
)
echo.
pause
popd
