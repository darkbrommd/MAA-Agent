"""Asynchronous, cached access to resource-history calendar days.

The service keeps all filesystem work in the Qt thread pool. Calling
request_day only advances request state and (re)starts the debounce timer,
so the GUI thread never reads JSONL files or stats history paths.
"""

from __future__ import annotations

import threading
import sys
from collections import OrderedDict
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, Signal

try:
    import resource_history
except ImportError:  # Support imports as app.gui.services.resource_service.
    app_dir = str(Path(__file__).resolve().parents[2])
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)
    import resource_history


Loader = Callable[[Path, date, int], list[dict]]
CacheKey = tuple[date, int]


@dataclass(frozen=True)
class ResourceSnapshot:
    day: date
    rows: tuple[dict, ...]
    signature: tuple
    from_cache: bool = False


def _calendar_date(value: date) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raise TypeError("day must be a datetime.date")


def _source_days(day: date, start_hour: int) -> tuple[date, ...]:
    """Return the legacy operational files used by load_calendar_day."""
    start_dt = datetime.combine(day, time.min)
    start = start_dt.timestamp()
    end = (start_dt + timedelta(days=1)).timestamp()
    return tuple(
        sorted(
            {
                resource_history.operational_date(
                    datetime.fromtimestamp(start), int(start_hour)
                ),
                resource_history.operational_date(
                    datetime.fromtimestamp(end - 1), int(start_hour)
                ),
            }
        )
    )


def resource_file_signature(
    base_dir: Path, day: date, start_hour: int = 4
) -> tuple:
    """Describe every JSONL source file that can contribute to a day.

    Missing files are represented with zero metadata so their later creation
    changes the signature. Entries are sorted by the resolved path string to
    keep cache validation deterministic.
    """
    calendar_day = _calendar_date(day)
    paths = {
        resource_history.history_path(base_dir, source_day, int(start_hour))
        .resolve()
        for source_day in _source_days(calendar_day, int(start_hour))
    }
    entries = []
    for path in sorted(paths, key=lambda value: str(value)):
        try:
            stat = path.stat()
            mtime_ns = int(stat.st_mtime_ns)
            size = int(stat.st_size)
        except OSError:
            mtime_ns = 0
            size = 0
        entries.append((str(path), mtime_ns, size))
    return tuple(entries)


@dataclass(frozen=True)
class _LoadOutcome:
    task_serial: int
    request_id: int
    start_hour: int
    emit_result: bool
    snapshot: ResourceSnapshot | None = None
    error: str | None = None


class _TaskSignals(QObject):
    finished = Signal(object)


class _LoadTask(QRunnable):
    def __init__(
        self,
        service: "ResourceService",
        task_serial: int,
        request_id: int,
        day: date,
        start_hour: int,
        emit_result: bool,
    ) -> None:
        super().__init__()
        self._service = service
        self._task_serial = task_serial
        self._request_id = request_id
        self._day = day
        self._start_hour = start_hour
        self._emit_result = emit_result
        self.signals = _TaskSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            snapshot = self._service._load_snapshot(
                self._day, self._start_hour
            )
            outcome = _LoadOutcome(
                task_serial=self._task_serial,
                request_id=self._request_id,
                start_hour=self._start_hour,
                emit_result=self._emit_result,
                snapshot=snapshot,
            )
        except Exception as exc:
            outcome = _LoadOutcome(
                task_serial=self._task_serial,
                request_id=self._request_id,
                start_hour=self._start_hour,
                emit_result=self._emit_result,
                error=f"{type(exc).__name__}: {exc}",
            )
        self.signals.finished.emit(outcome)


