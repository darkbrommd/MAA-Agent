from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal


INCIDENT_KEYWORDS = (
    "error", "fail", "exception", "warning", "critical", "alert", "missing",
    "未响应", "缺失", "失败", "异常", "报警", "恢复", "维护公告", "diagnostic",
)
ERROR_KEYWORDS = ("error", "fail", "exception", "warning", "critical", "失败", "异常", "未响应")


@dataclass(frozen=True)
class LogChunk:
    request_id: int
    filename: str
    text: str
    reset: bool
    offset: int
    identity: tuple[int, int] | None
    anchor: bytes
    exists: bool


def line_matches(line: str, mode: str, query: str = "") -> bool:
    lowered = line.lower()
    if mode == "错误与警告" and not any(key in lowered for key in ERROR_KEYWORDS):
        return False
    if mode == "事故与恢复" and not any(key in lowered for key in INCIDENT_KEYWORDS):
        return False
    return not query or query.lower() in lowered


def read_log_chunk(
    path: Path,
    *,
    request_id: int = 0,
    offset: int = 0,
    identity: tuple[int, int] | None = None,
    anchor: bytes = b"",
    initialized: bool = False,
    max_initial_bytes: int = 300_000,
) -> LogChunk:
    path = Path(path)
    try:
        stat = path.stat()
    except FileNotFoundError:
        reset = not initialized or identity is not None or bool(offset)
        return LogChunk(request_id, path.name, "", reset, 0, None, b"", False)
    # ctime changes on every write on POSIX, so it cannot identify one growing file.
    current_identity = (int(getattr(stat, "st_dev", 0)), int(getattr(stat, "st_ino", 0)))
    reset = not initialized or identity != current_identity or int(offset) > int(stat.st_size)
    if not reset and anchor and offset:
        with path.open("rb") as handle:
            handle.seek(max(0, int(offset) - len(anchor)))
            reset = handle.read(len(anchor)) != anchor
    start = max(0, int(stat.st_size) - int(max_initial_bytes)) if reset else max(0, int(offset))
    with path.open("rb") as handle:
        handle.seek(start)
        raw = handle.read()
        end = handle.tell()
        anchor_size = min(64, end)
        handle.seek(end - anchor_size)
        next_anchor = handle.read(anchor_size)
    text = raw.decode("utf-8", errors="replace")
    if reset and start > 0:
        first_break = text.find("\n")
        text = text[first_break + 1 :] if first_break >= 0 else ""
    return LogChunk(request_id, path.name, text, reset, end, current_identity, next_anchor, True)


class _LogSignals(QObject):
    done = Signal(object)
    failed = Signal(int, str)


class _LogWorker(QRunnable):
    def __init__(self, request_id: int, path: Path, state: tuple[int, tuple[int, int] | None, bytes, bool]):
        super().__init__()
        self.request_id = request_id
        self.path = path
        self.state = state
        self.signals = _LogSignals()

    def run(self) -> None:
        try:
            offset, identity, anchor, initialized = self.state
            self.signals.done.emit(
                read_log_chunk(
                    self.path,
                    request_id=self.request_id,
                    offset=offset,
                    identity=identity,
                    anchor=anchor,
                    initialized=initialized,
                )
            )
        except Exception as exc:
            self.signals.failed.emit(self.request_id, str(exc))


class LogService(QObject):
    chunk_ready = Signal(object)
    failed = Signal(str)

    def __init__(self, base_dir: Path, thread_pool: QThreadPool | None = None, parent=None):
        super().__init__(parent)
        self.base_dir = Path(base_dir)
        self.thread_pool = thread_pool or QThreadPool.globalInstance()
        self._states: dict[str, tuple[int, tuple[int, int] | None, bytes, bool]] = {}
        self._request_id = 0
        self._active: dict[int, _LogWorker] = {}
        self._pending_filename: str | None = None

    def request(self, filename: str = "agent.log") -> int:
        safe_name = "decision.log" if filename == "decision.log" else "agent.log"
        if self._active:
            active = next(iter(self._active.values()))
            if active.path.name != safe_name:
                self._pending_filename = safe_name
            return self._request_id
        self._request_id += 1
        request_id = self._request_id
        state = self._states.get(safe_name, (0, None, b"", False))
        worker = _LogWorker(request_id, self.base_dir / "logs" / safe_name, state)
        self._active[request_id] = worker
        worker.signals.done.connect(self._finish, Qt.ConnectionType.QueuedConnection)
        worker.signals.failed.connect(self._fail, Qt.ConnectionType.QueuedConnection)
        self.thread_pool.start(worker)
        return request_id

    def _finish(self, chunk: LogChunk) -> None:
        self._active.pop(chunk.request_id, None)
        if chunk.request_id != self._request_id:
            return
        self._states[chunk.filename] = (chunk.offset, chunk.identity, chunk.anchor, True)
        self.chunk_ready.emit(chunk)
        self._start_pending()

    def _fail(self, request_id: int, message: str) -> None:
        self._active.pop(request_id, None)
        if request_id == self._request_id:
            self.failed.emit(message)
        self._start_pending()

    def _start_pending(self) -> None:
        filename = self._pending_filename
        self._pending_filename = None
        if filename:
            self.request(filename)
