@echo off
chcp 65001 >nul
if not exist "%~dp0state" mkdir "%~dp0state"
echo enabled>"%~dp0state\copilot_notify.always"
echo "已开启：所有 Copilot 自动战斗完成或失败都会 Bark 通知。"
pause
