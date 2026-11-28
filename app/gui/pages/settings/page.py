from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

import diagnostic_commands
from notification_samples import SAMPLES, SAMPLES_BY_LABEL
from gui.components.toggle_switch import ToggleSwitch
from gui.components.time_picker import NoWheelComboBox, TimePicker
from gui.services.config_service import ConfigService


SECTIONS = (
    ("general", "常规与程序"),
    ("monitoring", "提醒设置"),
    ("bark_ai", "手机通知与 AI"),
    ("timing", "高级设置"),
    ("diagnostics", "通知测试"),
    ("about", "关于"),
)

TIMING_FIELDS = (
    ("每隔多久检查一次（秒）", "check_interval_seconds", "int"),
    ("开机后等待多久再提醒（分钟）", "startup_process_grace_minutes", "int"),
    ("程序关闭多久后提醒（分钟）", "process_missing_sustain_minutes", "int"),
    ("普通错误持续多久后提醒（分钟）", "generic_error_stall_minutes", "int"),
    ("连接异常持续多久后提醒（分钟）", "connection_error_stall_minutes", "int"),
    ("计划到点后等待多久（分钟）", "scheduled_run_grace_minutes", "int"),
    ("窗口卡住多久后提醒（分钟）", "hung_window_sustain_minutes", "int"),
    ("每隔多久检查代理连接（分钟）", "proxy_healthcheck_interval_minutes", "int"),
    ("代理连接异常多久后提醒（分钟）", "proxy_failure_sustain_minutes", "int"),
    ("在线状态上报间隔（分钟）", "heartbeat_interval_minutes", "int"),
)

LOCAL_PREVIEWS = {sample.label: sample.body for sample in SAMPLES}

TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
DIAGNOSTIC_CASES = {sample.label: (sample.kind, sample.target) for sample in SAMPLES}
FIELD_DEFAULTS = {
    "bark_server": "https://api.day.app",
    "bark_retry_attempts": 3,
    "bark_retry_sleep_seconds": 3,
    "ai_max_calls_per_day": 1,
    "memory_pressure_percent": 88,
    "memory_pressure_sustain_minutes": 30,
    "memory_critical_percent": 95,
    "memory_critical_sustain_minutes": 10,
    "memory_critical_available_mb": 1536,
    "resource_sample_abnormal_steady_minutes": 5,
    "news_check_times": ["00:05", "12:05"],
    "daily_report_time": "23:50",
    "schedule_day_start_time": "04:00",
    "check_interval_seconds": 60,
    "startup_process_grace_minutes": 10,
    "process_missing_sustain_minutes": 3,
    "generic_error_stall_minutes": 6,
    "connection_error_stall_minutes": 5,
    "scheduled_run_grace_minutes": 20,
    "hung_window_sustain_minutes": 2,
    "proxy_healthcheck_interval_minutes": 2,
    "proxy_failure_sustain_minutes": 10,
    "heartbeat_interval_minutes": 5,
}
BOOLEAN_DEFAULTS = {
    "gui_start_agent_on_launch": True,
    "gui_start_minimized": True,
    "enable_memory_alert": True,
    "enable_proxy_healthcheck": True,
    "enable_news_monitor": True,
    "ai_daily_report": True,
    "ai_daily_when_useful_only": True,
    "enable_daily_report": True,
}


class _NoWheelSpinBox(QSpinBox):
    def wheelEvent(self, event) -> None:
        if not self.hasFocus():
            event.ignore()
            return
        super().wheelEvent(event)


class _NoWheelDoubleSpinBox(QDoubleSpinBox):
    def wheelEvent(self, event) -> None:
        if not self.hasFocus():
            event.ignore()
            return
        super().wheelEvent(event)


