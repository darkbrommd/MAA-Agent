from __future__ import annotations

from PySide6.QtWidgets import QFrame, QLabel, QProgressBar, QVBoxLayout


class MetricCard(QFrame):
    """Compact KPI card with a readable value embedded in a progress bar."""

    def __init__(self, title: str, metric: str = "neutral", parent=None):
        super().__init__(parent)
        self.setObjectName("MetricCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 11, 14, 12)
        layout.setSpacing(7)
        self.title = QLabel(title)
        self.title.setObjectName("MutedLabel")
        layout.addWidget(self.title)
        self.progress = QProgressBar()
        self.progress.setObjectName("MetricProgress")
        self.progress.setProperty("metric", metric)
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(True)
        self.progress.setFixedHeight(25)
        layout.addWidget(self.progress)
        self.detail = QLabel("读取中…")
        self.detail.setObjectName("MutedLabel")
        layout.addWidget(self.detail)

    def set_metric(self, value: float, text: str, detail: str = "") -> None:
        percent = max(0, min(100, round(float(value))))
        self.progress.setValue(percent)
        self.progress.setFormat(text)
        self.detail.setText(detail or text)
