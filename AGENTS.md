# MAA Agent 项目交接与 AI 工作约定

> 本文件是本项目的首要交接入口，面向后续接手的 AI/开发者。开始工作前先完整阅读本文件；通常不需要重新通读全部历史对话、PDF、NBX 或所有源码。若本文件与较旧的 `docs/` 文档冲突，以当前代码、测试和本文件为准。

## 1. 项目定位

MAA Agent 是一套仅运行于 Windows 的 MAA 守护、提醒与桌面管理工具。默认管理一个 MAA，也支持可选的双实例模式。

它的核心价值不是“记录所有错误”，而是：

1. 可靠发现某次计划任务是否真正启动、成功结束或明确失败；
2. 在值得远程处理时通过 Bark 通知用户；
3. 对可恢复重试降噪，但绝不能把明确失败误判为恢复；
4. 监控 MAA、MuMu、内存、磁盘、窗口未响应、代理、官网维护公告等；
5. 在 GUI 中提供状态、时间轴、资源历史、公告、配置、日志和启停入口。

当前生产目录：`D:\MAA-Agent_0920`

回退目录：`D:\MAA-Agent`（旧版，不要与新版同时运行，也不要混用两边的 `state/`）

GitHub：`https://github.com/darkbrommd/MAA-Agent.git`

默认分支：`main`

## 2. 不可破坏的核心原则

### 2.1 告警正确性优先级最高

- 某个 MAA 任务链明确失败时必须告警。宁可在证据不足时保守告警，也不能静默吞掉真实失败。
- 但不要把每次内部重试都推送到手机。`SubTaskError` 通常只是可恢复遥测，应等待所属任务链终态。
- `TaskChainError` 和非收尾任务的 `TaskChainStopped` 是终态失败信号；其他任务继续、队列结束或 `AllTasksCompleted` 都不能证明该失败任务成功。
- 只有同一任务链之后明确 `TaskChainCompleted`，才可把此前该链的失败视为恢复。
- `OfflineConfirm` 出现在候选识别列表中不代表真的掉线；只有 callback 确认进入该节点，或出现明确登录失败语句并以失败终态结束，才作为故障证据。
- `CloseDown` 收尾竞态默认降噪。
- Copilot 的可选成功通知开关不能屏蔽 Copilot 的真实失败。
- 同一批次多个失败应保留各自状态，但 Bark 要做合并/冷却，避免一次发送大量重复消息。

任何修改 `agent.py` 的日志解析、pending incident、任务终态或 Bark 逻辑时，都必须先理解并保持上述语义，并补充回归测试。

### 2.2 不要让刷新破坏 GUI 状态

- 资源数据周期刷新不得让“未来计划”或“日程时间轴”整块消失再重建。
- 滚动列表更新应原子呈现，避免从上到下像打印机一样逐行绘制、撕裂或闪烁。
- 时间轴向上加载必须有边界和防重入，不能无限递归加载前一天并卡死。
- 鼠标滚轮只应作用于当前指针所在的可滚动区域，不能被其他页面全局抢占。
- 用户拖动的主页分栏比例需写入 `state/gui_state.json` 并在下次启动恢复。

### 2.3 用户数据和密钥绝不能进入 Git

- `config.json`、`config.json.bak`、`.venv/`、`state/`、`logs/`、`backups/` 已在 `.gitignore` 中。
- Bark key、DeepSeek API key、GitHub token、真实日志、运行状态和本机路径配置不得写入受跟踪文件、提交消息、测试样例或提示文本。
- 示例配置只能使用明显占位符。
- Git 凭据由 Windows Credential Manager / `wincred` 管理；不要读取、打印或复制 token。
- 每次推送前至少检查 `git diff --cached`、`git status`，涉及配置或认证时额外做秘密模式扫描。
- 曾经担心 Bark/DeepSeek key 被提交；截至提交 `b933671`，对当前跟踪文件及完整 Git 历史的扫描未发现对应凭据。

### 2.4 保持机器网络环境不变

这台机器位于中国大陆，GitHub/OpenAI 等依赖现有 VPN/代理。普通项目工作中禁止停止、重启、替换或修改 VPN、代理、DNS、TUN、防火墙。网络失败时只报告，不要擅自“修网络”。

## 3. 当前系统架构

```text
MAA/MuMu 进程与 MAA 日志
          │
          ▼
      agent.py ────── Bark / 日报 / 官网公告监督
          │
          ├── state/（运行状态、offset、incident、日程状态、资源历史）
          ├── logs/（agent.log、decision.log 等）
          └── config.json（本机配置与密钥，不入 Git）
          ▲
          │ 文件状态与诊断命令队列
  maa_agent_gui.py
          │
          ├── gui_widgets.py
          ├── schedule_utils.py
          ├── resource_history.py
          └── news_monitor.py
```

