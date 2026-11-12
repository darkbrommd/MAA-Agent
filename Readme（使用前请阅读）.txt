MAA Agent 使用说明

MAA Agent 是 MAA 的独立配套工具，可以查看计划和运行状态、发送异常提醒与每日日报，并提供可选的 DeepSeek 分析。默认连接一个 MAA，大多数用户按首次向导操作即可。

使用前准备

1. 先安装并配置 MAA，手动运行一次，确认 MAA 可以正常工作。
   MAA 官网：https://maa.plus/

2. 完整解压 MAA Agent 发布包。请保留包内的文件夹结构，不要只复制 EXE。

开始使用

1. 双击 MAA-Agent.exe。
2. 第一次运行时，在向导中选择包含 MAA.exe 的文件夹。
3. 完成向导后进入主界面。以后仍然双击 MAA-Agent.exe 即可。

MAA-Agent.exe 已包含运行环境，不需要另外安装 Python。

MAA-Agent-GUI.bat 是源码运行和排障备用入口，需要 Python 3.11 或更高版本。普通使用不需要打开它。

常用设置

MAA 路径：选择包含 MAA.exe 的文件夹。默认只需要配置一个 MAA。

Bark：可选。启用后可以把异常提醒和每日日报推送到 iPhone。

DeepSeek：可选。启用后可以自动分析日报中的异常、资源趋势和运行质量，并生成简短结论与建议。

日程改时或跳过：只调整 Agent 对这一次任务的监测预期，不会修改 MAA 自己的定时器。

本机配置保存在 config\config.json。大多数设置可以直接在程序的“设置”页面修改，保存后 Agent 会自动读取。

常见问题

首次运行找不到 MAA：请确认选择的文件夹中直接包含 MAA.exe，并先单独运行 MAA 一次。

Windows 提示“未知发布者”：这是未签名程序可能出现的系统提示。确认文件来自本项目 GitHub Release 后，可以选择“更多信息”并继续运行。

收不到提醒或日报：先检查 Bark 是否启用、Key 是否正确，再使用程序中的“通知测试”。DeepSeek 只负责可选的日报分析，不影响基础监测。

关闭窗口后 Agent 仍在运行：程序会隐藏到系统托盘继续监测。需要完全停止时，请从托盘或程序菜单选择“退出 Agent 和管理器”。

更多说明

完整中文说明：docs\README_FULL.md
英文说明：docs\README_EN.md
操作手册：docs\GUI_AND_OPERATIONS.md

作者：Taylor Yu
GitHub：https://github.com/darkbrommd

图标原作者：kataokasan
Pixiv ID：84325201
