from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout


@dataclass(frozen=True)
class NavigationItem:
    key: str
    label: str
    group: str


ITEMS = (
    NavigationItem("dashboard", "总览", "监控中心"),
    NavigationItem("schedule", "日程管理", "监控中心"),
    NavigationItem("resources", "资源监控", "监控中心"),
    NavigationItem("news", "公告中心", "监控中心"),
    NavigationItem("logs", "日志中心", "系统管理"),
    NavigationItem("settings", "设置", "系统管理"),
)


class NavigationRail(QFrame):
    page_requested = Signal(str)
    theme_requested = Signal(str)

    def __init__(self, version: str, theme: str = "dark", parent=None):
        super().__init__(parent)
        self.setObjectName("NavigationRail")
        self.setFixedWidth(192)
        self._theme = theme
        self._expanded = True
        self._buttons: dict[str, QPushButton] = {}
        self._button_labels: dict[str, str] = {}
        self._optional_labels: list[QLabel] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 16, 12, 12)
        layout.setSpacing(5)

        product_row = QHBoxLayout()
        self.product = QLabel("MAA Agent")
        self.product.setObjectName("PageTitle")
        product_row.addWidget(self.product, 1)
        collapse = QPushButton("‹")
        collapse.setFixedWidth(34)
        collapse.setToolTip("折叠 / 展开导航栏")
        collapse.clicked.connect(self._toggle_collapsed)
        self.collapse_button = collapse
        product_row.addWidget(collapse)
        layout.addLayout(product_row)
        subtitle = QLabel("2.0 Preview")
        subtitle.setObjectName("MutedLabel")
        self._optional_labels.append(subtitle)
        layout.addWidget(subtitle)
        layout.addSpacing(18)

        group = QButtonGroup(self)
        group.setExclusive(True)
        current_group = None
        for item in ITEMS:
            if item.group != current_group:
                if current_group is not None:
                    layout.addSpacing(10)
                heading = QLabel(item.group)
                heading.setObjectName("SectionLabel")
                self._optional_labels.append(heading)
                layout.addWidget(heading)
                current_group = item.group
            button = QPushButton(item.label)
            button.setObjectName("NavButton")
            button.setCheckable(True)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.clicked.connect(lambda _checked=False, key=item.key: self.page_requested.emit(key))
            group.addButton(button)
            self._buttons[item.key] = button
            self._button_labels[item.key] = item.label
            layout.addWidget(button)

        layout.addStretch(1)
        self.theme_button = QPushButton()
        self.theme_button.clicked.connect(self._toggle_theme)
        layout.addWidget(self.theme_button)
        version_label = QLabel(f"v{version}")
        version_label.setObjectName("MutedLabel")
        self._optional_labels.append(version_label)
        layout.addWidget(version_label)
        self.set_theme(theme)

    def set_current(self, key: str) -> None:
        button = self._buttons.get(key)
        if button is not None:
            button.setChecked(True)

    def set_theme(self, theme: str) -> None:
        self._theme = "light" if theme == "light" else "dark"
        if self._expanded:
            self.theme_button.setText("切换到深色" if self._theme == "light" else "切换到浅色")
        else:
            self.theme_button.setText("深" if self._theme == "light" else "浅")

    def _toggle_theme(self) -> None:
        self.theme_requested.emit("dark" if self._theme == "light" else "light")

    def _toggle_collapsed(self) -> None:
        self._expanded = not self._expanded
        self.setFixedWidth(192 if self._expanded else 68)
        self.product.setText("MAA Agent" if self._expanded else "MAA")
        self.collapse_button.setText("‹" if self._expanded else "›")
        for label in self._optional_labels:
            label.setVisible(self._expanded)
        for key, button in self._buttons.items():
            full = self._button_labels[key]
            button.setText(full if self._expanded else full[:1])
            button.setToolTip("" if self._expanded else full)
        self.theme_button.setText(
            ("切换到深色" if self._theme == "light" else "切换到浅色")
            if self._expanded
            else ("深" if self._theme == "light" else "浅")
        )
