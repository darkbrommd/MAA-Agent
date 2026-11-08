@echo off
chcp 65001 >nul
if not exist "%~dp0state" mkdir "%~dp0state"
echo enabled>"%~dp0state\copilot_notify.once"
echo 已开启：下一次 Copilot 自动战斗完成或失败都会 Bark 通知，通知后自动关闭。
pause