Agent 是后台监控主体；GUI 是控制和观察界面。关闭/隐藏 GUI 不等于停止 Agent。

## 4. 根目录程序与文件职责

### 核心 Python

| 文件 | 职责 |
|---|---|
| `agent.py` | 主守护进程。负责主循环、Bark 队列、incident、进程/窗口/磁盘/内存/代理检查、MAA 日志解析、计划漏跑、任务生命周期、日报、官网公告与 heartbeat。核心业务逻辑集中于此。 |
| `agent_runtime.py` | Agent 生命周期安全层。识别真实 `agent.py` 进程、Windows 命名互斥锁、PID/runtime 文件、正常及强制停止校验。防止编辑器命令行误判和 PID 复用误杀。 |
| `maa_agent_gui.py` | Tk 桌面管理器。主页、日程时间轴、资源历史、公告中心、设置、日志、托盘、Agent 启停和诊断入口。 |
| `first_run_setup.py` | 首次配置向导。默认生成单实例配置，可选第二个 MAA 和 Bark；已有 `config.json` 时不运行。 |
| `gui_widgets.py` | 可复用 Tk 控件：iOS 风格开关、可滚动容器、内嵌日历、颜色与滚轮处理。尽量保持独立、可单测。 |
| `schedule_utils.py` | 读取 MAA `gui.new.json`/`.bak` 定时器、生成基础计划、应用一次性 skip/move override。不会直接热改 MAA 自身定时器。 |
| `schedule_control.py` | 一次性计划例外的独立 CLI/Tk 工具；`schedule_control.bat` 的入口。 |
| `resource_history.py` | 采样和读取 CPU、内存、MAA/MuMu、主要进程等历史；兼容“运行日从 04:00 开始”和自然日图表拼接。 |
| `news_monitor.py` | 明日方舟官网公告增量抓取、正文清洗、摘要、维护时间解析，并按配置给受影响计划添加监督例外。常规轮询不使用 LLM。 |
| `diagnostic_commands.py` | GUI 与 Agent 之间的文件型诊断命令队列，采用 claim/replace 避免重复消费。 |
| `stop_agent.py` | 校验目标确实是本目录 Agent 后请求正常停止；`--force` 仅在超时后强制结束。 |
| `notification_drill.py` | 生成并发送人工合成的 Bark 演习通知，不制造真实故障，也不污染生产 incident。 |
| `test_bark.py` | 直接测试生产 Bark 通道，会真实发送通知。只在用户明确需要时运行。 |
| `test_deepseek.py` | 使用假日志测试 DeepSeek 连接，会消耗 API；非必要不要运行。 |

### 启停与辅助入口

| 文件 | 职责 |
|---|---|
| `MAA-Agent-GUI.bat` | 日常首选入口；通过隐藏 VBS 启动 GUI。 |
| `maa_agent_gui_hidden.vbs` | 用本目录 `.venv\Scripts\pythonw.exe` 无窗口启动 GUI。 |
| `start_agent.bat` / `start_agent_hidden.vbs` | 动态以脚本所在目录启动后台 Agent。 |
| `stop_agent.bat` / `stop_agent_hidden.vbs` | 请求 Agent 正常退出。 |
| `emergency_stop_agent.bat` | 先验证进程身份，再在必要时强制停止。 |
| `run_regression_tests.bat` | 使用项目虚拟环境运行全部 unittest。 |
| `preview_notifications.bat` | 只预览通知，不发 Bark。 |
| `test_all_notifications.bat` | 真实发送完整 Bark 演习通知；需用户明确知道会收到多条消息。 |
| `disable_daily_report_today.bat` / `enable_daily_report.bat` | 当天跳过/恢复日报，不需重启 Agent。 |
| `enable_copilot_notify_once.bat` / `enable_copilot_notify_always.bat` / `disable_copilot_notify.bat` | 控制 Copilot 通知状态；见“已知技术债”。 |

### 配置、资源与文档

