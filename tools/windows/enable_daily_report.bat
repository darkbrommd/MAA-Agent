@echo off
setlocal
for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI"
set "SKIP_FILE=%PROJECT_ROOT%\state\daily_report_skip_date.txt"
if exist "%SKIP_FILE%" del /f /q "%SKIP_FILE%"
echo.
echo Daily report enabled.
echo If today's report time has passed and today's report was not already sent, the Agent will send it on the next loop.
echo You do not need to restart MAA Agent.
echo.
pause
