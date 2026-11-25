from __future__ import annotations

import os
import sys
from pathlib import Path

if __package__ in {None, ""}:
    app_dir = Path(__file__).resolve().parents[1]
    if str(app_dir) not in sys.path:
        sys.path.insert(0, str(app_dir))

from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication

from gui.main_window import MainWindow
from gui.resource_paths import resource_path


def project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def main() -> int:
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    application = QApplication.instance() or QApplication(sys.argv)
    application.setApplicationName("MAA Agent")
    application.setOrganizationName("Taylor Yu")
    application.setFont(QFont("Microsoft YaHei UI", 10))
    base_dir = project_root()
    icon_path = resource_path("assets", "app_icon.ico")
    if icon_path.exists():
        application.setWindowIcon(QIcon(str(icon_path)))
    window = MainWindow(base_dir)
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
