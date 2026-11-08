@echo off
chcp 65001 >nul
setlocal
for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI"
if not exist "%PROJECT_ROOT%\state" mkdir "%PROJECT_ROOT%\state"
echo enabled>"%PROJECT_ROOT%\state\copilot_notify.always"
echo "已开启：所有 Copilot 自动战斗完成或失败都会 Bark 通知。"
pause
