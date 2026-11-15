# 手动诊断工具

这里的脚本会访问真实外部服务，不属于普通回归测试：

- `test_bark.py`：发送一条真实 Bark 测试通知；
- `test_ai.py`：使用本机配置调用 OpenAI 兼容的 AI API。

日常功能验证请优先运行 `tools/windows/run_regression_tests.bat`。只有明确需要测试对应账号或通道时，才运行这里的脚本。
