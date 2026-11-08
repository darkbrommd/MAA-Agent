# MAA Agent 测试与验收指南

这份指南用于确认安装、核心判断、GUI 和可选通知通道是否正常。普通回归测试使用临时目录和 mock，不会停止 MAA、修改真实配置或发送 Bark。

## 建议顺序

### 1. 自动回归测试

双击：

```text
tools\windows\run_regression_tests.bat
```

也可以在 PowerShell 中运行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q .
```

正常结果以 `OK` 结束。失败时先看失败用例名称，再定位对应模块；不要通过修改真实 `config.json`、`state/` 或 MAA 配置来让单元测试通过。

## 2. 首次配置与 GUI

在没有 `config.json` 的全新副本中双击 `MAA-Agent-GUI.bat`：

1. 自动安装脚本应能找到或创建 `.venv`；
2. 首次向导默认选择一个 MAA；
3. 选择包含 `MAA.exe` 的文件夹后可以进入主界面；
4. 未配置第二个 MAA 时，菜单、托盘、设置和通知测试不显示 MAA2；
5. 已有 `config.json` 时不会再次打开向导。

测试副本中的配置只用于验收，不要提交。

## 3. Agent 生命周期

优先在 GUI 中验证启动、停止和重启。辅助入口位于：

- `tools/windows/start_agent.bat`
- `tools/windows/stop_agent.bat`
- `tools/windows/emergency_stop_agent.bat`

验收重点：

- 同一安装目录只能运行一个 Agent；
- 编辑器、PowerShell 或日志中出现 `app/agent.py` 字样不会被误判为 Agent；
- 正常停止会清理对应的 PID 与运行记录；
- 强制停止只作用于经过脚本路径和进程身份校验的目标。

## 4. GUI 与日程

建议检查：

- 页面切换、窗口缩放和周期刷新不会让内容整块闪烁；
- 日程时间轴围绕当前时间打开，并能按需加载其他日期；
- 滚轮只滚动指针所在区域；
- 小窗口下设置页仍能到达全部按钮；
- 单次跳过/改时只改变 Agent 的监督预期，不修改 MAA 的长期定时器；
- 浅色和深色模式下状态文字都清晰可读。

## 5. 通知内容与 Bark

只预览通知，不发送手机：

```text
tools\windows\preview_notifications.bat
```

需要真实验证 Bark 时再运行：

```text
tools\windows\test_all_notifications.bat
```

后者会连续发送多条明确标注为测试的通知，但不会制造真实故障。运行前确认 `config.json` 中的 Bark 设置属于当前测试设备。

`tools/diagnostics/test_bark.py` 也会真实发送通知；`tools/diagnostics/test_deepseek.py` 会访问并消耗外部 API。两者都不属于普通回归测试。

## 6. 低风险现场检查

在不影响挂机任务的时间段，可以选择验证：

- 暂时关闭一个 MAA，确认持续时间达到阈值后出现对应提醒；
- 恢复 MAA，确认状态回到正常；
- 使用 GUI 的“通知测试”确认单项提醒模板；
- 修改一个非关键设置，确认运行中的 Agent 在下一轮重新读取配置。

不建议为了测试而吃满内存、填满磁盘、破坏网络或强制制造窗口未响应。对应判断由自动测试覆盖。

## 7. 发布包验收

发布前运行 `scripts\build_release.ps1`，然后确认：

- ZIP 中包含 `MAA-Agent-GUI.bat`、`tools/windows/`、中英文 README、公开 `AGENTS.md` 和 `CONTRIBUTING.md`；
- ZIP 中不包含 `config.json`、`AGENTS.local.md`、`.venv/`、`state/`、`logs/`、`backups/` 或密钥；
- SHA-256 文件与 ZIP 实际摘要一致；
- 解压到一个新的普通目录后，主启动器仍能找到安装脚本和 GUI。
