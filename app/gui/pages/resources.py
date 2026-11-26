from __future__ import annotations

import json
import os
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QObject, QRectF, QRunnable, QThreadPool, QTimer, Qt, Signal, QSize
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QFileDialog,
    QInputDialog,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from gui.components.resource_chart import ResourceChart
from gui.components.date_picker import DatePicker
from gui.components.responsive_layout import responsive_card_rect
from gui.components.dialogs import show_resource_detail
from gui.models.resource_model import ResourceModel
from gui.services.resource_service import ResourceService, ResourceSnapshot
from gui.themes.tokens import DARK, LIGHT
from project_paths import config_path
from resource_history import memory_values
from resource_whitelist import normalize_resource_whitelist, valid_process_regex


class _ConfigSignals(QObject):
    done = Signal(object)


class _WhitelistWorker(QRunnable):
    def __init__(self, base_dir: Path):
        super().__init__()
        self.base_dir = base_dir
        self.signals = _ConfigSignals()

    def run(self) -> None:
        try:
            value = json.loads(config_path(self.base_dir).read_text(encoding="utf-8-sig"))
            rows = normalize_resource_whitelist(value)
            start_hour = int(value.get("operational_day_start_hour", 4))
        except Exception:
            rows = []
            start_hour = 4
        self.signals.done.emit({"rows": tuple(dict(row) for row in rows), "start_hour": start_hour})


class _WhitelistSaveSignals(QObject):
    done = Signal(bool, str)


class _WhitelistSaveWorker(QRunnable):
    def __init__(self, base_dir: Path, rows: tuple[dict, ...]):
        super().__init__()
        self.base_dir = base_dir
        self.rows = rows
        self.signals = _WhitelistSaveSignals()

    def run(self) -> None:
        path = config_path(self.base_dir)
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
            value["resource_whitelist"] = [dict(row) for row in self.rows]
            path.parent.mkdir(parents=True, exist_ok=True)
            backup = path.with_suffix(path.suffix + ".bak")
            if path.exists():
                shutil.copy2(path, backup)
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporary, path)
            self.signals.done.emit(True, "名单已保存，之后会按新名单检查。")
        except Exception as exc:
            self.signals.done.emit(False, str(exc))


class _ResourceDelegate(QStyledItemDelegate):
    def __init__(self, theme: str, parent=None):
        super().__init__(parent)
        self.theme = theme

    def set_theme(self, theme: str) -> None:
        self.theme = theme

    def sizeHint(self, option, index) -> QSize:
        return QSize(1, 82)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        item = index.data(ResourceModel.ItemRole) or {}
        tokens = DARK if self.theme == "dark" else LIGHT
        rect = responsive_card_rect(option.rect, horizontal_margin=8, vertical_margin=4)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor(tokens.border), 1))
        painter.setBrush(QColor(tokens.card))
        painter.drawRoundedRect(rect, 9, 9)
        when = datetime.fromtimestamp(float(item.get("epoch", 0)))
        painter.setPen(QColor(tokens.warning))
        painter.setFont(QFont("Segoe UI", 14, QFont.Weight.DemiBold))
        painter.drawText(QRectF(rect.left() + 14, rect.top() + 8, 70, 28), Qt.AlignmentFlag.AlignVCenter, when.strftime("%H:%M"))
        reasons = "；".join(item.get("abnormal_reasons") or ()) or f"内存 {item.get('ram', 0):.0f}%"
        painter.setPen(QColor(tokens.text))
        painter.setFont(QFont("Microsoft YaHei UI", 10, QFont.Weight.DemiBold))
        painter.drawText(QRectF(rect.left() + 92, rect.top() + 7, rect.width() - 108, 26), Qt.AlignmentFlag.AlignVCenter, reasons)
        used, available, _total = memory_values(item)
        painter.setPen(QColor(tokens.muted))
        painter.setFont(QFont("Microsoft YaHei UI", 9))
        painter.drawText(QRectF(rect.left() + 92, rect.top() + 36, rect.width() - 108, 24), Qt.AlignmentFlag.AlignVCenter, f"已用 {used/1024:.1f} GB · 可用 {available/1024:.1f} GB · 双击查看详情")
        painter.restore()