| 路径 | 职责 |
|---|---|
| `config.example.json` | 可提交的配置模板，不得包含真实密钥。 |
| `config.json` | 生产配置，不入 Git。GUI 保存时会保留 `.bak`。 |
| `setup.ps1` | 首次启动时创建项目 `.venv` 并安装依赖；依赖完整时直接退出。 |
| `assets/` | GUI 图标等静态资源。 |
| `tests/` | 全部自动回归测试。 |
| `docs/` | 部署、GUI、测试和历史变更说明；部分内容是历史快照，可能落后于当前代码。 |
| `state/` | 生产状态，不入 Git。不要在测试中使用真实目录。 |
| `logs/` | 生产日志，不入 Git。 |
| `backups/` | 本机恢复副本，不入 Git。 |

## 5. 关键运行数据

这些文件都位于 `state/`，不应提交：

| 文件/目录 | 含义 |
|---|---|
| `agent.pid`、runtime/stop 文件 | Agent 进程身份和停止握手。 |
| `log_offsets/` | 每个 MAA 日志的读取位置。Agent 重启必须保留已有 offset，以读取停机期间追加内容；不能每次启动都跳到文件尾。 |
| `daily_YYYY-MM-DD.json` | 每日事件、告警、计划观测、资源摘要。跨午夜检查时可能需要读取前一天文件。 |
| `schedule_cache.json` | 短期计划快照，防止一次性 timer 触发后立即从 MAA 配置消失。 |
| `schedule_overrides.json` | 单次 skip/move 监督例外。 |
| `schedule_run_status.json` | 每个计划 occurrence 的 `running/completed/failed` 生命周期及错误详情。 |
| `scheduled_alert_*.txt` | 某次计划漏跑已经通知的标记。 |
| `issue_tracker/` | 未解决 MAA 问题及 alert 状态。 |
| `news_monitor.json` | 官网公告缓存、seen id 与增量拉取状态。 |
| `resource_history/*.jsonl` | 资源采样历史。 |
| `gui_state.json` | GUI 分栏等本地布局偏好。 |
| `bark_queue.json` | Bark 暂时发送失败时的有限离线队列。 |

写状态时优先沿用“临时文件 + replace”的原子写法。不要把不同安装目录的状态混合。

## 6. 当前主要功能和已实现的 UI 语义

### 6.1 主页

- 展示 Agent/MAA/MuMu 状态和 CPU、内存、磁盘。
- CPU 与内存数值显示在有颜色区分的进度条上；浅色/深色模式均需保持可读。
- “未来计划”和“最近事件与内存趋势”使用可拖动分栏，位置会持久化。
- 折叠只隐藏图表区域，不应把事故报告一起隐藏。
- 主页“清空日志”只设置主页显示 cutoff，不删除 `logs/agent.log`，也不影响“日志”页，因此不需要二次确认。
- 内存大户默认只展示前三项，避免直接铺满界面。

### 6.2 日程时间轴

- 日期标题可点击，打开居中的完整日历；日期输入视觉接近 Excel 日期框。
- 初始滚动位置围绕当前时间，不一次性加载无限日期；需要更多日期时按需扩展且必须有防重入/边界。
- 日期标题随滚动同步。
- 已结束计划使用低强调颜色；当前运行中为黄色；最近的下一次计划为绿色；明确失败为暗红色。
- 失败卡片提供“查看错误”，显示失败任务、时间、现象、可能原因和建议操作。
- 状态来源优先为 `schedule_run_status.json`；已明确 `failed` 的 occurrence 不能被后续 `AllTasksCompleted` 覆盖为成功。
- 时间轴 override 只改变 Agent 的监督预期，不修改 MAA 自己的长期定时器。

### 6.3 资源历史

- 图表鼠标移动时显示跟随竖线和对应数据点。
- 双击内存等指标进入详细视图。
- 资源刷新不得触发其他页面重建或闪烁。

### 6.4 官网公告

- 普通“立即更新”是增量拉取：从最新公告向后读取，碰到缓存中相同 id 即停止，以减少官网请求。
- “全部重拉”才重新抓取完整列表/正文，并需明确提示成本。
- 正文需清理官网导航尾巴，例如 `INFO / GO BACK / HOMEPAGE / INFORMATION / OPERATOR / WORLD / MEDIA` 等。
- 摘要应尽量以完整句子结束，不能只是机械截断。
- 官网时间写成 `24:00` 时必须规范化为次日 `00:00`，不能构造非法 Python hour=24。

### 6.5 设置

- 布尔配置统一使用 iOS 风格开关，包括内存报警总开关。
- “启用系统代理健康检查”同时显示在“设置”和“高级时间策略”，共用 `enable_proxy_healthcheck`。关闭后 Agent 不读取系统代理，也不发起外网探测；其他监控不受影响。
- 小窗口时设置页必须可滚动，按钮不能因窗口高度不足而不可达。
- 密钥字段默认遮罩；不要在日志或异常中输出值。

