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
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from gui.components.metric_card import MetricCard
from gui.components.resource_chart import ResourceChart
from gui.components.toggle_switch import ToggleSwitch
from gui.services.dashboard_service import DashboardService, DashboardSnapshot
from gui.services.log_service import LogChunk, LogService, line_matches
from gui.services.process_service import ProcessSnapshot
from gui.themes.tokens import DARK, LIGHT
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
        heading = QLabel(f"应用内存 #{rank}")
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
        memory = f"{process.memory_mb / 1024:.2f} GB" if process.memory_mb >= 1024 else f"{process.memory_mb:.0f} MB"
        suffix = f"{process.process_count} 个进程" if process.process_count > 1 else f"PID {process.pid}"
        self.value.setText(f"{memory} · {suffix}")


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
        self._schedule_rows: tuple[dict, ...] = ()
        self._override_workers: dict[_DashboardOverrideWorker, tuple[ToggleSwitch, bool]] = {}
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
        plans.setMinimumWidth(380)
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
        self.schedule_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.schedule_list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
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
        self.log_view.setPlaceholderText("（最近没有事故、异常或恢复记录）")
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
        self._schedule_rows = tuple(dict(row) for row in snapshot.schedule_rows)
        self._render_schedule_rows()
        self.chart.set_data(snapshot.resource_rows, snapshot.day)
        self.status_label.setText(f"已更新 · {datetime.now():%H:%M:%S}")

    def _render_schedule_rows(self) -> None:
        self.schedule_list.clear()
        for row in self._schedule_rows:
            if row.get("show_date_header"):
                self._add_schedule_day_header(row)
            self._add_schedule_card(row)
        if not self._schedule_rows:
            item = QListWidgetItem()
            item.setSizeHint(QSize(1, 54))
            self.schedule_list.addItem(item)
            empty = QLabel("未来 36 小时没有计划")
            empty.setObjectName("MutedLabel")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.schedule_list.setItemWidget(item, empty)

    def _add_schedule_day_header(self, row: dict) -> None:
        when = row.get("datetime")
        if not isinstance(when, datetime):
            return
        item = QListWidgetItem()
        item.setSizeHint(QSize(1, 34))
        self.schedule_list.addItem(item)
        header = QWidget()
        layout = QHBoxLayout(header)
        layout.setContentsMargins(8, 2, 8, 0)
        date_text = f"{when.year:04d}年{when.month:02d}月{when.day:02d}日"
        label = QLabel(f"{date_text} · 星期{'一二三四五六日'[when.weekday()]}")
        label.setStyleSheet("font-weight: 600;")
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setObjectName("ScheduleDayLine")
        layout.addWidget(label)
        layout.addWidget(line, 1)
        self.schedule_list.setItemWidget(item, header)

    def _add_schedule_card(self, row: dict) -> None:
        when = row.get("datetime")
        time_text = when.strftime("%H:%M") if isinstance(when, datetime) else "--:--"
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, row)
        item.setSizeHint(QSize(1, 88))
        self.schedule_list.addItem(item)
        widget = QFrame()
        widget.setObjectName("DashboardPlanCard")
        tokens = DARK if self._theme == "dark" else LIGHT
        state = str(row.get("state") or "future")
        if state == "failed":
            background, accent = tokens.failure_card, tokens.danger
        elif state == "running":
            background, accent = tokens.running_card, tokens.warning
        elif state == "next":
            background, accent = tokens.next_card, tokens.success
        elif state == "done":
            background, accent = tokens.ended_card, tokens.ended
        elif state == "off":
            background, accent = tokens.off_card, tokens.off
        else:
            background, accent = tokens.future_card, tokens.muted
        border = accent if state in {"failed", "running", "next"} else tokens.border
        widget.setStyleSheet(
            f"QFrame#DashboardPlanCard {{ background: {background}; border: 1px solid {border}; border-radius: 9px; }}"
        )
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 7, 9, 7)
        layout.setSpacing(10)
        rail = QFrame()
        rail.setFixedWidth(5)
        rail.setStyleSheet(f"background: {accent}; border: 0; border-radius: 2px;")
        layout.addWidget(rail)
        time_label = QLabel(time_text)
        time_label.setFixedWidth(66)
        time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        time_label.setStyleSheet(f"color: {accent}; font-family: 'Segoe UI'; font-size: 18px; font-weight: 700;")
        layout.addWidget(time_label)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(3)
        title = QLabel(str(row.get("display_label") or row.get("label") or "计划"))
        title.setStyleSheet("font-weight: 600;")
        title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        status_text = f"{row.get('status_title', '')} · {row.get('status_detail', '')}".strip(" ·")
        status = QLabel(status_text)
        status.setObjectName("MutedLabel")
        status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        status.setToolTip(status_text)
        content_layout.addWidget(title)
        content_layout.addWidget(status)
        layout.addWidget(content, 1)
        toggle = ToggleSwitch(not bool(row.get("skipped")), self._theme)
        toggle.setFixedSize(46, 26)
        toggle.setToolTip("只调整 Agent 对这一次计划的监督预期，不修改 MAA 定时器。")
        toggle.toggled.connect(lambda enabled, value=dict(row), switch=toggle: self._toggle_plan(value, switch, enabled))
        layout.addWidget(toggle)
        self.schedule_list.setItemWidget(item, widget)

    def _toggle_plan(self, item: dict, toggle: ToggleSwitch, enabled: bool) -> None:
        original = not bool(item.get("skipped"))
        if enabled == original:
            return
        toggle.setEnabled(False)
        worker = _DashboardOverrideWorker(self.base_dir, item)
        self._override_workers[worker] = (toggle, original)
        worker.signals.done.connect(lambda message, task=worker: self._override_finished(task, message))
        worker.signals.failed.connect(lambda message, task=worker: self._override_failed(task, message))
        QThreadPool.globalInstance().start(worker)

    def _override_finished(self, worker: _DashboardOverrideWorker, message: str) -> None:
        self._override_workers.pop(worker, None)
        self.status_label.setText(message)
        self._last_static_refresh = 0.0
        self.refresh_tick(force=True)

    def _override_failed(self, worker: _DashboardOverrideWorker, message: str) -> None:
        pending = self._override_workers.pop(worker, None)
        if pending is not None:
            toggle, original = pending
            toggle.setEnabled(True)
            toggle.setChecked(original)
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
        if values:
            self.log_view.setPlaceholderText("")
        elif self.page_state.get("log_cutoff"):
            self.log_view.setPlaceholderText("主页此前事件已隐藏；源日志仍完整保留。")
        else:
            self.log_view.setPlaceholderText("（最近没有事故、异常或恢复记录）")
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
        self._render_schedule_rows()
