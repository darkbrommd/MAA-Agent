<div align="center">
  <img src="assets/app_icon.png" width="128" alt="MAA Agent 图标">
  <h1>MAA Agent</h1>
  <p>监控 MAA、分析异常，并把每日摘要送到手机的 Windows 桌面助手</p>

  [![Release](https://img.shields.io/github/v/release/darkbrommd/MAA-Agent?display_name=tag&style=flat-square)](https://github.com/darkbrommd/MAA-Agent/releases/latest)
  [![Windows](https://img.shields.io/badge/Windows-10%20%7C%2011-0078D4?style=flat-square&logo=windows)](https://github.com/darkbrommd/MAA-Agent/releases/latest)
  [![Tests](https://img.shields.io/badge/tests-passing-2ea44f?style=flat-square)](#开发与测试)

  [下载最新版](https://github.com/darkbrommd/MAA-Agent/releases/latest) · [MAA 官网](https://maa.plus/) · [MAA 安装说明](https://docs.maa.plus/zh-cn/manual/install.html) · [English](README_EN.md)
</div>

---

MAA Agent 是一个监控 MAA、分析异常，并把每日摘要送到手机的 Windows 桌面助手。其主要功能为监测 MAA 有没有按计划运行。它把任务状态、资源趋势、官网公告和最近日志集中到一个窗口。开启 DeepSeek 后，日报会在出现值得关注的问题或资源压力时，自动生成一段包含结论、资源趋势、运行质量和建议的简短分析；配置 Bark 后，这份日报会推送到手机。

默认管理 **一个 MAA**，大多数用户跟着首次向导选择 MAA 文件夹即可。如果你同时运行两个 MAA，也可以在向导中选择双实例。

> MAA Agent 是独立的配套工具，不属于 MAA 官方。开始前，请先从 [MAA 官网](https://maa.plus/) 下载 MAA，并按 [安装说明](https://docs.maa.plus/zh-cn/manual/install.html) 完成配置。

## 它能做什么

| | 功能 | 说明 |
|---|---|---|
| 🗓️ | 看日程 | 集中查看接下来要运行的任务，以及运行中、完成或失败的状态。 |
| 🔔 | 发提醒 | MAA 没有运行、任务没有按时开始或电脑资源不足时，通过 Bark 提醒。 |
| 📊 | 看资源 | 查看 CPU、内存、磁盘以及 MAA/MuMu 的资源历史。 |
| 📝 | 自动日报与 AI 分析 | 每天汇总计划任务、资源趋势和待处理异常。开启 DeepSeek 后，会在出现值得关注的问题或资源压力时自动生成分析，给出结论和建议；配置 Bark 后，日报会把这些内容推送到手机。 |
| 📰 | 看公告 | 增量读取明日方舟官网公告，并识别可能影响计划的维护时间。 |
| 🧰 | 管理运行 | 从桌面窗口或托盘启动、停止和查看 Agent，也支持一个或两个 MAA。 |

## 四步开始使用

1. **先准备好 MAA。** [安装 MAA](https://docs.maa.plus/zh-cn/manual/install.html)，完成基础配置，并手动运行一次确认正常。
2. **下载 MAA Agent。** 从 [Releases](https://github.com/darkbrommd/MAA-Agent/releases/latest) 下载最新版 ZIP，完整解压到固定文件夹，例如 `D:\MAA-Agent`。
3. **双击 `MAA-Agent-GUI.bat`。** 第一次运行会自动创建 Python 环境并安装依赖，然后打开配置向导。
4. **跟着向导完成设置。** 选择一个或两个 MAA、指定 MAA 文件夹；想收手机提醒就填写 Bark Key，不需要可以跳过。

配置完成后会进入主界面。以后仍然双击 `MAA-Agent-GUI.bat`，也可以在设置中创建快捷方式和开机启动。

### 安装没有自动完成？

自动安装需要 Windows 10/11 和 Python 3.11 或更高版本。如果电脑没有 Python，请先从 [Python 官网](https://www.python.org/downloads/windows/) 安装，并勾选 **Add Python to PATH**，然后重新双击 `MAA-Agent-GUI.bat`。

仍想手动配置时，可按下面的兼容流程操作：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-gui.txt
Copy-Item config.example.json config.json
```

编辑 `config.json` 后再次打开 `MAA-Agent-GUI.bat`。现有用户的配置会原样保留，不会重新进入向导。

## 日常使用

- 主界面显示 MAA/MuMu 状态、未来计划、最近事件和资源趋势。
- “日程时间轴”可以为某一次运行设置跳过或改时；它只改变提醒预期，不会修改 MAA 自己的定时器。
- “设置”可调整开机启动、深色模式、实例别名、程序路径和 Bark 行为。
- “通知测试”可以确认 Bark 与各项提醒是否正常，不会关闭或修改 MAA。
- 关闭窗口后可以留在系统托盘，后台 Agent 会继续运行。

更完整的操作说明见 [`docs/GUI_AND_OPERATIONS.md`](docs/GUI_AND_OPERATIONS.md)，迁移说明见 [`docs/README_DEPLOY.md`](docs/README_DEPLOY.md)。

## 项目结构

| 路径 | 作用 |
|---|---|
| `agent.py` | 后台检查、MAA 日志读取、提醒和日报。 |
| `maa_agent_gui.py` | 桌面主界面。 |
| `first_run_setup.py` | 首次配置向导。 |
| `tools/windows/` | 首次安装、启停、测试等 Windows 辅助入口。 |
| `schedule_utils.py` | 读取 MAA 定时计划和单次调整。 |
| `resource_history.py` | 资源采样与历史。 |
| `news_monitor.py` | 官网公告读取与维护时间识别。 |
| `config.example.json` | 默认单实例配置模板；手动配置时复制为 `config.json`。 |
| `tests/` | 自动回归测试。 |

本机的 `config.json`、密钥、日志、运行状态、备份和虚拟环境不会进入 Git 或 Release 包。

## 可选功能

- **第二个 MAA：** 首次向导中选择“两个”，或之后在完整配置中添加 MAA2。
- **Bark：** iPhone 手机提醒，可在首次向导跳过，之后再到设置中补充。
- **DeepSeek 日报分析：** 默认关闭。开启后，日报默认只在出现未解决异常，或内存、CPU 长时间偏高时，才请它补写一段分析，每天最多一次。
- **外部 heartbeat：** 用于覆盖整机断电或完全断网等本机无法发送提醒的情况，默认不配置。

## 开发与测试

当前稳定版本：**v1.1.1**，版本号以根目录 [`VERSION`](VERSION) 为准。

```powershell
.\.venv\Scripts\python.exe -m compileall -q .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

贡献代码请先阅读 [`CONTRIBUTING.md`](CONTRIBUTING.md)；AI 助手和维护者另见 [`AGENTS.md`](AGENTS.md)，安全问题见 [`SECURITY.md`](SECURITY.md)。发布包由 `scripts\build_release.ps1` 生成。

## 作者与致谢

作者：[Taylor Yu](https://github.com/darkbrommd)

感谢应用图标原作者 **kataokasan**（Pixiv ID：**84325201**）。
