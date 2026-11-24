from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QWidget


def show_schedule_error(parent: QWidget, item: dict) -> None:
    error = dict(item.get("error") or {})
    when = item.get("datetime")
    text = (
        f"实例：{item.get('label') or 'MAA'}\n"
        f"计划时间：{when:%Y-%m-%d %H:%M}\n"
        f"失败任务：{error.get('task') or '当前任务'}\n"
        f"失败时间：{error.get('time') or '未知'}\n\n"
        f"发生了什么：{error.get('summary') or error.get('title') or '任务没有正常完成。'}\n\n"
        f"可能原因：{error.get('likely') or '没有更多可靠证据。'}\n\n"
        f"建议操作：{error.get('action') or '检查当前游戏画面与 MAA 日志。'}"
    )
    QMessageBox.critical(parent, str(error.get("title") or "运行错误详情"), text)
