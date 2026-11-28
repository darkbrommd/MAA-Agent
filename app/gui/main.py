from __future__ import annotations

import os
import sys
import ctypes
from pathlib import Path

if __package__ in {None, ""}:
    app_dir = Path(__file__).resolve().parents[1]
    if str(app_dir) not in sys.path:
        sys.path.insert(0, str(app_dir))

from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication, QDialog

from gui.first_run_wizard import FirstRunWizard, needs_first_run
from gui.main_window import MainWindow
from gui.resource_paths import resource_path
from gui.services.config_service import load_config_file
from gui.themes.tokens import load_stylesheet
from gui.tray import TrayController


def project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _set_windows_app_id() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("TaylorYu.MAAAgent")
    except (AttributeError, OSError):
        pass


def main() -> int:
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    _set_windows_app_id()
    application = QApplication.instance() or QApplication(sys.argv)
    application.setApplicationName("MAA Agent")
    application.setOrganizationName("Taylor Yu")
    application.setFont(QFont("Microsoft YaHei UI", 10))
    base_dir = project_root()
    icon_path = resource_path("assets", "app_icon.ico")
    icon = QIcon(str(icon_path)) if icon_path.exists() else QIcon()
    if icon_path.exists():
        application.setWindowIcon(icon)
    if needs_first_run(base_dir):
        application.setStyleSheet(load_stylesheet("dark"))
        wizard = FirstRunWizard(base_dir, "dark")
        if wizard.exec() != QDialog.DialogCode.Accepted:
            return 0
        application.setStyleSheet("")
    window = MainWindow(base_dir)
    if not icon.isNull():
        window.setWindowIcon(icon)
    tray = TrayController(icon, application)
    window.set_tray_controller(tray)
    application.setQuitOnLastWindowClosed(not tray.available)
    start_minimized = False
    if "--startup" in sys.argv:
        try:
            start_minimized = bool(load_config_file(base_dir).get("gui_start_minimized", True))
        except (OSError, ValueError):
            start_minimized = False
    if start_minimized and tray.available:
        window.hide_to_tray()
    else:
        window.show()
    result = application.exec()
    tray.shutdown()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
