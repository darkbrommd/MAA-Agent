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
from gui.components.time_picker import TimePicker
from gui.components.timeline_delegate import TimelineDelegate
from gui.models.schedule_model import ScheduleModel, schedule_row_key
from gui.services.config_service import ConfigService
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
                    self.signals.done.emit("已恢复这次计划的提醒。")
                else:
                    self.signals.done.emit("这次调整已经不存在，列表已更新。")
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
                self.signals.done.emit("这次提醒已更新。")
        except Exception as exc:
            self.signals.failed.emit(str(exc))


class SchedulePage(QWidget):
    def __init__(self, base_dir: Path, page_state: dict, theme: str = "dark", parent=None):
        super().__init__(parent)
        self.base_dir = Path(base_dir)
        self.page_state = page_state
        self.theme = theme
        stored = page_state.get("selected_day")
        self._has_stored_day = bool(stored)
        try:
            self.selected_day = date.fromisoformat(stored) if stored else date.today()
        except ValueError:
            self.selected_day = date.today()
        self._loading = False
        self._wheel_step_px = 48
        self._anchor_datetime: datetime | None = None
        self._preserve_key: tuple | None = None
        self._preserve_y = 0
        self._preserve_scroll_value = 0
        self._request_contexts: dict[int, dict] = {}
        self._active_request_id = 0
        self._override_workers: set[_OverrideWorker] = set()
        self._pending_override_keys: set[tuple] = set()
        self._pending_toggle_items: dict[tuple, tuple[dict, bool]] = {}
        self._pending_day_start = ""
        self._override_pool = QThreadPool(self)
        self._override_pool.setMaxThreadCount(1)
        self.service = ScheduleService(self.base_dir, parent=self)
        self.service.loaded.connect(self._on_loaded)
        self.service.failed.connect(self._on_failed)
        self.day_start_service = ConfigService(self.base_dir, self)
        self.day_start_service.loaded.connect(self._save_day_start_loaded)
        self.day_start_service.saved.connect(self._day_start_saved)
        self.day_start_service.failed.connect(self._day_start_failed)
        self.model = ScheduleModel(self)
        self.delegate = TimelineDelegate(theme, self)
        self.delegate.error_requested.connect(lambda item: show_schedule_error(self, item, self.theme))
        self.delegate.monitor_toggled.connect(self.toggle_occurrence)
        self.delegate.move_requested.connect(self.move_occurrence)
        self.delegate.clear_requested.connect(self.clear_occurrence)
        self.delegate.day_requested.connect(self._change_day_from_list)
        self._build_ui()
        self.delegate.repaint_requested.connect(self.list_view.viewport().update)
        initial_anchor = (
            datetime.now()
            if self.selected_day == date.today()
            else datetime.combine(self.selected_day, datetime.min.time()).replace(hour=12)
        )
        self.refresh("reset", initial_anchor, current=not self._has_stored_day)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 20)
        root.setSpacing(12)
        title_row = QHBoxLayout()
        title = QLabel("日程管理")
        title.setObjectName("PageTitle")
        title_row.addWidget(title)
        title_row.addStretch(1)
        title_row.addWidget(QLabel("日程每天从"))
        self.day_start_picker = TimePicker("04:00", add_stretch=False)
        title_row.addWidget(self.day_start_picker)
        title_row.addWidget(QLabel("开始"))
        self.day_start_apply = QPushButton("应用")
        self.day_start_apply.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.day_start_apply.clicked.connect(self._save_day_start)
        title_row.addWidget(self.day_start_apply)
        root.addLayout(title_row)
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
        now_button.clicked.connect(self.go_to_now)
        toolbar.addWidget(now_button)
        toolbar.addStretch(1)
        root.addLayout(toolbar)
        hint = QLabel("开关和日期修改只影响当次监测，不会影响 MAA 本身的计划执行。")
        hint.setObjectName("MutedLabel")
        root.addWidget(hint)
        self.list_view = QListView()
        self.list_view.setModel(self.model)
        self.list_view.setItemDelegate(self.delegate)
        self.list_view.setSelectionMode(QListView.SelectionMode.NoSelection)
        self.list_view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list_view.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.list_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_view.setResizeMode(QListView.ResizeMode.Adjust)
        self.list_view.setUniformItemSizes(False)
        self.list_view.viewport().installEventFilter(self)
        self.list_view.verticalScrollBar().valueChanged.connect(self._on_scroll)
        root.addWidget(self.list_view, 1)

    def set_theme(self, theme: str) -> None:
        self.theme = theme
        self.delegate.set_theme(theme)
        self.list_view.viewport().update()

    def on_activated(self) -> None:
        self.refresh_tick()

    def refresh_tick(self) -> None:
        if not self._loading:
            self._capture_view_anchor()
            self.refresh("preserve")

    def set_day(self, selected: date) -> None:
        self.selected_day = selected
        self.page_state["selected_day"] = selected.isoformat()
        self.date_picker.set_date(selected, emit=False)
        self._anchor_datetime = datetime.combine(selected, datetime.min.time()).replace(hour=12)
        self.refresh("reset", self._anchor_datetime)

    def go_to_now(self) -> None:
        self._anchor_datetime = datetime.now()
        self.refresh("reset", self._anchor_datetime, current=True)

    def _change_day_from_list(self, direction: int) -> None:
        if direction:
            self.set_day(self.selected_day + timedelta(days=direction))

    @staticmethod
    def _day_navigation_rows(selected_day: date, rows) -> tuple[dict, ...]:
        previous = selected_day - timedelta(days=1)
        following = selected_day + timedelta(days=1)
        return (
            {
                "kind": "day_navigation",
                "direction": -1,
                "target_day": previous,
                "text": f"‹  切换到上一天 · {previous.month}月{previous.day}日",
            },
            *(dict(row) for row in rows),
            {
                "kind": "day_navigation",
                "direction": 1,
                "target_day": following,
                "text": f"切换到下一天 · {following.month}月{following.day}日  ›",
            },
        )

    def refresh(
        self,
        mode: str = "preserve",
        anchor_datetime: datetime | None = None,
        current: bool = False,
    ) -> int:
        self._loading = True
        context = {
            "mode": mode,
            "anchor_datetime": anchor_datetime,
            "preserve_key": self._preserve_key,
            "preserve_y": self._preserve_y,
            "preserve_scroll_value": self._preserve_scroll_value,
            "selected_day": self.selected_day,
            "current": current,
        }
        request_id = self.service.request_current_day() if current else self.service.request_day(self.selected_day)
        self._request_contexts = {request_id: context}
        self._active_request_id = request_id
        return request_id

    def _on_loaded(self, request_id: int, snapshot: ScheduleSnapshot) -> None:
        context = self._request_contexts.pop(request_id, {})
        if request_id != self._active_request_id:
            return
        self._active_request_id = 0
        mode = str(context.get("mode") or "reset")
        loaded_day = snapshot.meta.get("selected_day")
        if loaded_day:
            self.selected_day = date.fromisoformat(str(loaded_day))
            self.page_state["selected_day"] = self.selected_day.isoformat()
            self.date_picker.set_date(self.selected_day, emit=False)
        start_time = str(snapshot.meta.get("schedule_day_start_time") or "04:00")
        if not self._pending_day_start:
            self.day_start_picker.set_time(start_time)
        display_rows = self._day_navigation_rows(self.selected_day, snapshot.rows)
        update_kind = "reset"
        if mode == "reset":
            self.model.set_rows(display_rows)
        else:
            update_kind = self.model.sync_rows(display_rows)
        self.delegate.sync_toggle_states(self.model.rows())
        if mode == "reset":
            self._anchor_datetime = None
            rows = self.model.rows()
            if len(rows) >= 2:
                model_index = self.model.index(1, 0)
                self.list_view.scrollTo(model_index, QListView.ScrollHint.PositionAtTop)
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

    def _on_failed(self, request_id: int, message: str) -> None:
        if request_id != self._active_request_id:
            return
        context = self._request_contexts.pop(request_id, {})
        self._active_request_id = 0
        self._loading = False
        self._show_transient_message(f"日程读取失败：{message}")

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
        row = self.model.row_for_key(key)
        if row < 0:
            bar.setValue(min(scroll_value, bar.maximum()))
            return
        index = self.model.index(row, 0)
        if update_kind == "reset":
            self.list_view.scrollTo(index, QListView.ScrollHint.PositionAtTop)
            bar.setValue(max(bar.minimum(), min(bar.maximum(), bar.value() - anchor_y)))
        self.list_view.setCurrentIndex(index)

    def _on_scroll(self, value: int) -> None:
        del value

    def eventFilter(self, watched, event) -> bool:
        if watched is self.list_view.viewport() and event.type() == QEvent.Type.Wheel:
            delta = event.angleDelta().y()
            bar = self.list_view.verticalScrollBar()
            if event.pixelDelta().isNull() and delta:
                steps = delta / 120.0
                bar.setValue(round(bar.value() - steps * self._wheel_step_px))
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def _show_transient_message(self, message: str) -> None:
        window = self.window()
        status_bar = getattr(window, "statusBar", None)
        if callable(status_bar):
            status_bar().showMessage(message, 6000)

    def _save_day_start(self) -> None:
        self._pending_day_start = self.day_start_picker.time()
        self.day_start_apply.setEnabled(False)
        self.day_start_apply.setText("保存中…")
        self.day_start_service.load()

    def _save_day_start_loaded(self, data: dict) -> None:
        if not self._pending_day_start:
            return
        updated = dict(data)
        updated["schedule_day_start_time"] = self._pending_day_start
        self.day_start_service.save(updated)

    def _day_start_saved(self, _path: Path) -> None:
        self._pending_day_start = ""
        self.day_start_apply.setEnabled(True)
        self.day_start_apply.setText("应用")
        self._show_transient_message("日程开始时间已保存。")
        self.refresh("reset")

    def _day_start_failed(self, message: str) -> None:
        self._pending_day_start = ""
        self.day_start_apply.setEnabled(True)
        self.day_start_apply.setText("应用")
        QMessageBox.warning(self, "没有保存成功", message)

    def toggle_occurrence(self, item: dict, enabled: bool) -> None:
        action = "clear" if enabled else "skip"
        if self._submit_override(item, action, None):
            key = schedule_row_key(item)
            self._pending_toggle_items[key] = (dict(item), not bool(item.get("skipped")))
            self.delegate.animate_toggle(item, enabled)

    def move_occurrence(self, item: dict) -> None:
        default = item["datetime"].strftime("%Y-%m-%d %H:%M")
        value, accepted = QInputDialog.getText(self, "修改这次提醒时间", "输入 YYYY-MM-DD HH:MM（可以跨天）：", text=default)
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
            self._show_transient_message("这条计划正在保存，请稍候。")
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
        self._show_transient_message(message)
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
        QMessageBox.warning(self, "调整失败", message)
