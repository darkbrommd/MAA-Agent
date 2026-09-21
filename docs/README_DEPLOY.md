# MAA Agent 2026-09-20 Revision 3 部署说明

当前推荐安装目录：`D:\MAA-Agent_0920`。

你已经保留原 `D:\MAA-Agent` 作为回退版，因此本次不要删除旧目录，也不要把两份 `state` 混在一起。

## 覆盖文件

把本包中的文件复制到 `D:\MAA-Agent_0920`，覆盖同名的 `agent.py`、`config.json`、测试脚本等即可。

不要覆盖/删除你现有的 `.venv`、生产 `state`、`logs`，以及你已经按 `_0920` 路径修改好的 start / stop BAT。

本版已把 Agent 自己拥有的 Copilot once/always 文件改为相对路径，因此会跟随当前安装目录，不再固定读取 `D:\MAA-Agent\state`。

## 部署后建议

先停止正在运行的 Agent，再执行：

```text
run_regression_tests.bat
preview_notifications.bat
test_all_notifications.bat
```

全部正常后，再使用你现有的 `_0920` start BAT 启动 Agent。

完整验收方法见 `TESTING_GUIDE.md`。

## 统一 GUI（当前版本新增）

双击 `MAA-Agent-GUI.bat`。新管理器包含运行状态、启停、资源、计划、时间策略、完整配置、日志和系统托盘。详细说明见 `GUI_AND_OPERATIONS.md`。

`start_agent.bat` 与 `stop_agent.bat` 已改为无等待、无常驻 CMD 的后台入口。它们不再使用硬编码目录；复制目录后会自动跟随脚本所在路径。

## 关于双目录

新版启动时会尝试识别其他 `MAA-Agent*\agent.py`，因此旧版仍在运行时，新版不会再启动第二份。

但原旧版代码并不知道新版的存在，所以不要在新版运行时误点旧版 start BAT。回退时始终先停止当前版本，再启动另一个目录。