## 7. 配置与时间边界

- 生产配置从 `config.json` 读取；模板见 `config.example.json`。
- 新安装缺少 `config.json` 时，GUI 先启动首次配置向导；默认一个 MAA、Bark 关闭，可选第二个 MAA。已有配置不迁移、不覆盖。
- 用户只配置一个 MAA 时，GUI 菜单、托盘、设置和通知测试应只展示已配置实例；不要假定 MAA2 必然存在。
- MAA 长期计划优先读取每个实例的 `config\gui.new.json`，失败后读 `.bak`，最后才使用 Agent 配置中的备用计划。
- MAA timer 的 nullable `IsEnabled` 可能代表一次性启用，不能简单按 false 处理。
- `operational_day_start_hour` 默认 4，只影响运行日统计；日历视图需要拼接成自然日 00:00–24:00。
- `news_check_times` 必须兼容旧配置中的 `24:xx`，将其解释为当天 `00:xx` 的 due slot；维护窗口结尾的 `24:00` 则是次日零点。
- GUI 修改监控参数后，Agent 下一轮会重新读取配置，通常不必重启。

## 8. 开发、验证和交付流程（后续 AI 必须遵守）

### 8.1 开工前

1. 先读本文件，再读与任务直接相关的模块和测试；不要为了“熟悉项目”无差别通读所有日志或历史对话。
2. 执行 `git status --short`，保留用户已有改动，不得覆盖或重置。
3. 确认工作目录是 `D:\MAA-Agent_0920`，避免误改旧版 `D:\MAA-Agent`。
4. 若任务涉及报警，先查看 `tests/test_revision2.py`；涉及 GUI/时间轴看 `tests/test_v2_features.py` 与 `tests/test_gui_widgets.py`；涉及公告看 `tests/test_news_center.py`。

### 8.2 修改时

- 主 AI 负责架构、跨模块判断、安全敏感逻辑和最终验收。
- DeepSeek subagent 只能做边界清晰、可验证的机械实现/测试/复核；不要把报警架构、安全或最终判断外包给它。
- 优先小范围修改，避免顺手重构已有稳定功能。
- 不要修改生产 `config.json`、真实 `state/` 或 `logs/` 来完成单元测试。
- 诊断不等于获准修复；用户只要求分析时不要顺手改代码。
- 不要重启电脑、Codex、VPN/代理。除非用户明确授权，也不要自行重启正在运行的 Agent/GUI。

### 8.3 文档同步（强制）

每次代码行为发生实质变化时，必须在同一轮更新本 `AGENTS.md`：

- 更新相关“文件职责 / 运行语义 / 已知问题”；
- 在下方“变更摘要”增加一条简短记录；
- 若操作方式、部署或验收步骤变化，再同步更新 `README.md` 或 `docs/` 对应文件。

不要把逐行实现细节写成长篇流水账；目标是让下一位 AI 无需追溯对话就能继续。

### 8.4 测试（强制）

首选：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

必要时再运行：

```powershell
.\.venv\Scripts\python.exe -m compileall -q .
```

当前完整基线：**130 tests，全部通过**（2026-11-08）。

注意：必须使用项目 `.venv`，系统 Python 可能缺少 `psutil` 等依赖。测试需要可写临时目录；权限受限环境中出现 `PermissionError` 不代表业务断言失败，应在获得正常临时目录权限后复跑。

不得把 `test_all_notifications.bat`、`test_bark.py`、`test_deepseek.py` 当作普通回归测试：前两者会真实发 Bark，后者会访问并消耗 DeepSeek API。

### 8.5 Git 与 GitHub（强制）

**任何修复、功能、配置模板或文档更新都必须及时同步到 GitHub，不得只停留在本机。** 除非用户在当前任务中明确说“不要提交/不要推送”，每轮完成修改后都必须：

1. `git status --short` 和 `git diff --check`；
2. 检查 staged diff，确认无配置、密钥、日志、state 或无关文件；
3. 运行相关测试，重要修改运行完整测试；
4. 创建语义清晰的小提交；
5. `git push origin main`；
6. 最终回复给出测试结果、提交 hash 和推送结果。

若网络失败，不得改变 VPN/代理；保留本地提交，明确记录“尚未推送”，并在下一轮项目工作开始时优先补推。任务只有在远端 `origin/main` 包含本轮提交后才算完成。GitHub 实际 push 时间不能回填，只有 commit author/committer date 可在历史重建时还原。

### 8.6 版本记录与发布（强制）

