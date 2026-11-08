@echo off
chcp 65001 >nul
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
if errorlevel 1 (
  echo.
  echo 安装没有完成，请根据上面的提示处理后重试。
  pause
  exit /b 1
)
start "" /b wscript.exe "%~dp0maa_agent_gui_hidden.vbs"
exit /b 0
