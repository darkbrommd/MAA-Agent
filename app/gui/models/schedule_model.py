from __future__ import annotations

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt


class ScheduleModel(QAbstractListModel):
    ItemRole = Qt.ItemDataRole.UserRole + 1

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: tuple[dict, ...] = ()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        item = self._rows[index.row()]
        if role == self.ItemRole:
            return item
        if role == Qt.ItemDataRole.DisplayRole:
            return f"{item['datetime']:%H:%M}  {item['label']}"
        if role == Qt.ItemDataRole.ToolTipRole:
            return item.get("status_detail", "")
        return None

    def set_rows(self, rows) -> None:
        self.beginResetModel()
        self._rows = tuple(dict(row) for row in rows)
        self.endResetModel()

    def item(self, row: int) -> dict | None:
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def rows(self) -> tuple[dict, ...]:
        return self._rows
