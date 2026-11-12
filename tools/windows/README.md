# Windows 辅助工具

日常使用只需要双击项目根目录的 `MAA-Agent.exe`。`MAA-Agent-GUI.bat` 与本目录脚本用于源码运行、维护、测试和兼容排障。

## Agent 管理

| 脚本 | 用途 |
|---|---|
| `start_agent.bat` | 后台启动 Agent。通常可直接在 GUI 中启动。 |
| `stop_agent.bat` | 请求 Agent 正常停止。 |
| `emergency_stop_agent.bat` | GUI 无法使用时，校验进程身份后强制停止。 |
| `schedule_control.bat` | 打开单次计划监督调整工具。 |

## 测试与通知

| 脚本 | 用途 |
|---|---|
| `run_regression_tests.bat` | 运行完整单元测试。 |
| `preview_notifications.bat` | 只预览通知内容，不发送 Bark。 |
| `test_all_notifications.bat` | 发送整套 Bark 测试通知，会连续推送多条消息。 |

## 可选状态开关

`enable_daily_report.bat`、`disable_daily_report_today.bat` 和三个 `*_copilot_notify*.bat` 用于兼容已有的快捷操作。新的日常设置优先从 GUI 完成。

`setup.ps1` 与三个 VBS 文件是启动器内部组件，一般不需要手动运行。
