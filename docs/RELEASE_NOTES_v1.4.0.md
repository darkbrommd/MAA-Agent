# MAA Agent v1.4.0

本版本完善设置页，并加入 GitHub Actions 自动测试与发布流程。

## 设置与资源报警

- “Bark 失败重试”扩展为完整的 Bark 通知设置，可修改服务地址、API Key 和重试方式；
- AI 设置支持 Base URL、模型名称和 API Key，未启用时相关控件显示为灰色；
- 内存报警可直接调整普通/严重阈值、持续时间和最低可用内存；
- 新增资源白名单窗口，支持程序图标、名称、路径、逐项启用和旧名单迁移；
- 资源白名单改用与资源详情一致的卡片列表，并补齐深色模式下禁用输入框的配色；
- 白名单程序作为预期重负载处理，但严重低内存仍会报警；
- 打包版“关于”页面改为读取 EXE 内嵌版本，避免旧 EXE 被外部版本文件显示成新版。
- 修复打包版后台 Agent 在管理器退出后丢失临时 CA 证书路径、导致 Bark HTTPS 发送失败的问题。

## CI/CD

- Push 和 Pull Request 自动在 Windows + Python 3.11/3.14 上运行测试；
- `main` 自动构建 Windows Release Artifact；
- `Release` 工作流可手动完成测试、构建、SHA-256、来源证明和 GitHub Release 发布；
- Dependabot 每周检查 Python 依赖和 GitHub Actions 更新。
