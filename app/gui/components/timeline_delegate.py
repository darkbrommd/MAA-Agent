from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEvent, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from gui.models.schedule_model import ScheduleModel
from gui.themes.tokens import DARK, LIGHT, ThemeTokens


@dataclass(frozen=True)
class CardLayout:
    card: QRectF
    rail: QRectF
    toggle: QRectF
    time: QRectF
    title: QRectF
    subtitle: QRectF
    state_badge: QRectF
    status: QRectF
    error_button: QRectF
    move_button: QRectF
    clear_button: QRectF


def compute_card_layout(rect: QRect | QRectF, item: dict) -> CardLayout:
    """Return every painted and clickable rectangle from one shared calculation."""
    bounds = QRectF(rect).adjusted(8, 5, -8, -5)
    left = bounds.left() + 18
    right = bounds.right() - 14
    button_width = 92
    button_height = 28
    button_gap = 6
    button_count = 1 + int(item.get("state") == "failed") + int(bool(item.get("override")))
    buttons_height = button_count * button_height + (button_count - 1) * button_gap
    button_top = bounds.center().y() - buttons_height / 2
    button_left = right - button_width
    button_rects: list[QRectF] = []
    for offset in range(button_count):
        button_rects.append(QRectF(button_left, button_top + offset * (button_height + button_gap), button_width, button_height))
    cursor = 0
    error_button = QRectF()
    if item.get("state") == "failed":
        error_button = button_rects[cursor]
        cursor += 1
    move_button = button_rects[cursor]
    cursor += 1
    clear_button = button_rects[cursor] if item.get("override") else QRectF()

    toggle = QRectF(left, bounds.center().y() - 12, 42, 24)
    time_left = toggle.right() + 14
    time = QRectF(time_left, bounds.top() + 15, 76, 31)
    text_left = time.right() + 12
    text_right = button_left - 14
    badge_width = min(92.0, max(74.0, (text_right - text_left) * 0.28))
    state_badge = QRectF(text_left, bounds.top() + 70, badge_width, 24)
    status = QRectF(state_badge.right() + 10, bounds.top() + 70, max(20.0, text_right - state_badge.right() - 10), 24)
    return CardLayout(
        card=bounds,
        rail=QRectF(bounds.left(), bounds.top() + 8, 5, max(10, bounds.height() - 16)),
        toggle=toggle,
        time=time,
        title=QRectF(text_left, bounds.top() + 12, max(20, text_right - text_left), 25),
        subtitle=QRectF(text_left, bounds.top() + 41, max(20, text_right - text_left), 21),
        state_badge=state_badge,
        status=status,
        error_button=error_button,
        move_button=move_button,
        clear_button=clear_button,
    )


class TimelineDelegate(QStyledItemDelegate):
    error_requested = Signal(object)
    monitor_toggled = Signal(object, bool)
    move_requested = Signal(object)
    clear_requested = Signal(object)

    def __init__(self, theme: str = "dark", parent=None):
        super().__init__(parent)
        self.tokens: ThemeTokens = DARK if theme == "dark" else LIGHT

    def set_theme(self, theme: str) -> None:
        self.tokens = DARK if theme == "dark" else LIGHT

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:
        return QSize(max(520, option.rect.width()), 122)

    def _state_colors(self, state: str) -> tuple[str, str]:
        tokens = self.tokens
        if state == "failed":
            return tokens.failure_card, tokens.danger
        if state == "running":
            return tokens.running_card, tokens.warning
        if state == "next":
            return tokens.next_card, tokens.success
        if state in {"done", "off"}:
            return tokens.ended_card, tokens.ended
        return tokens.card, tokens.muted

    def _draw_button(self, painter: QPainter, rect: QRectF, text: str, color: str, background: str) -> None:
        if rect.isEmpty():
            return
        painter.setPen(QPen(QColor(color), 1))
        painter.setBrush(QColor(background))
        painter.drawRoundedRect(rect, 7, 7)
        painter.setPen(QColor(color))
        painter.setFont(QFont("Microsoft YaHei UI", 9, QFont.Weight.DemiBold))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        item = index.data(ScheduleModel.ItemRole) or {}
        layout = compute_card_layout(option.rect, item)
        tokens = self.tokens
        state = str(item.get("state") or "future")
        background, accent = self._state_colors(state)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor(tokens.border), 1))
        painter.setBrush(QColor(background))
        painter.drawRoundedRect(layout.card, 10, 10)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(accent))
        painter.drawRoundedRect(layout.rail, 2.5, 2.5)
        if option.state & QStyle.StateFlag.State_Selected:
            painter.setPen(QPen(QColor(tokens.accent), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(layout.card.adjusted(1, 1, -1, -1), 9, 9)

        enabled = not bool(item.get("skipped"))
        track_color = tokens.success if enabled else tokens.disabled_background
        painter.setPen(QPen(QColor(tokens.success if enabled else tokens.border), 1))
        painter.setBrush(QColor(track_color))
        painter.drawRoundedRect(layout.toggle, 12, 12)
        knob_size = layout.toggle.height() - 6
        knob_x = layout.toggle.right() - knob_size - 3 if enabled else layout.toggle.left() + 3
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#FFFFFF" if enabled else tokens.disabled_text))
        painter.drawEllipse(QRectF(knob_x, layout.toggle.top() + 3, knob_size, knob_size))

        painter.setPen(QColor(accent))
        painter.setFont(QFont("Segoe UI", 16, QFont.Weight.DemiBold))
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

        badge_fill = QColor(accent)
        badge_fill.setAlpha(38 if self.tokens.name == "dark" else 28)
        painter.setPen(QPen(QColor(accent), 1))
        painter.setBrush(badge_fill)
        painter.drawRoundedRect(layout.state_badge, 7, 7)
        painter.setPen(QColor(accent))
        painter.setFont(QFont("Microsoft YaHei UI", 9, QFont.Weight.DemiBold))
        painter.drawText(layout.state_badge, Qt.AlignmentFlag.AlignCenter, str(item.get("status_title") or "等待运行"))
        painter.setPen(QColor(tokens.muted if state not in {"failed", "running", "next"} else accent))
        painter.setFont(QFont("Microsoft YaHei UI", 9))
        painter.drawText(layout.status, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, str(item.get("status_detail") or ""))

        self._draw_button(painter, layout.error_button, "查看错误", tokens.danger, background)
        self._draw_button(painter, layout.move_button, "修改时间", tokens.accent, background)
        self._draw_button(painter, layout.clear_button, "撤销例外", tokens.muted, background)
        painter.restore()

    def editorEvent(self, event, model, option, index) -> bool:
        if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            item = index.data(ScheduleModel.ItemRole) or {}
            layout = compute_card_layout(option.rect, item)
            position = event.position()
            if layout.toggle.contains(position):
                self.monitor_toggled.emit(item, bool(item.get("skipped")))
                return True
            if not layout.error_button.isEmpty() and layout.error_button.contains(position):
                self.error_requested.emit(item)
                return True
            if layout.move_button.contains(position):
                self.move_requested.emit(item)
                return True
            if not layout.clear_button.isEmpty() and layout.clear_button.contains(position):
                self.clear_requested.emit(item)
                return True
        return super().editorEvent(event, model, option, index)
