from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, Signal, QSize
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
from project_paths import config_path
from schedule_utils import add_override, remove_override


_LOG_TIME = re.compile(r"^\[?(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})")


class _OverviewProcessCard(QFrame):
    def __init__(self, rank: int, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 11, 14, 11)
        layout.setSpacing(4)
        heading = QLabel(f"内存占用 #{rank}")
        heading.setObjectName("MutedLabel")
        layout.addWidget(heading)
        self.name = QLabel("读取中…")
        self.name.setStyleSheet("font-size: 15px; font-weight: 600;")
        layout.addWidget(self.name)
        self.value = QLabel("—")
        self.value.setObjectName("MutedLabel")
        layout.addWidget(self.value)

    def set_process(self, process=None) -> None:
        if process is None:
            self.name.setText("暂无数据")
            self.value.setText("—")
            return
        self.name.setText(process.name)
        self.value.setText(f"{process.memory_mb / 1024:.2f} GB · PID {process.pid}" if process.memory_mb >= 1024 else f"{process.memory_mb:.0f} MB · PID {process.pid}")


class _DashboardOverrideSignals(QObject):
    done = Signal(str)
    failed = Signal(str)


class _DashboardOverrideWorker(QRunnable):
    def __init__(self, base_dir: Path, item: dict):
        super().__init__()
        self.base_dir = base_dir
        self.item = dict(item)
        self.signals = _DashboardOverrideSignals()

    def run(self) -> None:
        try:
            cfg = json.loads(config_path(self.base_dir).read_text(encoding="utf-8-sig"))
            if self.item.get("skipped"):
                override = self.item.get("override") or {}
                removed = remove_override(self.base_dir, cfg, override_id=override.get("id"))
                if not removed:
                    remove_override(
                        self.base_dir,
                        cfg,
                        label=str(self.item["label"]),
                        original_dt=self.item.get("original_datetime") or self.item["datetime"],
                    )
                message = "已恢复本次监督；MAA 定时器没有修改。"
            else:
                add_override(
                    self.base_dir,
                    cfg,
                    str(self.item["label"]),
                    self.item.get("original_datetime") or self.item["datetime"],
                    "skip",
                    note="MAA Agent 2.0 总览",
                )
                message = "已关闭本次监督；MAA 定时器没有修改。"
            self.signals.done.emit(message)
        except Exception as exc:
            self.signals.failed.emit(str(exc))


class DashboardPage(QWidget):
    open_schedule = Signal()

    def __init__(self, base_dir: Path, page_state: dict, theme: str = "dark", parent=None):
        super().__init__(parent)
        self.base_dir = Path(base_dir)
        self.page_state = page_state
        self._theme = theme
        self._last_static_refresh = 0.0
        self._log_lines: list[str] = []
        self._override_workers: set[_DashboardOverrideWorker] = set()
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
        self.memory_card = MetricCard("内存", "memory")
        metrics.addWidget(self.memory_card, 2)
        self.process_cards = tuple(_OverviewProcessCard(index) for index in range(1, 4))
        for card in self.process_cards:
            metrics.addWidget(card, 1)
        root.addLayout(metrics)

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
        self.memory_card.set_metric(snapshot.memory_percent, f"{snapshot.memory_percent:.0f}%", f"已用 {snapshot.memory_used_gb:.1f} GB")
        for index, card in enumerate(self.process_cards):
            card.set_process(snapshot.top_processes[index] if index < len(snapshot.top_processes) else None)

    def _apply_dashboard(self, snapshot: DashboardSnapshot) -> None:
        self.schedule_list.clear()
        for row in snapshot.schedule_rows:
            when = row.get("datetime")
            time_text = when.strftime("%m-%d %H:%M") if isinstance(when, datetime) else str(when or "")
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, row)
            item.setSizeHint(QSize(1, 72))
            self.schedule_list.addItem(item)
            widget = QWidget()
            layout = QHBoxLayout(widget)
            layout.setContentsMargins(8, 5, 8, 5)
            text = QLabel(
                f"{time_text}  {row.get('display_label') or row.get('label', '')}\n"
                f"{row.get('status_title', '')} · {row.get('status_detail', '')}"
            )
            text.setWordWrap(True)
            layout.addWidget(text, 1)
            toggle = QPushButton("恢复监测" if row.get("skipped") else "关闭本次")
            toggle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            toggle.setToolTip("只调整 Agent 对这一次计划的监督预期，不修改 MAA 定时器。")
            toggle.clicked.connect(lambda _checked=False, value=dict(row), button=toggle: self._toggle_plan(value, button))
            layout.addWidget(toggle)
            self.schedule_list.setItemWidget(item, widget)
        if not snapshot.schedule_rows:
            self.schedule_list.addItem("未来 36 小时没有计划")
        self.chart.set_data(snapshot.resource_rows, snapshot.day)
        self.status_label.setText(f"已更新 · {datetime.now():%H:%M:%S}")

    def _toggle_plan(self, item: dict, button: QPushButton) -> None:
        button.setEnabled(False)
        button.setText("正在保存…")
        worker = _DashboardOverrideWorker(self.base_dir, item)
        self._override_workers.add(worker)
        worker.signals.done.connect(lambda message, task=worker: self._override_finished(task, message))
        worker.signals.failed.connect(lambda message, task=worker: self._override_failed(task, message))
        QThreadPool.globalInstance().start(worker)

    def _override_finished(self, worker: _DashboardOverrideWorker, message: str) -> None:
        self._override_workers.discard(worker)
        self.status_label.setText(message)
        self._last_static_refresh = 0.0
        self.refresh_tick(force=True)

    def _override_failed(self, worker: _DashboardOverrideWorker, message: str) -> None:
        self._override_workers.discard(worker)
        self.status_label.setText(f"监督设置保存失败：{message}")
        self._last_static_refresh = 0.0
        self.refresh_tick(force=True)

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
