from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPoint, QRunnable, QThreadPool, QTimer, Qt, Signal
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
from gui.models.schedule_model import ScheduleModel, schedule_row_key
from gui.services.schedule_service import ScheduleService, ScheduleSnapshot
from project_paths import config_path
from schedule_utils import add_override, remove_override


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
            if self.action == "clear":
                override = self.item.get("override") or {}
                removed = remove_override(self.base_dir, cfg, override_id=override.get("id"))
                if not removed:
                    removed = remove_override(
                        self.base_dir,
                        cfg,
                        label=str(self.item["label"]),
                        original_dt=self.item.get("original_datetime") or self.item["datetime"],
                    )
                if removed:
                    self.signals.done.emit("已撤销本次例外；MAA 自己的定时器没有修改。")
                else:
                    self.signals.done.emit("本次例外已不存在，列表已刷新。")
            else:
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
        self._wheel_blocked = False
        self._extension_direction = 0
        self._anchor_datetime: datetime | None = None
        self._preserve_key: tuple | None = None
        self._preserve_y = 0
        self._preserve_scroll_value = 0
        self._request_contexts: dict[int, dict] = {}
        self._active_request_id = 0
        self._override_workers: set[_OverrideWorker] = set()
        self._pending_override_keys: set[tuple] = set()
        self._pending_toggle_items: dict[tuple, tuple[dict, bool]] = {}
        self._override_pool = QThreadPool(self)
        self._override_pool.setMaxThreadCount(1)
        self._wheel_quiet_timer = QTimer(self)
        self._wheel_quiet_timer.setSingleShot(True)
        self._wheel_quiet_timer.setInterval(460)
        self._wheel_quiet_timer.timeout.connect(self._release_wheel_block)
        self.service = ScheduleService(self.base_dir, parent=self)
        self.service.loaded.connect(self._on_loaded)
        self.service.failed.connect(self._on_failed)
        self.model = ScheduleModel(self)
        self.delegate = TimelineDelegate(theme, self)
        self.delegate.error_requested.connect(lambda item: show_schedule_error(self, item))
        self.delegate.monitor_toggled.connect(self.toggle_occurrence)
        self.delegate.move_requested.connect(self.move_occurrence)
        self.delegate.clear_requested.connect(self.clear_occurrence)
        self._build_ui()
        self.delegate.repaint_requested.connect(self.list_view.viewport().update)
        initial_anchor = (
            datetime.now()
            if self.selected_day == date.today()
            else datetime.combine(self.selected_day, datetime.min.time()).replace(hour=12)
        )
        self.refresh("reset", initial_anchor)

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
        previous.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        next_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        previous.clicked.connect(lambda: self.set_day(self.selected_day - timedelta(days=1)))
        next_button.clicked.connect(lambda: self.set_day(self.selected_day + timedelta(days=1)))
        toolbar.addWidget(previous)
        self.date_picker = DatePicker(self.selected_day)
        self.date_picker.date_changed.connect(self.set_day)
        toolbar.addWidget(self.date_picker)
        toolbar.addWidget(next_button)
        now_button = QPushButton("回到现在")
        now_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        now_button.clicked.connect(lambda: self.set_day(date.today()))
        toolbar.addWidget(now_button)
        toolbar.addStretch(1)
        root.addLayout(toolbar)
        hint = QLabel("直接使用卡片开关、修改时间或撤销例外；这些操作只调整 Agent 监督预期，不修改 MAA 定时器。")
        hint.setObjectName("MutedLabel")
        root.addWidget(hint)
        self.status = QLabel("正在读取日程…")
        self.status.setObjectName("MutedLabel")
        root.addWidget(self.status)
        self.list_view = QListView()
        self.list_view.setModel(self.model)
        self.list_view.setItemDelegate(self.delegate)
        self.list_view.setSelectionMode(QListView.SelectionMode.NoSelection)
        self.list_view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list_view.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.list_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_view.setResizeMode(QListView.ResizeMode.Adjust)
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
        self._cancel_wheel_block()
        self.refresh("reset", self._anchor_datetime)

    def refresh(self, mode: str = "preserve", anchor_datetime: datetime | None = None) -> int:
        if not mode.startswith("extend"):
            self._cancel_wheel_block()
        self._loading = True
        self.status.setText(f"正在读取 {self.window_start:%m-%d} 至 {self.window_end:%m-%d}…")
        start = datetime.combine(self.window_start, datetime.min.time())
        end = datetime.combine(self.window_end + timedelta(days=1), datetime.min.time()) - timedelta(microseconds=1)
        context = {
            "mode": mode,
            "anchor_datetime": anchor_datetime,
            "preserve_key": self._preserve_key,
            "preserve_y": self._preserve_y,
            "preserve_scroll_value": self._preserve_scroll_value,
        }
        request_id = self.service.request_window(start, end)
        self._request_contexts = {request_id: context}
        self._active_request_id = request_id
        return request_id

    def _on_loaded(self, request_id: int, snapshot: ScheduleSnapshot) -> None:
        context = self._request_contexts.pop(request_id, {})
        if request_id != self._active_request_id:
            return
        self._active_request_id = 0
        mode = str(context.get("mode") or "reset")
        update_kind = "reset"
        if mode == "reset":
            self.model.set_rows(snapshot.rows)
        else:
            update_kind = self.model.sync_rows(snapshot.rows)
        self.delegate.sync_toggle_states(self.model.rows())
        self.status.setText(f"已加载 {snapshot.start:%m-%d} 至 {snapshot.end:%m-%d} · {len(snapshot.rows)} 个计划")
        if mode == "reset":
            target = context.get("anchor_datetime") or datetime.now()
            self._anchor_datetime = None
            rows = self.model.rows()
            if rows:
                index = min(range(len(rows)), key=lambda row: abs((rows[row]["datetime"] - target).total_seconds()))
                model_index = self.model.index(index, 0)
                self.list_view.scrollTo(model_index, QListView.ScrollHint.PositionAtCenter)
                self.list_view.setCurrentIndex(model_index)
        else:
            key = context.get("preserve_key")
            y = int(context.get("preserve_y") or 0)
            value = int(context.get("preserve_scroll_value") or 0)
            QTimer.singleShot(
                0,
                lambda kind=update_kind, load_mode=mode, anchor_key=key, anchor_y=y, scroll_value=value:
                    self._restore_after_update(kind, load_mode, anchor_key, anchor_y, scroll_value),
            )
        self._loading = False
        self._edge_lock = False
        if mode.startswith("extend"):
            QCoreApplication.removePostedEvents(self.list_view.viewport(), QEvent.Type.Wheel)
            self._wheel_quiet_timer.start()
        else:
            self._cancel_wheel_block()

    def _on_failed(self, request_id: int, message: str) -> None:
        if request_id != self._active_request_id:
            return
        self._request_contexts.pop(request_id, None)
        self._active_request_id = 0
        self._loading = False
        self._edge_lock = False
        self._cancel_wheel_block()
        self.status.setText(f"日程读取失败：{message}")

    def _capture_view_anchor(self) -> None:
        index = self.list_view.indexAt(QPoint(12, 12))
        if not index.isValid():
            index = self.list_view.currentIndex()
        item = self.model.item(index.row()) if index.isValid() else None
        self._preserve_key = schedule_row_key(item) if item else None
        self._preserve_y = self.list_view.visualRect(index).top() if index.isValid() else 0
        self._preserve_scroll_value = self.list_view.verticalScrollBar().value()

    def _restore_after_update(
        self,
        update_kind: str,
        mode: str,
        key: tuple | None = None,
        anchor_y: int | None = None,
        scroll_value: int | None = None,
    ) -> None:
        bar = self.list_view.verticalScrollBar()
        key = self._preserve_key if key is None else key
        anchor_y = self._preserve_y if anchor_y is None else anchor_y
        scroll_value = self._preserve_scroll_value if scroll_value is None else scroll_value
        if mode == "extend_down" and update_kind == "append":
            bar.setValue(scroll_value)
            return
        row = self.model.row_for_key(key)
        if row < 0:
            bar.setValue(min(scroll_value, bar.maximum()))
            return
        index = self.model.index(row, 0)
        if update_kind in {"prepend", "reset"}:
            self.list_view.scrollTo(index, QListView.ScrollHint.PositionAtTop)
            bar.setValue(max(bar.minimum(), min(bar.maximum(), bar.value() - anchor_y)))
        self.list_view.setCurrentIndex(index)

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
        if self._edge_lock or self._wheel_blocked or not self._scroll_intent:
            return
        bar = self.list_view.verticalScrollBar()
        direction = -1 if value <= bar.minimum() + 2 else (1 if value >= bar.maximum() - 2 else 0)
        self._try_extend(direction)

    def _try_extend(self, direction: int) -> bool:
        if not direction or self._edge_lock or self._wheel_blocked or self._loading:
            return False
        loaded_days = (self.window_end - self.window_start).days + 1
        if loaded_days >= self._max_window_days:
            return False
        self._edge_lock = True
        self._wheel_blocked = True
        self._extension_direction = direction
        self._wheel_quiet_timer.stop()
        self._scroll_intent = False
        self._capture_view_anchor()
        if direction < 0:
            self.window_start -= timedelta(days=2)
            mode = "extend_up"
        else:
            self.window_end += timedelta(days=2)
            mode = "extend_down"
        self.refresh(mode)
        return True

    def eventFilter(self, watched, event) -> bool:
        if watched is self.list_view.viewport() and event.type() == QEvent.Type.Wheel:
            delta = event.angleDelta().y()
            wheel_direction = -1 if delta > 0 else (1 if delta < 0 else 0)
            if self._wheel_blocked or self._loading:
                if self._wheel_blocked and not self._loading and wheel_direction == -self._extension_direction:
                    self._cancel_wheel_block()
                else:
                    if self._wheel_blocked:
                        self._wheel_quiet_timer.start()
                    event.accept()
                    return True
            bar = self.list_view.verticalScrollBar()
            at_edge = (wheel_direction < 0 and bar.value() <= bar.minimum() + 2) or (
                wheel_direction > 0 and bar.value() >= bar.maximum() - 2
            )
            if at_edge and self._try_extend(wheel_direction):
                event.accept()
                return True
            self._scroll_intent = True
            QTimer.singleShot(140, self._clear_scroll_intent)
        return super().eventFilter(watched, event)

    def _clear_scroll_intent(self) -> None:
        self._scroll_intent = False

    def _release_wheel_block(self) -> None:
        if self._loading or self._edge_lock:
            return
        self._wheel_blocked = False
        self._extension_direction = 0

    def _cancel_wheel_block(self) -> None:
        self._wheel_quiet_timer.stop()
        self._wheel_blocked = False
        self._extension_direction = 0

    def toggle_occurrence(self, item: dict, enabled: bool) -> None:
        action = "clear" if enabled else "skip"
        if self._submit_override(item, action, None):
            key = schedule_row_key(item)
            self._pending_toggle_items[key] = (dict(item), not bool(item.get("skipped")))
            self.delegate.animate_toggle(item, enabled)

    def move_occurrence(self, item: dict) -> None:
        default = item["datetime"].strftime("%Y-%m-%d %H:%M")
        value, accepted = QInputDialog.getText(self, "修改本次预期时间", "输入 YYYY-MM-DD HH:MM（可跨天）：", text=default)
        if not accepted:
            return
        try:
            target = datetime.strptime(value.strip(), "%Y-%m-%d %H:%M")
        except ValueError:
            QMessageBox.warning(self, "时间格式错误", "请使用 YYYY-MM-DD HH:MM，例如 2026-09-20 23:30。")
            return
        self._submit_override(item, "move", target)

    def clear_occurrence(self, item: dict) -> None:
        if item.get("override"):
            self._submit_override(item, "clear", None)

    def _submit_override(self, item: dict, action: str, target: datetime | None) -> bool:
        key = schedule_row_key(item)
        if key in self._pending_override_keys:
            self.status.setText("这条计划的调整正在保存，请稍候。")
            return False
        worker = _OverrideWorker(self.base_dir, dict(item), action, target)
        self._override_workers.add(worker)
        self._pending_override_keys.add(key)
        worker.signals.done.connect(lambda message, task=worker, item_key=key: self._override_finished(task, item_key, message))
        worker.signals.failed.connect(lambda message, task=worker, item_key=key: self._override_failed(task, item_key, message))
        self._override_pool.start(worker)
        return True

    def _override_finished(self, worker: _OverrideWorker, key: tuple, message: str) -> None:
        self._override_workers.discard(worker)
        self._pending_override_keys.discard(key)
        self._pending_toggle_items.pop(key, None)
        self.status.setText(message)
        active = self._request_contexts.get(self._active_request_id, {})
        if active.get("mode") == "reset":
            self.refresh("reset", active.get("anchor_datetime"))
        else:
            self._capture_view_anchor()
            self.refresh("preserve")

    def _override_failed(self, worker: _OverrideWorker, key: tuple, message: str) -> None:
        self._override_workers.discard(worker)
        self._pending_override_keys.discard(key)
        pending = self._pending_toggle_items.pop(key, None)
        if pending is not None:
            item, original_enabled = pending
            self.delegate.animate_toggle(item, original_enabled)
            QTimer.singleShot(180, lambda: self.delegate.sync_toggle_states(self.model.rows()))
        self.status.setText(f"调整失败：{message}")
        QMessageBox.warning(self, "调整失败", message)
