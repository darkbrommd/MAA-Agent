from __future__ import annotations

import re

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QWidget


class NoWheelComboBox(QComboBox):
    def wheelEvent(self, event) -> None:
        if not self.hasFocus():
            event.ignore()
            return
        super().wheelEvent(event)


class TimePicker(QWidget):
    changed = Signal()

    def __init__(self, value: str = "00:00", parent=None, *, add_stretch: bool = True):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.hour = NoWheelComboBox()
        self.hour.addItems([f"{number:02d}" for number in range(25)])
        self.minute = NoWheelComboBox()
        self.minute.addItems([f"{number:02d}" for number in range(60)])
        layout.addWidget(self.hour)
        layout.addWidget(QLabel("时"))
        layout.addWidget(self.minute)
        layout.addWidget(QLabel("分"))
        if add_stretch:
            layout.addStretch(1)
        self.hour.currentIndexChanged.connect(lambda _index: self.changed.emit())
        self.minute.currentIndexChanged.connect(lambda _index: self.changed.emit())
        self.set_time(value)

    def set_time(self, value: str) -> None:
        match = re.fullmatch(r"(\d{1,2}):(\d{2})", str(value or ""))
        hour, minute = (int(match.group(1)), int(match.group(2))) if match else (0, 0)
        self.hour.setCurrentIndex(max(0, min(24, hour)))
        self.minute.setCurrentIndex(max(0, min(59, minute)))

    def time(self) -> str:
        hour = 0 if self.hour.currentIndex() == 24 else self.hour.currentIndex()
        return f"{hour:02d}:{self.minute.currentIndex():02d}"
