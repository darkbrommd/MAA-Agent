from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui.services.log_service import LogChunk, LogService, line_matches


LOG_LABELS = {
    "agent.log": "运行日志（agent.log）",
    "decision.log": "决策日志（decision.log）",
}


class LogsPage(QWidget):
    def __init__(self, base_dir: Path, page_state: dict, theme: str = "dark", parent=None):
        super().__init__(parent)
        self.base_dir = Path(base_dir)
        self.page_state = page_state
        self._raw: dict[str, list[str]] = {"agent.log": [], "decision.log": []}
        self._follow = bool(page_state.get("follow", True))
        self._build_ui()
        self.service = LogService(self.base_dir, parent=self)
        self.service.chunk_ready.connect(self._apply_chunk)
        self.service.failed.connect(lambda message: self.status.setText(f"读取失败：{message}"))

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 24)
        root.setSpacing(12)
        title = QLabel("日志中心")
        title.setObjectName("PageTitle")
        root.addWidget(title)
        tools = QHBoxLayout()
        self.file = QComboBox()
        for filename, label in LOG_LABELS.items():
            self.file.addItem(label, filename)
        stored_file = str(self.page_state.get("file") or "agent.log")
        self.file.setCurrentIndex(max(0, self.file.findData(stored_file)))
        self.file.currentIndexChanged.connect(self._change_file)
        self.mode = QComboBox()
        self.mode.addItems(("全部", "错误与警告", "事故与恢复"))
        self.mode.setCurrentText(str(self.page_state.get("mode") or "全部"))
        self.mode.currentTextChanged.connect(self._change_filter)
        self.search = QLineEdit(str(self.page_state.get("search") or ""))
        self.search.setPlaceholderText("搜索关键词")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._change_filter)
        self.follow = QPushButton()
        self.follow.clicked.connect(self._toggle_follow)
        self._update_follow_button()
        copy = QPushButton("复制")
        copy.clicked.connect(self._copy)
        open_file = QPushButton("打开日志文件")
        open_file.clicked.connect(self._open)
        refresh = QPushButton("立即刷新")
        refresh.clicked.connect(self.refresh)
        for button in (self.follow, copy, open_file, refresh):
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        tools.addWidget(self.file)
        tools.addWidget(self.mode)
        tools.addWidget(self.search, 1)
        tools.addWidget(self.follow)
        tools.addWidget(copy)
        tools.addWidget(open_file)
        tools.addWidget(refresh)
        root.addLayout(tools)
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.view.setMaximumBlockCount(5000)
        root.addWidget(self.view, 1)
        self.status = QLabel("增量读取；不会清空重插已有内容。")
        self.status.setObjectName("MutedLabel")
        root.addWidget(self.status)

    def on_activated(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        self.service.request(self._current_filename())

    def _apply_chunk(self, chunk: LogChunk) -> None:
        lines = chunk.text.splitlines()
        stored = self._raw.setdefault(chunk.filename, [])
        if chunk.reset:
            stored[:] = lines[-5000:]
            if chunk.filename == self._current_filename():
                self._render()
        elif lines:
            stored.extend(lines)
            del stored[:-5000]
            if chunk.filename == self._current_filename():
                for line in lines:
                    if line_matches(line, self.mode.currentText(), self.search.text().strip()):
                        self.view.appendPlainText(line)
                self._scroll_if_following()
        label = LOG_LABELS.get(chunk.filename, chunk.filename)
        self.status.setText(f"{label} · 已读到 {chunk.offset:,} 字节" if chunk.exists else f"{label} 尚不存在")

    def _render(self, *_args) -> None:
        mode = self.mode.currentText()
        query = self.search.text().strip()
        values = [line for line in self._raw.get(self._current_filename(), ()) if line_matches(line, mode, query)]
        self.view.setPlainText("\n".join(values[-5000:]))
        self._scroll_if_following()

    def _current_filename(self) -> str:
        return str(self.file.currentData() or "agent.log")

    def _change_file(self, _index: int) -> None:
        filename = self._current_filename()
        self.page_state["file"] = filename
        self._render()
        self.refresh()

    def _change_filter(self, *_args) -> None:
        self.page_state["mode"] = self.mode.currentText()
        self.page_state["search"] = self.search.text()
        self._render()

    def _toggle_follow(self) -> None:
        self._follow = not self._follow
        self.page_state["follow"] = self._follow
        self._update_follow_button()
        self._scroll_if_following()

    def _update_follow_button(self) -> None:
        self.follow.setText("暂停自动滚底" if self._follow else "恢复自动滚底")

    def _scroll_if_following(self) -> None:
        if self._follow:
            bar = self.view.verticalScrollBar()
            bar.setValue(bar.maximum())

    def _copy(self) -> None:
        cursor = self.view.textCursor()
        QApplication.clipboard().setText(cursor.selectedText() if cursor.hasSelection() else self.view.toPlainText())

    def _open(self) -> None:
        path = self.base_dir / "logs" / self._current_filename()
        if path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        else:
            self.status.setText(f"文件不存在：{path}")

    def set_theme(self, theme: str) -> None:
        del theme
