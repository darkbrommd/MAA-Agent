from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QApplication,
    QLabel,
    QMainWindow,
    QMessageBox,
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
from gui.pages.settings import SettingsPage
from gui.services.agent_control_service import AgentControlService
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
        note = QLabel("这个页面暂时没有内容。")
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
        self.agent_control = AgentControlService(self.base_dir, self)
        self.agent_control.busy_changed.connect(self._set_agent_controls_busy)
        self.agent_control.finished.connect(self._agent_action_finished)
        self.process_service.snapshot_ready.connect(self._show_process_snapshot)
        self.process_service.failed.connect(self._show_process_failure)
        self._pages: dict[str, QWidget] = {}
        self._page_indexes: dict[str, int] = {}
        self._current_page = ""
        self._latest_process_snapshot: ProcessSnapshot | None = None
        self._agent_busy = False
        self._tray = None
        self._allow_close = False
        self._theme = self.app_state.theme
        self._build_ui()
        self.app_state.theme_changed.connect(self._apply_theme)
        self._apply_theme(self._theme)
        self.setWindowTitle("MAA Agent 2.0")
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
        self.status_bar = QFrame()
        self.status_bar.setObjectName("TopStatusBar")
        self.status_bar.setFixedHeight(56)
        status_layout = QHBoxLayout(self.status_bar)
        status_layout.setContentsMargins(18, 8, 18, 8)
        status_layout.setSpacing(8)
        status_layout.addStretch(1)
        self.agent_action = QPushButton("启动 Agent")
        self.agent_action.setObjectName("PrimaryButton")
        self.agent_action.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.agent_action.clicked.connect(self._run_primary_agent_action)
        self.restart_action = QPushButton("重启 Agent")
        self.restart_action.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.restart_action.setEnabled(False)
        self.restart_action.clicked.connect(lambda: self._confirm_agent_action("restart"))
        status_layout.addWidget(self.agent_action)
        status_layout.addWidget(self.restart_action)
        content_layout.addWidget(self.status_bar)
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
            page = SchedulePage(self.base_dir, self.app_state.page_state(key), self._theme)
            self.refresh_manager.register("schedule", 5000, page.refresh_tick)
            return page
        if key == "resources":
            page = ResourcesPage(self.base_dir, self.app_state.page_state(key), self._theme)
            self.refresh_manager.register("resources", 1000, page.refresh_uptime)
            return page
        if key == "news":
            return NewsPage(self.base_dir, self.app_state.page_state(key), self._theme)
        if key == "logs":
            page = LogsPage(self.base_dir, self.app_state.page_state(key), self._theme)
            self.refresh_manager.register("logs", 1500, page.refresh)
            return page
        if key == "settings":
            page = SettingsPage(self.base_dir, self.app_state.page_state(key), self._theme)
            page.config_saved.connect(self.process_service.refresh)
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
        self.status_bar.setVisible(key == "dashboard")
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
        self.agent_action.setText("停止 Agent" if snapshot.agent_running else "启动 Agent")
        self.agent_action.setEnabled(not self._agent_busy)
        self.restart_action.setEnabled(snapshot.agent_running and not self._agent_busy)
        dashboard = self._pages.get("dashboard")
        if isinstance(dashboard, DashboardPage):
            dashboard.update_process_snapshot(snapshot)

    def _show_process_failure(self, message: str) -> None:
        dashboard = self._pages.get("dashboard")
        if isinstance(dashboard, DashboardPage):
            dashboard.show_process_failure(message)

    def _run_primary_agent_action(self) -> None:
        running = bool(self._latest_process_snapshot and self._latest_process_snapshot.agent_running)
        if running:
            self._confirm_agent_action("stop")
        else:
            self.agent_control.run("start")

    def _confirm_agent_action(self, action: str) -> None:
        text = "要停止 Agent 吗？MAA 和 MuMu 会继续运行。" if action == "stop" else "要重新启动 Agent 吗？MAA 和 MuMu 会继续运行。"
        if QMessageBox.question(self, "停止 Agent" if action == "stop" else "重启 Agent", text) == QMessageBox.StandardButton.Yes:
            self.agent_control.run(action)

    def _set_agent_controls_busy(self, busy: bool) -> None:
        self._agent_busy = busy
        self.agent_action.setEnabled(not busy)
        running = bool(self._latest_process_snapshot and self._latest_process_snapshot.agent_running)
        self.restart_action.setEnabled(running and not busy)
        if busy:
            self.agent_action.setText("处理中…")

    def _agent_action_finished(self, ok: bool, message: str) -> None:
        if not ok:
            QMessageBox.warning(self, "Agent 操作未完成", message)
        self.statusBar().showMessage(message, 6000)
        QTimer.singleShot(900, self.process_service.refresh)

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
        if self._tray is not None and self._tray.available and not self._allow_close:
            event.ignore()
            self.hide_to_tray()
            return
        self.app_state.flush()
        self.process_service.wait()
        for page in self._pages.values():
            for name in ("service", "day_start_service"):
                service = getattr(page, name, None)
                wait = getattr(service, "wait", None)
                if callable(wait):
                    wait()
        super().closeEvent(event)

    def _set_refresh_visibility(self, visible: bool) -> None:
        self.refresh_manager.set_window_visible(visible)
        if hasattr(self, "process_timer"):
            if visible and not self.process_timer.isActive():
                self.process_timer.start()
                self.process_service.refresh()
            elif not visible and self.process_timer.isActive():
                self.process_timer.stop()

    def set_tray_controller(self, tray) -> None:
        self._tray = tray
        tray.show_requested.connect(self.show_from_tray)
        tray.hide_requested.connect(self.hide_to_tray)
        tray.agent_action_requested.connect(self._tray_agent_action)
        tray.quit_requested.connect(self.quit_manager)

    def hide_to_tray(self) -> None:
        self.hide()
        self._set_refresh_visibility(False)
        if self._tray is not None:
            self._tray.show_message("MAA Agent", "已收起到任务栏右下角，后台任务会继续运行。")

    def show_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self._set_refresh_visibility(True)
        self.refresh_manager.trigger_active()

    def _tray_agent_action(self, action: str) -> None:
        if action == "start":
            self.agent_control.run("start")
            return
        self.show_from_tray()
        self._confirm_agent_action(action)

    def quit_manager(self) -> None:
        self._allow_close = True
        if self._tray is not None:
            self._tray.shutdown()
        self.close()
        application = QApplication.instance()
        if application is not None:
            application.quit()
