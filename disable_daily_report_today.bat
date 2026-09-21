@echo off
setlocal
set "STATE_DIR=%~dp0state"
if not exist "%STATE_DIR%" mkdir "%STATE_DIR%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$d=(Get-Date).ToString('yyyy-MM-dd'); Set-Content -LiteralPath '%STATE_DIR%\daily_report_skip_date.txt' -Value $d -Encoding ASCII"
echo.
echo Daily report disabled for today only.
echo It will automatically become enabled again tomorrow unless you click this BAT again.
echo You do not need to restart MAA Agent.
echo.
pause
