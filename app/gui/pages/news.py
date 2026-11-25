from __future__ import annotations

from html import escape
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListView,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from gui.models.news_model import NewsModel
from gui.services.news_service import NewsService, NewsSnapshot


class NewsPage(QWidget):
    def __init__(self, base_dir: Path, page_state: dict, theme: str = "dark", parent=None):
        super().__init__(parent)
        self.base_dir = Path(base_dir)
        self.page_state = page_state
        self._requested_mode = "local"
        self.model = NewsModel(self)
        self._build_ui()
        self.service = NewsService(self.base_dir, parent=self)
        self.service.loaded.connect(self._apply_snapshot)
        self.service.failed.connect(self._failed)
        self.service.progress.connect(self._progress)
        self.service.busy_changed.connect(self._set_busy)
        self._split_save = QTimer(self)
        self._split_save.setSingleShot(True)
        self._split_save.setInterval(180)
        self._split_save.timeout.connect(lambda: self.page_state.__setitem__("splitter_sizes", self.splitter.sizes()))
        self.splitter.splitterMoved.connect(lambda *_: self._split_save.start())
        QTimer.singleShot(0, self._restore_state)
        self.service.load_local()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 24)
        root.setSpacing(12)
        header = QHBoxLayout()
        title = QLabel("公告中心")
        title.setObjectName("PageTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.incremental = QPushButton("增量更新")
        self.incremental.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.incremental.clicked.connect(self._refresh_incremental)
        self.full = QPushButton("全部重拉")
        self.full.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.full.clicked.connect(self._refresh_full)
        header.addWidget(self.incremental)
        header.addWidget(self.full)
        root.addLayout(header)
        self.status = QLabel("正在读取本地缓存…")
        self.status.setObjectName("MutedLabel")
        root.addWidget(self.status)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.list = QListView()
        self.list.setModel(self.model)
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.setAlternatingRowColors(False)
        self.list.setWordWrap(True)
        self.list.setSpacing(4)
        self.list.selectionModel().currentChanged.connect(self._show_current)
        self.detail = QTextBrowser()
        self.detail.setOpenExternalLinks(False)
        self.detail.anchorClicked.connect(QDesktopServices.openUrl)
        self.splitter.addWidget(self.list)
        self.splitter.addWidget(self.detail)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 7)
        root.addWidget(self.splitter, 1)

    def on_activated(self) -> None:
        pass

    def _restore_state(self) -> None:
        values = self.page_state.get("splitter_sizes", (340, 760))
        if isinstance(values, (list, tuple)) and len(values) == 2:
            self.splitter.setSizes([max(1, int(value)) for value in values])
        self.list.verticalScrollBar().setValue(int(self.page_state.get("list_scroll") or 0))
        self.detail.verticalScrollBar().setValue(int(self.page_state.get("detail_scroll") or 0))

    def _refresh_incremental(self) -> None:
        self._requested_mode = "incremental"
        if self.service.refresh(full=False):
            self.status.setText("正在增量更新；遇到已缓存公告会尽快停止。")

    def _refresh_full(self) -> None:
        answer = QMessageBox.question(
            self,
            "全部重拉",
            "全部重拉会访问更多官网页面，耗时和流量都高于增量更新。确定继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._requested_mode = "full"
        if self.service.refresh(full=True):
            self.status.setText("正在全部重拉…")

    def _apply_snapshot(self, snapshot: NewsSnapshot) -> None:
        current = self.model.item(self.list.currentIndex().row())
        current_cid = str((current or {}).get("cid") or self.page_state.get("selected_cid") or "")
        list_scroll = self.list.verticalScrollBar().value()
        detail_scroll = self.detail.verticalScrollBar().value()
        self.model.set_rows(snapshot.items)
        row = self.model.row_for_cid(current_cid)
        if row < 0 and self.model.rowCount() > 0:
            row = 0
        if row >= 0:
            index = self.model.index(row, 0)
            self.list.setCurrentIndex(index)
            self._show_current(index)
        else:
            self.detail.setPlainText("本地还没有公告缓存，可点击“增量更新”。")
        self.list.verticalScrollBar().setValue(list_scroll)
        self.detail.verticalScrollBar().setValue(detail_scroll)
        if self._requested_mode == "local":
            self.status.setText(f"本地缓存 {snapshot.total} 条 · 更新于 {snapshot.updated_at}")
        else:
            verb = "全部重拉" if self._requested_mode == "full" else "增量更新"
            self.status.setText(f"{verb}完成 · 新增 {snapshot.new_count} 条 · 共 {snapshot.total} 条")
        if snapshot.errors:
            self.status.setText(self.status.text() + f" · {len(snapshot.errors)} 个页面失败")
        self._requested_mode = "local"

    def _show_current(self, index, _previous=None) -> None:
        item = self.model.item(index.row())
        if not item:
            return
        self.page_state["selected_cid"] = str(item.get("cid") or "")
        title = escape(str(item.get("title") or "官网公告"))
        when = escape(str(item.get("display_time") or item.get("published_at") or "时间未知"))
        brief = escape(str(item.get("brief") or item.get("summary") or "暂无摘要")).replace("\n", "<br>")
        body = escape(str(item.get("body") or item.get("content") or "")).replace("\n", "<br>")
        url = escape(str(item.get("url") or ""), quote=True)
        link = f'<p><a href="{url}">在浏览器打开原文</a></p>' if url else ""
        self.detail.setHtml(f"<h2>{title}</h2><p>{when}</p><p>{brief}</p>{body and f'<hr><p>{body}</p>'}{link}")

    def _progress(self, current: int, total: int, message: str) -> None:
        prefix = f"{current}/{total} · " if total else ""
        self.status.setText(prefix + message)

    def _set_busy(self, busy: bool) -> None:
        self.incremental.setEnabled(not busy)
        self.full.setEnabled(not busy)

    def _failed(self, message: str) -> None:
        self.status.setText(f"公告更新失败：{message}")
        QMessageBox.warning(self, "公告更新失败", message)

    def hideEvent(self, event) -> None:
        self.page_state["list_scroll"] = self.list.verticalScrollBar().value()
        self.page_state["detail_scroll"] = self.detail.verticalScrollBar().value()
        super().hideEvent(event)

    def set_theme(self, theme: str) -> None:
        del theme