- 根目录 `VERSION` 是当前稳定版本号的唯一来源，格式必须是 `MAJOR.MINOR.PATCH`；GUI 和发布脚本必须读取它，不得另设手工版本常量。
- 普通提交可以继续使用当前稳定版本；准备发布时必须先按语义化版本更新 `VERSION`。修复递增 PATCH，兼容的新功能递增 MINOR，不兼容变更递增 MAJOR。
- 每次版本升级必须同步更新中英文 README 的当前版本、对应 `docs/RELEASE_NOTES_vX.Y.Z.md`、Git 标签 `vX.Y.Z` 和 GitHub Release，并上传重新生成的 ZIP 与 SHA-256。
- 发布完成后必须远端核对：Release 非 draft、标签指向预期提交、附件已上传且摘要与本地一致。
- 不得覆盖或移动既有 Release 标签；修复已发布内容时创建新版本。

## 9. 回归测试分工

| 测试文件 | 主要覆盖 |
|---|---|
| `tests/test_revision2.py` | Agent 生命周期、计划读取/覆盖、日志 callback、终态失败、pending/recovery、进程/代理/Bark 队列、日报、资源压力等核心逻辑。 |
| `tests/test_v2_features.py` | 公告时间解析、24:00、主页日志隔离、时间轴下一计划及生命周期卡片、持久状态等。 |
| `tests/test_gui_widgets.py` | Toggle、ScrollableFrame、InlineCalendar、颜色和滚轮等 GUI 控件。 |
| `tests/test_news_center.py` | 公告缓存兼容、增量/全量抓取、正文清洗、失败保留旧缓存、supervision override。 |

新增缺陷修复必须有能在旧代码上失败、在新代码上通过的回归测试，尤其是报警和无限加载类问题。

## 10. 已知技术债与注意事项

1. `docs/CHANGES_2026-09-20.md` 等历史文档中“`TaskChainStopped` 默认静默”“任意后续进展都可恢复 pending”等描述已经过时；当前规则见本文件 2.1 和现有测试。
2. GUI 仍是单进程 Tk；耗时网络/抓取必须在线程执行，再通过 `root.after` 回 UI 线程。
3. `agent.py` 较大，但在没有完整测试保护前不要仅为了“美观”大拆模块。优先修行为，再做有边界的抽取。
4. 外部 heartbeat 默认未配置，因此整机断电、完全断网、Windows 死机时本机无法自报；这是架构边界，不是 Bark 重试能解决的问题。

## 11. 重要历史节点

以下提交已推送到 `origin/main`：

| 提交 | 日期 | 内容 |
|---|---|---|
| `a70a3b1` | 2026-09-21 | Kanban P0 修复与 GUI 资源视图重做。 |
| `33349dc` | 2026-09-21 | 修复主页面板并加入公告中心。 |
| `dc7b4b4` / `797aef5` | 2026-09-21 | 浅色模式与深色只读控件主题修复。 |
| `6d687d5` | 2026-09-21 | 日程和资源 UI 卡片重设计。 |
| `07c70b4` | 2026-09-21 | 稳定日程刷新和日期导航。 |
| `8337e2a` | 2026-09-21 | 阻止时间轴递归无限加载。 |
| `6805a76` | 2026-09-21 | 滚动更新原子呈现，缓解撕裂。 |
| `6ba92bc` | 2026-09-22 | 主页布局记忆、日志视图隔离、滚轮、公告增量/清洗与 24:00 修复。 |
| `0f8aa44` | 2026-09-24 | 保留明确 MAA 终态失败，正确区分可恢复重试。 |
| `b933671` | 2026-09-26 | 时间轴显示运行中、失败、下一计划并提供错误详情。 |

## 12. 变更摘要（后续持续追加）

- 2026-09-29：建立本交接文档，汇总当前架构、文件职责、告警不变量、GUI 语义、测试/安全/Git 规范及已知技术债。
- 2026-09-29：将代理健康检查开关加入“设置”页并与高级页同步；关闭时不读取代理配置、不发外网探测。
- 2026-11-08：整理 v1.0.0 公开发布：补齐中英文 README、部署依赖和可重复打包脚本；设置页加入作者信息；Copilot 通知 BAT 改为相对路径，避免跨安装目录写状态。
- 2026-11-08：增加 `VERSION` 单一版本来源与强制 GitHub 同步/发布规则；明确每次修复、功能和文档更新均需及时提交并推送。
- 2026-11-08：v1.1.0 增加首次安装/配置向导，默认改为单 MAA 与可选 Bark；GUI 按实际实例动态显示；中英文 README 改为面向普通用户的快速开始页面。
