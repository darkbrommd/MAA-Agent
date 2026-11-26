from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
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
from gui.components.toggle_switch import ToggleSwitch
from gui.services.config_service import ConfigService, masked_config


SECTIONS = (
    ("general", "常规与实例"),
    ("monitoring", "监控与报警"),
    ("bark_ai", "Bark 与 AI 日报"),
    ("timing", "高级时间策略"),
    ("diagnostics", "诊断与通知测试"),
    ("all_config", "全部配置"),
    ("about", "关于"),
)

TIMING_FIELDS = (
    ("检查间隔（秒）", "check_interval_seconds", "int"),
    ("开机宽限（分钟）", "startup_process_grace_minutes", "float"),
    ("进程缺失持续（分钟）", "process_missing_sustain_minutes", "float"),
    ("普通错误无进展（分钟）", "generic_error_stall_minutes", "float"),
    ("连接错误无进展（分钟）", "connection_error_stall_minutes", "float"),
    ("计划漏跑宽限（分钟）", "scheduled_run_grace_minutes", "float"),
    ("窗口未响应持续（分钟）", "hung_window_sustain_minutes", "float"),
    ("代理探测间隔（分钟）", "proxy_healthcheck_interval_minutes", "float"),
    ("代理失败持续（分钟）", "proxy_failure_sustain_minutes", "float"),
    ("外部心跳间隔（分钟）", "heartbeat_interval_minutes", "float"),
)

LOCAL_PREVIEWS = {
    "内存不足": "发生了什么：系统内存持续吃紧，当前 96%，可用 1.2 GB。\n你要做什么：如果不是主动运行重负载程序，请检查异常占用。",
    "任务未按时开始": "发生了什么：MAA1 16:00 的计划仍没有观察到实际启动。\n你要做什么：检查 MAA、MuMu 和定时任务状态。",
    "窗口未响应": "发生了什么：MAA/MuMu 窗口持续未响应。\n你要做什么：远程确认对应程序是否卡住。",
    "官网维护": "检测到新的停机维护公告。这里仅预览排版，不读取官网，也不发送 Bark。",
}

TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
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
    "news_check_times": ["00:05", "12:05"],
    "daily_report_time": "23:50",
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


class _NoWheelComboBox(QComboBox):
    def wheelEvent(self, event) -> None:
        if not self.hasFocus():
            event.ignore()
            return
        super().wheelEvent(event)


