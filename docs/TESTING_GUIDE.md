# MAA Agent 测试与验收指南

适用版本：2026-09-20 Revision 3（通知演习 / 文案收敛 / 双目录兼容）

当前建议部署目录：`D:\MAA-Agent_0920`

保留回退目录：`D:\MAA-Agent`

本指南的目标不是让你“证明每一行代码都绝对不会出错”，而是用几种不同层级的测试确认：Agent 能启动、监控规则能正常判断、Bark 通知确实能到 iPhone、通知内容是人能直接看懂的、临时计划覆盖可以工作，以及出现问题时可以迅速回退到旧版。

---

## 1. 先理解现在有哪几类测试

| 层级 | 测什么 | 会不会影响真实挂机 | 推荐程度 |
|---|---|---:|---:|
| 回归测试 | Python 规则、计划匹配、异常恢复、代理判断、日报、override 等逻辑 | 否 | 必做 |
| 通知演习 | Bark 通道、标题、正文、通知等级、手机展示 | 否；只会真的给手机发“演习通知” | 必做一次 |
| 真实启动/停止测试 | 新 Agent 的启动、停止、重复启动保护 | 很低 | 必做 |
| 轻度现场测试 | 主动关闭一个 MAA、临时断系统代理等 | 有少量影响 | 可选 |
| 极端现场测试 | 强行吃满内存、制造程序未响应、填满磁盘 | 有风险 | 不建议 |

这里最重要的区别是：`test_all_notifications.bat` 是“消防警铃测试”，它验证警铃本身，不会为了测试“内存报警”真的把 64 GB 内存吃满，也不会为了测试“重启通知”真的执行重启。

真正的异常判定逻辑由 `run_regression_tests.bat` 中的回归测试覆盖；真实系统只建议做几个风险很低的现场测试。

---

## 2. 第一次部署后的推荐验收顺序

### A. 确认只有新版 Agent 会被启动

你现在保留了：

```text
D:\MAA-Agent          ← 原版 / 回退版
D:\MAA-Agent_0920     ← 当前新版
```

这是很好的回退结构，但**不要同时运行两个 Agent**。

新版已经增加了跨目录检查：如果旧版已经在运行，再启动 `_0920`，新版会发现另一个 `MAA-Agent*\agent.py` 并自动退出。

不过旧版代码本身并不知道新版的存在，所以反向场景——新版正在运行时又手动启动旧版——不能依赖旧版自动阻止。因此回退时始终遵循：

```text
先停止当前版本 → 确认它已经退出 → 再启动另一个版本
```

如果怀疑两个都开了，可以在任务管理器“详细信息”中查看 Python，或者在命令提示符执行：

```bat
wmic process where "name='python.exe' or name='pythonw.exe'" get ProcessId,CommandLine
```

目标是只看到一个 `agent.py`。

### B. 跑回归测试

在 `D:\MAA-Agent_0920` 中双击：

```text
run_regression_tests.bat
```

正常结果应当以：

```text
OK
```

结束，并且所有 test 都是 `ok`。

这些测试使用临时目录和 mock，不会：

- 给你真的发 Bark；
- 调用 DeepSeek；
- 关 MAA；
- 修改 `D:\MAA\MAA1` / `MAA2` 配置；
- 覆盖生产 `state`；
- 改真实 schedule override。

如果这一步失败，不建议继续做真实挂机测试。先看失败的 test 名称，它一般能直接定位是哪一类逻辑坏了。

### C. 先预览全部通知，不发手机

双击：

```text
preview_notifications.bat
```

它会打印 24 种通知，但不会发送。

这是最适合检查“标题是不是人话”“有没有内部术语”“正文是不是太长”的方式。

### D. 做一次 Bark 全通知演习

双击：

```text
test_all_notifications.bat
```

它会连续发送 24 条带有明显前缀的测试通知：

```text
🧪 [通知演习 1/24] ...
```

每一条正文最前面也会写：

```text
这是测试通知，不代表机器真的发生了对应故障。
```

正常情况下，命令行最后应显示：

```text
DRILL PASSED
[PASS] All notification drill messages were accepted by Bark.
```

演习**不会**写生产 incident、不会写 alert cooldown、不会污染每日报警统计，也不会在 Bark 发送失败时往正式 Bark retry queue 塞 24 条测试消息。

如果某几条失败，脚本会列出具体 case ID，并以非 0 exit code 退出。

### E. 真正启动新版 Agent

使用你已经修改好的 `_0920` 版 start BAT。

启动时 Bark 应该类似：

```text
MAA Agent 已启动

时间：2026-09-20 20:xx:xx
初始状态：
MAA：MAA1运行 / MAA2运行
MuMu：已运行
内存：xx%（可用 xx.x GB）
系统代理：已检测到系统代理
```

如果启动时 MAA 还没拉起来，显示：

```text
MAA1未检测到 / MAA2未检测到
```

也是正常的——这只是“启动瞬间的事实快照”，不是故障通知。

**正常启动通知里不应该再出现：**

