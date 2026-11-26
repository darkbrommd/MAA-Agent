from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from gui.themes.tokens import DARK, LIGHT
from resource_history import memory_values


def _card() -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("Card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(14, 12, 14, 12)
    layout.setSpacing(5)
    return frame, layout


def _metric_card(title: str, value: str, caption: str) -> QFrame:
    frame, layout = _card()
    heading = QLabel(title)
    heading.setObjectName("MutedLabel")
    number = QLabel(value)
    number.setStyleSheet("font-family: 'Segoe UI'; font-size: 18px; font-weight: 700;")
    detail = QLabel(caption)
    detail.setObjectName("MutedLabel")
    detail.setWordWrap(True)
    layout.addWidget(heading)
    layout.addWidget(number)
    layout.addWidget(detail)
    return frame


def _memory_row(name: str, subtitle: str, memory_mb: float, accent: str) -> QFrame:
    frame = QFrame()
    frame.setObjectName("Card")
    layout = QHBoxLayout(frame)
    layout.setContentsMargins(13, 9, 13, 9)
    text = QVBoxLayout()
    text.setSpacing(2)
    title = QLabel(name)
    title.setStyleSheet("font-weight: 600;")
    detail = QLabel(subtitle)
    detail.setObjectName("MutedLabel")
    text.addWidget(title)
    text.addWidget(detail)
    layout.addLayout(text, 1)
    value = QLabel(f"{memory_mb / 1024:.2f} GB")
    value.setStyleSheet(f"color: {accent}; font-family: 'Segoe UI'; font-size: 15px; font-weight: 700;")
    layout.addWidget(value)
    return frame


class ScheduleErrorDialog(QDialog):
    def __init__(self, item: dict, theme: str = "dark", parent=None):
        super().__init__(parent)
        tokens = DARK if theme == "dark" else LIGHT
        error = dict(item.get("error") or {})
        when = item.get("datetime")
        self.setWindowTitle(str(error.get("title") or "运行错误详情"))
        self.resize(680, 520)
        self.setMinimumSize(560, 440)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        header, header_layout = _card()
        title = QLabel(str(error.get("title") or "任务没有正常完成"))
        title.setStyleSheet(f"color: {tokens.danger}; font-size: 19px; font-weight: 700;")
        occurrence = when.strftime("%Y-%m-%d %H:%M") if isinstance(when, datetime) else "时间未知"
        meta = QLabel(f"{item.get('display_label') or item.get('label') or 'MAA'} · 计划 {occurrence} · 失败 {error.get('time') or '时间未知'}")
        meta.setObjectName("MutedLabel")
        meta.setWordWrap(True)
        header_layout.addWidget(title)
        header_layout.addWidget(meta)
        root.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(
            f"QScrollArea {{ background: {tokens.background}; border: 0; }} "
            f"QScrollArea > QWidget > QWidget {{ background: {tokens.background}; }}"
        )
        body = QWidget()
        body.setStyleSheet(f"background: {tokens.background};")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(10)
        sections = (
            ("失败任务", error.get("task") or "当前任务"),
            ("发生了什么", error.get("summary") or error.get("title") or "任务没有正常完成。"),
            ("可能原因", error.get("likely") or "暂时无法确定。"),
            ("建议操作", error.get("action") or "检查当前游戏画面与 MAA 日志。"),
        )
        for heading, value in sections:
            frame, layout = _card()
            label = QLabel(heading)
            label.setStyleSheet("font-weight: 600;")
            content = QLabel(str(value))
            content.setWordWrap(True)
            content.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            if heading == "发生了什么":
                content.setStyleSheet(f"color: {tokens.danger};")
            layout.addWidget(label)
            layout.addWidget(content)
            body_layout.addWidget(frame)
        body_layout.addStretch(1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)


class ResourceDetailDialog(QDialog):
    def __init__(self, row: dict, theme: str = "dark", parent=None):
        super().__init__(parent)
        tokens = DARK if theme == "dark" else LIGHT
        when = datetime.fromtimestamp(float(row.get("epoch", 0)))
        used, _available, total = memory_values(row)
        reasons = "；".join(str(value) for value in (row.get("abnormal_reasons") or ())) or "这次检查没有发现明显异常"
        self.setWindowTitle("内存占用详情")
        self.resize(900, 680)
        self.setMinimumSize(720, 540)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(
            f"QScrollArea {{ background: {tokens.background}; border: 0; }} "
            f"QScrollArea > QWidget > QWidget {{ background: {tokens.background}; }}"
        )
        body = QWidget()
        body.setStyleSheet(f"background: {tokens.background};")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        header, header_layout = _card()
        title = QLabel(when.strftime("%Y-%m-%d  %H:%M:%S"))
        title.setStyleSheet("font-family: 'Segoe UI'; font-size: 20px; font-weight: 700;")
        reason = QLabel(reasons)
        reason.setWordWrap(True)
        reason.setStyleSheet(f"color: {tokens.danger if row.get('abnormal') else tokens.muted};")
        header_layout.addWidget(title)
        header_layout.addWidget(reason)
        layout.addWidget(header)

        metrics = QGridLayout()
        metrics.setHorizontalSpacing(8)
        metrics.setVerticalSpacing(8)
        metric_rows = (
            ("系统内存", f"{float(row.get('ram', 0)):.0f}%", f"已用 {used / 1024:.2f} / {total / 1024:.2f} GB"),
            ("Agent", f"{float(row.get('agent_mb', 0)) / 1024:.2f} GB", "后台运行"),
            ("MAA", f"{float(row.get('maa_mb', 0)) / 1024:.2f} GB", f"{row.get('maa_count', 0)} 个 MAA"),
            ("MuMu", f"{float(row.get('mumu_mb', 0)) / 1024:.2f} GB", f"{row.get('mumu_count', 0)} 个进程"),
        )
        for index, values in enumerate(metric_rows):
            metrics.addWidget(_metric_card(*values), index // 2, index % 2)
        layout.addLayout(metrics)

        apps = tuple(row.get("top_apps") or ())
        processes = tuple(row.get("top_processes") or ())
        for section_title, values, grouped in (
            ("占用内存较多的程序", apps, True),
            ("当时的程序明细", processes, False),
        ):
            heading = QLabel(section_title)
            heading.setStyleSheet("font-size: 15px; font-weight: 600;")
            layout.addWidget(heading)
            if not values:
                empty, empty_layout = _card()
                text = QLabel("这次记录没有保存程序明细。")
                text.setObjectName("MutedLabel")
                empty_layout.addWidget(text)
                layout.addWidget(empty)
                continue
            for item in values:
                if grouped:
                    subtitle = f"{item.get('category') or '其他'} · {item.get('count', 1)} 个进程"
                else:
                    subtitle = str(item.get("category") or "其他")
                layout.addWidget(_memory_row(str(item.get("name") or "未知程序"), subtitle, float(item.get("mb", 0)), tokens.success))
        layout.addStretch(1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)


def show_schedule_error(parent: QWidget, item: dict, theme: str = "dark") -> None:
    ScheduleErrorDialog(item, theme, parent).exec()


def show_resource_detail(parent: QWidget, row: dict, theme: str = "dark") -> None:
    ResourceDetailDialog(row, theme, parent).exec()
