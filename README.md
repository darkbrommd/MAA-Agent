# MAA Agent

Windows 上的 MAA 双实例守护与桌面管理器。它负责监控 MAA/MuMu、计划执行、资源与日志状态，并通过 Bark 发送事故通知；GUI 提供启停、日程例外、报警演习、资源历史和配置入口。

## 本地运行

1. 创建 Python 虚拟环境并安装 `requirements-gui.txt`。
2. 将 `config.example.json` 复制为 `config.json`，填写本机路径和密钥。
3. 双击 `MAA-Agent-GUI.bat`。

`config.json`、`state/`、`logs/`、虚拟环境和备份目录不会进入 Git；请勿提交 Bark 或 DeepSeek 密钥。

## 验证

```powershell
.\.venv\Scripts\python.exe -m compileall -q .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

详细操作与历史变更见 `docs/`。

## 开发交接

后续 AI/开发者先阅读 `AGENTS.md`，再按 `docs/ROADMAP.md` 及 P0/P1/P2 分级文档继续；不需要重新追溯全部历史对话。每轮行为变更需同步文档、运行测试、提交并推送 GitHub。
