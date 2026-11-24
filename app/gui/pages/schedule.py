from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QRunnable, QThreadPool, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListView,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui.components.date_picker import DatePicker
from gui.components.dialogs import show_schedule_error
from gui.components.timeline_delegate import TimelineDelegate
from gui.models.schedule_model import ScheduleModel
from gui.services.schedule_service import ScheduleService, ScheduleSnapshot
from project_paths import config_path
from schedule_utils import add_override


class _OverrideSignals(QObject):
    done = Signal(str)
    failed = Signal(str)


class _OverrideWorker(QRunnable):
    def __init__(self, base_dir: Path, item: dict, action: str, target: datetime | None):
        super().__init__()
        self.base_dir = base_dir
        self.item = item
        self.action = action
        self.target = target
        self.signals = _OverrideSignals()

    def run(self) -> None:
        try:
            cfg = json.loads(config_path(self.base_dir).read_text(encoding="utf-8-sig"))
            add_override(
                self.base_dir,
                cfg,
                str(self.item["label"]),
                self.item.get("original_datetime") or self.item["datetime"],
                self.action,
                new_dt=self.target,
                note="MAA Agent 2.0 GUI",
            )
            self.signals.done.emit("本次监督预期已更新；MAA 自己的定时器没有修改。")
        except Exception as exc:
            self.signals.failed.emit(str(exc))


