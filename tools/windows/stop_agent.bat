@echo off
setlocal
for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI"
cd /d "%PROJECT_ROOT%"
start "" /b wscript.exe "%~dp0stop_agent_hidden.vbs"
exit /b 0
