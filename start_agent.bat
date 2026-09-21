@echo off
cd /d "%~dp0"
start "" /b wscript.exe "%~dp0start_agent_hidden.vbs"
exit /b 0