class ResourceService(QObject):
    """Load resource snapshots without blocking the UI thread."""

    loaded = Signal(int, object)
    failed = Signal(int, str)

    def __init__(
        self,
        base_dir: Path,
        *,
        thread_pool: QThreadPool | None = None,
        debounce_ms: int = 100,
        cache_days: int = 7,
        loader=None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._base_dir = Path(base_dir)
        self._thread_pool = (
            thread_pool if thread_pool is not None else QThreadPool.globalInstance()
        )
        self._debounce_ms = max(0, int(debounce_ms))
        self._cache_limit = max(0, int(cache_days))
        self._loader: Loader = (
            loader
            if loader is not None
            else resource_history.load_calendar_day
        )

        self._cache: OrderedDict[CacheKey, ResourceSnapshot] = OrderedDict()
        self._cache_lock = threading.RLock()
        self._current_request_id = 0
        self._pending: tuple[int, date, int] | None = None
        self._task_serial = 0
        self._active_tasks: dict[int, _LoadTask] = {}

        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.timeout.connect(self._start_pending)

    @property
    def current_request_id(self) -> int:
        return self._current_request_id

    def request_day(self, day: date, start_hour: int = 4) -> int:
        self._current_request_id += 1
        request_id = self._current_request_id
        self._pending = (
            request_id,
            _calendar_date(day),
            int(start_hour),
        )
        self._debounce_timer.start(self._debounce_ms)
        return request_id

    def cached_snapshot(
        self, day: date, start_hour: int = 4
    ) -> ResourceSnapshot | None:
        key = (_calendar_date(day), int(start_hour))
        with self._cache_lock:
            snapshot = self._cache.get(key)
            if snapshot is None:
                return None
            self._cache.move_to_end(key)
            return replace(snapshot, from_cache=True)

    def clear_cache(self) -> None:
        with self._cache_lock:
            self._cache.clear()

    def _start_pending(self) -> None:
        pending = self._pending
        self._pending = None
        if pending is None:
            return
        request_id, day, start_hour = pending
        if request_id != self._current_request_id:
            return
        self._start_load(request_id, day, start_hour, emit_result=True)

    def _start_load(
        self,
        request_id: int,
        day: date,
        start_hour: int,
        *,
        emit_result: bool,
    ) -> None:
        self._task_serial += 1
        task_serial = self._task_serial
        task = _LoadTask(
            self,
            task_serial,
            request_id,
            day,
            start_hour,
            emit_result,
        )
        self._active_tasks[task_serial] = task
        task.signals.finished.connect(
            self._on_outcome, Qt.ConnectionType.QueuedConnection
        )
        self._thread_pool.start(task)

    def _load_snapshot(self, day: date, start_hour: int) -> ResourceSnapshot:
        key = (day, start_hour)
        signature_before = resource_file_signature(
            self._base_dir, day, start_hour
        )
        cached = self._cached_for_signature(key, signature_before)
        if cached is not None:
            return replace(cached, from_cache=True)

        rows = self._loader(self._base_dir, day, start_hour)
        snapshot = ResourceSnapshot(
            day=day,
            rows=tuple(rows or ()),
            signature=resource_file_signature(
                self._base_dir, day, start_hour
            ),
        )
        if snapshot.signature == signature_before:
            self._store_cached(key, snapshot)
        return snapshot

    def _cached_for_signature(
        self, key: CacheKey, signature: tuple
    ) -> ResourceSnapshot | None:
        with self._cache_lock:
            snapshot = self._cache.get(key)
            if snapshot is None:
                return None
            if snapshot.signature != signature:
                self._cache.pop(key, None)
                return None
            self._cache.move_to_end(key)
            return snapshot

    def _store_cached(self, key: CacheKey, snapshot: ResourceSnapshot) -> None:
        if self._cache_limit <= 0:
            return
        with self._cache_lock:
            self._cache.pop(key, None)
            self._cache[key] = replace(snapshot, from_cache=False)
            while len(self._cache) > self._cache_limit:
                self._cache.popitem(last=False)

    def _on_outcome(self, outcome: _LoadOutcome) -> None:
        self._active_tasks.pop(outcome.task_serial, None)
        if not outcome.emit_result:
            return
        if outcome.request_id != self._current_request_id:
            return
        if outcome.error is not None:
            self.failed.emit(outcome.request_id, outcome.error)
            return
        if outcome.snapshot is None:
            self.failed.emit(outcome.request_id, "资源快照为空")
            return

        self.loaded.emit(outcome.request_id, outcome.snapshot)
        self._prefetch_adjacent(
            outcome.snapshot.day, outcome.start_hour
        )

    def _prefetch_adjacent(self, day: date, start_hour: int) -> None:
        for offset in (-1, 1):
            self._start_load(
                0,
                day + timedelta(days=offset),
                start_hour,
                emit_result=False,
            )
