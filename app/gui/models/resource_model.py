from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt

from resource_history import memory_values


class ResourceModel(QAbstractListModel):
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
            when = datetime.fromtimestamp(float(item.get("epoch", 0)))
            used, available, _total = memory_values(item)
            return f"{when:%H:%M}  内存 {item.get('ram', 0):.0f}%  已用 {used/1024:.1f} GB  可用 {available/1024:.1f} GB"
        return None

    def set_rows(self, rows) -> None:
        self.beginResetModel()
        self._rows = tuple(dict(row) for row in rows if row.get("abnormal"))
        self.endResetModel()

    def item(self, row: int) -> dict | None:
        return self._rows[row] if 0 <= row < len(self._rows) else None
