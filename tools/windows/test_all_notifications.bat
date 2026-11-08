@echo off
setlocal
for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI"
pushd "%PROJECT_ROOT%"
title MAA Agent - Bark Notification Drill

echo ============================================================
echo MAA Agent Bark Notification Drill
echo This will send ALL synthetic test notifications to your iPhone.
echo It will NOT stop MAA, break the proxy, fill RAM, or reboot Windows.
echo ============================================================
echo.

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" app\notification_drill.py
) else (
    py -3 app\notification_drill.py
)

set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
    echo [PASS] All notification drill messages were accepted by Bark.
) else (
    echo [FAIL] One or more drill messages failed. See output above.
)
echo.
pause
popd
exit /b %RC%
