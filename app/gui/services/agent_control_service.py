from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from agent_runtime import (
    PACKAGED_AGENT_FLAG,
    agent_entry_path,
    find_running_agents,
    is_script_for_installation,
    request_stop,
    validated_agent_for_dir,
)


NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
NEW_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


def _agent_python(base_dir: Path) -> Path:
    bundled = base_dir / ".venv" / "Scripts" / "python.exe"
    if bundled.exists():
        return bundled
    current = Path(sys.executable)
    console = current.with_name("python.exe")
    return console if console.exists() else current


def start_agent_process(base_dir: Path) -> tuple[bool, str]:
    running = validated_agent_for_dir(base_dir)
    if running:
        return False, f"Agent 已在运行（PID {running['pid']}）。"
    others = [
        row for row in find_running_agents()
        if not is_script_for_installation(row["script"], base_dir)
    ]
    if others:
        return False, f"检测到另一份 Agent：{others[0]['script']}（PID {others[0]['pid']}）。"
    try:
        entry = agent_entry_path(base_dir)
        packaged = entry.suffix.lower() == ".exe"
        command = [str(entry), PACKAGED_AGENT_FLAG] if packaged else [str(_agent_python(base_dir)), str(entry)]
        environment = None
        if packaged:
            environment = os.environ.copy()
            environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        subprocess.Popen(
            command,
            cwd=str(base_dir),
            creationflags=NO_WINDOW | NEW_GROUP,
            close_fds=True,
            env=environment,
        )
        return True, "启动请求已发送。"
    except OSError as exc:
        return False, f"启动失败：{exc}"


class _Signals(QObject):
    done = Signal(bool, str)


class _AgentActionWorker(QRunnable):
    def __init__(self, base_dir: Path, action: str):
        super().__init__()
        self.base_dir = base_dir
        self.action = action
        self.signals = _Signals()

    def run(self) -> None:
        if self.action == "start":
            ok, message = start_agent_process(self.base_dir)
            self.signals.done.emit(ok, message)
            return
        result = request_stop(self.base_dir, timeout_seconds=20, force=False)
        if self.action == "restart" and result.status in {"not_running", "stopped"}:
            ok, message = start_agent_process(self.base_dir)
            self.signals.done.emit(ok, message if ok else f"停止完成，但{message}")
            return
        self.signals.done.emit(result.status in {"not_running", "stopped"}, result.message)


class AgentControlService(QObject):
    busy_changed = Signal(bool)
    finished = Signal(bool, str)

    def __init__(self, base_dir: Path, parent=None):
        super().__init__(parent)
        self.base_dir = Path(base_dir)
        self._worker: _AgentActionWorker | None = None

    def run(self, action: str) -> bool:
        if self._worker is not None or action not in {"start", "stop", "restart"}:
            return False
        worker = _AgentActionWorker(self.base_dir, action)
        self._worker = worker
        worker.signals.done.connect(self._finish)
        self.busy_changed.emit(True)
        QThreadPool.globalInstance().start(worker)
        return True

    def _finish(self, ok: bool, message: str) -> None:
        self._worker = None
        self.busy_changed.emit(False)
        self.finished.emit(ok, message)
