from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from first_run_setup import build_first_run_config
from gui.components.toggle_switch import ToggleSwitch
from gui.services.config_service import atomic_save_config_file
from project_paths import config_path, config_template_path


def needs_first_run(base_dir: Path) -> bool:
    path = config_path(base_dir)
    if not path.exists():
        return True
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True
    if not isinstance(data, dict):
        return True
    if data.get("setup_completed", False):
        return False
    # Existing installations predate setup_completed. A real MAA mapping is
    # sufficient evidence that they are configured and must not be interrupted.
    return not bool(data.get("maa_instance_dirs"))


class FirstRunWizard(QDialog):
    def __init__(self, base_dir: Path, theme: str = "dark", parent=None):
        super().__init__(parent)
        self.base_dir = Path(base_dir)
        self.theme = theme
        self.setWindowTitle("欢迎使用 MAA Agent")
        self.setModal(True)
        self.resize(720, 520)
        self.setMinimumSize(640, 480)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 22)
        layout.setSpacing(14)
        title = QLabel("第一次使用，只需要几步")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        note = QLabel("先选择 MAA 所在的文件夹。其他选项以后也可以在设置里调整。")
        note.setObjectName("MutedLabel")
        note.setWordWrap(True)
        layout.addWidget(note)

        instance_card = QFrame()
        instance_card.setObjectName("Card")
        instance_layout = QVBoxLayout(instance_card)
        instance_layout.setContentsMargins(16, 14, 16, 16)
        heading = QLabel("MAA 在哪里？")
        heading.setObjectName("CardTitle")
        instance_layout.addWidget(heading)
        count_row = QHBoxLayout()
        count_row.addWidget(QLabel("你要管理几个 MAA？"))
        self.instance_count = QComboBox()
        self.instance_count.addItems(("一个（推荐）", "两个"))
        self.instance_count.currentIndexChanged.connect(self._sync_second)
        count_row.addWidget(self.instance_count)
        count_row.addStretch(1)
        instance_layout.addLayout(count_row)
        self.maa1_path = QLineEdit()
        self.maa2_path = QLineEdit()
        instance_layout.addWidget(self._path_row("MAA 所在文件夹", self.maa1_path))
        self.maa2_row = self._path_row("第二个 MAA 的文件夹", self.maa2_path)
        instance_layout.addWidget(self.maa2_row)
        layout.addWidget(instance_card)

        bark_card = QFrame()
        bark_card.setObjectName("Card")
        bark_layout = QVBoxLayout(bark_card)
        bark_layout.setContentsMargins(16, 14, 16, 16)
        bark_row = QHBoxLayout()
        bark_text = QVBoxLayout()
        bark_text.addWidget(QLabel("使用 Bark 接收手机提醒（可选）"))
        hint = QLabel("现在不需要也可以跳过，以后再到设置里打开。")
        hint.setObjectName("MutedLabel")
        bark_text.addWidget(hint)
        bark_row.addLayout(bark_text, 1)
        self.enable_bark = ToggleSwitch(False, self.theme)
        self.enable_bark.toggled.connect(self._sync_bark)
        bark_row.addWidget(self.enable_bark, 0, Qt.AlignmentFlag.AlignVCenter)
        bark_layout.addLayout(bark_row)
        self.bark_key = QLineEdit()
        self.bark_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.bark_key.setPlaceholderText("Bark Key")
        bark_layout.addWidget(self.bark_key)
        layout.addWidget(bark_card)
        layout.addStretch(1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = QPushButton("退出")
        cancel.clicked.connect(self.reject)
        finish = QPushButton("保存并开始使用")
        finish.setObjectName("PrimaryButton")
        finish.clicked.connect(self._finish)
        actions.addWidget(cancel)
        actions.addWidget(finish)
        layout.addLayout(actions)
        self._sync_second()
        self._sync_bark(False)

    def _path_row(self, label: str, edit: QLineEdit) -> QWidget:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        text = QLabel(label)
        text.setMinimumWidth(118)
        browse = QPushButton("浏览…")
        browse.clicked.connect(lambda: self._browse(edit))
        row_layout.addWidget(text)
        row_layout.addWidget(edit, 1)
        row_layout.addWidget(browse)
        return row

    def _browse(self, edit: QLineEdit) -> None:
        selected = QFileDialog.getExistingDirectory(self, "选择包含 MAA.exe 的文件夹", edit.text())
        if selected:
            edit.setText(selected)

    def _sync_second(self, *_args) -> None:
        self.maa2_row.setVisible(self.instance_count.currentIndex() == 1)

    def _sync_bark(self, enabled: bool) -> None:
        self.bark_key.setVisible(bool(enabled))

    def selected_directories(self) -> list[Path]:
        values = [self.maa1_path.text().strip()]
        if self.instance_count.currentIndex() == 1:
            values.append(self.maa2_path.text().strip())
        directories: list[Path] = []
        for value in values:
            directory = Path(value) if value else Path()
            if not value or not directory.is_dir() or not (directory / "MAA.exe").is_file():
                raise ValueError("没有找到 MAA.exe，请重新选择 MAA 所在的文件夹。")
            directories.append(directory)
        return directories

    def _finish(self) -> None:
        try:
            directories = self.selected_directories()
            if self.enable_bark.isChecked() and not self.bark_key.text().strip():
                raise ValueError("打开 Bark 通知后，需要填写 Bark Key。")
            template = json.loads(config_template_path(self.base_dir).read_text(encoding="utf-8"))
            config = build_first_run_config(
                template,
                directories,
                enable_bark=self.enable_bark.isChecked(),
                bark_key=self.bark_key.text(),
            )
            atomic_save_config_file(self.base_dir, config)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            QMessageBox.warning(self, "请检查填写内容", str(exc))
            return
        self.accept()
