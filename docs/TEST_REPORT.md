# MAA Agent Revision 3 Test Report

Date: 2026-09-20

## Result

- Python compile / import checks: PASS
- `config.json` parse: PASS
- Regression suite: **44 / 44 PASS**
- Notification drill catalogue: **24 cases**, dry-run PASS
- Notification catalogue jargon check: PASS
- Agent-owned path isolation test: PASS
- Cross-directory MAA-Agent detection test: PASS
- Non-Python editor/PowerShell false-positive rejection: PASS
- Python 3.14 virtualenv launcher-parent exclusion: PASS
- Production BAT lifecycle and no-lingering-CMD test: PASS
- GUI/tray smoke and GUI single-instance test: PASS
- Existing Revision 2 regression coverage retained: PASS

## New Revision 3 coverage

The additional tests verify:

- Copilot once/always files follow the current Agent directory instead of hard-coded `D:\MAA-Agent`.
- Startup status is a user-facing state snapshot and does not expose startup grace / DeepSeek policy / callback jargon.
- A new Agent copy can detect another `MAA-Agent*\agent.py` process in a backup/sibling directory.
- The 24 notification drill templates avoid `TaskChainStart`, `TaskChainError`, PID, UUID, grace-window reasoning and similar internal implementation language.
- Notification drill can preview all cases without sending Bark or mutating production incident/cooldown state.

## Existing regression coverage

The full suite still covers schedule config and `.bak` fallback, one-time timers, multi-day skip/move overrides, actual callback matching, missed/unverified schedules, delayed starts within the miss grace, error stall/recovery lifecycle, process mapping confidence, proxy failure/recovery, heartbeat status handling, Bark queue throttling, daily-report due/sent/skip semantics, report rendering, and aggregation of low-value events.

## Notification drill behavior

`test_all_notifications.bat` invokes `notification_drill.py` and sends 24 synthetic Bark messages. The drill uses the same Bark server/key, payload builder, payload splitting and HTTP transport used by the Agent, but deliberately does **not**:

- stop or launch MAA;
- fill memory or disk;
- disable the proxy;
- reboot Windows;
- write production incidents;
- write alert cooldown markers;
- increment production daily Bark statistics;
- enqueue failed drill messages into the production Bark retry queue.

Every drill message is prefixed with `🧪 [通知演习]` and states that it is synthetic.

## User-facing wording review

Normal Bark notifications were reviewed so that timing thresholds and internal decision rules stay in code/logs rather than the phone UI. Startup notification now reports only launch time and an initial status snapshot. MAA process, hung-window, pending-error, schedule-miss and proxy notifications report current facts, likely causes and action suggestions without exposing internal sustain/grace/callback details.
