# 参与 MAA Agent 开发

感谢你愿意改进 MAA Agent。小型修复、文档补充、测试和界面改进都很欢迎。

## 准备环境

- Windows 10/11
- Python 3.11 或更高版本
- 已安装并能独立运行的 MAA（涉及运行联调时）

克隆仓库后，可以直接双击 `MAA-Agent-GUI.bat` 创建本地环境，也可以手动执行：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements\gui.txt
```

复制 `config\config.example.json` 为 `config\config.json` 只用于本机运行。请不要提交本机配置、密钥、日志或状态目录。

## 建议流程

1. 从最新的 `main` 创建分支。
2. 先阅读 `AGENTS.md` 中与修改相关的行为约定。
3. 保持改动聚焦，并为行为修复或新功能补充测试。
4. 更新会受影响的 README 或 `docs/` 页面。
5. 提交 Pull Request，说明改动目的、验证方式和用户可见影响。

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe -m compileall -q .
```

普通回归测试不会发送 Bark。以下入口具有外部影响，请仅在确实需要时使用：

- `tools/windows/test_all_notifications.bat` 和 `tools/diagnostics/test_bark.py`：发送真实 Bark 通知；
- `tools/diagnostics/test_deepseek.py`：访问并消耗外部 API；
- `tools/windows/emergency_stop_agent.bat`：停止本目录中经过身份校验的 Agent。

## 提交内容

- 不提交 `config.json`、`.venv/`、`state/`、`logs/`、`backups/` 或真实密钥。
- 示例路径和凭据使用明显占位符。
- 用户界面文字优先使用清楚、日常的表达；技术细节放在日志或开发文档中。
- 如果修改任务终态、提醒、时间轴或进程停止逻辑，请在 Pull Request 中明确列出相应测试。

## 版本与发布

维护者以 `metadata/VERSION` 为版本来源。正式发布还需要对应 Release Notes、Git 标签、GitHub Release、ZIP 和 SHA-256；普通 Pull Request 不需要自行创建版本标签。

如果发现安全问题或意外包含敏感信息，请先联系维护者，不要在公开 Issue 中粘贴密钥、真实配置或完整日志。
