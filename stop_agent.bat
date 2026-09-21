@echo off
cd /d "%~dp0"
start "" /b wscript.exe "%~dp0stop_agent_hidden.vbs"
exit /b 0
