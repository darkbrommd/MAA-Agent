@echo off
cd /d "%~dp0"
start "" /b wscript.exe "%~dp0maa_agent_gui_hidden.vbs"
exit /b 0
