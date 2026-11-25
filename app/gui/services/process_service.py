from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import psutil
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from project_paths import config_path


@dataclass(frozen=True)
class InstanceProcessState:
    label: str
    display_name: str
    running: bool
    pids: tuple[int, ...]


@dataclass(frozen=True)
class ProcessMemoryState:
    name: str
    pid: int
    memory_mb: float


@dataclass(frozen=True)
class ProcessSnapshot:
    cpu_percent: float
    memory_percent: float
    memory_used_gb: float
    disk_percent: float
    disk_used_gb: float
    disk_total_gb: float
    top_processes: tuple[ProcessMemoryState, ...]
    agent_running: bool
    agent_pid: int | None
    maa_instances: tuple[InstanceProcessState, ...]
    mumu_count: int
    mumu_memory_mb: float


class _WorkerSignals(QObject):
    done = Signal(object)
    failed = Signal(str)


class _ProcessWorker(QRunnable):
    def __init__(self, base_dir: Path):
        super().__init__()
        self.base_dir = base_dir
        self.signals = _WorkerSignals()

    def run(self) -> None:
        try:
            try:
                cfg = json.loads(config_path(self.base_dir).read_text(encoding="utf-8-sig"))
            except (FileNotFoundError, OSError, json.JSONDecodeError):
                cfg = {}
            rows = []
            for proc in psutil.process_iter(["pid", "name", "exe", "memory_info", "cmdline"]):
                try:
                    rows.append({
                        "pid": int(proc.pid),
                        "name": str(proc.info.get("name") or ""),
                        "exe": str(proc.info.get("exe") or ""),
                        "memory": int(getattr(proc.info.get("memory_info"), "rss", 0) or 0),
                        "cmdline": tuple(str(value) for value in (proc.info.get("cmdline") or ())),
                    })
                except (psutil.Error, OSError, TypeError, ValueError):
                    continue
            snapshot = _build_snapshot(cfg, rows, self.base_dir)
            self.signals.done.emit(snapshot)
        except Exception as exc:
            self.signals.failed.emit(str(exc))


def _path_is_below(executable: str, directory: str) -> bool:
    if not executable or not directory:
        return False
    try:
        return Path(executable).resolve().is_relative_to(Path(directory).resolve())
    except (OSError, ValueError):
        return False


def _build_snapshot(cfg: dict, rows: list[dict], disk_path: Path | None = None) -> ProcessSnapshot:
    instance_dirs = {str(key): str(value) for key, value in (cfg.get("maa_instance_dirs") or {}).items()}
    aliases = {str(key): str(value).strip() for key, value in (cfg.get("maa_instance_aliases") or {}).items()}
    instance_pids: dict[str, list[int]] = {label: [] for label in instance_dirs}
    mumu_names = {str(value).lower() for value in (cfg.get("mumu_process_names") or ())}
    agent_pid = None
    mumu_count = 0
    mumu_memory = 0
    for row in rows:
        name = row["name"].lower()
        command = " ".join(row["cmdline"]).lower()
        if agent_pid is None and ((name == "maa-agent.exe" and "--maa-agent-background" in command) or (name.startswith("python") and "agent.py" in command)):
            agent_pid = row["pid"]
        if name == "maa.exe":
            for label, directory in instance_dirs.items():
                if _path_is_below(row["exe"], directory):
                    instance_pids[label].append(row["pid"])
                    break
        if name in mumu_names or name.startswith(("mumu", "nemu")):
            mumu_count += 1
            mumu_memory += row["memory"]
    vm = psutil.virtual_memory()
    try:
        disk = psutil.disk_usage(str(disk_path or Path.cwd()))
        disk_percent = float(disk.percent)
        disk_used_gb = float(disk.used) / 1024**3
        disk_total_gb = float(disk.total) / 1024**3
    except (OSError, psutil.Error):
        disk_percent = disk_used_gb = disk_total_gb = 0.0
    top_processes = tuple(
        ProcessMemoryState(
            str(row.get("name") or f"PID {row.get('pid', '?')}"),
            int(row.get("pid") or 0),
            float(row.get("memory") or 0) / 1048576,
        )
        for row in sorted(rows, key=lambda item: int(item.get("memory") or 0), reverse=True)[:3]
    )
    return ProcessSnapshot(
        cpu_percent=float(psutil.cpu_percent(interval=None)),
        memory_percent=float(vm.percent),
        memory_used_gb=float(vm.total - vm.available) / 1024**3,
        disk_percent=disk_percent,
        disk_used_gb=disk_used_gb,
        disk_total_gb=disk_total_gb,
        top_processes=top_processes,
        agent_running=agent_pid is not None,
        agent_pid=agent_pid,
        maa_instances=tuple(
            InstanceProcessState(
                label,
                f"{aliases[label]}（{label}）" if aliases.get(label) else label,
                bool(instance_pids[label]),
                tuple(instance_pids[label]),
            )
            for label in sorted(instance_pids)
        ),
        mumu_count=mumu_count,
        mumu_memory_mb=mumu_memory / 1048576,
    )


class ProcessService(QObject):
    snapshot_ready = Signal(object)
    failed = Signal(str)

    def __init__(self, base_dir: Path, thread_pool: QThreadPool | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self.base_dir = Path(base_dir)
        self.thread_pool = thread_pool or QThreadPool.globalInstance()
        self._running = False

    def refresh(self) -> None:
        if self._running:
            return
        self._running = True
        worker = _ProcessWorker(self.base_dir)
        worker.signals.done.connect(self._finish)
        worker.signals.failed.connect(self._fail)
        self.thread_pool.start(worker)

    def _finish(self, snapshot: ProcessSnapshot) -> None:
        self._running = False
        self.snapshot_ready.emit(snapshot)

    def _fail(self, message: str) -> None:
        self._running = False
        self.failed.emit(message)
