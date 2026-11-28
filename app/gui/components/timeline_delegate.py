from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEasingCurve, QEvent, QRect, QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import QStyledItemDelegate, QStyleOptionViewItem

from gui.models.schedule_model import ScheduleModel
from gui.models.schedule_model import schedule_row_key
from gui.components.responsive_layout import responsive_card_rect
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
    source = QRectF(rect)
    if item.get("show_date_header"):
        source.adjust(0, 31, 0, 0)
    bounds = responsive_card_rect(source, horizontal_margin=8, vertical_margin=5)
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
    time = QRectF(time_left, bounds.center().y() - 20, 82, 40)
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
        title=QRectF(text_left, bounds.top() + 11, max(20, text_right - text_left), 30),
        subtitle=QRectF(text_left, bounds.top() + 41, max(20, text_right - text_left), 21),
        state_badge=state_badge,
        status=status,
        error_button=error_button,
        move_button=move_button,
        clear_button=clear_button,
    )


def compute_day_navigation_layout(rect: QRect | QRectF) -> QRectF:
    return QRectF(rect).adjusted(8, 6, -8, -6)


class TimelineDelegate(QStyledItemDelegate):
    error_requested = Signal(object)
    monitor_toggled = Signal(object, bool)
    move_requested = Signal(object)
    clear_requested = Signal(object)
    day_requested = Signal(int)
    repaint_requested = Signal()

    def __init__(self, theme: str = "dark", parent=None):
        super().__init__(parent)
        self.tokens: ThemeTokens = DARK if theme == "dark" else LIGHT
        self._toggle_progress: dict[tuple, float] = {}
        self._toggle_targets: dict[tuple, bool] = {}
        self._toggle_animations: dict[tuple, QVariantAnimation] = {}

    def set_theme(self, theme: str) -> None:
        self.tokens = DARK if theme == "dark" else LIGHT

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:
        item = index.data(ScheduleModel.ItemRole) or {}
        if item.get("kind") == "day_navigation":
            return QSize(1, 56)
        return QSize(1, 153 if item.get("show_date_header") else 122)

    @staticmethod
    def _blend(start: str, end: str, progress: float) -> QColor:
        first = QColor(start)
        second = QColor(end)
        value = max(0.0, min(1.0, progress))
        return QColor(
            round(first.red() + (second.red() - first.red()) * value),
            round(first.green() + (second.green() - first.green()) * value),
            round(first.blue() + (second.blue() - first.blue()) * value),
        )

    def toggle_progress(self, item: dict) -> float:
        key = schedule_row_key(item)
        return self._toggle_progress.get(key, 0.0 if item.get("skipped") else 1.0)

    def animate_toggle(self, item: dict, enabled: bool) -> None:
        key = schedule_row_key(item)
        start = self.toggle_progress(item)
        old = self._toggle_animations.pop(key, None)
        if old is not None:
            old.stop()
            old.deleteLater()
        self._toggle_targets[key] = enabled
        animation = QVariantAnimation(self)
        animation.setDuration(140)
        animation.setStartValue(start)
        animation.setEndValue(1.0 if enabled else 0.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.valueChanged.connect(lambda value, item_key=key: self._set_toggle_progress(item_key, float(value)))
        animation.finished.connect(lambda item_key=key, task=animation: self._finish_toggle_animation(item_key, task))
        self._toggle_animations[key] = animation
        animation.start()

    def _set_toggle_progress(self, key: tuple, value: float) -> None:
        self._toggle_progress[key] = value
        self.repaint_requested.emit()

    def _finish_toggle_animation(self, key: tuple, animation: QVariantAnimation) -> None:
        if self._toggle_animations.get(key) is animation:
            self._toggle_animations.pop(key, None)
        animation.deleteLater()
        self.repaint_requested.emit()

    def sync_toggle_states(self, rows: tuple[dict, ...]) -> None:
        """Release optimistic animation state after the worker result reaches the model."""
        actual = {
            schedule_row_key(item): not bool(item.get("skipped"))
            for item in rows
            if item.get("kind") != "day_navigation"
        }
        for key, target in tuple(self._toggle_targets.items()):
            if actual.get(key) != target:
                continue
            animation = self._toggle_animations.pop(key, None)
            if animation is not None:
                animation.stop()
                animation.deleteLater()
            self._toggle_targets.pop(key, None)
            self._toggle_progress.pop(key, None)
        self.repaint_requested.emit()

    def _state_colors(self, state: str) -> tuple[str, str]:
        tokens = self.tokens
        if state == "failed":
            return tokens.failure_card, tokens.danger
        if state == "running":
            return tokens.running_card, tokens.warning
        if state == "next":
            return tokens.next_card, tokens.success
        if state == "done":
            return tokens.ended_card, tokens.ended
        if state == "off":
            return tokens.off_card, tokens.off
        return tokens.future_card, tokens.muted

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
        tokens = self.tokens
        if item.get("kind") == "day_navigation":
            rect = compute_day_navigation_layout(option.rect)
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(QPen(QColor(tokens.border), 1))
            painter.setBrush(QColor(tokens.disabled_background))
            painter.drawRoundedRect(rect, 8, 8)
            painter.setPen(QColor(tokens.accent))
            painter.setFont(QFont("Microsoft YaHei UI", 10, QFont.Weight.DemiBold))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(item.get("text") or "切换运行日"))
            painter.restore()
            return
        layout = compute_card_layout(option.rect, item)
        state = str(item.get("state") or "future")
        background, accent = self._state_colors(state)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if item.get("show_date_header"):
            when = item.get("datetime")
            header_day = item.get("header_day") or (when.date() if when else None)
            day_text = f"{header_day.year:04d}年{header_day.month:02d}月{header_day.day:02d}日" if header_day else "日期"
            weekday = "一二三四五六日"[header_day.weekday()] if header_day else ""
            header = QRectF(option.rect).adjusted(12, 1, -12, -option.rect.height() + 29)
            painter.setPen(QColor(tokens.text))
            text = f"{day_text} · 星期{weekday}"
            font = QFont("Microsoft YaHei UI", 11, QFont.Weight.DemiBold)
            painter.setFont(font)
            painter.drawText(header, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
            line_left = min(header.right(), header.left() + QFontMetricsF(font).horizontalAdvance(text) + 14)
            painter.setPen(QPen(QColor(tokens.border), 1))
            line = QRectF(line_left, header.center().y(), max(0, header.right() - line_left), 1)
            painter.drawLine(line.topLeft(), line.topRight())
        border = QColor(accent if state in {"failed", "running", "next"} else tokens.border)
        border.setAlpha(190 if state in {"failed", "running", "next"} else 255)
        painter.setPen(QPen(border, 1))
        painter.setBrush(QColor(background))
        painter.drawRoundedRect(layout.card, 10, 10)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(accent))
        painter.drawRoundedRect(layout.rail, 2.5, 2.5)
        progress = self.toggle_progress(item)
        painter.setPen(QPen(self._blend(tokens.border, tokens.success, progress), 1))
        painter.setBrush(self._blend(tokens.disabled_background, tokens.success, progress))
        painter.drawRoundedRect(layout.toggle, 12, 12)
        knob_size = layout.toggle.height() - 6
        knob_x = layout.toggle.left() + 3 + (layout.toggle.width() - knob_size - 6) * progress
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._blend(tokens.disabled_text, "#FFFFFF", progress))
        painter.drawEllipse(QRectF(knob_x, layout.toggle.top() + 3, knob_size, knob_size))

        painter.setPen(QColor(accent))
        painter.setFont(QFont("Segoe UI", 19, QFont.Weight.Bold))
        when = item.get("datetime")
        painter.drawText(layout.time, Qt.AlignmentFlag.AlignCenter, when.strftime("%H:%M") if when else "--:--")
        painter.setPen(QColor(tokens.text))
        painter.setFont(QFont("Microsoft YaHei UI", 11, QFont.Weight.DemiBold))
        painter.drawText(layout.title, Qt.AlignmentFlag.AlignVCenter, str(item.get("display_label") or item.get("label") or "计划"))
        painter.setPen(QColor(tokens.muted))
        painter.setFont(QFont("Microsoft YaHei UI", 10))
        day_text = when.strftime("%Y-%m-%d") if when else ""
        config = str(item.get("config") or "默认任务")
        painter.drawText(layout.subtitle, Qt.AlignmentFlag.AlignVCenter, f"{day_text} · {config}")

        badge_fill = QColor(accent)
        badge_fill.setAlpha(38 if self.tokens.name == "dark" else 28)
        painter.setPen(QPen(QColor(accent), 1))
        painter.setBrush(badge_fill)
        painter.drawRoundedRect(layout.state_badge, 7, 7)
        painter.setPen(QColor(accent))
        painter.setFont(QFont("Microsoft YaHei UI", 10, QFont.Weight.DemiBold))
        painter.drawText(layout.state_badge, Qt.AlignmentFlag.AlignCenter, str(item.get("status_title") or "等待运行"))
        painter.setPen(QColor(tokens.muted if state not in {"failed", "running", "next"} else accent))
        painter.setFont(QFont("Microsoft YaHei UI", 10))
        painter.drawText(layout.status, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, str(item.get("status_detail") or ""))

        self._draw_button(painter, layout.error_button, "查看错误", tokens.danger, background)
        self._draw_button(painter, layout.move_button, "修改时间", tokens.accent, background)
        self._draw_button(painter, layout.clear_button, "恢复计划", tokens.muted, background)
        painter.restore()

    def editorEvent(self, event, model, option, index) -> bool:
        if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            item = index.data(ScheduleModel.ItemRole) or {}
            if item.get("kind") == "day_navigation":
                if compute_day_navigation_layout(option.rect).contains(event.position()):
                    self.day_requested.emit(int(item.get("direction") or 0))
                    return True
                return False
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
