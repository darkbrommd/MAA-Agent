from __future__ import annotations

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt


def schedule_row_key(item: dict) -> tuple[str, object, object, str]:
    return (
        str(item.get("label") or ""),
        item.get("original_datetime") or item.get("datetime"),
        item.get("timer_id"),
        str(item.get("config") or ""),
    )


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

    def sync_rows(self, rows) -> str:
        """Update one loaded window without resetting when it only grew at an edge."""
        incoming = tuple(dict(row) for row in rows)
        if not self._rows:
            self.set_rows(incoming)
            return "reset"
        old_keys = tuple(schedule_row_key(row) for row in self._rows)
        new_keys = tuple(schedule_row_key(row) for row in incoming)
        if old_keys == new_keys:
            self._rows = incoming
            if incoming:
                self.dataChanged.emit(self.index(0, 0), self.index(len(incoming) - 1, 0))
            return "update"
        if len(incoming) > len(self._rows) and new_keys[: len(old_keys)] == old_keys:
            old_count = len(self._rows)
            self._rows = tuple(incoming[:old_count])
            if old_count:
                self.dataChanged.emit(self.index(0, 0), self.index(old_count - 1, 0))
            self.beginInsertRows(QModelIndex(), old_count, len(incoming) - 1)
            self._rows = incoming
            self.endInsertRows()
            return "append"
        if len(incoming) > len(self._rows) and new_keys[-len(old_keys) :] == old_keys:
            added = len(incoming) - len(self._rows)
            self.beginInsertRows(QModelIndex(), 0, added - 1)
            self._rows = incoming
            self.endInsertRows()
            if old_keys:
                self.dataChanged.emit(self.index(added, 0), self.index(len(incoming) - 1, 0))
            return "prepend"
        self.set_rows(incoming)
        return "reset"

    def row_for_key(self, key: tuple | None) -> int:
        if key is None:
            return -1
        for index, item in enumerate(self._rows):
            if schedule_row_key(item) == key:
                return index
        return -1

    def item(self, row: int) -> dict | None:
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def rows(self) -> tuple[dict, ...]:
        return self._rows
