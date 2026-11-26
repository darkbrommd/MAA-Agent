from __future__ import annotations

import json
import os
import shutil
from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from project_paths import config_path


SECRET_KEYS = {"bark_key", "ai_api_key", "deepseek_api_key", "openai_api_key"}


def load_config_file(base_dir: Path) -> dict:
    path = config_path(base_dir)
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("config.json 顶层必须是对象。")
    return data


def atomic_save_config_file(base_dir: Path, data: dict) -> Path:
    if not isinstance(data, dict):
        raise ValueError("配置必须是 JSON 对象。")
    path = config_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    backup = path.with_suffix(path.suffix + ".bak")
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    temporary.write_text(payload, encoding="utf-8")
    try:
        parsed = json.loads(temporary.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("写入后的配置不是 JSON 对象。")
        if path.exists():
            shutil.copy2(path, backup)
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return path


def masked_config(data: dict) -> dict:
    result = deepcopy(data)
    for key in SECRET_KEYS:
        if key in result and result[key]:
            result[key] = "••••••••"
    return result


class _WorkerSignals(QObject):
    loaded = Signal(object)
    saved = Signal(object)
    failed = Signal(str)


class _LoadWorker(QRunnable):
    def __init__(self, base_dir: Path, signals: _WorkerSignals):
        super().__init__()
        self.base_dir = Path(base_dir)
        self.signals = signals

    def run(self) -> None:
        try:
            self.signals.loaded.emit(load_config_file(self.base_dir))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self.signals.failed.emit(str(exc))


class _SaveWorker(QRunnable):
    def __init__(self, base_dir: Path, data: dict, signals: _WorkerSignals):
        super().__init__()
        self.base_dir = Path(base_dir)
        self.data = deepcopy(data)
        self.signals = signals

    def run(self) -> None:
        try:
            path = atomic_save_config_file(self.base_dir, self.data)
            self.signals.saved.emit(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self.signals.failed.emit(str(exc))


class ConfigService(QObject):
    loaded = Signal(object)
    saved = Signal(object)
    failed = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self, base_dir: Path, parent: QObject | None = None):
        super().__init__(parent)
        self.base_dir = Path(base_dir)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._signals = _WorkerSignals(self)
        self._signals.loaded.connect(self._loaded)
        self._signals.saved.connect(self._saved)
        self._signals.failed.connect(self._failed)

    def load(self) -> None:
        self.busy_changed.emit(True)
        self._pool.start(_LoadWorker(self.base_dir, self._signals))

    def save(self, data: dict) -> None:
        self.busy_changed.emit(True)
        self._pool.start(_SaveWorker(self.base_dir, data, self._signals))

    def _loaded(self, data: dict) -> None:
        self.busy_changed.emit(False)
        self.loaded.emit(data)

    def _saved(self, path: Path) -> None:
        self.busy_changed.emit(False)
        self.saved.emit(path)

    def _failed(self, message: str) -> None:
        self.busy_changed.emit(False)
        self.failed.emit(message)

    def wait(self, timeout_ms: int = 1500) -> bool:
        return self._pool.waitForDone(timeout_ms)
