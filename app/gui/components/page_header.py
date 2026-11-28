from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget


HEADER_HEIGHT = 42


class PageHeader(QWidget):
    """One-line page header whose title and actions share one visual baseline."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setFixedHeight(HEADER_HEIGHT)
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(8)
        self.title = QLabel(title)
        self.title.setObjectName("PageTitle")
        self._normalize(self.title)
        self.layout.addWidget(self.title)
        self.layout.addStretch(1)

    def add_action(self, widget: QWidget) -> None:
        self._normalize(widget)
        self.layout.addWidget(widget)

    @staticmethod
    def _normalize(widget: QWidget) -> None:
        widget.setFixedHeight(HEADER_HEIGHT)
        if isinstance(widget, QLabel):
            widget.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
