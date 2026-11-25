from __future__ import annotations

from PySide6.QtCore import QRect, QRectF


def responsive_card_rect(
    rect: QRect | QRectF,
    *,
    horizontal_margin: float = 8,
    vertical_margin: float = 4,
    full_width_until: float = 1440,
    maximum_width: float = 1720,
    wide_growth: float = 0.35,
) -> QRectF:
    """Keep cards fluid at normal sizes without turning them into ultra-wide strips."""
    bounds = QRectF(rect).adjusted(horizontal_margin, vertical_margin, -horizontal_margin, -vertical_margin)
    available = max(0.0, bounds.width())
    if available <= full_width_until:
        width = available
    else:
        width = min(maximum_width, full_width_until + (available - full_width_until) * wide_growth)
    left = bounds.left() + max(0.0, available - width) / 2
    return QRectF(left, bounds.top(), max(0.0, width), max(0.0, bounds.height()))
