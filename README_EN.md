<div align="center">
  <img src="assets/app_icon.png" width="128" alt="MAA Agent icon">
  <h1>MAA Agent</h1>
  <p>A Windows desktop app that keeps an eye on your MAA</p>

  [![Release](https://img.shields.io/github/v/release/darkbrommd/MAA-Agent?display_name=tag&style=flat-square)](https://github.com/darkbrommd/MAA-Agent/releases/latest)
  [![Windows](https://img.shields.io/badge/Windows-10%20%7C%2011-0078D4?style=flat-square&logo=windows)](https://github.com/darkbrommd/MAA-Agent/releases/latest)
  [![Tests](https://img.shields.io/badge/tests-passing-2ea44f?style=flat-square)](#development-and-tests)

  [Download](https://github.com/darkbrommd/MAA-Agent/releases/latest) · [MAA website](https://maa.plus/) · [MAA installation guide](https://docs.maa.plus/en-us/manual/install.html) · [简体中文](README.md)
</div>

---

MAA Agent runs in the background and checks whether MAA starts its scheduled work. One window brings together upcoming runs, resource usage, official notices, and recent logs. It can also send Bark reminders when MAA is not running, a task does not start on time, or memory and disk space are running low.

It manages **one MAA installation by default**. For most users, setup is just choosing the MAA folder in the first-run wizard. A second MAA can be added during setup if needed.

> MAA Agent is an independent companion project and is not part of MAA. Download MAA from the [official website](https://maa.plus/) and follow its [installation guide](https://docs.maa.plus/en-us/manual/install.html) before you start.

## What it does

| | Feature | Description |
|---|---|---|
| 🗓️ | Schedule | See upcoming runs and whether they are running, completed, or failed. |
| 🔔 | Reminders | Use Bark when MAA is not running, a task is late, or system resources are low. |
| 📊 | Resources | Review CPU, memory, disk, MAA, and MuMu history. |
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
| `agent.py` | Background checks, MAA log reading, reminders, and reports. |
| `maa_agent_gui.py` | Desktop application. |
| `first_run_setup.py` | First-run setup wizard. |
| `setup.ps1` | Creates the Python environment and installs dependencies. |
| `schedule_utils.py` | Reads MAA schedules and applies one-time monitoring changes. |
| `resource_history.py` | Resource sampling and history. |
| `news_monitor.py` | Official-notice updates and maintenance-window detection. |
| `config.example.json` | Single-MAA default template; copy it to `config.json` for manual setup. |
| `tests/` | Automated regression tests. |

Local `config.json`, secrets, logs, runtime state, backups, and the virtual environment are excluded from Git and release packages.

## Optional features

- **Second MAA:** choose “two” in the first-run wizard, or add MAA2 later through the full configuration editor.
- **Bark:** optional iPhone reminders; skip it during setup and add it later if preferred.
- **DeepSeek:** used only for optional AI daily reports and disabled by default.
- **External heartbeat:** covers full power or network outages that a local app cannot report; it is not configured by default.

## Development and tests

Current stable version: **v1.1.0**. The root [`VERSION`](VERSION) file is authoritative.

```powershell
.\.venv\Scripts\python.exe -m compileall -q .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Contributors and coding agents should read [`AGENTS.md`](AGENTS.md) first. `scripts\build_release.ps1` creates release packages; every version upgrade synchronizes the version file, documentation, Git tag, and GitHub Release.

## Author and acknowledgement

Author: [Taylor Yu](https://github.com/darkbrommd)

Thanks to the original application icon artist **kataokasan** (Pixiv ID: **84325201**).
