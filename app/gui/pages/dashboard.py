from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from gui.components.metric_card import MetricCard
from gui.components.resource_chart import ResourceChart
from gui.services.dashboard_service import DashboardService, DashboardSnapshot
from gui.services.log_service import LogChunk, LogService, line_matches
from gui.services.process_service import ProcessSnapshot


_LOG_TIME = re.compile(r"^\[?(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})")


class DashboardPage(QWidget):
    open_schedule = Signal()

    def __init__(self, base_dir: Path, page_state: dict, theme: str = "dark", parent=None):
        super().__init__(parent)
        self.base_dir = Path(base_dir)
        self.page_state = page_state
        self._theme = theme
        self._last_static_refresh = 0.0
        self._log_lines: list[str] = []
        self._build_ui()
        self.dashboard_service = DashboardService(self.base_dir, parent=self)
        self.dashboard_service.loaded.connect(self._apply_dashboard)
        self.dashboard_service.failed.connect(lambda message: self.status_label.setText(f"总览读取失败：{message}"))
        self.log_service = LogService(self.base_dir, parent=self)
        self.log_service.chunk_ready.connect(self._apply_log_chunk)
        self.log_service.failed.connect(lambda message: self.status_label.setText(f"日志读取失败：{message}"))
        self._split_save = QTimer(self)
        self._split_save.setSingleShot(True)
        self._split_save.setInterval(180)
        self._split_save.timeout.connect(self._save_splitters)
        self.main_splitter.splitterMoved.connect(lambda *_: self._split_save.start())
        self.activity_splitter.splitterMoved.connect(lambda *_: self._split_save.start())
        QTimer.singleShot(0, self._restore_splitters)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 24)
        root.setSpacing(12)
        title = QLabel("总览")
        title.setObjectName("PageTitle")
        root.addWidget(title)

        metrics = QHBoxLayout()
        metrics.setSpacing(12)
        self.cpu_card = MetricCard("CPU", "cpu")
        self.memory_card = MetricCard("内存", "memory")
        self.disk_card = MetricCard("磁盘", "disk")
        for card in (self.cpu_card, self.memory_card, self.disk_card):
            metrics.addWidget(card, 1)
        root.addLayout(metrics)
        self.top_processes = QLabel("内存大户：读取中")
        self.top_processes.setObjectName("MutedLabel")
        self.top_processes.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self.top_processes)

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.setChildrenCollapsible(False)
        plans = QFrame()
        plans.setObjectName("Card")
        plans_layout = QVBoxLayout(plans)
        plans_layout.setContentsMargins(14, 14, 14, 14)
        plans_header = QHBoxLayout()
        plans_header.addWidget(QLabel("未来 36 小时"))
        plans_header.addStretch(1)
        open_schedule = QPushButton("打开日程管理 →")
        open_schedule.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        open_schedule.clicked.connect(self.open_schedule)
        plans_header.addWidget(open_schedule)
        plans_layout.addLayout(plans_header)
        self.schedule_list = QListWidget()
        self.schedule_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.schedule_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        plans_layout.addWidget(self.schedule_list, 1)

        activity = QFrame()
        activity.setObjectName("Card")
        activity_layout = QVBoxLayout(activity)
        activity_layout.setContentsMargins(14, 14, 14, 14)
        tools = QHBoxLayout()
        tools.addWidget(QLabel("最近事件与内存趋势"))
        tools.addStretch(1)
        self.log_mode = QComboBox()
        self.log_mode.addItems(("事故与恢复", "全部"))
        self.log_mode.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.log_mode.setCurrentText(str(self.page_state.get("log_mode") or "事故与恢复"))
        self.log_mode.currentTextChanged.connect(self._change_log_mode)
        tools.addWidget(self.log_mode)
        self.chart_toggle = QPushButton("折叠趋势")
        self.chart_toggle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.chart_toggle.clicked.connect(self._toggle_chart)
        tools.addWidget(self.chart_toggle)
        clear = QPushButton("清空主页显示")
        clear.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        clear.setToolTip("只从主页隐藏此前事件，不删除 logs/agent.log，也不影响日志中心。")
        clear.clicked.connect(self._clear_home_log)
        tools.addWidget(clear)
        activity_layout.addLayout(tools)
        self.activity_splitter = QSplitter(Qt.Orientation.Vertical)
        self.activity_splitter.setChildrenCollapsible(False)
        self.chart = ResourceChart(self._theme)
        self.chart.setMinimumHeight(150)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(1200)
        self.log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.activity_splitter.addWidget(self.chart)
        self.activity_splitter.addWidget(self.log_view)
        activity_layout.addWidget(self.activity_splitter, 1)

        self.main_splitter.addWidget(plans)
        self.main_splitter.addWidget(activity)
        self.main_splitter.setStretchFactor(0, 4)
        self.main_splitter.setStretchFactor(1, 6)
        root.addWidget(self.main_splitter, 1)
        self.status_label = QLabel("等待刷新")
        self.status_label.setObjectName("MutedLabel")
        root.addWidget(self.status_label)

        if bool(self.page_state.get("chart_collapsed")):
            self.chart.setVisible(False)
            self.chart_toggle.setText("展开趋势")

    def on_activated(self) -> None:
        self.refresh_tick(force=True)

    def refresh_tick(self, force: bool = False) -> None:
        self.log_service.request("agent.log")
        now = time.monotonic()
        if force or now - self._last_static_refresh >= 15.0:
            if self.dashboard_service.refresh():
                self._last_static_refresh = now

    def update_process_snapshot(self, snapshot: ProcessSnapshot) -> None:
        self.cpu_card.set_metric(snapshot.cpu_percent, f"{snapshot.cpu_percent:.0f}%", "当前处理器负载")
        self.memory_card.set_metric(snapshot.memory_percent, f"{snapshot.memory_percent:.0f}%", f"已用 {snapshot.memory_used_gb:.1f} GB")
        self.disk_card.set_metric(
            snapshot.disk_percent,
            f"{snapshot.disk_percent:.0f}%",
            f"{snapshot.disk_used_gb:.0f} / {snapshot.disk_total_gb:.0f} GB" if snapshot.disk_total_gb else "暂不可用",
        )
        values = [f"{item.name} {item.memory_mb:.0f} MB" for item in snapshot.top_processes]
        self.top_processes.setText("内存大户：" + (" · ".join(values) if values else "暂无数据"))

    def _apply_dashboard(self, snapshot: DashboardSnapshot) -> None:
        self.schedule_list.clear()
        for row in snapshot.schedule_rows:
            when = row.get("datetime")
            time_text = when.strftime("%m-%d %H:%M") if isinstance(when, datetime) else str(when or "")
            item = QListWidgetItem(f"{time_text}  {row.get('label', '')}\n{row.get('status_title', '')} · {row.get('status_detail', '')}")
            item.setData(Qt.ItemDataRole.UserRole, row)
            self.schedule_list.addItem(item)
        if not snapshot.schedule_rows:
            self.schedule_list.addItem("未来 36 小时没有计划")
        self.chart.set_data(snapshot.resource_rows, snapshot.day)
        self.status_label.setText(f"已更新 · {datetime.now():%H:%M:%S}")

    def _apply_log_chunk(self, chunk: LogChunk) -> None:
        incoming = chunk.text.splitlines()
        if chunk.reset:
            self._log_lines = incoming[-2000:]
            self._render_logs()
            return
        if not incoming:
            return
        self._log_lines.extend(incoming)
        del self._log_lines[:-2000]
        for line in incoming:
            if self._line_visible(line):
                self.log_view.appendPlainText(line)
        bar = self.log_view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _line_visible(self, line: str) -> bool:
        if not line_matches(line, self.log_mode.currentText()):
            return False
        cutoff = str(self.page_state.get("log_cutoff") or "")
        if not cutoff:
            return True
        match = _LOG_TIME.match(line)
        return not match or match.group(1).replace("T", " ") > cutoff.replace("T", " ")

    def _render_logs(self) -> None:
        values = [line for line in self._log_lines if self._line_visible(line)][-120:]
        self.log_view.setPlainText("\n".join(values))
        self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())

    def _change_log_mode(self, mode: str) -> None:
        self.page_state["log_mode"] = mode
        self._render_logs()

    def _clear_home_log(self) -> None:
        self.page_state["log_cutoff"] = datetime.now().isoformat(timespec="seconds")
        self.log_view.clear()
        self.log_view.setPlaceholderText("主页此前事件已隐藏；源日志仍完整保留。")

    def _toggle_chart(self) -> None:
        visible = not self.chart.isVisible()
        self.chart.setVisible(visible)
        self.chart_toggle.setText("折叠趋势" if visible else "展开趋势")
        self.page_state["chart_collapsed"] = not visible

    def _restore_splitters(self) -> None:
        for splitter, key, fallback in (
            (self.main_splitter, "main_splitter_sizes", (420, 620)),
            (self.activity_splitter, "activity_splitter_sizes", (280, 220)),
        ):
            values = self.page_state.get(key, fallback)
            if isinstance(values, (list, tuple)) and len(values) == 2:
                splitter.setSizes([max(1, int(value)) for value in values])

    def _save_splitters(self) -> None:
        self.page_state["main_splitter_sizes"] = self.main_splitter.sizes()
        self.page_state["activity_splitter_sizes"] = self.activity_splitter.sizes()

    def set_theme(self, theme: str) -> None:
        self._theme = theme
        self.chart.set_theme(theme)
