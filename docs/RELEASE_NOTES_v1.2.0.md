# MAA Agent v1.2.0

## 主要变化

- 新增免安装 Python 的 `MAA-Agent.exe`，双击即可进入首次配置或管理界面。
- 同一个 EXE 同时承担管理器与后台 Agent 模式，任务栏和任务管理器不再显示为 Python 图标。
- 保留 `MAA-Agent-GUI.bat` 与完整源码，作为兼容、开发和排障入口。
- 整理根目录：完整文档、本机配置、依赖清单和版本元数据分别归入对应文件夹。
- 新版配置默认位于 `config/config.json`，继续兼容旧版根目录配置。

## 使用

1. 下载 `MAA-Agent-v1.2.0-Windows.zip` 并完整解压。
2. 确认已经安装并配置好 MAA。
3. 双击 `MAA-Agent.exe`，按首次向导选择包含 `MAA.exe` 的文件夹。

Windows 可能对未签名的新程序显示来源提示；确认文件来自本项目 Release 后，可选择“更多信息 → 仍要运行”。
