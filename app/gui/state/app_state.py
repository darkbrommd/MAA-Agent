from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal


class _PageState(dict):
    def __init__(self, values: dict, changed):
        super().__init__(values)
        self._changed = changed

    def __setitem__(self, key, value) -> None:
        if self.get(key) == value:
            return
        super().__setitem__(key, value)
        self._changed()


class _StateWriteWorker(QRunnable):
    def __init__(self, path: Path, data: dict):
        super().__init__()
        self.path = path
        self.data = data

    def run(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)


class AppState(QObject):
    theme_changed = Signal(str)

    def __init__(self, base_dir: Path, parent: QObject | None = None):
        super().__init__(parent)
        self.base_dir = Path(base_dir)
        self.path = self.base_dir / "state" / "gui_state.json"
        self._data = self._load()
        self._page_state: dict[str, _PageState] = {}
        self._writer_pool = QThreadPool(self)
        self._writer_pool.setMaxThreadCount(1)
        self._page_save_timer = QTimer(self)
        self._page_save_timer.setSingleShot(True)
        self._page_save_timer.setInterval(220)
        self._page_save_timer.timeout.connect(self._save)

    def _load(self) -> dict:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {}

    @property
    def theme(self) -> str:
        return "light" if self._data.get("theme") == "light" else "dark"

    def set_theme(self, theme: str) -> None:
        theme = "light" if theme == "light" else "dark"
        if theme == self.theme:
            return
        self._data["theme"] = theme
        self._save()
        self.theme_changed.emit(theme)

    def page_state(self, page: str) -> dict:
        state = self._page_state.get(page)
        if state is not None:
            return state
        pages = self._data.get("pages")
        stored = pages.get(page, {}) if isinstance(pages, dict) else {}
        state = _PageState(stored if isinstance(stored, dict) else {}, lambda: self._page_changed(page))
        self._page_state[page] = state
        return state

    def _page_changed(self, page: str) -> None:
        pages = self._data.setdefault("pages", {})
        pages[page] = dict(self._page_state[page])
        self._page_save_timer.start()

    def value(self, key: str, default=None):
        return self._data.get(key, default)

    def set_value(self, key: str, value) -> None:
        if self._data.get(key) == value:
            return
        self._data[key] = value
        self._save()

    def _save(self) -> None:
        self._writer_pool.start(_StateWriteWorker(self.path, deepcopy(self._data)))

    def flush(self) -> None:
        if self._page_save_timer.isActive():
            self._page_save_timer.stop()
            self._save()
        self._writer_pool.waitForDone(1500)
