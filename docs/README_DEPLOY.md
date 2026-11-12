# MAA Agent 部署与迁移说明

公开版本的完整从零安装步骤见根目录 [`README.md`](../README.md)，英文版见 [`README_EN.md`](../README_EN.md)。本文件补充升级和迁移时容易出错的边界。

## 从零部署

1. 将 Release ZIP 解压到固定目录。
2. 安装 Python 3.11 或更高版本。
3. 双击 `MAA-Agent.exe`，直接进入首次配置向导；无需安装 Python。
4. 向导默认配置一个 MAA，可选第二个 MAA 和 Bark。完成后进入主界面。

如需源码运行或排障，双击备用的 `MAA-Agent-GUI.bat`；也可自行创建 `.venv`、安装 `requirements/gui.txt`，再将 `config/config.example.json` 复制为私有的 `config/config.json`。

脚本均根据自身目录计算路径，因此安装目录名称不必包含 `MAA-Agent_0920`。

## 从旧版迁移

1. 使用旧版 GUI 或旧版自带的停止脚本正常停止 Agent；新版脚本位于 `tools/windows/stop_agent.bat`。
2. 备份旧目录的 `config.json` 和 `state/`；日志可按需另行保留。
3. 将新版本解压到新目录，不要直接覆盖仍在运行的旧目录。
4. 比较 `config/config.example.json` 与自己的配置，保留本机路径和密钥并补充新增配置；旧版根目录 `config.json` 仍可继续使用。
5. 确认旧 Agent 已停止后，再把需要连续保留的 `state/` 复制到新目录。
6. 在新目录重新建立 `.venv`、安装依赖、运行测试并启动 GUI。

不要让新旧目录中的 Agent 同时运行，也不要让两个安装目录长期共享一个 `state/`。新版会识别其他 `MAA-Agent*` 目录中的 Agent，但非常旧的版本可能不知道新版存在。

## 更新后的验证

```powershell
.\.venv\Scripts\python.exe -m compileall -q .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

`tools/windows/preview_notifications.bat` 只预览通知；`tools/windows/test_all_notifications.bat` 和 `tools/diagnostics/test_bark.py` 会真实发送 Bark；`tools/diagnostics/test_deepseek.py` 会消耗 API。这些真实通道测试不属于普通回归测试。

v1.1.1 起，根目录只保留日常入口 `MAA-Agent-GUI.bat`。如果曾为旧版辅助 BAT 创建快捷方式，请改为 `tools/windows/` 中的新位置；GUI 创建的快捷方式不受影响。

v1.1.2 起，Python 程序源码统一放在 `app/`，真实联网的手动诊断工具放在 `tools/diagnostics/`。日常仍然只需从根目录启动 `MAA-Agent-GUI.bat`。

v1.2.0 起，`MAA-Agent.exe` 是默认入口，同一个 EXE 分别承担管理器和后台 Agent 模式；BAT 与源码保留为兼容和排障入口。

详细操作见 [`GUI_AND_OPERATIONS.md`](GUI_AND_OPERATIONS.md)，完整验收方法见 [`TESTING_GUIDE.md`](TESTING_GUIDE.md)。
