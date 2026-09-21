@echo off
if not exist "D:\MAA-Agent\state" mkdir "D:\MAA-Agent_0920\state"
echo enabled>"D:\MAA-Agent_0920\state\copilot_notify.once"
echo 已开启：下一次 Copilot 自动战斗完成或失败都会 Bark 通知，通知后自动关闭。
pause