class ResourcesPage(QWidget):
    def __init__(self, base_dir: Path, page_state: dict, theme: str = "dark", parent=None):
        super().__init__(parent)
        self.base_dir = Path(base_dir)
        self.page_state = page_state
        stored = page_state.get("selected_day")
        try:
            self.selected_day = date.fromisoformat(stored) if stored else date.today()
        except ValueError:
            self.selected_day = date.today()
        self.theme = theme
        self.start_hour = 4
        self.service = ResourceService(self.base_dir, parent=self)
        self.service.loaded.connect(self._on_loaded)
        self.service.failed.connect(self._on_failed)
        self.model = ResourceModel(self)
        self.delegate = _ResourceDelegate(theme, self)
        self._whitelist_workers: set[_WhitelistWorker] = set()
        self._whitelist_rows: list[dict] = []
        self._whitelist_save_worker: _WhitelistSaveWorker | None = None
        self._splitter_save_timer = QTimer(self)
        self._splitter_save_timer.setSingleShot(True)
        self._splitter_save_timer.setInterval(220)
        self._splitter_save_timer.timeout.connect(self._save_splitter_sizes)
        self._build_ui()
        self._load_whitelist()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 20)
        root.setSpacing(12)
        header = QHBoxLayout()
        title = QLabel("资源监控")
        title.setObjectName("PageTitle")
        header.addWidget(title)
        header.addStretch(1)
        segments = QButtonGroup(self)
        segments.setExclusive(True)
        history_button = QPushButton("历史趋势")
        whitelist_button = QPushButton("高占用程序")
        for index, button in enumerate((history_button, whitelist_button)):
            button.setCheckable(True)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            segments.addButton(button, index)
            header.addWidget(button)
        history_button.setChecked(True)
        segments.idClicked.connect(self._switch_segment)
        root.addLayout(header)
        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        history = QWidget()
        history_layout = QVBoxLayout(history)
        history_layout.setContentsMargins(0, 0, 0, 0)
        navigation = QHBoxLayout()
        previous = QPushButton("‹")
        next_button = QPushButton("›")
        today = QPushButton("回到今天")
        for button in (previous, today, next_button):
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        previous.clicked.connect(lambda: self.request_day(self.selected_day - timedelta(days=1)))
        next_button.clicked.connect(lambda: self.request_day(self.selected_day + timedelta(days=1)))
        today.clicked.connect(lambda: self.request_day(date.today()))
        navigation.addWidget(previous)
        self.date_picker = DatePicker(self.selected_day)
        self.date_picker.date_changed.connect(self.request_day)
        navigation.addWidget(self.date_picker)
        navigation.addWidget(next_button)
        navigation.addWidget(today)
        navigation.addStretch(1)
        self.day_label = QLabel()
        navigation.addWidget(self.day_label)
        history_layout.addLayout(navigation)
        self.summary = QLabel("正在载入记录…")
        self.summary.setObjectName("MutedLabel")
        history_layout.addWidget(self.summary)
        self.chart = ResourceChart(self.theme)
        self.chart.snapshot_activated.connect(self._show_detail)
        self.history_splitter = QSplitter(Qt.Orientation.Vertical)
        self.history_splitter.setChildrenCollapsible(False)
        self.history_splitter.setOpaqueResize(True)
        self.history_splitter.setHandleWidth(6)
        self.history_splitter.addWidget(self.chart)
        anomaly_panel = QWidget()
        anomaly_layout = QVBoxLayout(anomaly_panel)
        anomaly_layout.setContentsMargins(0, 0, 0, 0)
        anomaly_layout.setSpacing(6)
        anomaly_title = QLabel("内存过高记录")
        anomaly_title.setStyleSheet("font-weight: 600;")
        anomaly_layout.addWidget(anomaly_title)
        self.anomalies = QListView()
        self.anomalies.setModel(self.model)
        self.anomalies.setItemDelegate(self.delegate)
        self.anomalies.setSelectionMode(QListView.SelectionMode.NoSelection)
        self.anomalies.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.anomalies.setUniformItemSizes(True)
        self.anomalies.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.anomalies.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.anomalies.setResizeMode(QListView.ResizeMode.Adjust)
        self.anomalies.doubleClicked.connect(lambda index: self._show_detail(self.model.item(index.row()) or {}))
        anomaly_layout.addWidget(self.anomalies, 1)
        anomaly_panel.setMinimumHeight(140)
        self.history_splitter.addWidget(anomaly_panel)
        self.history_splitter.setStretchFactor(0, 3)
        self.history_splitter.setStretchFactor(1, 2)
        self.history_splitter.splitterMoved.connect(lambda _position, _index: self._splitter_save_timer.start())
        history_layout.addWidget(self.history_splitter, 1)
        saved_sizes = self.page_state.get("splitter_sizes")
        sizes = [int(value) for value in saved_sizes] if isinstance(saved_sizes, list) and len(saved_sizes) == 2 else [480, 250]
        QTimer.singleShot(0, lambda values=sizes: self.history_splitter.setSizes(values))
        self.stack.addWidget(history)
        whitelist = QWidget()
        whitelist_layout = QVBoxLayout(whitelist)
        whitelist_layout.setContentsMargins(0, 0, 0, 0)
        note = QLabel("把游戏等常用高占用程序加入这里，运行时可减少不必要的提醒。内存快用完时仍会提醒。")
        note.setObjectName("MutedLabel")
        whitelist_layout.addWidget(note)
        whitelist_actions = QHBoxLayout()
        self.whitelist_add = QPushButton("添加程序…")
        self.whitelist_add_regex = QPushButton("按名称添加…")
        self.whitelist_rename = QPushButton("修改名称")
        self.whitelist_toggle = QPushButton("启用 / 停用")
        self.whitelist_remove = QPushButton("删除")
        self.whitelist_add.clicked.connect(self._add_whitelist)
        self.whitelist_add_regex.clicked.connect(self._add_regex_whitelist)
        self.whitelist_rename.clicked.connect(self._rename_whitelist)
        self.whitelist_toggle.clicked.connect(self._toggle_whitelist)
        self.whitelist_remove.clicked.connect(self._remove_whitelist)
        for button in (self.whitelist_add, self.whitelist_add_regex, self.whitelist_rename, self.whitelist_toggle, self.whitelist_remove):
            whitelist_actions.addWidget(button)
        whitelist_actions.addStretch(1)
        whitelist_layout.addLayout(whitelist_actions)
        self.whitelist_status = QLabel("")
        self.whitelist_status.setObjectName("MutedLabel")
        whitelist_layout.addWidget(self.whitelist_status)
        self.whitelist = QListWidget()
        self.whitelist.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.whitelist.setWordWrap(True)
        whitelist_layout.addWidget(self.whitelist, 1)
        self.stack.addWidget(whitelist)
        segment = int(self.page_state.get("segment", 0))
        segment = segment if segment in (0, 1) else 0
        self.stack.setCurrentIndex(segment)
        (history_button if segment == 0 else whitelist_button).setChecked(True)
        self._update_day_label()

    def _save_splitter_sizes(self) -> None:
        sizes = self.history_splitter.sizes()
        if len(sizes) == 2 and all(size > 0 for size in sizes):
            self.page_state["splitter_sizes"] = sizes

    def set_theme(self, theme: str) -> None:
        self.theme = theme
        self.chart.set_theme(theme)
        self.delegate.set_theme(theme)
        self._render_whitelist()
        self.anomalies.viewport().update()

    def _switch_segment(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.page_state["segment"] = index

    def request_day(self, selected: date) -> None:
        self.selected_day = selected
        self.page_state["selected_day"] = selected.isoformat()
        self.date_picker.set_date(selected, emit=False)
        self._update_day_label()
        self.summary.setText("正在载入记录…")
        self.service.request_day(selected, self.start_hour)

    def _update_day_label(self) -> None:
        end = self.selected_day + timedelta(days=1)
        self.day_label.setText(f"{self.selected_day:%Y-%m-%d 00:00} → {end:%m-%d 00:00}")

    def _on_loaded(self, request_id: int, snapshot: ResourceSnapshot) -> None:
        if snapshot.day != self.selected_day:
            return
        rows = snapshot.rows
        self.chart.set_data(rows, snapshot.day)
        self.model.set_rows(rows)
        if rows:
            ram = [float(row.get("ram", 0)) for row in rows]
            used = [memory_values(row)[0] for row in rows]
            abnormal = sum(bool(row.get("abnormal")) for row in rows)
            self.summary.setText(f"共 {len(rows)} 次记录 · 内存 {min(ram):.0f}%–{max(ram):.0f}% · 最高已用 {max(used)/1024:.1f} GB · 过高 {abnormal} 次")
        else:
            self.summary.setText("这一天还没有资源记录。")

    def _on_failed(self, request_id: int, message: str) -> None:
        self.summary.setText(f"无法读取资源记录：{message}")

    def _show_detail(self, row: dict) -> None:
        if not row:
            return
        show_resource_detail(self, row, self.theme)

    def _load_whitelist(self) -> None:
        worker = _WhitelistWorker(self.base_dir)
        self._whitelist_workers.add(worker)
        worker.signals.done.connect(lambda rows, task=worker: self._show_whitelist(task, rows))
        QThreadPool.globalInstance().start(worker)

    def _show_whitelist(self, worker: _WhitelistWorker, payload) -> None:
        self._whitelist_workers.discard(worker)
        self.start_hour = int(payload.get("start_hour", 4))
        self._whitelist_rows = [dict(row) for row in payload.get("rows", ())]
        self._render_whitelist()
        self.request_day(self.selected_day)

    def _render_whitelist(self) -> None:
        self.whitelist.clear()
        if not self._whitelist_rows:
            self.whitelist.addItem("还没有添加程序")
            return
        for index, row in enumerate(self._whitelist_rows):
            enabled = bool(row.get("enabled", True))
            pattern = str(row.get("process_regex") or "")
            target = f"名称规则 · {pattern}" if pattern else (row.get("path") or row.get("process_name") or "未指定路径")
            text = f"{'●' if enabled else '○'}  {row.get('name') or row.get('process_name') or '程序'}\n    {target}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, index)
            if not enabled:
                item.setForeground(QColor((DARK if self.theme == "dark" else LIGHT).disabled_text))
            self.whitelist.addItem(item)

    def _selected_whitelist_index(self) -> int | None:
        item = self.whitelist.currentItem()
        if item is None:
            QMessageBox.information(self, "请选择程序", "请先选择一个程序。")
            return None
        value = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(value, int) or not 0 <= value < len(self._whitelist_rows):
            QMessageBox.information(self, "请选择程序", "请先选择一个程序。")
            return None
        return value

    def _add_whitelist(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, "选择需要减少占用提醒的程序", "", "程序文件 (*.exe);;所有文件 (*.*)")
        if not path:
            return
        executable = Path(path)
        identity = os.path.normcase(os.path.abspath(path))
        if any(os.path.normcase(os.path.abspath(str(row.get("path") or ""))) == identity for row in self._whitelist_rows if row.get("path")):
            QMessageBox.information(self, "已经存在", "这个程序已经在名单里。")
            return
        self._whitelist_rows.append({"name": executable.stem, "process_name": executable.name, "path": str(executable), "enabled": True})
        self._render_whitelist()
        self.whitelist.setCurrentRow(len(self._whitelist_rows) - 1)
        self._save_whitelist()

    def _add_regex_whitelist(self) -> None:
        pattern, accepted = QInputDialog.getText(
            self,
            "按程序名称添加",
            "输入名称匹配规则，例如 .*GTAV.*（表示名称中包含 GTAV）：",
        )
        pattern = pattern.strip()
        if not accepted or not pattern:
            return
        if not valid_process_regex(pattern):
            QMessageBox.warning(self, "规则无法使用", "这个名称规则无法识别，请检查后再试。")
            return
        if any(str(row.get("process_regex") or "").casefold() == pattern.casefold() for row in self._whitelist_rows):
            QMessageBox.information(self, "已经存在", "这个名称规则已经在名单里。")
            return
        name, named = QInputDialog.getText(self, "规则显示名称", "显示名称：", text=pattern)
        if not named:
            return
        self._whitelist_rows.append({
            "name": name.strip() or pattern,
            "process_name": "",
            "process_regex": pattern,
            "path": "",
            "enabled": True,
        })
        self._render_whitelist()
        self.whitelist.setCurrentRow(len(self._whitelist_rows) - 1)
        self._save_whitelist()

    def _rename_whitelist(self) -> None:
        index = self._selected_whitelist_index()
        if index is None:
            return
        row = self._whitelist_rows[index]
        value, accepted = QInputDialog.getText(self, "修改显示名称", "显示名称：", text=str(row.get("name") or ""))
        if not accepted or not value.strip():
            return
        row["name"] = value.strip()
        self._render_whitelist()
        self.whitelist.setCurrentRow(index)
        self._save_whitelist()

    def _toggle_whitelist(self) -> None:
        index = self._selected_whitelist_index()
        if index is None:
            return
        self._whitelist_rows[index]["enabled"] = not bool(self._whitelist_rows[index].get("enabled", True))
        self._render_whitelist()
        self.whitelist.setCurrentRow(index)
        self._save_whitelist()

    def _remove_whitelist(self) -> None:
        index = self._selected_whitelist_index()
        if index is None:
            return
        row = self._whitelist_rows[index]
        if QMessageBox.question(self, "从名单中删除", f"确定删除“{row.get('name') or row.get('process_name')}”吗？") != QMessageBox.StandardButton.Yes:
            return
        self._whitelist_rows.pop(index)
        self._render_whitelist()
        self._save_whitelist()

    def _save_whitelist(self) -> None:
        if self._whitelist_save_worker is not None:
            self.whitelist_status.setText("正在保存上一项更改，请稍候…")
            return
        self._set_whitelist_actions_enabled(False)
        self.whitelist_status.setText("正在保存…")
        worker = _WhitelistSaveWorker(self.base_dir, tuple(dict(row) for row in self._whitelist_rows))
        self._whitelist_save_worker = worker
        worker.signals.done.connect(self._whitelist_saved)
        QThreadPool.globalInstance().start(worker)

    def _whitelist_saved(self, ok: bool, message: str) -> None:
        self._whitelist_save_worker = None
        self._set_whitelist_actions_enabled(True)
        self.whitelist_status.setText(message)
        if not ok:
            QMessageBox.warning(self, "名单没有保存成功", message)

    def _set_whitelist_actions_enabled(self, enabled: bool) -> None:
        for button in (self.whitelist_add, self.whitelist_add_regex, self.whitelist_rename, self.whitelist_toggle, self.whitelist_remove):
            button.setEnabled(enabled)
