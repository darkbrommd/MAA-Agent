# MAA Agent 应用源码

这里集中放置后台 Agent、桌面 GUI、计划、资源、公告和通知等运行代码。普通用户不需要直接运行本目录中的 Python 文件，请使用项目根目录的 `MAA-Agent-GUI.bat`。

主要入口：

- `maa_agent_gui.py`：桌面管理器；
- `agent.py`：后台监控；
- `stop_agent.py`：经过身份校验的停止入口；
- `schedule_control.py`：单次计划监督工具；
- `notification_drill.py`：通知预览与通道测试。

这些入口会把上一级目录视为项目根目录，因此配置、日志和状态仍保存在根目录的 `config.json`、`logs/` 与 `state/` 中。
