# MAA Agent v1.0.0

首个整理后的公开发布版本。

## 内容

- Windows 双实例 MAA 守护、日志终态判断和 Bark 事故通知。
- 日程时间轴、漏跑监督、单次跳过/改时例外。
- MAA/MuMu/内存/磁盘/窗口/代理监控和资源历史。
- 明日方舟官网公告增量监控与维护窗口监督。
- 桌面 GUI、系统托盘、开机启动、配置编辑、日志和报警演习。
- 中英文 README、从零部署说明和可重复 Release 打包脚本。
- 设置页加入作者 Taylor Yu 与 GitHub 主页。
- 修复公开部署依赖清单及 Copilot 通知脚本的安装目录硬编码。

## 安装

下载 `MAA-Agent-v1.0.0-Windows-source.zip`，解压后按包内 `README.md` 创建 `.venv`、安装依赖、复制并编辑 `config.example.json`，然后运行 `MAA-Agent-GUI.bat`。

Release 包不包含 `config.json`、密钥、日志、状态、备份或虚拟环境。
