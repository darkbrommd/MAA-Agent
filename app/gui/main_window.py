from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from gui.navigation import ITEMS, NavigationRail
from gui.pages.dashboard import DashboardPage
from gui.pages.logs import LogsPage
from gui.pages.news import NewsPage
from gui.pages.resources import ResourcesPage
from gui.pages.schedule import SchedulePage
from gui.services.process_service import ProcessService, ProcessSnapshot
from gui.state.app_state import AppState
from gui.state.refresh_manager import RefreshManager
from gui.themes.tokens import load_stylesheet


class _PlaceholderPage(QWidget):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 24)
        heading = QLabel(title)
        heading.setObjectName("PageTitle")
        layout.addWidget(heading)
        note = QLabel("这个页面将在后续阶段迁移；当前旧版功能保持不变。")
        note.setObjectName("MutedLabel")
        layout.addWidget(note)
        layout.addStretch(1)


class MainWindow(QMainWindow):
    def __init__(self, base_dir: Path, parent=None):
        super().__init__(parent)
        self.base_dir = Path(base_dir)
        self.app_state = AppState(self.base_dir, self)
        self.refresh_manager = RefreshManager(self)
        self.process_service = ProcessService(self.base_dir, parent=self)
        self.process_service.snapshot_ready.connect(self._show_process_snapshot)
        self.process_service.failed.connect(
            lambda message: self._set_status_pill(self.agent_pill, "Agent · 状态读取失败", "error")
        )
        self._pages: dict[str, QWidget] = {}
        self._page_indexes: dict[str, int] = {}
        self._current_page = ""
        self._latest_process_snapshot: ProcessSnapshot | None = None
        self._theme = self.app_state.theme
        self._build_ui()
        self.app_state.theme_changed.connect(self._apply_theme)
        self._apply_theme(self._theme)
        self.setWindowTitle("MAA Agent 2.0 Preview")
        self.resize(1320, 860)
        self.setMinimumSize(1040, 700)
        self.show_page("schedule")
        self.process_timer = QTimer(self)
        self.process_timer.setInterval(4000)
        self.process_timer.timeout.connect(self.process_service.refresh)
        self.process_timer.start()
        self.process_service.refresh()

    def _version(self) -> str:
        try:
            return (self.base_dir / "metadata" / "VERSION").read_text(encoding="utf-8").strip()
        except OSError:
            return "2.0-preview"

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("AppRoot")
        self.setCentralWidget(root)
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        self.navigation = NavigationRail(self._version(), self._theme)
        self.navigation.page_requested.connect(self.show_page)
        self.navigation.theme_requested.connect(self.app_state.set_theme)
        shell.addWidget(self.navigation)
        content = QWidget()
        content.setObjectName("ContentArea")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        status_bar = QFrame()
        status_bar.setObjectName("TopStatusBar")
        status_bar.setFixedHeight(56)
        status_layout = QHBoxLayout(status_bar)
        status_layout.setContentsMargins(18, 8, 18, 8)
        status_layout.setSpacing(8)
        self.agent_pill = QLabel("Agent · 读取中")
        self.agent_pill.setObjectName("StatusPill")
        self.agent_pill.setProperty("status", "warning")
        self.maa_pill = QLabel("MAA · 读取中")
        self.maa_pill.setObjectName("StatusPill")
        self.maa_pill.setProperty("status", "warning")
        self.mumu_pill = QLabel("MuMu · 读取中")
        self.mumu_pill.setObjectName("StatusPill")
        self.mumu_pill.setProperty("status", "warning")
        status_layout.addWidget(self.agent_pill)
        status_layout.addWidget(self.maa_pill)
        status_layout.addWidget(self.mumu_pill)
        status_layout.addStretch(1)
        self.agent_action = QPushButton("启动 Agent")
        self.agent_action.setObjectName("PrimaryButton")
        self.agent_action.setEnabled(False)
        self.agent_action.setToolTip("Agent 启停将在托盘生命周期阶段接入；当前生产 Agent 不受影响。")
        more = QPushButton("⋯")
        more.setEnabled(False)
        status_layout.addWidget(self.agent_action)
        status_layout.addWidget(more)
        content_layout.addWidget(status_bar)
        self.stack = QStackedWidget()
        content_layout.addWidget(self.stack, 1)
        shell.addWidget(content, 1)

    def _create_page(self, key: str) -> QWidget:
        if key == "dashboard":
            page = DashboardPage(self.base_dir, self.app_state.page_state(key), self._theme)
            page.open_schedule.connect(lambda: self.show_page("schedule"))
            self.refresh_manager.register("dashboard", 2000, page.refresh_tick)
            if self._latest_process_snapshot is not None:
                page.update_process_snapshot(self._latest_process_snapshot)
            return page
        if key == "schedule":
            return SchedulePage(self.base_dir, self.app_state.page_state(key), self._theme)
        if key == "resources":
            return ResourcesPage(self.base_dir, self.app_state.page_state(key), self._theme)
        if key == "news":
            return NewsPage(self.base_dir, self.app_state.page_state(key), self._theme)
        if key == "logs":
            page = LogsPage(self.base_dir, self.app_state.page_state(key), self._theme)
            self.refresh_manager.register("logs", 1500, page.refresh)
            return page
        title = next((item.label for item in ITEMS if item.key == key), key)
        return _PlaceholderPage(title)

    def show_page(self, key: str) -> None:
        if key not in {item.key for item in ITEMS}:
            return
        if key not in self._pages:
            page = self._create_page(key)
            self._pages[key] = page
            self._page_indexes[key] = self.stack.addWidget(page)
        self.stack.setCurrentIndex(self._page_indexes[key])
        self._current_page = key
        self.navigation.set_current(key)
        self.refresh_manager.set_active_page(key)
        if hasattr(self, "process_timer"):
            self.process_timer.setInterval(2000 if key == "dashboard" else 4000)
        activated = getattr(self._pages[key], "on_activated", None)
        if callable(activated):
            activated()

    def _apply_theme(self, theme: str) -> None:
        self._theme = theme
        self.setStyleSheet(load_stylesheet(theme))
        self.navigation.set_theme(theme)
        for page in self._pages.values():
            setter = getattr(page, "set_theme", None)
            if callable(setter):
                setter(theme)

    def _show_process_snapshot(self, snapshot: ProcessSnapshot) -> None:
        self._latest_process_snapshot = snapshot
        self._set_status_pill(
            self.agent_pill,
            f"Agent · {'运行中' if snapshot.agent_running else '已停止'}",
            "running" if snapshot.agent_running else "stopped",
        )
        if snapshot.maa_instances:
            values = [f"{item.label} {'运行中' if item.running else '未运行'}" for item in snapshot.maa_instances]
            running_count = sum(1 for item in snapshot.maa_instances if item.running)
            state = "running" if running_count == len(snapshot.maa_instances) else ("warning" if running_count else "stopped")
            self._set_status_pill(self.maa_pill, " · ".join(values), state)
        else:
            self._set_status_pill(self.maa_pill, "MAA · 未配置", "stopped")
        self._set_status_pill(
            self.mumu_pill,
            f"MuMu · {snapshot.mumu_count} 个进程" if snapshot.mumu_count else "MuMu · 未运行",
            "running" if snapshot.mumu_count else "stopped",
        )
        self.agent_action.setText("停止 Agent" if snapshot.agent_running else "启动 Agent")
        dashboard = self._pages.get("dashboard")
        if isinstance(dashboard, DashboardPage):
            dashboard.update_process_snapshot(snapshot)

    @staticmethod
    def _set_status_pill(label: QLabel, text: str, status: str) -> None:
        label.setText(text)
        if label.property("status") == status:
            return
        label.setProperty("status", status)
        label.style().unpolish(label)
        label.style().polish(label)

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.WindowStateChange:
            visible = not bool(self.windowState() & Qt.WindowState.WindowMinimized)
            self._set_refresh_visibility(visible)
        return super().event(event)

    def showEvent(self, event) -> None:
        self._set_refresh_visibility(True)
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        self._set_refresh_visibility(False)
        super().hideEvent(event)

    def closeEvent(self, event) -> None:
        self.app_state.flush()
        super().closeEvent(event)

    def _set_refresh_visibility(self, visible: bool) -> None:
        self.refresh_manager.set_window_visible(visible)
        if hasattr(self, "process_timer"):
            if visible and not self.process_timer.isActive():
                self.process_timer.start()
                self.process_service.refresh()
            elif not visible and self.process_timer.isActive():
                self.process_timer.stop()
