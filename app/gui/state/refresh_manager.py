from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QObject, QTimer


@dataclass
class RefreshRegistration:
    page: str
    timer: QTimer
    callback: Callable[[], None]


class RefreshManager(QObject):
    """Runs only refresh jobs that belong to the visible page."""

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._active_page = ""
        self._window_visible = True
        self._registrations: list[RefreshRegistration] = []

    def register(self, page: str, interval_ms: int, callback: Callable[[], None]) -> QTimer:
        timer = QTimer(self)
        timer.setInterval(max(100, int(interval_ms)))
        timer.timeout.connect(callback)
        registration = RefreshRegistration(page, timer, callback)
        self._registrations.append(registration)
        self._sync_timer(registration)
        return timer

    def set_active_page(self, page: str) -> None:
        self._active_page = page
        self._sync_all()

    def set_window_visible(self, visible: bool) -> None:
        self._window_visible = bool(visible)
        self._sync_all()

    def trigger_active(self) -> None:
        if not self._window_visible:
            return
        for registration in self._registrations:
            if registration.page == self._active_page:
                registration.callback()

    def _sync_all(self) -> None:
        for registration in self._registrations:
            self._sync_timer(registration)

    def _sync_timer(self, registration: RefreshRegistration) -> None:
        should_run = self._window_visible and registration.page == self._active_page
        if should_run and not registration.timer.isActive():
            registration.timer.start()
        elif not should_run and registration.timer.isActive():
            registration.timer.stop()
