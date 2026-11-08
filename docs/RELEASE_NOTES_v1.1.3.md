# MAA Agent v1.1.3

- 修复 Windows `cmd.exe` 在 LF-only 启动脚本中吞掉下一行开头字符，导致 `powershell.exe` 变成 `wershell.exe` 而无法启动 GUI 的问题。
- 固定 Windows BAT/CMD/VBS 脚本为 CRLF 换行，并增加回归校验。
- GUI 启动器优先使用 Windows 系统 PowerShell 的绝对路径。
