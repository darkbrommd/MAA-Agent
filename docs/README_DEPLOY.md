# MAA Agent 部署与迁移说明

公开版本的完整从零安装步骤见根目录 [`README.md`](../README.md)，英文版见 [`README_EN.md`](../README_EN.md)。本文件补充升级和迁移时容易出错的边界。

## 从零部署

1. 将 Release ZIP 解压到固定目录。
2. 用 Python 3.11 或更高版本创建项目内 `.venv`。
3. 安装 `requirements-gui.txt`。
4. 将 `config.example.json` 复制为本机私有的 `config.json`，填写 MAA、日志、Bark 和可选 DeepSeek 配置。
5. 运行回归测试，然后双击 `MAA-Agent-GUI.bat`。

脚本均根据自身目录计算路径，因此安装目录名称不必包含 `MAA-Agent_0920`。

## 从旧版迁移

1. 使用旧版 GUI 或 `stop_agent.bat` 正常停止 Agent。
2. 备份旧目录的 `config.json` 和 `state/`；日志可按需另行保留。
3. 将新版本解压到新目录，不要直接覆盖仍在运行的旧目录。
4. 比较 `config.example.json` 与自己的 `config.json`，保留本机路径和密钥并补充新增配置。
5. 确认旧 Agent 已停止后，再把需要连续保留的 `state/` 复制到新目录。
6. 在新目录重新建立 `.venv`、安装依赖、运行测试并启动 GUI。

不要让新旧目录中的 Agent 同时运行，也不要让两个安装目录长期共享一个 `state/`。新版会识别其他 `MAA-Agent*` 目录中的 Agent，但非常旧的版本可能不知道新版存在。

## 更新后的验证

```powershell
.\.venv\Scripts\python.exe -m compileall -q .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

`preview_notifications.bat` 只预览通知；`test_all_notifications.bat` 会真实连续发送 Bark；`test_deepseek.py` 会消耗 API。后两项不属于普通回归测试。

详细操作见 [`GUI_AND_OPERATIONS.md`](GUI_AND_OPERATIONS.md)，完整验收方法见 [`TESTING_GUIDE.md`](TESTING_GUIDE.md)。
