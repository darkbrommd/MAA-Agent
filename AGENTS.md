# MAA Agent 开发指南

MAA Agent 是一个 Windows 桌面工具，用来查看 MAA 的计划任务、资源占用和官网公告，并在需要时通过 Bark 提醒用户。默认管理一个 MAA，也支持可选的第二个实例。

这份文件写给修改代码的贡献者和 AI 助手。先读与当前任务相关的部分即可；更完整的使用说明在 `README.md` 和 `docs/` 中。

如果根目录存在 `AGENTS.local.md`，请在本文件之后阅读它。该文件只保存本机路径、维护习惯和临时排查记录，不进入 Git；项目行为、隐私和发布约定仍以公开文档为基础。

## 开始修改前

1. 运行 `git status --short`，了解并保留工作区中已有的改动。
2. 阅读相关模块和测试；历史日志只在排查问题时查看。
3. 如果用户只要求分析或评审，先给出结论，不修改代码。
4. 将改动控制在任务范围内；行为变化应同时更新测试和相关文档。

## 项目结构

| 路径 | 作用 |
|---|---|
| `agent.py` | 后台监控、MAA 日志解析、提醒、日报和公告监督。 |
| `agent_runtime.py` | Agent 单实例、进程身份校验和安全停止。 |
| `maa_agent_gui.py` | Tk 桌面管理器。 |
| `first_run_setup.py` | 首次配置向导。 |
| `schedule_utils.py` / `schedule_control.py` | MAA 计划读取和单次监督调整。 |
| `resource_history.py` | 资源采样和历史读取。 |
| `news_monitor.py` | 官网公告增量读取与维护时间解析。 |
| `gui_widgets.py` | 可复用 Tk 控件。 |
| `tools/windows/` | Windows 辅助入口；根目录只保留日常启动器。 |
| `tests/` | 自动回归测试。 |
| `docs/` | 部署、操作、测试和历史说明。 |

Agent 是后台监控主体，GUI 是控制和观察界面。隐藏或关闭 GUI 不等于停止 Agent。

## 需要保持的行为

### 任务与提醒

- 明确的任务链终态失败需要提醒。证据不足时按失败处理，避免把真实失败静默掉。
- `SubTaskError` 通常是可恢复的内部重试；先等待所属任务链终态，再决定是否提醒。
- `TaskChainError` 和非收尾任务的 `TaskChainStopped` 属于失败信号。明确失败只有在同一任务链随后报告 `TaskChainCompleted` 时才视为恢复。
- `OfflineConfirm` 只有在 callback 确认进入该节点，或出现明确登录失败并以失败终态结束时，才作为掉线证据。
- 同一批失败可以合并和冷却通知，但各任务的失败状态仍需保留。
- 修改这些规则时，先阅读 `tests/test_revision2.py`，并为新行为补充回归用例。

### 界面与时间轴

- 周期刷新应保留当前页面、滚动位置、分栏比例和已经呈现的数据，避免整块闪烁或重建。
- 时间轴按需加载并设置边界与防重入；滚轮只作用于指针所在的可滚动区域。
- 设置页在小窗口下仍应能滚动到全部选项和按钮。
- 时间轴上的跳过或改时只改变 Agent 的监督预期，不修改 MAA 自己的定时器。
- 官网或配置中的 `24:00` 需要规范化为次日 `00:00`。

### 配置与本地状态

- 默认配置一个 MAA；菜单、托盘、设置和通知测试都要能处理没有 MAA2 的情况。
- 生产配置来自 `config.json`。首次运行向导只在该文件不存在时启动，不覆盖已有配置。
- `state/` 中的 offset、计划状态、资源历史和通知队列需要兼容 Agent 重启；写入状态时优先沿用“临时文件 + replace”的原子方式。
- 不同安装目录的 `config.json`、`state/` 和 `logs/` 不应混用。

## 隐私与安全

- `config.json`、`.venv/`、`state/`、`logs/`、`backups/` 和 `disabled_alerts/` 都是本机数据，不进入版本库或 Release。
- Bark Key、DeepSeek Key、GitHub token、真实日志和用户的本机路径不要写入受跟踪文件、测试样例或提交信息。
- 示例配置使用明显占位符；日志和异常中不要输出密钥值。
- 不改动用户的 VPN、代理、DNS、TUN、防火墙或其他系统网络设置。连接失败时保留现场并报告结果。
- 停止进程前使用 `agent_runtime.py` / `stop_agent.py` 的身份校验，避免根据模糊命令行误伤其他 Python 进程。

## 实现习惯

- 目标环境是 Windows 10/11 和 Python 3.11+。
- Tk 仍是单 UI 线程；网络和耗时任务放到后台线程，再通过 `root.after` 更新界面。
- 优先做边界清楚的小修改。需要重构时，先用测试固定现有行为。
- 保持现有用户配置向后兼容；新增配置应提供安全、可理解的默认值。
- 面向用户的文字尽量直接说明“发生了什么、会做什么”，少使用内部函数名和防御性话术。

## 验证

相关修改先运行对应测试，合并或发布前运行完整测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe -m compileall -q .
```

测试分工：

| 文件 | 主要覆盖 |
|---|---|
| `tests/test_revision2.py` | Agent 生命周期、日志终态、提醒、计划、资源与队列。 |
| `tests/test_v2_features.py` | GUI 格式、公告时间、首次配置和公开发布元数据。 |
| `tests/test_gui_widgets.py` | 自定义 Tk 控件、颜色和滚轮。 |
| `tests/test_news_center.py` | 公告缓存、增量读取、正文清理和维护监督。 |

`tools/windows/test_all_notifications.bat` 和 `test_bark.py` 会真实发送 Bark；`test_deepseek.py` 会访问外部 API。它们只在明确需要验证对应通道时运行，不属于普通回归测试。

## 文档、提交与发布

- 用户操作变化同步更新 `README.md`、`README_EN.md` 或 `docs/` 中对应页面。
- 保持提交聚焦，并在交付说明中列出验证结果。
- 人类贡献流程见 `CONTRIBUTING.md`。
- 根目录 `VERSION` 是稳定版本号的唯一来源，使用 `MAJOR.MINOR.PATCH`。
- 发布版本时同步更新中英文 README、`docs/RELEASE_NOTES_vX.Y.Z.md`、Git 标签和 GitHub Release，并上传 ZIP 与 SHA-256。
- 已发布的标签保持不动；修复已经发布的内容时创建新版本。