class _NoWheelSpinBox(QSpinBox):
    def wheelEvent(self, event) -> None:
        if not self.hasFocus():
            event.ignore()
            return
        super().wheelEvent(event)


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
        self._toggles: dict[str, list[ToggleSwitch]] = {}
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
        subtitle = QLabel("改动先保存在本页草稿中；只有点击“保存配置”才会写入 config.json。主题开关仍单独保存。")
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
            self._build_all_config,
            self._build_about,
        )
        for builder in self._builders:
            self.section_stack.addWidget(self._scroll_page(builder()))
        outer.addLayout(body, 1)

        footer = QFrame()
        footer.setObjectName("SettingsFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(14, 10, 14, 10)
        self.save_status = QLabel("正在读取配置…")
        self.save_status.setObjectName("MutedLabel")
        footer_layout.addWidget(self.save_status, 1)
        self.discard_button = QPushButton("放弃更改")
        self.discard_button.clicked.connect(self._discard)
        self.save_button = QPushButton("保存配置")
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
        page, layout = self._section("常规与实例", "MAA 实例数量决定顶部状态与设置中是否显示 MAA2。显示备注会用于状态胶囊。")
        behavior, behavior_layout = self._card("启动与后台行为")
        self._add_toggle(behavior_layout, "打开管理器时确保 Agent 已启动", "gui_start_agent_on_launch")
        self._add_toggle(behavior_layout, "开机启动时直接进入托盘", "gui_start_minimized")
        layout.addWidget(behavior)

        instances, instances_layout = self._card("MAA 实例")
        count_row = QHBoxLayout()
        count_row.addWidget(QLabel("管理实例数量"))
        self.instance_count = _NoWheelComboBox()
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
            directory_row = QWidget()
            directory_layout = QHBoxLayout(directory_row)
            directory_layout.setContentsMargins(0, 0, 0, 0)
            directory_layout.addWidget(directory, 1)
            directory_layout.addWidget(browse)
            form.addRow("安装目录", directory_row)
            form.addRow("显示备注", alias)
            form.addRow("启动程序", executable)
            card_layout.addLayout(form)
            self._add_toggle(card_layout, "随 Agent 启动", f"autostart:{label}")
            instances_layout.addWidget(card)
            self.instance_cards[label] = card
            self.instance_fields[label] = {"directory": directory, "alias": alias, "executable": executable}
        mumu_form = QFormLayout()
        self.mumu_path = QLineEdit()
        self.mumu_path.textEdited.connect(self._mark_dirty)
        mumu_form.addRow("MuMu 可执行文件", self.mumu_path)
        instances_layout.addLayout(mumu_form)
        self._add_toggle(instances_layout, "MuMu 随 Agent 启动", "autostart:MuMu")
        layout.addWidget(instances)
        layout.addStretch(1)
        return page

    def _build_monitoring(self) -> QWidget:
        page, layout = self._section("监控与报警")
        memory, memory_layout = self._card("内存报警")
        self._add_toggle(memory_layout, "启用内存报警", "enable_memory_alert")
        form = QFormLayout()
        for label, key in (
            ("普通报警阈值（%）", "memory_pressure_percent"),
            ("普通报警持续（分钟）", "memory_pressure_sustain_minutes"),
            ("严重报警阈值（%）", "memory_critical_percent"),
            ("严重报警持续（分钟）", "memory_critical_sustain_minutes"),
            ("严重报警最低可用内存（MB）", "memory_critical_available_mb"),
        ):
            self._add_line(form, label, key)
        memory_layout.addLayout(form)
        layout.addWidget(memory)
        proxy, proxy_layout = self._card("网络与公告")
        self._add_toggle(proxy_layout, "启用系统代理健康检查", "enable_proxy_healthcheck", "关闭后不读取系统代理，也不发起外网健康探测。")
        self._add_toggle(proxy_layout, "启用官网公告监控", "enable_news_monitor")
        form = QFormLayout()
        self._add_line(form, "公告检查时间（逗号分隔）", "news_check_times")
        proxy_layout.addLayout(form)
        layout.addWidget(proxy)
        layout.addStretch(1)
        return page

    def _build_bark_ai(self) -> QWidget:
        page, layout = self._section("Bark 与 AI 日报", "密钥默认遮罩，不会写入日志或 GUI 状态文件。")
        bark, bark_layout = self._card("Bark 通知")
        self._add_toggle(bark_layout, "启用 Bark 通知", "enable_bark")
        self._add_toggle(bark_layout, "发送失败后持续补发", "bark_retry_infinite")
        form = QFormLayout()
        self._add_line(form, "服务地址", "bark_server")
        self._add_line(form, "API Key", "bark_key", secret=True)
        self._add_line(form, "发送尝试次数", "bark_retry_attempts")
        self._add_line(form, "重试间隔（秒）", "bark_retry_sleep_seconds")
        bark_layout.addLayout(form)
        layout.addWidget(bark)
        ai, ai_layout = self._card("AI 分析（OpenAI 兼容接口）")
        self._add_toggle(ai_layout, "启用 AI 分析", "use_ai")
        self._add_toggle(ai_layout, "启用 AI 日报", "ai_daily_report")
        self._add_toggle(ai_layout, "仅在有价值时调用 AI", "ai_daily_when_useful_only")
        form = QFormLayout()
        self._add_line(form, "接口地址（Base URL）", "ai_base_url")
        self._add_line(form, "模型名称", "ai_model")
        self._add_line(form, "API Key", "ai_api_key", secret=True)
        self._add_line(form, "每日最多调用次数", "ai_max_calls_per_day")
        ai_layout.addLayout(form)
        layout.addWidget(ai)
        layout.addStretch(1)
        return page

    def _build_timing(self) -> QWidget:
        page, layout = self._section("高级时间策略", "这些值会在 Agent 下一轮读取配置时生效；不会修改 MAA 自己的定时器。")
        proxy, proxy_layout = self._card("代理与日报")
        self._add_toggle(proxy_layout, "启用系统代理健康检查", "enable_proxy_healthcheck")
        self._add_toggle(proxy_layout, "启用每日报告", "enable_daily_report")
        form = QFormLayout()
        self._add_line(form, "日报时间（HH:MM）", "daily_report_time")
        proxy_layout.addLayout(form)
        layout.addWidget(proxy)
        timing, timing_layout = self._card("判断与采样时间")
        form = QFormLayout()
        for label, key, _kind in TIMING_FIELDS:
            self._add_line(form, label, key)
        timing_layout.addLayout(form)
        layout.addWidget(timing)
        layout.addStretch(1)
        return page

    def _build_diagnostics(self) -> QWidget:
        page, layout = self._section("诊断与通知测试", "本地预览不会发送 Bark；真实推送会交给正在运行的 Agent，点击后仍需确认。")
        preview, preview_layout = self._card("本地预览（安全，不联网）")
        buttons = QHBoxLayout()
        for label in LOCAL_PREVIEWS:
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, name=label: self._show_preview(name))
            buttons.addWidget(button)
        preview_layout.addLayout(buttons)
        self.preview_text = QPlainTextEdit()
        self.preview_text.setReadOnly(True)
        self.preview_text.setMaximumHeight(150)
        self.preview_text.setPlaceholderText("选择一个提醒样例查看排版。")
        preview_layout.addWidget(self.preview_text)
        layout.addWidget(preview)
        real, real_layout = self._card("真实推送测试")
        warning = QLabel("这会通过正式 Bark 通道向手机发送一条测试通知，不会停止或修改 MAA。")
        warning.setWordWrap(True)
        real_layout.addWidget(warning)
        real_button = QPushButton("发送 Bark 通道测试…")
        real_button.clicked.connect(self._enqueue_real_test)
        real_layout.addWidget(real_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.diagnostic_status = QLabel("尚未执行真实推送。")
        self.diagnostic_status.setObjectName("MutedLabel")
        real_layout.addWidget(self.diagnostic_status)
        layout.addWidget(real)
        layout.addStretch(1)
        return page

    def _build_all_config(self) -> QWidget:
        page, layout = self._section("全部配置", "为避免密钥意外露出，这里只读显示并自动遮罩敏感值。需要编辑冷门字段时可打开本机 config.json。")
        actions = QHBoxLayout()
        open_button = QPushButton("在记事本中打开 config.json")
        open_button.clicked.connect(self._open_config_file)
        actions.addWidget(open_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.all_config_text = QPlainTextEdit()
        self.all_config_text.setReadOnly(True)
        self.all_config_text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self.all_config_text, 1)
        return page

    def _build_about(self) -> QWidget:
        page, layout = self._section("关于")
        card, card_layout = self._card("MAA Agent")
        try:
            version = (self.base_dir / "metadata" / "VERSION").read_text(encoding="utf-8").strip()
        except OSError:
            version = "2.0 Preview"
        card_layout.addWidget(QLabel(f"版本 {version}"))
        description = QLabel("PySide6 GUI 2.0 预览版。当前仍与稳定 Tk GUI 并行，不会自动替换默认入口。")
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
        if row == 5:
            self._refresh_all_config()

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
                edit.setText(str(value) if value is not None else "")
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
        self.save_status.setText("配置已读取。")
        self._refresh_all_config()

    def _collect(self) -> dict:
        data = deepcopy(self._snapshot)
        int_fields = {"bark_retry_attempts", "ai_max_calls_per_day"}
        float_fields = {
            "memory_pressure_percent", "memory_critical_percent", "memory_critical_available_mb",
            "memory_pressure_sustain_minutes", "memory_critical_sustain_minutes", "bark_retry_sleep_seconds",
            *(key for key, kind in self._timing_kinds.items() if kind == "float"),
        }
        int_fields.update(key for key, kind in self._timing_kinds.items() if kind == "int")
        list_fields = {"news_check_times"}
        for key, edit in self._line_edits.items():
            text = edit.text().strip()
            if key in int_fields:
                if text:
                    data[key] = int(text)
            elif key in float_fields:
                if text:
                    data[key] = float(text)
            elif key in list_fields:
                data[key] = [item.strip() for item in text.replace("，", ",").split(",") if item.strip()]
            else:
                data[key] = text
        for key, toggles in self._toggles.items():
            if key.startswith("autostart:"):
                continue
            data[key] = toggles[0].isChecked()
        count = 2 if self.instance_count.currentIndex() == 1 else 1
        labels = [f"MAA{index}" for index in range(1, count + 1)]
        directories = {label: self.instance_fields[label]["directory"].text().strip() for label in labels}
        aliases = {label: self.instance_fields[label]["alias"].text().strip() for label in labels}
        if any(not value for value in directories.values()):
            raise ValueError("请填写所有已启用 MAA 实例的安装目录。")
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
        data["launch_paths"] = launch_paths
        autostart = {key: value for key, value in (data.get("auto_start_apps_with_agent") or {}).items() if not str(key).upper().startswith("MAA")}
        for label in (*labels, "MuMu"):
            autostart[label] = self._toggles[f"autostart:{label}"][0].isChecked()
        data["auto_start_apps_with_agent"] = autostart
        news_times = data.get("news_check_times") or []
        if not news_times or any(not TIME_RE.fullmatch(str(value)) for value in news_times):
            raise ValueError("官网检查时间请使用 HH:MM，并用逗号分隔。")
        if not TIME_RE.fullmatch(str(data.get("daily_report_time", ""))):
            raise ValueError("日报时间必须是 HH:MM，例如 23:50。")
        retry_attempts = int(data.get("bark_retry_attempts", 0))
        retry_sleep = float(data.get("bark_retry_sleep_seconds", -1))
        if retry_attempts < 1 or retry_sleep < 0:
            raise ValueError("Bark 重试次数至少为 1，间隔不能为负数。")
        memory_pressure = float(data.get("memory_pressure_percent", 0))
        memory_critical = float(data.get("memory_critical_percent", 0))
        pressure_minutes = float(data.get("memory_pressure_sustain_minutes", -1))
        critical_minutes = float(data.get("memory_critical_sustain_minutes", -1))
        memory_available = float(data.get("memory_critical_available_mb", 0))
        if not 1 <= memory_pressure <= 100 or not 1 <= memory_critical <= 100:
            raise ValueError("内存占用阈值必须在 1% 到 100% 之间。")
        if memory_pressure > memory_critical:
            raise ValueError("普通报警阈值不能高于严重报警阈值。")
        if pressure_minutes < 0 or critical_minutes < 0 or memory_available <= 0:
            raise ValueError("内存报警持续时间不能为负数，最低可用内存必须大于 0。")
        for _label, key, _kind in TIMING_FIELDS:
            if float(data.get(key, 0)) < 0:
                raise ValueError(f"{key} 不能为负数。")
        if data.get("enable_bark") and (
            not str(data.get("bark_server", "")).strip() or not str(data.get("bark_key", "")).strip()
        ):
            raise ValueError("启用 Bark 时需要填写服务地址和 API Key。")
        if data.get("use_ai") and (not str(data.get("ai_model", "")).strip() or not str(data.get("ai_api_key", "")).strip()):
            raise ValueError("启用 AI 分析时需要填写模型名称和 API Key。")
        return data

    def _mark_dirty(self, *_args) -> None:
        if self._loading:
            return
        self._dirty = True
        self.save_status.setText("有尚未保存的更改。")

    def _discard(self) -> None:
        self._load_snapshot(self._snapshot)
        self.save_status.setText("已放弃本页尚未保存的更改。")

    def _save(self) -> None:
        try:
            data = self._collect()
        except (TypeError, ValueError) as exc:
            QMessageBox.warning(self, "配置还不能保存", str(exc))
            return
        self._pending_save = deepcopy(data)
        self.service.save(data)

    def _set_busy(self, busy: bool) -> None:
        self.save_button.setEnabled(not busy)
        self.discard_button.setEnabled(not busy)
        self.section_list.setEnabled(not busy)
        self.section_stack.setEnabled(not busy)
        if busy:
            self.save_status.setText("正在处理配置…")

    def _save_finished(self, _path: Path) -> None:
        if self._pending_save is not None:
            self._snapshot = self._pending_save
        self._pending_save = None
        self._dirty = False
        self.save_status.setText("配置已保存，并已更新 config.json.bak。Agent 会在下一轮自动读取。")
        self._refresh_all_config()
        self.config_saved.emit()

    def _service_failed(self, message: str) -> None:
        self._pending_save = None
        self.save_status.setText("配置操作失败。")
        QMessageBox.warning(self, "配置操作失败", message)

    def _refresh_all_config(self) -> None:
        source = self._snapshot
        if self._dirty:
            try:
                source = self._collect()
            except (TypeError, ValueError):
                source = self._snapshot
        self.all_config_text.setPlainText(json.dumps(masked_config(source), ensure_ascii=False, indent=2))

    def _show_preview(self, name: str) -> None:
        self.preview_text.setPlainText(f"【本地预览】{name}\n\n{LOCAL_PREVIEWS[name]}")

    def _enqueue_real_test(self) -> None:
        answer = QMessageBox.question(
            self,
            "确认真实推送",
            "确定通过正式 Bark 通道发送一条测试通知吗？\n这不会停止或修改 MAA。",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            command_id = diagnostic_commands.enqueue(self.base_dir, "bark_transport")
        except OSError as exc:
            self.diagnostic_status.setText(f"无法提交测试：{exc}")
            return
        self.diagnostic_status.setText(f"测试已提交给 Agent（命令 {command_id[:8]}…），请留意手机通知。")

    def _open_config_file(self) -> None:
        from project_paths import config_path

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(config_path(self.base_dir))))

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
