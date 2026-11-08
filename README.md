# MAA Agent

[English](README_EN.md) | 简体中文

MAA Agent 是一套仅运行于 Windows 的双实例 MAA 守护、告警与桌面管理工具。它从 MAA 日志和 Windows 运行状态中判断计划任务是否真正启动、完成或明确失败，在需要人工处理时通过 Bark 通知手机，并在桌面 GUI 中集中展示日程、资源、公告、配置和日志。

> 本项目是独立的辅助管理工具，不属于 MAA 官方项目。使用前请先安装并配置好 MAA；当前版本按 MAA1/MAA2 双实例场景设计。

## 主要功能

- 监控 MAA、MuMu、内存、磁盘、窗口未响应和可选的代理健康状态。
- 解析 MAA callback：可恢复的 `SubTaskError` 默认降噪，明确的任务链终态失败必须告警。
- 读取 MAA 定时器，展示未来计划、运行中/完成/失败状态，并检测计划漏跑。
- 支持单次“跳过/改时”监督例外；它只改变 Agent 的报警预期，不会修改 MAA 自身定时器。
- Bark 事故通知、有限离线队列、冷却合并、日报和可选 DeepSeek 摘要。
- 明日方舟官网公告增量抓取，识别维护时间并为受影响计划添加监督例外。
- Tk 桌面管理器：运行状态、日程时间轴、资源历史、官网公告、报警演习、设置、完整配置和日志。
- Windows 托盘、开机启动、快捷方式和安全的单实例启停。

## 系统结构

```text
MAA / MuMu / MAA 日志
          |
          v
       agent.py --------> Bark / 日报 / 公告监督
          |
          +--> state/ 运行状态与历史（不提交）
          +--> logs/  本地日志（不提交）
          +--> config.json 本机路径与密钥（不提交）
          ^
          |
   maa_agent_gui.py
   状态、时间轴、资源、公告、设置与启停
```

GUI 是控制和观察界面，Agent 是后台监控主体；隐藏或退出 GUI 不等于停止 Agent。

## 要求

- Windows 10/11
- Python 3.11 或更高版本（安装时勾选 “Add Python to PATH”）
- 已安装并配置好的 MAA；MuMu 模拟器为当前默认适配对象
- Bark iOS 客户端及设备 key（若要接收手机通知）
- DeepSeek API key 为可选项，只用于启用对应 AI 功能时

## 安装与部署

1. 从 [Releases](https://github.com/darkbrommd/MAA-Agent/releases) 下载最新的 `MAA-Agent-vX.Y.Z-Windows-source.zip`，解压到固定目录，例如 `D:\MAA-Agent`。不要直接在 ZIP 内运行。
2. 在项目目录打开 PowerShell，创建虚拟环境并安装依赖：

   ```powershell
   py -3.11 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install --upgrade pip
   .\.venv\Scripts\python.exe -m pip install -r requirements-gui.txt
   ```

3. 创建本机配置：

   ```powershell
   Copy-Item config.example.json config.json
   ```

4. 编辑 `config.json`，至少检查以下内容：

   - `bark_key`：Bark 设备 key；不使用 Bark 时将 `enable_bark` 设为 `false`。
   - `maa_instance_dirs`：MAA1/MAA2 安装目录。
   - `maa_gui_config_paths`：两个实例的 `config\gui.new.json`。
   - `maa_log_paths`：两个实例的 `debug\asst.log`。
   - `launch_paths`：可选，供 GUI 一键启动 MAA/MuMu。
   - `deepseek_api_key`：可选；不使用时保持空字符串并将 `use_deepseek` 设为 `false`。

5. 双击 `MAA-Agent-GUI.bat`。首次进入“设置”页后可配置开机启动、托盘行为、实例别名和启动路径。默认配置会让开机启动时直接进入托盘；若未看到窗口，请检查通知区域。
6. 建议首次运行回归测试：

   ```powershell
   .\.venv\Scripts\python.exe -m unittest discover -s tests -v
   ```

配置保存后，运行中的 Agent 通常会在下一轮自动读取，不需要重启。请勿同时运行两个安装目录中的 Agent，也不要混用两边的 `state/`。

## 常用入口

| 文件 | 用途 |
|---|---|
| `MAA-Agent-GUI.bat` | 打开桌面管理器，日常推荐入口。 |
| `start_agent.bat` / `stop_agent.bat` | 后台启动或正常停止 Agent。 |
| `emergency_stop_agent.bat` | 校验进程身份后执行紧急停止。 |
| `run_regression_tests.bat` | 使用项目虚拟环境运行全部测试。 |
| `preview_notifications.bat` | 预览通知模板，不发送 Bark。 |
| `test_all_notifications.bat` | 真实发送整套演习通知；会连续推送。 |
| `schedule_control.bat` | 管理某一次计划的监督例外。 |

## 项目目录

| 路径 | 说明 |
|---|---|
| `agent.py` | 后台主循环、监控、日志解析、告警、日报和公告监督。 |
| `maa_agent_gui.py` | Tk 桌面管理器。 |
| `agent_runtime.py` | 单实例、PID 身份校验与安全停止。 |
| `schedule_utils.py` / `schedule_control.py` | MAA 计划读取与单次监督例外。 |
| `resource_history.py` | 资源采样和历史读取。 |
| `news_monitor.py` | 官网公告增量抓取和维护窗口解析。 |
| `notification_drill.py` | 不制造真实故障的报警演习。 |
| `config.example.json` | 可公开的配置模板。复制为 `config.json` 后填写本机信息。 |
| `assets/` | 应用图标。 |
| `tests/` | 自动回归测试。 |
| `docs/` | 操作、测试、路线图和历史变更文档。 |

`config.json`、`.venv/`、`state/`、`logs/`、`backups/` 和密钥均为本机数据，不进入 Git 或 Release 包。

## 更新与卸载

更新前正常停止 Agent，备份自己的 `config.json` 和 `state/`，将新版本解压到新目录，再复制本机配置与状态。确认新版本正常后再删除旧目录。卸载时先停止 Agent，并在 GUI“设置”页关闭开机启动，然后删除安装目录和你创建的快捷方式。

## 安全说明

- 不要提交或分享 `config.json`；Bark key、DeepSeek key 和真实路径可能位于其中。
- `test_all_notifications.bat` 与 `test_bark.py` 会真实发送 Bark；`test_deepseek.py` 会访问并消耗 DeepSeek API。
- 时间轴中的单次开关只改变监督预期，不会阻止或修改 MAA 的实际计划。
- 本机完全断电、断网或 Windows 死机时，本地 Agent 无法自行通知；需另配外部 heartbeat 才能覆盖。

## 开发

开发者和后续 AI 请先阅读 `AGENTS.md`。常用验证：

```powershell
.\.venv\Scripts\python.exe -m compileall -q .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

发布包由 `scripts\build_release.ps1` 从 Git 跟踪文件生成，并自动排除内部交接文档、开发路线图和所有本机数据。

## 作者与致谢

作者：[Taylor Yu](https://github.com/darkbrommd)

感谢应用图标原作者 **kataokasan**（Pixiv ID：**84325201**）。
