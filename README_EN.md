<div align="center">
  <img src="assets/app_icon.png" width="128" alt="MAA Agent icon">
  <h1>MAA Agent</h1>
  <p>A Windows desktop companion that monitors MAA, explains what needs attention, and sends a daily summary to your phone</p>

  [![Release](https://img.shields.io/github/v/release/darkbrommd/MAA-Agent?display_name=tag&style=flat-square)](https://github.com/darkbrommd/MAA-Agent/releases/latest)
  [![Windows](https://img.shields.io/badge/Windows-10%20%7C%2011-0078D4?style=flat-square&logo=windows)](https://github.com/darkbrommd/MAA-Agent/releases/latest)
  [![Tests](https://img.shields.io/badge/tests-passing-2ea44f?style=flat-square)](#development-and-tests)

  [Download](https://github.com/darkbrommd/MAA-Agent/releases/latest) · [MAA website](https://maa.plus/) · [MAA installation guide](https://docs.maa.plus/en-us/manual/install.html) · [简体中文](README.md)
</div>

---

MAA Agent does more than check whether MAA runs on schedule. It brings task status, resource trends, official notices, and recent logs into one window. With DeepSeek enabled, the daily report adds a short analysis—a conclusion, resource trend, run quality, and a suggestion—when something noteworthy comes up. With Bark configured, that report goes to your phone.

It manages **one MAA installation by default**. For most users, setup is just choosing the MAA folder in the first-run wizard. A second MAA can be added during setup if needed.

> MAA Agent is an independent companion project and is not part of MAA. Download MAA from the [official website](https://maa.plus/) and follow its [installation guide](https://docs.maa.plus/en-us/manual/install.html) before you start.

## What it does

| | Feature | Description |
|---|---|---|
| 🗓️ | Schedule | See upcoming runs and whether they are running, completed, or failed. |
| 🔔 | Reminders | Use Bark when MAA is not running, a task is late, or system resources are low. |
| 📊 | Resources | Review CPU, memory, disk, MAA, and MuMu history. |
| 📝 | Daily report & AI analysis | Summarizes scheduled runs, resource trends, and open issues each day. With DeepSeek enabled, it analyzes noteworthy issues and adds a conclusion and suggested next steps; with Bark configured, the report goes to your phone. |
| 📰 | Notices | Read new official Arknights notices and detect maintenance windows. |
| 🧰 | Controls | Start, stop, and inspect the Agent from the desktop window or tray; one or two MAA installations are supported. |

## Get started in four steps

1. **Set up MAA first.** [Install MAA](https://docs.maa.plus/en-us/manual/install.html), finish its basic configuration, and run one task manually to make sure it works.
2. **Download MAA Agent.** Get the latest ZIP from [Releases](https://github.com/darkbrommd/MAA-Agent/releases/latest) and fully extract it to a permanent folder such as `D:\MAA-Agent`.
3. **Double-click `MAA-Agent-GUI.bat`.** On the first run it creates the Python environment, installs dependencies, and opens the setup wizard.
4. **Follow the wizard.** Choose one or two MAA installations and select each MAA folder. Add a Bark key for phone reminders, or skip it.

After setup, the main window opens. Use `MAA-Agent-GUI.bat` again later, or create desktop and Windows startup shortcuts in Settings.

### Automatic setup did not finish?

Automatic setup requires Windows 10/11 and Python 3.11 or newer. If Python is missing, install it from [python.org](https://www.python.org/downloads/windows/), enable **Add Python to PATH**, and open `MAA-Agent-GUI.bat` again.

The manual setup path remains available:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-gui.txt
Copy-Item config.example.json config.json
```

Edit `config.json`, then reopen `MAA-Agent-GUI.bat`. Existing configurations are preserved and do not trigger the wizard again.

## Daily use

- The dashboard shows MAA/MuMu status, upcoming runs, recent events, and resource trends.
- The timeline can skip or reschedule monitoring for one run. It does not edit MAA's own timers.
- Settings covers Windows startup, dark mode, aliases, launch paths, and Bark behavior.
- Notification tests check Bark and reminder behavior without stopping or changing MAA.
- Closing the window can leave the Agent running in the system tray.

See [`docs/GUI_AND_OPERATIONS.md`](docs/GUI_AND_OPERATIONS.md) for detailed operation and [`docs/README_DEPLOY.md`](docs/README_DEPLOY.md) for migration notes.

## Repository map

| Path | Role |
|---|---|
| [`app/`](app/) | Agent, desktop UI, first-run setup, schedule, resource, and notice source code. |
| `tools/windows/` | Windows helpers for setup, lifecycle, and testing. |
| `tools/diagnostics/` | Manual diagnostics that call the real Bark or DeepSeek service. |
| `config.example.json` | Single-MAA default template; copy it to `config.json` for manual setup. |
| `tests/` | Automated regression tests. |

Local `config.json`, secrets, logs, runtime state, backups, and the virtual environment are excluded from Git and release packages.

## Optional features

- **Second MAA:** choose “two” in the first-run wizard, or add MAA2 later through the full configuration editor.
- **Bark:** optional iPhone reminders; skip it during setup and add it later if preferred.
- **DeepSeek daily analysis:** off by default. Once enabled, the daily report asks for a short analysis only when there are unresolved incidents or memory usage or CPU load has stayed high for a while — one call per day by default.
- **External heartbeat:** covers full power or network outages that a local app cannot report; it is not configured by default.

## Development and tests

Current stable version: **v1.1.5**. The root [`VERSION`](VERSION) file is authoritative.

```powershell
.\.venv\Scripts\python.exe -m compileall -q .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Contributors should read [`CONTRIBUTING.md`](CONTRIBUTING.md); coding agents and maintainers can also use [`AGENTS.md`](AGENTS.md). See [`SECURITY.md`](SECURITY.md) for private vulnerability reporting. `scripts\build_release.ps1` creates release packages.

## Author and acknowledgement

Author: [Taylor Yu](https://github.com/darkbrommd)

Thanks to the original application icon artist **kataokasan** (Pixiv ID: **84325201**).
