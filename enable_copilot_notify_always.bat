@echo off
chcp 65001 >nul
if not exist "D:\MAA-Agent_0920\state" mkdir "D:\MAA-Agent\state"
echo enabled>"D:\MAA-Agent_0920\state\copilot_notify.always"
echo "已开启：所有 Copilot 自动战斗完成或失败都会 Bark 通知。"
pause