<div align="center">
  <img src="assets/app_icon.png" width="112" alt="MAA Agent 图标">
  <h1>MAA Agent</h1>
  <p>查看 MAA 计划与运行状态，接收异常提醒、每日日报和可选的 DeepSeek 分析。</p>

  [下载最新版](https://github.com/darkbrommd/MAA-Agent/releases/latest) · [MAA 官网](https://maa.plus/) · [完整说明](docs/README_FULL.md) · [English](docs/README_EN.md)
</div>

> MAA Agent 是独立配套工具，不属于 MAA 官方。使用前请先[安装并配置 MAA](https://docs.maa.plus/zh-cn/manual/install.html)，手动运行一次确认正常。

## 快速开始

1. 从 [Releases](https://github.com/darkbrommd/MAA-Agent/releases/latest) 下载最新版 ZIP，并完整解压到固定文件夹。
2. 打开 `MAA-Agent.exe`。第一次运行时按向导选择 MAA 所在文件夹；多数用户保持默认的单 MAA 即可。
3. 需要手机提醒时配置 Bark；需要自动分析日报时，再填写 DeepSeek API Key 并启用对应选项。

### BAT 备用入口

`MAA-Agent-GUI.bat` 保留为源码运行和排障备用入口，需要 Windows 10/11 与 Python 3.11+。首次打开会自动创建 `.venv` 并安装依赖，以后直接双击即可。

## 常用设置

- **MAA 路径：** 指向包含 `MAA.exe` 的文件夹；默认只配置一个 MAA。
- **Bark：** 可选，用于把异常提醒和日报推送到 iPhone。
- **DeepSeek：** 可选，启用后可对日报中的异常、资源趋势和运行质量自动生成结论与建议。
- **日程改时/跳过：** 只改变 Agent 对本次任务的监督预期，不会修改 MAA 自己的定时器。
- **本机配置：** 保存在 `config/config.json`；修改前可备份同目录文件，密钥不要上传或公开。

## 常见问题

| 情况 | 处理方法 |
|---|---|
| 首次运行找不到 MAA | 确认选择的目录中直接包含 `MAA.exe`，并先单独运行 MAA 一次。 |
| EXE 被 Windows 提示未知来源 | 在确认文件来自本项目 Release 后，选择“更多信息 → 仍要运行”。 |
| BAT 安装失败 | 安装 Python 3.11 或更高版本，并勾选 **Add Python to PATH**，再重新打开 BAT。 |
| 修改 MAA 定时时间后仍显示旧时间 | 在管理器中刷新；Agent 会重新读取 MAA 配置。若只想临时改一次，请使用日程时间轴的“改时”。 |
| 收不到提醒/日报 | 检查 Bark 是否启用及 Key 是否正确，再从“通知测试”验证；DeepSeek 仅负责可选的 AI 分析。 |
| 管理器关闭后 Agent 仍在运行 | 这是正常行为。请从托盘或管理器明确选择“停止 Agent”。 |

完整功能、手动部署和项目结构见[完整中文说明](docs/README_FULL.md)，运行操作见[操作手册](docs/GUI_AND_OPERATIONS.md)。

## 作者与致谢

作者：[Taylor Yu](https://github.com/darkbrommd)<br>
图标原作者：**kataokasan**（Pixiv ID：**84325201**）。
