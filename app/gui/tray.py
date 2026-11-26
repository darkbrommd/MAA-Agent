from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon


class TrayController(QObject):
    show_requested = Signal()
    hide_requested = Signal()
    agent_action_requested = Signal(str)
    quit_requested = Signal()

    def __init__(self, icon: QIcon, parent: QObject | None = None):
        super().__init__(parent)
        self.available = QSystemTrayIcon.isSystemTrayAvailable()
        self.icon: QSystemTrayIcon | None = None
        if not self.available:
            return
        tray = QSystemTrayIcon(icon, self)
        tray.setToolTip("MAA Agent")
        menu = QMenu()
        show_action = QAction("打开管理器", menu)
        show_action.triggered.connect(self.show_requested)
        menu.addAction(show_action)
        hide_action = QAction("隐藏到托盘", menu)
        hide_action.triggered.connect(self.hide_requested)
        menu.addAction(hide_action)
        menu.addSeparator()
        for label, action in (("启动 Agent", "start"), ("停止 Agent", "stop"), ("重启 Agent", "restart")):
            item = QAction(label, menu)
            item.triggered.connect(lambda _checked=False, value=action: self.agent_action_requested.emit(value))
            menu.addAction(item)
        menu.addSeparator()
        quit_action = QAction("退出管理器（Agent 继续运行）", menu)
        quit_action.triggered.connect(self.quit_requested)
        menu.addAction(quit_action)
        tray.setContextMenu(menu)
        tray.activated.connect(self._activated)
        tray.show()
        self.icon = tray

    def _activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_requested.emit()

    def show_message(self, title: str, message: str) -> None:
        if self.icon is not None:
            self.icon.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 2500)

    def shutdown(self) -> None:
        if self.icon is not None:
            self.icon.hide()
            self.icon.deleteLater()
            self.icon = None


def quit_application() -> None:
    application = QApplication.instance()
    if application is not None:
        application.quit()