```text
启动后 10 分钟不会报警
宽限时间
DeepSeek 只用于……
达到阈值后……
TaskChainStart / TaskChainError
PID / UUID
```

这些属于内部策略或排障细节，应留在代码、日志和测试文档中，而不是日常手机通知里。

### F. 真正停止新版 Agent

用你修改好的 stop BAT。

预期 Bark：

```text
MAA Agent 已停止
时间：...
这是你手动请求停止后的正常退出。
```

然后确认 `D:\MAA-Agent_0920\state\agent.pid` 已被清理，相关 `agent.py` Python 进程也不存在。

---

## 3. 通知演习具体覆盖什么

`notification_drill.py` 当前有 24 个 case：

| Case ID | 测试通知 |
|---|---|
| `agent_started` | Agent 正常启动 |
| `duplicate_start` | 重复启动被拒绝 |
| `agent_stopped` | 手动停止 |
| `start_bat_missing` | MAA 启动脚本不存在 |
| `start_bat_failed` | MAA 启动脚本执行失败 |
| `maa_instance_missing` | 能明确识别某个 MAA 掉线 |
| `maa_count_missing` | 只能确认少了一个 MAA、不能可靠点名 |
| `hung_window` | MAA / MuMu 长时间未响应 |
| `disk_low` | 磁盘空间过低 |
| `memory_pressure` | 持续内存压力 |
| `login_conflict` | 游戏账号疑似顶号 / 登录失效 |
| `game_update` | 明日方舟更新 / 维护阻断 |
| `maa_internal` | MAA 初始化 / 内部异常 |
| `crash` | MAA / 游戏疑似崩溃 |
| `adb_connection` | MuMu / ADB 持续异常 |
| `game_network` | 游戏 / 网络持续异常 |
| `task_failed` | 任务持续失败 |
| `schedule_missed` | 计划任务疑似没跑 |
| `proxy_failure` | 系统代理 / 外网出口异常 |
| `copilot_success` | Copilot 自动战斗完成 |
| `copilot_failure` | Copilot 自动战斗失败 |
| `agent_error` | Agent 自身异常 |
| `reboot` | 准备重启电脑的通知；演习不会执行 shutdown |
| `daily_report` | 日报样例 |

也可以在命令提示符中单独测试某一种：

```bat
cd /d D:\MAA-Agent_0920
.venv\Scripts\python.exe notification_drill.py --only proxy_failure
```

多个 case 用逗号分隔：

```bat
.venv\Scripts\python.exe notification_drill.py --only agent_started,maa_instance_missing,proxy_failure
```

只看列表：

```bat
.venv\Scripts\python.exe notification_drill.py --list
```

只预览不发送：

```bat
.venv\Scripts\python.exe notification_drill.py --dry-run
```

另外有一个非默认的 Bark 长消息分段测试：

```bat
.venv\Scripts\python.exe notification_drill.py --only daily_report --split-test
```

它专门验证长正文能不能按 Bark payload 大小自动拆分。正常日常演习不需要每次都跑它，因为它会额外产生多条手机通知。

---

## 4. 几个值得做的真实现场测试

### 4.1 MAA 进程掉线测试

这是最值得做的真实异常测试之一，因为风险较低，而且正是这个 Agent 的核心用途。

前提：此时 MAA 没在跑重要的一次性任务。

操作：让新版 Agent 正常运行，然后手动关闭 MAA1，仅保留 MAA2。

当前内部配置是进程缺失需要持续一段时间才会认定异常；默认约 3 分钟。这里的等待时间属于**测试参数**，所以在测试文档里写出来没有问题，但它不会再出现在 Bark 正文里。

预期 Bark：

```text
MAA1 已退出
发生了什么：当前没有检测到 MAA1 的 MAA 进程。
...
```

然后重新打开 MAA1。恢复本身默认不会再单独 Bark 一次，避免“坏一次 + 好一次”形成双倍通知；恢复会写入日报的自动恢复统计。

### 4.2 重复启动测试

新版已经运行时，再点一次 `_0920` start BAT。

预期：第二个实例自行退出，并 Bark：

```text
MAA Agent 未重复启动
```

如果旧版 `D:\MAA-Agent\agent.py` 正在运行，再启动新版，新版也应该拒绝启动，并在通知中给出已经运行的脚本路径。

### 4.3 Schedule override 测试

双击：

```text
schedule_control.bat
```

选择未来的一次计划，做一次：

```text
跳过这一次
```

然后重新打开 GUI，应看到该次计划仍然显示为“本次跳过”。对应状态写在：

```text
D:\MAA-Agent_0920\state\schedule_overrides.json
```

之后点“撤销一次性例外”，确认恢复正常。

还可以测试 move，例如把：

```text
MAA1 22:00
```

临时移动到：

```text
23:30
```

注意：这个 override **只改变 Agent 对本次任务的监督预期，不会修改 MAA 自己真正的 timer**。所以它适合告诉 Agent“今晚这次我有意不跑 / 我按特殊时间处理”，长期执行时间仍然应该在 MAA 自己的“设置 → 定时执行”中修改。

