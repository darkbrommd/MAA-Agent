from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal

import resource_history
from gui.services.schedule_service import decorate_schedule_rows
from project_paths import config_path
from schedule_utils import effective_occurrences, override_file_path


@dataclass(frozen=True)
class DashboardSnapshot:
    schedule_rows: tuple[dict, ...]
    resource_rows: tuple[dict, ...]
    day: date


def _read_mapping(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}


def dashboard_source_signature(base_dir: Path, now: datetime | None = None) -> tuple:
    """Cheap source check; the minute bucket also advances time-based labels."""
    base_dir = Path(base_dir)
    now = now or datetime.now()
    cfg = _read_mapping(config_path(base_dir))
    paths = [
        config_path(base_dir),
        base_dir / "state" / "schedule_run_status.json",
        override_file_path(base_dir, cfg),
    ]
    for label in set(cfg.get("maa_instance_dirs") or {}) | set(cfg.get("maa_gui_config_paths") or {}):
        explicit = (cfg.get("maa_gui_config_paths") or {}).get(label)
        instance = (cfg.get("maa_instance_dirs") or {}).get(label)
        path = Path(explicit) if explicit else (Path(instance) / "config" / "gui.new.json" if instance else None)
        if path:
            paths.extend((path, Path(str(path) + ".bak")))
    for offset in range(3):
        day = (now + timedelta(days=offset)).date()
        paths.append(base_dir / "state" / f"daily_{day.isoformat()}.json")
    start_hour = int(cfg.get("operational_day_start_hour", 4) or 4)
    start = datetime.combine(now.date(), datetime.min.time())
    source_days = {
        resource_history.operational_date(start, start_hour),
        resource_history.operational_date(start + timedelta(days=1, seconds=-1), start_hour),
    }
    paths.extend(resource_history.history_path(base_dir, day, start_hour) for day in source_days)
    values = []
    for path in paths:
        try:
            stat = path.stat()
            values.append((str(path), int(stat.st_mtime_ns), int(stat.st_size)))
        except OSError:
            values.append((str(path), None, None))
    return (now.strftime("%Y-%m-%d %H:%M"), tuple(values))


def load_dashboard_snapshot(base_dir: Path, now: datetime | None = None) -> DashboardSnapshot:
    base_dir = Path(base_dir)
    now = now or datetime.now()
    cfg = _read_mapping(config_path(base_dir))
    # Keep recently due occurrences visible while the Agent confirms whether
    # they actually started.  Starting exactly at ``now`` made a 04:00 row
    # disappear from the dashboard at 04:00:01.
    rows, _meta = effective_occurrences(
        base_dir,
        cfg,
        now - timedelta(hours=2),
        now + timedelta(hours=36),
        include_skipped=True,
    )
    state_dir = base_dir / "state"
    run_state = _read_mapping(state_dir / "schedule_run_status.json")
    run_records = run_state.get("runs") if isinstance(run_state.get("runs"), dict) else run_state
    daily_states = {
        day: _read_mapping(state_dir / f"daily_{day.isoformat()}.json")
        for day in {row["datetime"].date() for row in rows}
    }
    decorated = decorate_schedule_rows(
        rows,
        run_records,
        daily_states,
        set(),
        now,
        float(cfg.get("scheduled_run_grace_minutes", 15) or 15),
    )[:16]
    aliases = {str(key): str(value).strip() for key, value in (cfg.get("maa_instance_aliases") or {}).items()}
    for row in decorated:
        label = str(row.get("label") or "")
        row["display_label"] = f"{aliases[label]}（{label}）" if aliases.get(label) else label
    start_hour = int(cfg.get("operational_day_start_hour", 4) or 4)
    resource_rows = resource_history.load_calendar_day(base_dir, now.date(), start_hour)
    return DashboardSnapshot(tuple(decorated), tuple(dict(row) for row in resource_rows), now.date())


class _DashboardSignals(QObject):
    done = Signal(object)
    failed = Signal(str)


class _DashboardWorker(QRunnable):
    def __init__(self, base_dir: Path, previous_signature: tuple | None):
        super().__init__()
        self.base_dir = base_dir
        self.previous_signature = previous_signature
        self.signals = _DashboardSignals()

    def run(self) -> None:
        try:
            signature = dashboard_source_signature(self.base_dir)
            snapshot = None if signature == self.previous_signature else load_dashboard_snapshot(self.base_dir)
            self.signals.done.emit((signature, snapshot))
        except Exception as exc:
            self.signals.failed.emit(str(exc))


class DashboardService(QObject):
    loaded = Signal(object)
    failed = Signal(str)

    def __init__(self, base_dir: Path, thread_pool: QThreadPool | None = None, parent=None):
        super().__init__(parent)
        self.base_dir = Path(base_dir)
        self.thread_pool = thread_pool or QThreadPool.globalInstance()
        self._running = False
        self._worker: _DashboardWorker | None = None
        self._signature: tuple | None = None

    def refresh(self) -> bool:
        if self._running:
            return False
        self._running = True
        worker = _DashboardWorker(self.base_dir, self._signature)
        self._worker = worker
        worker.signals.done.connect(self._finish, Qt.ConnectionType.QueuedConnection)
        worker.signals.failed.connect(self._fail, Qt.ConnectionType.QueuedConnection)
        self.thread_pool.start(worker)
        return True

    def _finish(self, result: tuple) -> None:
        self._running = False
        self._worker = None
        self._signature, snapshot = result
        if snapshot is not None:
            self.loaded.emit(snapshot)

    def _fail(self, message: str) -> None:
        self._running = False
        self._worker = None
        self.failed.emit(message)
