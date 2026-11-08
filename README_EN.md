# MAA Agent

English | [简体中文](README.md)

MAA Agent is a Windows-only watchdog, alerting service, and desktop manager for two MAA instances. It determines whether scheduled MAA jobs actually started, completed, or reached a terminal failure by reading MAA logs and Windows runtime state. Actionable incidents can be sent to a phone through Bark, while the desktop GUI provides schedules, resource history, official notices, configuration, and logs in one place.

Current stable version: **v1.0.0** (the root [`VERSION`](VERSION) file and GitHub Releases are authoritative).

> This is an independent companion project and is not an official MAA project. Install and configure MAA first. The current release is designed around an MAA1/MAA2 setup.

## Features

- Monitors MAA, MuMu, memory, disks, unresponsive windows, and optional proxy health.
- Parses MAA callbacks: recoverable `SubTaskError` retries are normally suppressed, while explicit terminal task-chain failures must alert.
- Reads MAA timers, shows future/running/completed/failed jobs, and detects missed scheduled runs.
- Supports one-time skip/reschedule supervision overrides without changing MAA's own timers.
- Sends Bark incident alerts with cooldown, aggregation, a bounded offline queue, daily reports, and optional DeepSeek summaries.
- Incrementally checks official Arknights notices, extracts maintenance windows, and supervises affected runs.
- Includes a Tk desktop manager for status, schedule timeline, resource history, notices, alert drills, settings, full configuration, and logs.
- Provides a Windows tray icon, startup integration, shortcuts, and validated single-instance lifecycle controls.

## Architecture

```text
MAA / MuMu / MAA logs
          |
          v
       agent.py --------> Bark / reports / notice supervision
          |
          +--> state/ local runtime state and history (private)
          +--> logs/  local logs (private)
          +--> config.json local paths and secrets (private)
          ^
          |
   maa_agent_gui.py
   status, timeline, resources, notices, settings, lifecycle
```

The Agent is the background monitor; the GUI is its control and observability surface. Hiding or closing the GUI does not stop the Agent.

## Requirements

- Windows 10 or 11
- Python 3.11 or newer (enable “Add Python to PATH” during installation)
- An installed and configured MAA setup; MuMu is the currently targeted emulator family
- Bark for iOS and a device key if phone alerts are required
- A DeepSeek API key only if you enable the optional AI features

## Installation

1. Download the latest `MAA-Agent-vX.Y.Z-Windows-source.zip` from [Releases](https://github.com/darkbrommd/MAA-Agent/releases), then extract it to a permanent folder such as `D:\MAA-Agent`. Do not run it from inside the ZIP.
2. Open PowerShell in the extracted folder and install the environment:

   ```powershell
   py -3.11 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install --upgrade pip
   .\.venv\Scripts\python.exe -m pip install -r requirements-gui.txt
   ```

3. Create your private configuration:

   ```powershell
   Copy-Item config.example.json config.json
   ```

4. Edit `config.json` and review at least:

   - `bark_key`; set `enable_bark` to `false` if Bark is not used.
   - `maa_instance_dirs` for MAA1 and MAA2.
   - `maa_gui_config_paths` for each `config\gui.new.json`.
   - `maa_log_paths` for each `debug\asst.log`.
   - Optional `launch_paths` used by the GUI to launch MAA/MuMu.
   - Optional `deepseek_api_key`; leave it empty and keep `use_deepseek` disabled when unused.

5. Double-click `MAA-Agent-GUI.bat`. Use the Settings tab to configure Windows startup, tray behavior, instance aliases, and launch paths. The default configuration starts minimized during Windows startup, so check the notification area if no window appears.
6. Run the regression suite after the first installation:

   ```powershell
   .\.venv\Scripts\python.exe -m unittest discover -s tests -v
   ```

The running Agent normally reloads saved configuration on its next loop. Do not run Agents from two installation folders at once, and never share one folder's `state/` with another installation.

## Entry points

| File | Purpose |
|---|---|
| `MAA-Agent-GUI.bat` | Opens the desktop manager; recommended daily entry point. |
| `start_agent.bat` / `stop_agent.bat` | Starts the Agent in the background or requests a clean stop. |
| `emergency_stop_agent.bat` | Validates process identity before an emergency stop. |
| `run_regression_tests.bat` | Runs the full test suite in the project virtual environment. |
| `preview_notifications.bat` | Previews notification templates without sending Bark. |
| `test_all_notifications.bat` | Sends the full alert drill to Bark; expect multiple real notifications. |
| `schedule_control.bat` | Manages one-time supervision overrides. |

## Repository map

| Path | Role |
|---|---|
| `agent.py` | Monitor loop, log parsing, alerts, reporting, and notice supervision. |
| `maa_agent_gui.py` | Tk desktop manager. |
| `agent_runtime.py` | Single-instance locking, PID validation, and safe shutdown. |
| `schedule_utils.py` / `schedule_control.py` | MAA timer reading and one-time overrides. |
| `resource_history.py` | Resource sampling and historical views. |
| `news_monitor.py` | Incremental official-notice fetching and maintenance parsing. |
| `notification_drill.py` | Synthetic alert drills without creating real failures. |
| `config.example.json` | Public template; copy it to the private `config.json`. |
| `assets/` | Application icons. |
| `tests/` | Automated regression tests. |
| `docs/` | Operations, testing, roadmap, and historical notes. |

`config.json`, `.venv/`, `state/`, `logs/`, `backups/`, and all secrets are local-only and are excluded from Git and release archives.

## Updating and removal

Before updating, stop the Agent cleanly and back up `config.json` and `state/`. Extract the new version to a new directory, then copy your local configuration and state. Delete the old installation only after validating the new one. To uninstall, stop the Agent, disable Windows startup in Settings, then remove the installation folder and any shortcuts you created.

## Safety notes

- Never commit or share `config.json`; it may contain Bark/DeepSeek keys and local paths.
- `test_all_notifications.bat` and `test_bark.py` send real Bark messages. `test_deepseek.py` consumes the DeepSeek API.
- Timeline overrides only change monitoring expectations; they do not prevent or edit MAA jobs.
- A local Agent cannot report a full power loss, complete network outage, or Windows freeze. An external heartbeat service is required for those cases.

## Development

Contributors and future coding agents should read `AGENTS.md` first. Standard verification:

```powershell
.\.venv\Scripts\python.exe -m compileall -q .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

`scripts\build_release.ps1` builds a release archive from Git-tracked files and excludes internal handoff material, development roadmaps, and all machine-local data.

The version is maintained only in the root `VERSION` file. Before publishing an upgrade, update it using semantic versioning and synchronize the release notes, Git tag, and GitHub Release. The GUI and packaging script read it automatically.

## Author and acknowledgement

Author: [Taylor Yu](https://github.com/darkbrommd)

Thanks to the original application icon artist **kataokasan** (Pixiv ID: **84325201**).