class SchedulePage(QWidget):
    def __init__(self, base_dir: Path, page_state: dict, theme: str = "dark", parent=None):
        super().__init__(parent)
        self.base_dir = Path(base_dir)
        self.page_state = page_state
        stored = page_state.get("selected_day")
        try:
            self.selected_day = date.fromisoformat(stored) if stored else date.today()
        except ValueError:
            self.selected_day = date.today()
        self.window_start = self.selected_day - timedelta(days=3)
        self.window_end = self.selected_day + timedelta(days=3)
        self._max_window_days = 31
        self._loading = False
        self._edge_lock = False
        self._scroll_intent = False
        self._anchor_datetime: datetime | None = None
        self._override_workers: set[_OverrideWorker] = set()
        self.service = ScheduleService(self.base_dir, parent=self)
        self.service.loaded.connect(self._on_loaded)
        self.service.failed.connect(self._on_failed)
        self.model = ScheduleModel(self)
        self.delegate = TimelineDelegate(theme, self)
        self.delegate.error_requested.connect(lambda item: show_schedule_error(self, item))
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 20)
        root.setSpacing(12)
        title = QLabel("日程管理")
        title.setObjectName("PageTitle")
        root.addWidget(title)
        toolbar = QHBoxLayout()
        previous = QPushButton("‹")
        next_button = QPushButton("›")
        previous.clicked.connect(lambda: self.set_day(self.selected_day - timedelta(days=1)))
        next_button.clicked.connect(lambda: self.set_day(self.selected_day + timedelta(days=1)))
        toolbar.addWidget(previous)
        self.date_picker = DatePicker(self.selected_day)
        self.date_picker.date_changed.connect(self.set_day)
        toolbar.addWidget(self.date_picker)
        toolbar.addWidget(next_button)
        now_button = QPushButton("回到现在")
        now_button.clicked.connect(lambda: self.set_day(date.today()))
        toolbar.addWidget(now_button)
        toolbar.addStretch(1)
        override = QPushButton("调整本次监督预期…")
        override.clicked.connect(self.open_override)
        toolbar.addWidget(override)
        root.addLayout(toolbar)
        hint = QLabel("这里的 skip/move 只调整 Agent 的监督预期，不会修改 MAA 自己的定时器。")
        hint.setObjectName("MutedLabel")
        root.addWidget(hint)
        self.status = QLabel("正在读取日程…")
        self.status.setObjectName("MutedLabel")
        root.addWidget(self.status)
        self.list_view = QListView()
        self.list_view.setModel(self.model)
        self.list_view.setItemDelegate(self.delegate)
        self.list_view.setSelectionMode(QListView.SelectionMode.SingleSelection)
        self.list_view.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.list_view.setUniformItemSizes(True)
        self.list_view.viewport().installEventFilter(self)
        self.list_view.verticalScrollBar().valueChanged.connect(self._on_scroll)
        root.addWidget(self.list_view, 1)

    def set_theme(self, theme: str) -> None:
        self.delegate.set_theme(theme)
        self.list_view.viewport().update()

    def set_day(self, selected: date) -> None:
        self.selected_day = selected
        self.page_state["selected_day"] = selected.isoformat()
        self.date_picker.set_date(selected, emit=False)
        self.window_start = selected - timedelta(days=3)
        self.window_end = selected + timedelta(days=3)
        self._anchor_datetime = datetime.combine(selected, datetime.min.time()).replace(hour=12)
        self.refresh()

    def refresh(self) -> None:
        self._loading = True
        self.status.setText(f"正在读取 {self.window_start:%m-%d} 至 {self.window_end:%m-%d}…")
        start = datetime.combine(self.window_start, datetime.min.time())
        end = datetime.combine(self.window_end + timedelta(days=1), datetime.min.time()) - timedelta(microseconds=1)
        self.service.request_window(start, end)

    def _on_loaded(self, request_id: int, snapshot: ScheduleSnapshot) -> None:
        self._loading = True
        self.model.set_rows(snapshot.rows)
        self.status.setText(f"已加载 {snapshot.start:%m-%d} 至 {snapshot.end:%m-%d} · {len(snapshot.rows)} 个计划")
        target = self._anchor_datetime or datetime.now()
        self._anchor_datetime = None
        rows = self.model.rows()
        if rows:
            index = min(range(len(rows)), key=lambda row: abs((rows[row]["datetime"] - target).total_seconds()))
            model_index = self.model.index(index, 0)
            self.list_view.scrollTo(model_index, QListView.ScrollHint.PositionAtCenter)
            self.list_view.setCurrentIndex(model_index)
        self._loading = False
        self._edge_lock = False

    def _on_failed(self, request_id: int, message: str) -> None:
        self._loading = False
        self._edge_lock = False
        self.status.setText(f"日程读取失败：{message}")

    def _on_scroll(self, value: int) -> None:
        if self._loading:
            return
        index = self.list_view.indexAt(QPoint(12, 12))
        if index.isValid():
            item = self.model.item(index.row())
            visible_day = item["datetime"].date()
            self.selected_day = visible_day
            self.page_state["selected_day"] = visible_day.isoformat()
            self.date_picker.set_date(visible_day, emit=False)
        if self._edge_lock or not self._scroll_intent:
            return
        bar = self.list_view.verticalScrollBar()
        loaded_days = (self.window_end - self.window_start).days + 1
        if loaded_days >= self._max_window_days:
            return
        direction = -1 if value <= bar.minimum() + 2 else (1 if value >= bar.maximum() - 2 else 0)
        if not direction:
            return
        self._edge_lock = True
        anchor = self.model.item(index.row()) if index.isValid() else None
        self._anchor_datetime = anchor.get("datetime") if anchor else None
        if direction < 0:
            self.window_start -= timedelta(days=2)
        else:
            self.window_end += timedelta(days=2)
        self.refresh()

    def eventFilter(self, watched, event) -> bool:
        if watched is self.list_view.viewport() and event.type() == QEvent.Type.Wheel:
            self._scroll_intent = True
            QTimer.singleShot(180, self._clear_scroll_intent)
        return super().eventFilter(watched, event)

    def _clear_scroll_intent(self) -> None:
        self._scroll_intent = False

    def open_override(self) -> None:
        index = self.list_view.currentIndex()
        item = self.model.item(index.row()) if index.isValid() else None
        if not item:
            QMessageBox.information(self, "请选择计划", "请先在时间轴中选中一次计划。")
            return
        box = QMessageBox(self)
        box.setWindowTitle("调整本次监督预期")
        box.setText("这只改变 Agent 对本次运行的监督预期，不会修改 MAA 定时器。")
        skip_button = box.addButton("本次跳过", QMessageBox.ButtonRole.AcceptRole)
        move_button = box.addButton("本次改时", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is skip_button:
            self._submit_override(item, "skip", None)
        elif clicked is move_button:
            default = item["datetime"].strftime("%Y-%m-%d %H:%M")
            value, accepted = QInputDialog.getText(self, "修改本次预期时间", "输入 YYYY-MM-DD HH:MM：", text=default)
            if not accepted:
                return
            try:
                target = datetime.strptime(value.strip(), "%Y-%m-%d %H:%M")
            except ValueError:
                QMessageBox.warning(self, "时间格式错误", "请使用 YYYY-MM-DD HH:MM。")
                return
            self._submit_override(item, "move", target)

    def _submit_override(self, item: dict, action: str, target: datetime | None) -> None:
        worker = _OverrideWorker(self.base_dir, dict(item), action, target)
        self._override_workers.add(worker)
        worker.signals.done.connect(lambda message, task=worker: self._override_finished(task, message))
        worker.signals.failed.connect(lambda message, task=worker: self._override_failed(task, message))
        QThreadPool.globalInstance().start(worker)

    def _override_finished(self, worker: _OverrideWorker, message: str) -> None:
        self._override_workers.discard(worker)
        self.status.setText(message)
        self.refresh()

    def _override_failed(self, worker: _OverrideWorker, message: str) -> None:
        self._override_workers.discard(worker)
        QMessageBox.warning(self, "调整失败", message)
