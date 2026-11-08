@echo off
chcp 65001 >nul
setlocal
for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI"

del "%PROJECT_ROOT%\state\copilot_notify.once" 2>nul
del "%PROJECT_ROOT%\state\copilot_notify.always" 2>nul

echo 已关闭 Copilot 通知。日常挂机 Copilot 将继续静默。
pause
