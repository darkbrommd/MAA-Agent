from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal

from news_monitor import load_news_cache, refresh_news_cache
from project_paths import config_path


@dataclass(frozen=True)
class NewsSnapshot:
    items: tuple[dict, ...]
    updated_at: str
    status: str
    new_count: int = 0
    total: int = 0
    errors: tuple[str, ...] = ()


def _snapshot(payload: dict) -> NewsSnapshot:
    items = tuple(dict(item) for item in (payload.get("items") or ()) if isinstance(item, dict))
    return NewsSnapshot(
        items=items,
        updated_at=str(payload.get("updated_at") or "尚未更新"),
        status=str(payload.get("status") or "local"),
        new_count=int(payload.get("new_count") or 0),
        total=int(payload.get("total") or len(items)),
        errors=tuple(str(value) for value in (payload.get("errors") or ())),
    )


class _NewsSignals(QObject):
    done = Signal(int, object)
    failed = Signal(int, str)
    progress = Signal(int, int, str)


class _NewsWorker(QRunnable):
    def __init__(self, request_id: int, base_dir: Path, mode: str):
        super().__init__()
        self.request_id = request_id
        self.base_dir = base_dir
        self.mode = mode
        self.signals = _NewsSignals()

    def run(self) -> None:
        try:
            try:
                cfg = json.loads(config_path(self.base_dir).read_text(encoding="utf-8-sig"))
            except (FileNotFoundError, OSError, json.JSONDecodeError):
                cfg = {}
            if self.mode == "local":
                payload = load_news_cache(self.base_dir, cfg)
            else:
                payload = refresh_news_cache(
                    self.base_dir,
                    cfg,
                    progress=lambda current, total, message: self.signals.progress.emit(
                        int(current), int(total), str(message)
                    ),
                    full=self.mode == "full",
                )
            self.signals.done.emit(self.request_id, _snapshot(payload))
        except Exception as exc:
            self.signals.failed.emit(self.request_id, str(exc))


class NewsService(QObject):
    loaded = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int, str)
    busy_changed = Signal(bool)

    def __init__(self, base_dir: Path, thread_pool: QThreadPool | None = None, parent=None):
        super().__init__(parent)
        self.base_dir = Path(base_dir)
        self.thread_pool = thread_pool or QThreadPool.globalInstance()
        self._request_id = 0
        self._active: dict[int, _NewsWorker] = {}
        self._busy = False

    def load_local(self) -> bool:
        return self._start("local")

    def refresh(self, *, full: bool = False) -> bool:
        return self._start("full" if full else "incremental")

    def _start(self, mode: str) -> bool:
        if self._busy:
            return False
        self._busy = True
        self.busy_changed.emit(True)
        self._request_id += 1
        worker = _NewsWorker(self._request_id, self.base_dir, mode)
        self._active[self._request_id] = worker
        worker.signals.done.connect(self._finish, Qt.ConnectionType.QueuedConnection)
        worker.signals.failed.connect(self._fail, Qt.ConnectionType.QueuedConnection)
        worker.signals.progress.connect(self.progress, Qt.ConnectionType.QueuedConnection)
        self.thread_pool.start(worker)
        return True

    def _finish(self, request_id: int, snapshot: NewsSnapshot) -> None:
        self._active.pop(request_id, None)
        if request_id != self._request_id:
            return
        self._busy = False
        self.busy_changed.emit(False)
        self.loaded.emit(snapshot)

    def _fail(self, request_id: int, message: str) -> None:
        self._active.pop(request_id, None)
        if request_id != self._request_id:
            return
        self._busy = False
        self.busy_changed.emit(False)
        self.failed.emit(message)
