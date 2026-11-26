from __future__ import annotations

import calendar
from datetime import date

from PySide6.QtCore import QDate, QPoint, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class _CalendarDayButton(QPushButton):
    _COLORS = {"warning": "#E3B341", "normal": "#38B880", "none": "#6E7681"}

    def __init__(self, text: str, marker: str = "", parent=None):
        super().__init__(text, parent)
        self.marker = marker

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        color = self._COLORS.get(self.marker)
        if not color:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        painter.drawEllipse(self.width() // 2 - 3, self.height() - 8, 6, 6)


class _CalendarPanel(QFrame):
    date_selected = Signal(object)
    month_changed = Signal(int, int)

    def __init__(self, selected: date, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("Card")
        self.setFixedSize(420, 390)
        self._selected = selected
        self._year = selected.year
        self._month = selected.month
        self._year_view = False
        self._markers: dict[date, str] = {}
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(16, 16, 16, 16)
        self._header = QHBoxLayout()
        previous = QToolButton(text="‹")
        next_button = QToolButton(text="›")
        self.title = QPushButton()
        self.title.setFlat(True)
        previous.clicked.connect(lambda: self._shift(-1))
        next_button.clicked.connect(lambda: self._shift(1))
        self.title.clicked.connect(self._toggle_view)
        self._header.addWidget(previous)
        self._header.addWidget(self.title, 1)
        self._header.addWidget(next_button)
        self._layout.addLayout(self._header)
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 4, 0, 0)
        self.grid.setSpacing(4)
        self._layout.addWidget(self.grid_host, 1)
        footer = QHBoxLayout()
        today_button = QPushButton("回到今天")
        today_button.clicked.connect(lambda: self._choose(date.today()))
        footer.addWidget(today_button)
        footer.addStretch(1)
        self._layout.addLayout(footer)
        self._draw()

    def set_date(self, selected: date) -> None:
        self._selected = selected
        self._year = selected.year
        self._month = selected.month
        self._year_view = False
        self._draw()

    def set_markers(self, markers: dict[date, str]) -> None:
        self._markers.update(markers)
        if not self._year_view:
            self._draw()

    def _clear(self) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()

    def _toggle_view(self) -> None:
        self._year_view = not self._year_view
        self._draw()

    def _shift(self, delta: int) -> None:
        if self._year_view:
            self._year += delta
        else:
            value = QDate(self._year, self._month, 1).addMonths(delta)
            self._year, self._month = value.year(), value.month()
        self._draw()
        if not self._year_view:
            self.month_changed.emit(self._year, self._month)

    def _draw(self) -> None:
        self._clear()
        if self._year_view:
            self.title.setText(f"{self._year} 年")
            for month in range(1, 13):
                button = QPushButton(f"{month} 月")
                button.setCheckable(True)
                button.setChecked(month == self._month and self._year == self._selected.year)
                button.clicked.connect(lambda _checked=False, value=month: self._pick_month(value))
                self.grid.addWidget(button, (month - 1) // 3, (month - 1) % 3)
            return
        self.title.setText(f"{self._year} 年 {self._month} 月")
        for column, text in enumerate(("一", "二", "三", "四", "五", "六", "日")):
            label = QLabel(text, alignment=Qt.AlignmentFlag.AlignCenter)
            label.setObjectName("MutedLabel")
            self.grid.addWidget(label, 0, column)
        weeks = calendar.Calendar(firstweekday=0).monthdayscalendar(self._year, self._month)
        for row_index, week in enumerate(weeks, start=1):
            for column, day_number in enumerate(week):
                if not day_number:
                    self.grid.addWidget(QLabel(""), row_index, column)
                    continue
                current = date(self._year, self._month, day_number)
                button = _CalendarDayButton(str(day_number), self._markers.get(current, ""))
                button.setCheckable(True)
                button.setChecked(current == self._selected)
                button.clicked.connect(lambda _checked=False, value=day_number: self._choose(date(self._year, self._month, value)))
                self.grid.addWidget(button, row_index, column)

    def _pick_month(self, month: int) -> None:
        self._month = month
        self._year_view = False
        self._draw()
        self.month_changed.emit(self._year, self._month)

    def _choose(self, selected: date) -> None:
        self._selected = selected
        self.date_selected.emit(selected)
        self.close()


class DatePicker(QWidget):
    date_changed = Signal(object)
    month_changed = Signal(int, int)

    def __init__(self, selected: date | None = None, parent=None):
        super().__init__(parent)
        self._date = selected or date.today()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.button = QPushButton()
        self.button.clicked.connect(self.open_calendar)
        layout.addWidget(self.button)
        self._popup = _CalendarPanel(self._date, self)
        self._popup.date_selected.connect(self.set_date)
        self._popup.month_changed.connect(self.month_changed)
        self._update_text()

    def date(self) -> date:
        return self._date

    def set_date(self, selected: date, emit: bool = True) -> None:
        if not isinstance(selected, date):
            return
        changed = selected != self._date
        self._date = selected
        self._popup.set_date(selected)
        self._update_text()
        if emit and changed:
            self.date_changed.emit(selected)

    def _update_text(self) -> None:
        self.button.setText(f"{self._date:%Y-%m-%d}  ▾")

    def set_markers(self, markers: dict[date, str]) -> None:
        self._popup.set_markers(markers)

    def open_calendar(self) -> None:
        self._popup.set_date(self._date)
        self.month_changed.emit(self._date.year, self._date.month)
        window = self.window()
        center = window.mapToGlobal(window.rect().center())
        point = QPoint(center.x() - self._popup.width() // 2, center.y() - self._popup.height() // 2)
        self._popup.move(point)
        self._popup.show()
        self._popup.raise_()
