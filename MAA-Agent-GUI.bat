@echo off
@rem Source/diagnostic backup launcher. Packaged users should prefer MAA-Agent.exe.
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "POWERSHELL_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%POWERSHELL_EXE%" set "POWERSHELL_EXE=powershell.exe"
"%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\windows\setup.ps1"
if errorlevel 1 (
  echo.
  echo Setup did not complete. Please resolve the message above and try again.
  pause
  exit /b 1
)
start "" /b wscript.exe "%~dp0tools\windows\maa_agent_gui_hidden.vbs"
exit /b 0