### 4.4 日报跳过按钮测试

双击：

```text
disable_daily_report_today.bat
```

检查：

```text
D:\MAA-Agent_0920\state\daily_report_skip_date.txt
```

应当写入当天日期。

再双击：

```text
enable_daily_report.bat
```

当天 skip 文件应被清除。

为了测试开关本身，没有必要真的一直等到 23:50；回归测试已经验证了“到点后当天仅发一次”“当天 skip 不发”“重启后不重复发”的逻辑。

### 4.5 系统代理测试

这个测试只适合你的 Windows 确实使用系统 HTTP/HTTPS 代理时做。

如果是纯 TUN 模式，Agent 默认不会把 Google 直连失败当做代理故障，因此可能根本不启用这个探针，这是设计行为。

如果当前确实使用系统代理，可以在不影响其他重要任务的时段暂时停掉代理客户端或让该本地代理端口不可用。内部默认需要持续失败约 10 分钟才会通知，以过滤节点切换或几秒钟抖动。

预期 Bark：

```text
备用机代理/外网出口异常
发生了什么：系统代理已经无法正常访问外网。
...
```

恢复代理后，incident 会自动结案并进入日报的恢复统计。

---

## 5. 不建议为了“真实测试”去制造的故障

### 内存

不要为了测试 RAM 警报故意开脚本吃掉 60 GB 内存。通知演习已经验证手机展示，回归测试验证状态机。真实把内存吃满会同时影响 MuMu、页面文件、Windows 和 MAA，本身可能制造与测试无关的新故障。

### 磁盘

不要为了触发磁盘报警往 C/D 盘塞几十 GB 临时文件。磁盘不足是最容易制造真实数据损坏或更新失败的测试之一，收益很低。

### 程序未响应

不要为了测试 hung detector 强行 suspend MuMu/MAA。`IsHungAppWindow` 依赖 GUI 消息泵状态，人工 suspend 与真实卡死并不完全等价，而且可能损害当前任务。

### Windows 重启

通知演习中的“准备重启电脑”只是通知样例，**不会调用 `shutdown`**。没有必要为验证 Bark 而让挂机机真的重启。

---

## 6. 如何判断这套 Agent 已经可以正式挂着

建议至少满足以下结果后再认为验收完成：

| 项目 | 合格标准 |
|---|---|
| 回归测试 | 全部 PASS |
| 通知演习 | 24/24 被 Bark 接受；iPhone 能收到 |
| 启动通知 | 只显示“已启动 + 初始状态”，没有内部阈值解释 |
| 停止通知 | stop BAT 后正常退出并收到停止通知 |
| 重复启动 | 第二个新版实例被阻止 |
| MAA 实例识别 | 正常情况下能看到 MAA1 / MAA2，而不是只能统计数量 |
| Schedule GUI | skip / move / clear 能保存并重新读取 |
| 日报开关 | disable / enable 当天即时生效 |
| 运行 24–48 小时 | 没有低价值 Bark 轰炸，没有明显漏掉真实问题 |

最后这一项最重要。监控程序的真实质量不是“能报多少错误”，而是运行一两天后，你看到一条 Bark 时仍然愿意认真看它。

---

## 7. 回退流程

如果 `_0920` 版出现你无法接受的问题：

```text
1. 用 D:\MAA-Agent_0920 对应的 stop BAT 停止新版。
2. 确认新版 agent.py Python 进程已经退出。
3. 不需要删除 D:\MAA-Agent_0920，也不要把新版 state 倒灌给旧版。
4. 用原 D:\MAA-Agent 的 start BAT 启动旧版。
```

两套目录各自保留自己的：

```text
state\
logs\
disabled_alerts\
```

因此回退不需要迁移状态。

Revision 3 已把 Copilot 的 `once/always` 状态文件改成相对于当前 Agent 目录解析，所以 `_0920` 不会再误读 `D:\MAA-Agent\state` 中旧版的 Copilot 状态。

---

## 8. 关于“哪些东西应该出现在手机通知里”

这版采用的原则是：**判定规则留在程序内部，当前事实和行动建议给人看。**

例如内部确实仍然存在：

```text
开机保护时间
进程缺失持续时间
TaskChainStart / TaskChainError
pending stall 时间
内存压力持续时间
计划任务 grace
Bark cooldown
```

这些东西是 Agent 做判断所必需的，但正常 Bark 不应该向你解释“我为什么在第 10 分钟而不是第 9 分钟报警”。

手机上更合理的是：

```text
MAA1 已退出
当前没有检测到 MAA1。
可能是退出、崩溃或没有启动。
如果不是你主动关的，值得远程看一眼。
```

而不是：

```text
MAA1 已连续 3 分钟不存在，因此超过 sustain threshold……
```

同样，日报可以保留 P95、峰值、最低可用内存、高负载累计时间等真正能帮助你判断机器长期状态的数据；但不再把 callback 名、UUID、PID、fallback 分支、宽限策略等内部实现细节当作主要信息。

这也是本轮专门重新清理通知文案的原因。
