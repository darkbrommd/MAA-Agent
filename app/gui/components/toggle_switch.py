from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from gui.themes.tokens import DARK, LIGHT, ThemeTokens


class ToggleSwitch(QWidget):
    """Small animated switch shared by card and settings interactions."""

    toggled = Signal(bool)

    def __init__(self, checked: bool = False, theme: str = "dark", parent=None):
        super().__init__(parent)
        self._checked = bool(checked)
        self._progress = 1.0 if self._checked else 0.0
        self._tokens: ThemeTokens = DARK if theme == "dark" else LIGHT
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(140)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(lambda value: self._set_progress(float(value)))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip("开启或关闭本次运行监测")

    def sizeHint(self) -> QSize:
        return QSize(46, 26)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool, *, animate: bool = True) -> None:
        checked = bool(checked)
        self._checked = checked
        target = 1.0 if checked else 0.0
        if not animate:
            self._animation.stop()
            self._set_progress(target)
            return
        self._animation.stop()
        self._animation.setStartValue(self._progress)
        self._animation.setEndValue(target)
        self._animation.start()

    def set_theme(self, theme: str) -> None:
        self._tokens = DARK if theme == "dark" else LIGHT
        self.update()

    def _set_progress(self, value: float) -> None:
        self._progress = max(0.0, min(1.0, value))
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled() and self.rect().contains(event.position().toPoint()):
            self.setChecked(not self._checked)
            self.toggled.emit(self._checked)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    @staticmethod
    def _blend(start: str, end: str, progress: float) -> QColor:
        first, second = QColor(start), QColor(end)
        return QColor(
            round(first.red() + (second.red() - first.red()) * progress),
            round(first.green() + (second.green() - first.green()) * progress),
            round(first.blue() + (second.blue() - first.blue()) * progress),
        )

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        tokens = self._tokens
        track = self.rect().adjusted(1, 1, -1, -1)
        progress = self._progress
        track_color = self._blend(tokens.disabled_background, tokens.success, progress)
        border_color = self._blend(tokens.border, tokens.success, progress)
        if not self.isEnabled():
            track_color.setAlpha(130)
            border_color.setAlpha(150)
        painter.setPen(QPen(border_color, 1))
        painter.setBrush(track_color)
        radius = track.height() / 2
        painter.drawRoundedRect(track, radius, radius)
        knob_size = max(8, track.height() - 6)
        knob_x = track.left() + 3 + (track.width() - knob_size - 6) * progress
        knob = QColor("#FFFFFF") if self.isEnabled() else QColor(tokens.disabled_text)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(knob)
        painter.drawEllipse(round(knob_x), track.top() + 3, knob_size, knob_size)
