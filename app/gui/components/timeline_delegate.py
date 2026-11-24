from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEvent, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from gui.themes.tokens import DARK, LIGHT, ThemeTokens
from gui.models.schedule_model import ScheduleModel


@dataclass(frozen=True)
class CardLayout:
    card: QRectF
    time: QRectF
    title: QRectF
    subtitle: QRectF
    status: QRectF
    error_button: QRectF


def compute_card_layout(rect: QRect | QRectF, item: dict) -> CardLayout:
    bounds = QRectF(rect).adjusted(8, 5, -8, -5)
    left = bounds.left() + 16
    time_width = 78
    action_width = 100 if item.get("state") == "failed" else 0
    right = bounds.right() - 14
    text_left = left + time_width + 14
    text_right = right - action_width - (12 if action_width else 0)
    return CardLayout(
        card=bounds,
        time=QRectF(left, bounds.top() + 14, time_width, 30),
        title=QRectF(text_left, bounds.top() + 12, max(20, text_right - text_left), 25),
        subtitle=QRectF(text_left, bounds.top() + 40, max(20, text_right - text_left), 23),
        status=QRectF(text_left, bounds.top() + 65, max(20, text_right - text_left), 22),
        error_button=QRectF(right - action_width, bounds.center().y() - 17, action_width, 34) if action_width else QRectF(),
    )


class TimelineDelegate(QStyledItemDelegate):
    error_requested = Signal(object)

    def __init__(self, theme: str = "dark", parent=None):
        super().__init__(parent)
        self.tokens: ThemeTokens = DARK if theme == "dark" else LIGHT

    def set_theme(self, theme: str) -> None:
        self.tokens = DARK if theme == "dark" else LIGHT

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:
        return QSize(max(320, option.rect.width()), 112)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        item = index.data(ScheduleModel.ItemRole) or {}
        layout = compute_card_layout(option.rect, item)
        tokens = self.tokens
        state = item.get("state", "future")
        background = tokens.card
        accent = tokens.muted
        if state == "failed":
            background, accent = tokens.failure_card, tokens.danger
        elif state == "running":
            accent = tokens.warning
        elif state == "next":
            accent = tokens.success
        elif state in {"done", "off"}:
            accent = tokens.ended
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor(tokens.border), 1))
        painter.setBrush(QColor(background))
        painter.drawRoundedRect(layout.card, 10, 10)
        if option.state & QStyle.StateFlag.State_Selected:
            painter.setPen(QPen(QColor(tokens.accent), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(layout.card.adjusted(1, 1, -1, -1), 9, 9)
        painter.setPen(QColor(accent))
        font = QFont("Segoe UI", 16, QFont.Weight.DemiBold)
        painter.setFont(font)
        when = item.get("datetime")
        painter.drawText(layout.time, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, when.strftime("%H:%M") if when else "--:--")
        painter.setPen(QColor(tokens.text))
        painter.setFont(QFont("Microsoft YaHei UI", 11, QFont.Weight.DemiBold))
        painter.drawText(layout.title, Qt.AlignmentFlag.AlignVCenter, str(item.get("label") or "计划"))
        painter.setPen(QColor(tokens.muted))
        painter.setFont(QFont("Microsoft YaHei UI", 9))
        day_text = when.strftime("%Y-%m-%d") if when else ""
        config = str(item.get("config") or "默认任务")
        painter.drawText(layout.subtitle, Qt.AlignmentFlag.AlignVCenter, f"{day_text} · {config}")
        painter.setPen(QColor(accent))
        painter.drawText(layout.status, Qt.AlignmentFlag.AlignVCenter, f"{item.get('status_title', '')} · {item.get('status_detail', '')}")
        if not layout.error_button.isEmpty():
            painter.setPen(QPen(QColor(tokens.danger), 1))
            painter.setBrush(QColor(tokens.failure_card))
            painter.drawRoundedRect(layout.error_button, 7, 7)
            painter.setPen(QColor(tokens.danger))
            painter.drawText(layout.error_button, Qt.AlignmentFlag.AlignCenter, "查看错误")
        painter.restore()

    def editorEvent(self, event, model, option, index) -> bool:
        if event.type() == QEvent.Type.MouseButtonRelease:
            item = index.data(ScheduleModel.ItemRole) or {}
            layout = compute_card_layout(option.rect, item)
            if not layout.error_button.isEmpty() and layout.error_button.contains(event.position()):
                self.error_requested.emit(item)
                return True
        return super().editorEvent(event, model, option, index)