class _MemoryAmountPicker(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._updating = False
        self._unit = "MB"
        self._megabytes = 1
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.value = _NoWheelDoubleSpinBox()
        self.unit = NoWheelComboBox()
        self.unit.addItems(("MB", "GB"))
        layout.addWidget(self.value)
        layout.addWidget(self.unit)
        layout.addStretch(1)
        self.value.valueChanged.connect(self._value_changed)
        self.unit.currentTextChanged.connect(self._change_unit)
        self._configure("MB")

    def _configure(self, unit: str) -> None:
        if unit == "GB":
            self.value.setDecimals(1)
            self.value.setRange(0.1, 1024.0)
            self.value.setSingleStep(0.5)
        else:
            self.value.setDecimals(0)
            self.value.setRange(1, 1024 * 1024)
            self.value.setSingleStep(128)

    def _value_changed(self, value: float) -> None:
        if self._updating:
            return
        self._megabytes = max(1, int(round(value * (1024 if self._unit == "GB" else 1))))
        self.changed.emit()

    def _change_unit(self, unit: str) -> None:
        if self._updating or unit == self._unit:
            return
        megabytes = self._megabytes
        self._updating = True
        try:
            self._unit = unit
            self._configure(unit)
            self.value.setValue(megabytes / 1024 if unit == "GB" else megabytes)
        finally:
            self._updating = False
        self.changed.emit()

    def set_megabytes(self, value: int | float) -> None:
        megabytes = max(1, int(round(float(value or 0))))
        unit = "GB" if megabytes >= 1024 else "MB"
        self._updating = True
        try:
            self._megabytes = megabytes
            self._unit = unit
            self.unit.setCurrentText(unit)
            self._configure(unit)
            self.value.setValue(megabytes / 1024 if unit == "GB" else megabytes)
        finally:
            self._updating = False

    def megabytes(self) -> int:
        return self._megabytes


class SettingsPage(QWidget):
    config_saved = Signal()

    def __init__(self, base_dir: Path, page_state: dict, theme: str, parent=None):
        super().__init__(parent)
        self.base_dir = Path(base_dir)
        self.page_state = page_state
        self.theme = theme
        self.service = ConfigService(self.base_dir, self)
        self.service.loaded.connect(self._load_snapshot)
        self.service.saved.connect(self._save_finished)
        self.service.failed.connect(self._service_failed)
        self.service.busy_changed.connect(self._set_busy)
        self._snapshot: dict = {}
        self._pending_save: dict | None = None
        self._loading = False
        self._dirty = False
        self._line_edits: dict[str, QLineEdit] = {}
        self._spin_boxes: dict[str, QSpinBox] = {}
        self._time_pickers: dict[str, TimePicker] = {}
        self._news_time_rows: list[tuple[QWidget, TimePicker]] = []
        self._toggles: dict[str, list[ToggleSwitch]] = {}
        self.preview_buttons: dict[str, QPushButton] = {}
        self._selected_preview = ""
        self._pending_diagnostic_id = ""
        self._pending_diagnostic_description = ""
        self._diagnostic_timer = QTimer(self)
        self._diagnostic_timer.setInterval(1000)
        self._diagnostic_timer.timeout.connect(self._poll_diagnostic_result)
        self._timing_kinds = {key: kind for _label, key, kind in TIMING_FIELDS}
        self._build_ui()
        self.service.load()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 18)
        outer.setSpacing(12)
        title = QLabel("设置")
        title.setObjectName("PageTitle")
        outer.addWidget(title)
        subtitle = QLabel("改好后点击右下角保存就可以。")
        subtitle.setObjectName("MutedLabel")
        subtitle.setWordWrap(True)
        outer.addWidget(subtitle)

        body = QHBoxLayout()
        body.setSpacing(14)
        self.section_list = QListWidget()
        self.section_list.setObjectName("SettingsSectionList")
        self.section_list.setFixedWidth(190)
        self.section_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for key, label in SECTIONS:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.section_list.addItem(item)
        self.section_list.currentRowChanged.connect(self._select_section)
        body.addWidget(self.section_list)
        self.section_stack = QStackedWidget()
        body.addWidget(self.section_stack, 1)
        self._builders = (
            self._build_general,
            self._build_monitoring,
            self._build_bark_ai,
            self._build_timing,
            self._build_diagnostics,
            self._build_about,
        )
        for builder in self._builders:
            self.section_stack.addWidget(self._scroll_page(builder()))
        outer.addLayout(body, 1)

        footer = QFrame()
        footer.setObjectName("SettingsFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(14, 10, 14, 10)
        self.save_status = QLabel("正在加载设置…")
        self.save_status.setObjectName("MutedLabel")
        footer_layout.addWidget(self.save_status, 1)
        self.discard_button = QPushButton("放弃更改")
        self.discard_button.clicked.connect(self._discard)
        self.save_button = QPushButton("保存")
        self.save_button.setObjectName("PrimaryButton")
        self.save_button.clicked.connect(self._save)
        footer_layout.addWidget(self.discard_button)
        footer_layout.addWidget(self.save_button)
        outer.addWidget(footer)
        row = int(self.page_state.get("section", 0) or 0)
        self.section_list.setCurrentRow(max(0, min(row, len(SECTIONS) - 1)))

    @staticmethod
    def _scroll_page(content: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setObjectName("SettingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        return scroll

    @staticmethod
    def _section(title: str, note: str = "") -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(2, 0, 8, 8)
        layout.setSpacing(12)
        heading = QLabel(title)
        heading.setObjectName("SectionTitle")
        layout.addWidget(heading)
        if note:
            description = QLabel(note)
            description.setObjectName("MutedLabel")
            description.setWordWrap(True)
            layout.addWidget(description)
        return page, layout

    @staticmethod
    def _card(title: str) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(10)
        label = QLabel(title)
        label.setObjectName("CardTitle")
        layout.addWidget(label)
        return card, layout

    def _add_line(self, form: QFormLayout, label: str, key: str, *, secret: bool = False) -> QLineEdit:
        edit = QLineEdit()
        edit.setObjectName("SecretField" if secret else "")
        if secret:
            edit.setEchoMode(QLineEdit.EchoMode.Password)
        edit.textEdited.connect(self._mark_dirty)
        form.addRow(label, edit)
        self._line_edits[key] = edit
        return edit

    def _add_spin(self, form: QFormLayout, label: str, key: str, maximum: int = 99999) -> QSpinBox:
        spin = _NoWheelSpinBox()
        spin.setRange(0, maximum)
        spin.valueChanged.connect(self._mark_dirty)
        form.addRow(label, spin)
        self._spin_boxes[key] = spin
        return spin

    def _add_time(self, form: QFormLayout, label: str, key: str, default: str) -> TimePicker:
        picker = TimePicker(default)
        picker.changed.connect(self._mark_dirty)
        form.addRow(label, picker)
        self._time_pickers[key] = picker
        return picker

    @staticmethod
    def _path_row(edit: QLineEdit, button: QPushButton) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(edit, 1)
        layout.addWidget(button)
        return row

    def _browse_executable(self, target: QLineEdit, title: str) -> None:
        selected, _filter = QFileDialog.getOpenFileName(
            self,
            title,
            target.text(),
            "Windows 程序 (*.exe)",
        )
        if selected:
            target.setText(selected)
            self._mark_dirty()

    def _add_news_time(self, value: str = "00:00") -> None:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        picker = TimePicker(value)
        picker.changed.connect(self._mark_dirty)
        remove = QPushButton("删除")
        remove.clicked.connect(lambda _checked=False, host=row: self._remove_news_time(host))
        layout.addWidget(picker, 1)
        layout.addWidget(remove)
        self.news_times_layout.addWidget(row)
        self._news_time_rows.append((row, picker))
        self._mark_dirty()

    def _remove_news_time(self, row: QWidget) -> None:
        self._news_time_rows = [(host, picker) for host, picker in self._news_time_rows if host is not row]
        self.news_times_layout.removeWidget(row)
        row.deleteLater()
        self._mark_dirty()

    def _set_news_times(self, values) -> None:
        for row, _picker in self._news_time_rows:
            self.news_times_layout.removeWidget(row)
            row.deleteLater()
        self._news_time_rows.clear()
        for value in values or ("00:05", "12:05"):
            self._add_news_time(str(value))

    def _add_toggle(self, layout: QVBoxLayout, label: str, key: str, note: str = "") -> ToggleSwitch:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 3, 0, 3)
        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)
        text_layout.addWidget(QLabel(label))
        if note:
            hint = QLabel(note)
            hint.setObjectName("MutedLabel")
            hint.setWordWrap(True)
            text_layout.addWidget(hint)
        row_layout.addLayout(text_layout, 1)
        toggle = ToggleSwitch(theme=self.theme)
        toggle.setToolTip(label)
        toggle.toggled.connect(lambda checked, field=key: self._set_bool(field, checked))
        row_layout.addWidget(toggle, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(row)
        self._toggles.setdefault(key, []).append(toggle)
        return toggle

    def _build_general(self) -> QWidget:
        page, layout = self._section("常规与程序", "选择平时使用一个还是两个 MAA，也可以给它们加上容易辨认的备注名。")
        behavior, behavior_layout = self._card("启动方式")
        self._add_toggle(behavior_layout, "打开管理器时自动启动 Agent", "gui_start_agent_on_launch")
        self._add_toggle(behavior_layout, "开机启动时不弹出窗口", "gui_start_minimized", "需要时可从任务栏右下角打开。")
        layout.addWidget(behavior)

        instances, instances_layout = self._card("要管理的 MAA")
        count_row = QHBoxLayout()
        count_row.addWidget(QLabel("MAA 数量"))
        self.instance_count = NoWheelComboBox()
        self.instance_count.addItems(("一个（推荐）", "两个"))
        self.instance_count.currentIndexChanged.connect(self._instance_count_changed)
        count_row.addWidget(self.instance_count)
        count_row.addStretch(1)
        instances_layout.addLayout(count_row)
        self.instance_cards: dict[str, QFrame] = {}
        self.instance_fields: dict[str, dict[str, QLineEdit]] = {}
        for label in ("MAA1", "MAA2"):
            card, card_layout = self._card(label)
            form = QFormLayout()
            directory = QLineEdit()
            alias = QLineEdit()
            executable = QLineEdit()
            directory.textEdited.connect(self._mark_dirty)
            alias.textEdited.connect(self._mark_dirty)
            executable.textEdited.connect(self._mark_dirty)
            browse = QPushButton("浏览…")
            browse.clicked.connect(lambda _checked=False, target=directory: self._browse_directory(target))
            form.addRow("MAA 所在文件夹", self._path_row(directory, browse))
            form.addRow("备注名（可选）", alias)
            executable_browse = QPushButton("浏览…")
            executable_browse.clicked.connect(
                lambda _checked=False, target=executable, name=label:
                    self._browse_executable(target, f"选择 {name} 启动文件")
            )
            form.addRow("启动文件（通常不用改）", self._path_row(executable, executable_browse))
            card_layout.addLayout(form)
            self._add_toggle(card_layout, "启动 Agent 时一起打开", f"autostart:{label}")
            instances_layout.addWidget(card)
            self.instance_cards[label] = card
            self.instance_fields[label] = {"directory": directory, "alias": alias, "executable": executable}
        mumu_form = QFormLayout()
        self.mumu_path = QLineEdit()
        self.mumu_path.textEdited.connect(self._mark_dirty)
        mumu_browse = QPushButton("浏览…")
        mumu_browse.clicked.connect(lambda _checked=False: self._browse_executable(self.mumu_path, "选择 MuMu 启动文件"))
        mumu_form.addRow("MuMu 启动文件", self._path_row(self.mumu_path, mumu_browse))
        instances_layout.addLayout(mumu_form)
        self._add_toggle(instances_layout, "启动 Agent 时一起打开 MuMu", "autostart:MuMu")
        layout.addWidget(instances)
        layout.addStretch(1)
        return page

    def _build_monitoring(self) -> QWidget:
        page, layout = self._section("提醒设置")
        memory, memory_layout = self._card("内存不足提醒")
        self._add_toggle(memory_layout, "内存占用过高时提醒我", "enable_memory_alert")
        form = QFormLayout()
        for label, key in (
            ("内存达到（%）", "memory_pressure_percent"),
            ("持续多久后提醒（分钟）", "memory_pressure_sustain_minutes"),
            ("严重占用达到（%）", "memory_critical_percent"),
            ("严重状态持续（分钟）", "memory_critical_sustain_minutes"),
        ):
            maximum = 100 if key.endswith("_percent") else 99999
            self._add_spin(form, label, key, maximum)
        self.memory_available = _MemoryAmountPicker()
        self.memory_available.changed.connect(self._mark_dirty)
        form.addRow("可用内存低于", self.memory_available)
        self._add_spin(form, "情况没变化时每隔多久记录（分钟）", "resource_sample_abnormal_steady_minutes")
        memory_layout.addLayout(form)
        layout.addWidget(memory)
        proxy, proxy_layout = self._card("代理与公告")
        self._add_toggle(proxy_layout, "代理连接异常时提醒我", "enable_proxy_healthcheck", "定时确认 Windows 系统代理能否访问外部网站。")
        self._add_toggle(proxy_layout, "有新的维护公告时提醒我", "enable_news_monitor")
        times_header = QHBoxLayout()
        times_header.addWidget(QLabel("每天检查时间"))
        times_header.addStretch(1)
        add_time = QPushButton("＋ 添加时间")
        add_time.clicked.connect(lambda: self._add_news_time("00:00"))
        times_header.addWidget(add_time)
        proxy_layout.addLayout(times_header)
        self.news_times_layout = QVBoxLayout()
        self.news_times_layout.setSpacing(7)
        proxy_layout.addLayout(self.news_times_layout)
        layout.addWidget(proxy)
        layout.addStretch(1)
        return page

    def _build_bark_ai(self) -> QWidget:
        page, layout = self._section("手机通知与 AI")
        bark, bark_layout = self._card("手机通知（Bark）")
        self._add_toggle(bark_layout, "用 Bark 发送手机提醒", "enable_bark")
        self._add_toggle(bark_layout, "发送失败后继续重试", "bark_retry_infinite")
        form = QFormLayout()
        self._add_line(form, "Bark 服务地址", "bark_server")
        self._add_line(form, "Bark Key", "bark_key", secret=True)
        self._add_line(form, "每次尝试次数", "bark_retry_attempts")
        self._add_line(form, "重试间隔（秒）", "bark_retry_sleep_seconds")
        bark_layout.addLayout(form)
        layout.addWidget(bark)
        ai, ai_layout = self._card("AI 日报摘要")
        self._add_toggle(ai_layout, "使用 AI 整理日报", "use_ai")
        self._add_toggle(ai_layout, "在日报中使用 AI", "ai_daily_report")
        self._add_toggle(ai_layout, "只有出现值得关注的内容时才使用", "ai_daily_when_useful_only")
        form = QFormLayout()
        self._add_line(form, "AI 接口地址", "ai_base_url")
        self._add_line(form, "使用的模型", "ai_model")
        self._add_line(form, "API Key", "ai_api_key", secret=True)
        self._add_line(form, "每日最多调用次数", "ai_max_calls_per_day")
        ai_layout.addLayout(form)
        layout.addWidget(ai)
        layout.addStretch(1)
        return page

    def _build_timing(self) -> QWidget:
        page, layout = self._section("高级设置", "通常不用改。需要调整检查频率或日报时间时再来这里。")
        proxy, proxy_layout = self._card("日报与代理")
        self._add_toggle(proxy_layout, "代理连接异常时提醒我", "enable_proxy_healthcheck")
        self._add_toggle(proxy_layout, "启用每日报告", "enable_daily_report")
        form = QFormLayout()
        self._add_time(form, "日报时间", "daily_report_time", "23:50")
        proxy_layout.addLayout(form)
        layout.addWidget(proxy)
        timing, timing_layout = self._card("检查频率")
        form = QFormLayout()
        for label, key, _kind in TIMING_FIELDS:
            self._add_spin(form, label, key)
        timing_layout.addLayout(form)
        layout.addWidget(timing)
        layout.addStretch(1)
        return page

    def _build_diagnostics(self) -> QWidget:
        page, layout = self._section("通知测试", "先选择一个通知样例，确认内容是否合适。")
        preview, preview_layout = self._card("查看通知样例")
        buttons = QGridLayout()
        buttons.setSpacing(8)
        self.preview_group = QButtonGroup(self)
        self.preview_group.setExclusive(True)
        for index, label in enumerate(LOCAL_PREVIEWS):
            button = QPushButton(label)
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, name=label: self._show_preview(name))
            self.preview_group.addButton(button)
            self.preview_buttons[label] = button
            buttons.addWidget(button, index // 4, index % 4)
        preview_layout.addLayout(buttons)
        self.preview_text = QPlainTextEdit()
        self.preview_text.setReadOnly(True)
        self.preview_text.setMaximumHeight(150)
        self.preview_text.setPlaceholderText("选择一个提醒样例查看排版。")
        preview_layout.addWidget(self.preview_text)
        layout.addWidget(preview)
        real, real_layout = self._card("功能测试")
        warning = QLabel("测试以上功能是否正常运行并发送通知。")
        warning.setWordWrap(True)
        real_layout.addWidget(warning)
        real_button = QPushButton("测试以上功能")
        real_button.clicked.connect(self._enqueue_real_test)
        real_layout.addWidget(real_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.diagnostic_status = QLabel("还没有进行测试。")
        self.diagnostic_status.setObjectName("MutedLabel")
        real_layout.addWidget(self.diagnostic_status)
        layout.addWidget(real)
        layout.addStretch(1)
        return page

    def _build_about(self) -> QWidget:
        page, layout = self._section("关于")
        card, card_layout = self._card("MAA Agent")
        try:
            version = (self.base_dir / "metadata" / "VERSION").read_text(encoding="utf-8").strip()
        except OSError:
            version = "2.0 Preview"
        card_layout.addWidget(QLabel(f"版本 {version}"))
        description = QLabel("这是正在试用的新界面。")
        description.setWordWrap(True)
        description.setObjectName("MutedLabel")
        card_layout.addWidget(description)
        layout.addWidget(card)
        layout.addStretch(1)
        return page

    def _select_section(self, row: int) -> None:
        if row < 0:
            return
        self.section_stack.setCurrentIndex(row)
        self.page_state["section"] = row

    def _set_bool(self, key: str, checked: bool) -> None:
        if self._loading:
            return
        self._loading = True
        try:
            for toggle in self._toggles.get(key, []):
                if toggle.isChecked() != checked:
                    toggle.setChecked(checked)
        finally:
            self._loading = False
        self._mark_dirty()

    def _instance_count_changed(self, index: int) -> None:
        self.instance_cards["MAA2"].setVisible(index == 1)
        if "MAA2 未运行" in self.preview_buttons:
            self.preview_buttons["MAA2 未运行"].setVisible(index == 1)
        if index == 0 and self._selected_preview == "MAA2 未运行":
            self._selected_preview = ""
            self.preview_group.setExclusive(False)
            self.preview_buttons["MAA2 未运行"].setChecked(False)
            self.preview_group.setExclusive(True)
            self.preview_text.clear()
        if not self._loading:
            self._mark_dirty()

    def _browse_directory(self, target: QLineEdit) -> None:
        selected = QFileDialog.getExistingDirectory(self, "选择包含 MAA.exe 的文件夹", target.text())
        if selected:
            target.setText(selected)
            self._mark_dirty()

    def _load_snapshot(self, data: dict) -> None:
        self._snapshot = deepcopy(data)
        self._loading = True
        try:
            for key, edit in self._line_edits.items():
                value = data.get(key, FIELD_DEFAULTS.get(key, ""))
                if isinstance(value, list):
                    value = ", ".join(str(item) for item in value)
                elif self._timing_kinds.get(key) == "int" and value not in (None, ""):
                    value = int(float(value))
                edit.setText(str(value) if value is not None else "")
            for key, spin in self._spin_boxes.items():
                value = data.get(key, FIELD_DEFAULTS.get(key, 0))
                spin.setValue(int(float(value or 0)))
            self.memory_available.set_megabytes(data.get("memory_critical_available_mb", FIELD_DEFAULTS["memory_critical_available_mb"]))
            for key, picker in self._time_pickers.items():
                picker.set_time(str(data.get(key, FIELD_DEFAULTS.get(key, "00:00"))))
            self._set_news_times(data.get("news_check_times") or FIELD_DEFAULTS["news_check_times"])
            for key, toggles in self._toggles.items():
                if key.startswith("autostart:"):
                    value = bool((data.get("auto_start_apps_with_agent") or {}).get(key.split(":", 1)[1], False))
                else:
                    value = bool(data.get(key, BOOLEAN_DEFAULTS.get(key, False)))
                for toggle in toggles:
                    toggle.setChecked(value, animate=False)
            directories = data.get("maa_instance_dirs") or {}
            aliases = data.get("maa_instance_aliases") or {}
            launch_paths = data.get("launch_paths") or {}
            self.instance_count.setCurrentIndex(1 if "MAA2" in directories else 0)
            for label, fields in self.instance_fields.items():
                fields["directory"].setText(str(directories.get(label, "")))
                fields["alias"].setText(str(aliases.get(label, "")))
                fields["executable"].setText(str(launch_paths.get(label, "")))
            self.mumu_path.setText(str(launch_paths.get("MuMu", "")))
            self._instance_count_changed(self.instance_count.currentIndex())
        finally:
            self._loading = False
        self._dirty = False
        self.save_status.setText("设置已加载。")

    def _collect(self) -> dict:
        data = deepcopy(self._snapshot)
        int_fields = {"bark_retry_attempts", "ai_max_calls_per_day", "resource_sample_abnormal_steady_minutes"}
        float_fields = {
            "bark_retry_sleep_seconds",
            *(key for key, kind in self._timing_kinds.items() if kind == "float"),
        }
        int_fields.update(key for key, kind in self._timing_kinds.items() if kind == "int")
        for key, edit in self._line_edits.items():
            text = edit.text().strip()
            if key in int_fields:
                if text:
                    data[key] = int(text)
            elif key in float_fields:
                if text:
                    data[key] = float(text)
            else:
                data[key] = text
        for key, spin in self._spin_boxes.items():
            data[key] = int(spin.value())
        data["memory_critical_available_mb"] = self.memory_available.megabytes()
        for key, picker in self._time_pickers.items():
            data[key] = picker.time()
        data.setdefault("schedule_day_start_time", FIELD_DEFAULTS["schedule_day_start_time"])
        data["news_check_times"] = [picker.time() for _row, picker in self._news_time_rows]
        for key, toggles in self._toggles.items():
            if key.startswith("autostart:"):
                continue
            data[key] = toggles[0].isChecked()
        count = 2 if self.instance_count.currentIndex() == 1 else 1
        labels = [f"MAA{index}" for index in range(1, count + 1)]
        directories = {label: self.instance_fields[label]["directory"].text().strip() for label in labels}
        aliases = {label: self.instance_fields[label]["alias"].text().strip() for label in labels}
        if any(not value for value in directories.values()):
            raise ValueError("请填写每个 MAA 所在的文件夹。")
        data["maa_instance_dirs"] = directories
        data["maa_instance_aliases"] = aliases
        previous_dirs = self._snapshot.get("maa_instance_dirs") or {}
        previous_gui_paths = self._snapshot.get("maa_gui_config_paths") or {}
        data["maa_gui_config_paths"] = {
            label: str(previous_gui_paths[label])
            if str(previous_dirs.get(label, "")) == path and previous_gui_paths.get(label)
            else str(Path(path) / "config" / "gui.new.json")
            for label, path in directories.items()
        }
        if set(labels) == set(previous_dirs) and all(str(previous_dirs.get(label, "")) == directories[label] for label in labels):
            data["maa_log_paths"] = list(self._snapshot.get("maa_log_paths") or [])
        else:
            data["maa_log_paths"] = [str(Path(directories[label]) / "debug" / "asst.log") for label in labels]
        launch_paths = {key: value for key, value in (data.get("launch_paths") or {}).items() if not str(key).upper().startswith("MAA")}
        launch_paths.update({
            label: self.instance_fields[label]["executable"].text().strip() or str(Path(path) / "MAA.exe")
            for label, path in directories.items()
        })
        launch_paths["MuMu"] = self.mumu_path.text().strip()
        for label, path in launch_paths.items():
            if path and Path(path).suffix.lower() != ".exe":
                raise ValueError(f"{label} 的启动文件需要选择 .exe 文件。")
        data["launch_paths"] = launch_paths
        autostart = {key: value for key, value in (data.get("auto_start_apps_with_agent") or {}).items() if not str(key).upper().startswith("MAA")}
        for label in (*labels, "MuMu"):
            autostart[label] = self._toggles[f"autostart:{label}"][0].isChecked()
        data["auto_start_apps_with_agent"] = autostart
        news_times = data.get("news_check_times") or []
        if not news_times or any(not TIME_RE.fullmatch(str(value)) for value in news_times):
            raise ValueError("请至少保留一个有效的官网检查时间。")
        if not TIME_RE.fullmatch(str(data.get("daily_report_time", ""))):
            raise ValueError("请选择有效的日报时间。")
        if not TIME_RE.fullmatch(str(data.get("schedule_day_start_time", ""))):
            raise ValueError("请选择有效的日程开始时间。")
        retry_attempts = int(data.get("bark_retry_attempts", 0))
        retry_sleep = float(data.get("bark_retry_sleep_seconds", -1))
        if retry_attempts < 1 or retry_sleep < 0:
            raise ValueError("Bark 重试次数至少为 1，间隔不能为负数。")
        memory_pressure = float(data.get("memory_pressure_percent", 0))
        memory_critical = float(data.get("memory_critical_percent", 0))
        pressure_minutes = float(data.get("memory_pressure_sustain_minutes", -1))
        critical_minutes = float(data.get("memory_critical_sustain_minutes", -1))
        steady_sample_minutes = int(data.get("resource_sample_abnormal_steady_minutes", 0))
        memory_available = float(data.get("memory_critical_available_mb", 0))
        if not 1 <= memory_pressure <= 100 or not 1 <= memory_critical <= 100:
            raise ValueError("内存占用阈值必须在 1% 到 100% 之间。")
        if memory_pressure > memory_critical:
            raise ValueError("普通报警阈值不能高于严重报警阈值。")
        if pressure_minutes < 0 or critical_minutes < 0 or memory_available <= 0 or steady_sample_minutes < 1:
            raise ValueError("内存提醒时间不能为负数；最低可用内存和记录间隔必须大于 0。")
        for _label, key, _kind in TIMING_FIELDS:
            if float(data.get(key, 0)) < 0:
                raise ValueError("时间不能小于 0。")
        if data.get("enable_bark") and (
            not str(data.get("bark_server", "")).strip() or not str(data.get("bark_key", "")).strip()
        ):
            raise ValueError("打开 Bark 通知后，需要填写服务地址和 Bark Key。")
        if data.get("use_ai") and (not str(data.get("ai_model", "")).strip() or not str(data.get("ai_api_key", "")).strip()):
            raise ValueError("打开 AI 日报后，需要填写使用的模型和 API Key。")
        return data

    def _mark_dirty(self, *_args) -> None:
        if self._loading:
            return
        self._dirty = True
        self.save_status.setText("有改动还没有保存。")

    def _discard(self) -> None:
        self._load_snapshot(self._snapshot)
        self.save_status.setText("已恢复为上次保存的内容。")

    def _save(self) -> None:
        try:
            data = self._collect()
        except (TypeError, ValueError) as exc:
            QMessageBox.warning(self, "还有内容需要修改", str(exc))
            return
        self._pending_save = deepcopy(data)
        self.service.save(data)

    def _set_busy(self, busy: bool) -> None:
        self.save_button.setEnabled(not busy)
        self.discard_button.setEnabled(not busy)
        self.section_list.setEnabled(not busy)
        self.section_stack.setEnabled(not busy)
        if busy:
            self.save_status.setText("正在保存…")

    def _save_finished(self, _path: Path) -> None:
        if self._pending_save is not None:
            self._snapshot = self._pending_save
        self._pending_save = None
        self._dirty = False
        self.save_status.setText("已保存。")
        self.config_saved.emit()

    def _service_failed(self, message: str) -> None:
        self._pending_save = None
        self.save_status.setText("保存失败。")
        QMessageBox.warning(self, "没有保存成功", message)

    def _show_preview(self, name: str) -> None:
        self._selected_preview = name
        sample = SAMPLES_BY_LABEL[name]
        self.preview_text.setPlainText(f"{sample.title}\n\n{sample.body}")

    def _enqueue_real_test(self) -> None:
        selected = self._selected_preview
        description = selected or "Bark 通知"
        answer = QMessageBox.question(
            self,
            "测试以上功能？",
            f"将测试“{description}”，并向手机发送通知。继续吗？",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            kind, target = DIAGNOSTIC_CASES.get(selected, ("bark_transport", ""))
            self._pending_diagnostic_id = diagnostic_commands.enqueue(self.base_dir, kind, target)
            self._pending_diagnostic_description = description
        except OSError:
            self.diagnostic_status.setText("没有开始测试，请稍后重试。")
            return
        self.diagnostic_status.setText(f"正在测试“{description}”…")
        self._diagnostic_timer.start()

    def _poll_diagnostic_result(self) -> None:
        path = diagnostic_commands.result_path(self.base_dir)
        try:
            result = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if str(result.get("id") or "") != self._pending_diagnostic_id:
            return
        self._diagnostic_timer.stop()
        self._pending_diagnostic_id = ""
        description = self._pending_diagnostic_description or "Bark 通知"
        self._pending_diagnostic_description = ""
        if result.get("ok"):
            self.diagnostic_status.setText(f"“{description}”已测试，请检查手机是否收到通知。")
        else:
            self.diagnostic_status.setText(f"“{description}”没有完成，请检查通知设置后重试。")

    def set_theme(self, theme: str) -> None:
        self.theme = theme
        for toggles in self._toggles.values():
            for toggle in toggles:
                toggle.set_theme(theme)

    def on_activated(self) -> None:
        pass

    def close(self) -> bool:
        self.service.wait()
        return super().close()
