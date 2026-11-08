# MAA Agent v1.1.0

这次更新让新用户更容易开始使用，并将默认场景调整为一个 MAA。

## 新增

- 首次运行自动创建 Python 环境并安装依赖。
- 首次配置向导：选择一个或两个 MAA、指定安装目录，并可选填写 Bark Key。
- 单实例成为默认配置；只配置一个 MAA 时，菜单、托盘、设置和通知测试不会显示无关的 MAA2 项目。
- 中英文 README 重新设计，加入 MAA 官方安装入口、下载按钮、徽章和四步快速开始。

## 改进

- 已有 `config.json` 的用户继续直接进入主界面，不会触发向导。
- 保留手动创建 `.venv` 和编辑 `config.json` 的部署方式。
- 通知测试页面改用更直接的说明，移除奇怪比喻和内部函数术语。
- Bark 在默认模板中改为可选且默认关闭。

## 安装

1. 先按照 [MAA 官方文档](https://docs.maa.plus/zh-cn/manual/install.html) 安装并配置 MAA。
2. 下载 `MAA-Agent-v1.1.0-Windows-source.zip` 并完整解压。
3. 双击 `MAA-Agent-GUI.bat`，等待自动安装完成，再按向导选择 MAA 文件夹。

Release 包不包含用户配置、密钥、日志、状态、备份或虚拟环境。
