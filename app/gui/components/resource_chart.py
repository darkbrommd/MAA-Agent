from __future__ import annotations

from datetime import date, datetime, timedelta

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QToolTip, QWidget

from gui.themes.tokens import DARK, LIGHT, ThemeTokens
from resource_history import memory_values


class ResourceChart(QWidget):
    snapshot_activated = Signal(object)

    def __init__(self, theme: str = "dark", parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumHeight(200)
        self._tokens: ThemeTokens = DARK if theme == "dark" else LIGHT
        self._rows: tuple[dict, ...] = ()
        self._day = date.today()
        self._pixmap = QPixmap()
        self._hover_index: int | None = None

    def set_theme(self, theme: str) -> None:
        self._tokens = DARK if theme == "dark" else LIGHT
        self._rebuild_cache()
        self.update()

    def set_data(self, rows, day: date) -> None:
        self._rows = tuple(dict(row) for row in rows)
        self._day = day
        self._hover_index = None
        self._rebuild_cache()
        self.update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._rebuild_cache()

    def _plot_rect(self) -> QRectF:
        return QRectF(58, 18, max(20, self.width() - 78), max(20, self.height() - 62))

    def _x_for_row(self, row: dict, plot: QRectF) -> float:
        start = datetime.combine(self._day, datetime.min.time()).timestamp()
        ratio = (float(row.get("epoch", start)) - start) / 86400.0
        return plot.left() + max(0.0, min(1.0, ratio)) * plot.width()

    def _rebuild_cache(self) -> None:
        if self.width() <= 1 or self.height() <= 1:
            return
        ratio = max(1.0, self.devicePixelRatioF())
        pixmap = QPixmap(round(self.width() * ratio), round(self.height() * ratio))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(QColor(self._tokens.card))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        plot = self._plot_rect()
        painter.setPen(QPen(QColor(self._tokens.border), 1))
        for step in range(5):
            y = plot.top() + plot.height() * step / 4
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(QColor(self._tokens.muted))
            painter.setFont(QFont("Segoe UI", 8))
            painter.drawText(QRectF(4, y - 9, 48, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{100-step*25}%")
            painter.setPen(QPen(QColor(self._tokens.border), 1))
        for hour in (0, 6, 12, 18, 24):
            x = plot.left() + plot.width() * hour / 24
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            painter.setPen(QColor(self._tokens.muted))
            painter.drawText(QRectF(x - 22, plot.bottom() + 5, 44, 20), Qt.AlignmentFlag.AlignCenter, f"{hour:02d}:00")
            painter.setPen(QPen(QColor(self._tokens.border), 1))
        if self._rows:
            memory = QPolygonF()
            cpu = QPolygonF()
            for row in self._rows:
                x = self._x_for_row(row, plot)
                memory.append(QPointF(x, plot.bottom() - plot.height() * max(0, min(100, float(row.get("ram", 0)))) / 100))
                cpu.append(QPointF(x, plot.bottom() - plot.height() * max(0, min(100, float(row.get("cpu", 0)))) / 100))
            painter.setPen(QPen(QColor(self._tokens.accent), 2))
            painter.drawPolyline(memory)
            painter.setPen(QPen(QColor(self._tokens.warning), 1.5))
            painter.drawPolyline(cpu)
            painter.setPen(QColor(self._tokens.accent))
            painter.drawText(QRectF(plot.left(), 0, 70, 18), "内存")
            painter.setPen(QColor(self._tokens.warning))
            painter.drawText(QRectF(plot.left() + 72, 0, 70, 18), "CPU")
        else:
            painter.setPen(QColor(self._tokens.muted))
            painter.drawText(plot, Qt.AlignmentFlag.AlignCenter, "这一天还没有资源记录")
        painter.end()
        self._pixmap = pixmap

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._pixmap)
        if self._hover_index is None or not 0 <= self._hover_index < len(self._rows):
            return
        plot = self._plot_rect()
        row = self._rows[self._hover_index]
        x = self._x_for_row(row, plot)
        painter.setPen(QPen(QColor(self._tokens.text), 1, Qt.PenStyle.DashLine))
        painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
        y = plot.bottom() - plot.height() * max(0, min(100, float(row.get("ram", 0)))) / 100
        painter.setBrush(QColor(self._tokens.accent))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(x, y), 4, 4)

    def _nearest_index(self, x: float) -> int | None:
        if not self._rows:
            return None
        plot = self._plot_rect()
        return min(range(len(self._rows)), key=lambda index: abs(self._x_for_row(self._rows[index], plot) - x))

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self._hover_index = self._nearest_index(event.position().x())
        if self._hover_index is not None:
            row = self._rows[self._hover_index]
            when = datetime.fromtimestamp(float(row.get("epoch", 0)))
            used, available, _total = memory_values(row)
            QToolTip.showText(event.globalPosition().toPoint(), f"{when:%H:%M}\n内存 {row.get('ram', 0):.1f}% · CPU {row.get('cpu', 0):.1f}%\n已用 {used/1024:.1f} GB · 可用 {available/1024:.1f} GB", self)
        self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover_index = None
        QToolTip.hideText()
        self.update()
        super().leaveEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        index = self._nearest_index(event.position().x())
        if index is not None:
            self.snapshot_activated.emit(self._rows[index])
        super().mouseDoubleClickEvent(event)
