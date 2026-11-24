from __future__ import annotations

import json
import os
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


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
        self._page_state: dict[str, dict] = {}
        self._writer_pool = QThreadPool(self)
        self._writer_pool.setMaxThreadCount(1)

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
        return self._page_state.setdefault(page, {})

    def value(self, key: str, default=None):
        return self._data.get(key, default)

    def set_value(self, key: str, value) -> None:
        if self._data.get(key) == value:
            return
        self._data[key] = value
        self._save()

    def _save(self) -> None:
        self._writer_pool.start(_StateWriteWorker(self.path, dict(self._data)))
