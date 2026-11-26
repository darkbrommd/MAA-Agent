from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from project_paths import config_path
from schedule_utils import effective_occurrences


@dataclass(frozen=True)
class ScheduleSnapshot:
    start: datetime
    end: datetime
    rows: tuple[dict, ...]
    meta: dict


def occurrence_key(label: str, when: datetime) -> str:
    return f"{label}|{when:%Y-%m-%d %H:%M}"


def _read_mapping(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}


def schedule_run_records(value: dict) -> dict:
    """Accept both the current {runs: {...}} file and the legacy flat mapping."""
    runs = value.get("runs") if isinstance(value, dict) else None
    return runs if isinstance(runs, dict) else value


def decorate_schedule_rows(
    rows: list[dict],
    run_records: dict,
    daily_states: dict[date, dict],
    alerted_keys: set[str],
    now: datetime,
    grace_minutes: float = 15.0,
) -> tuple[dict, ...]:
    future_candidates = []
    for row in rows:
        when = row["datetime"]
        run = run_records.get(occurrence_key(str(row["label"]), when)) or {}
        if when > now and not row.get("skipped") and run.get("status") not in {"completed", "failed"}:
            future_candidates.append(when)
    next_time = min(future_candidates) if future_candidates else None
    output = []
    for source in rows:
        row = dict(source)
        when = row["datetime"]
        key = occurrence_key(str(row["label"]), when)
        run = run_records.get(key) or {}
        run_status = run.get("status")
        state = "future"
        title = "等待运行"
        detail = "到点后会提醒"
        error = {}
        if row.get("skipped"):
            state, title, detail = "off", "已关闭", "这次计划不会提醒"
        elif run_status == "failed":
            error = dict(run.get("error") or {})
            state, title = "failed", "运行失败"
            detail = str(error.get("title") or "任务没有正常完成")
        elif run_status == "running":
            try:
                updated = datetime.strptime(str(run.get("updated_at")), "%Y-%m-%d %H:%M:%S")
                fresh = timedelta(minutes=-5) <= now - updated <= timedelta(hours=6)
            except (TypeError, ValueError):
                fresh = False
            if fresh:
                state, title = "running", "正在进行"
                detail = f"当前：{run.get('current_task') or '正在执行的任务'}"
            else:
                state, title, detail = "off", "还没确认结果", "运行状态长时间没有更新"
        elif run_status == "completed":
            completed = str(run.get("completed_at") or "")
            suffix = f" · {completed[11:16]} 完成" if len(completed) >= 16 else ""
            state, title, detail = "done", "已完成", f"任务已完成{suffix}"
        else:
            daily = daily_states.get(when.date(), {})
            expected = when.strftime("%Y-%m-%d %H:%M")
            observed = (daily.get("observed_runs") or {}).get(row["label"], [])
            if any(str(item.get("occurrence")) == expected for item in observed if isinstance(item, dict)):
                state, title, detail = "done", "已经开始", "已确认这次计划启动"
            elif key in alerted_keys:
                state, title, detail = "failed", "可能没有启动", "计划没有触发、程序已退出，或上一个任务还没有结束"
                error = {
                    "title": "没有检测到计划启动",
                    "summary": f"{row['label']} 在 {when:%Y-%m-%d %H:%M} 可能没有启动。",
                    "likely": "计划未触发、MAA/MuMu 已退出，或上一轮任务仍在运行。",
                    "action": "检查 MAA 里的计划、程序状态和对应时段的日志。",
                    "task": "计划启动",
                    "time": expected,
                }
            elif any(expected in str(item) and str(row["label"]) in str(item) for item in (daily.get("schedule") or {}).get("unverified", [])):
                state, title, detail = "off", "无法确认", "当时 Agent 还没有运行"
            elif when <= now:
                delay = now - when
                if delay <= timedelta(minutes=max(0.0, float(grace_minutes))):
                    state, title, detail = "running", "等待启动确认", "计划已到点，等待 Agent 检测启动"
                else:
                    state, title, detail = "off", "等待确认", "还没有确认这次计划启动"
            elif next_time is not None and when == next_time:
                state, title, detail = "next", "下一个计划", "即将执行"
        row.update(state=state, status_title=title, status_detail=detail, error=error)
        output.append(row)
    previous_day = None
    for row in output:
        current_day = row["datetime"].date()
        row["show_date_header"] = current_day != previous_day
        previous_day = current_day
    return tuple(output)


class _ScheduleSignals(QObject):
    done = Signal(int, object)
    failed = Signal(int, str)


class _ScheduleWorker(QRunnable):
    def __init__(self, request_id: int, base_dir: Path, start: datetime, end: datetime):
        super().__init__()
        self.request_id = request_id
        self.base_dir = base_dir
        self.start = start
        self.end = end
        self.signals = _ScheduleSignals()

    def run(self) -> None:
        try:
            cfg = _read_mapping(config_path(self.base_dir))
            rows, meta = effective_occurrences(self.base_dir, cfg, self.start, self.end, include_skipped=True)
            state_dir = self.base_dir / "state"
            run_records = schedule_run_records(_read_mapping(state_dir / "schedule_run_status.json"))
            daily_states = {day: _read_mapping(state_dir / f"daily_{day.isoformat()}.json") for day in {row["datetime"].date() for row in rows}}
            alerted = set()
            for row in rows:
                expected = row["datetime"].strftime("%Y-%m-%d %H:%M")
                digest = hashlib.sha1(f"{row['label']}|{expected}".encode("utf-8", errors="ignore")).hexdigest()
                if (state_dir / f"scheduled_alert_{digest}.txt").exists():
                    alerted.add(occurrence_key(str(row["label"]), row["datetime"]))
            decorated = decorate_schedule_rows(
                rows,
                run_records,
                daily_states,
                alerted,
                datetime.now(),
                float(cfg.get("scheduled_run_grace_minutes", 15) or 15),
            )
            aliases = {str(key): str(value).strip() for key, value in (cfg.get("maa_instance_aliases") or {}).items()}
            for row in decorated:
                label = str(row.get("label") or "")
                row["display_label"] = f"{aliases[label]}（{label}）" if aliases.get(label) else label
            self.signals.done.emit(self.request_id, ScheduleSnapshot(self.start, self.end, decorated, dict(meta)))
        except Exception as exc:
            self.signals.failed.emit(self.request_id, str(exc))


class ScheduleService(QObject):
    loaded = Signal(int, object)
    failed = Signal(int, str)

    def __init__(self, base_dir: Path, thread_pool: QThreadPool | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self.base_dir = Path(base_dir)
        self.thread_pool = thread_pool or QThreadPool.globalInstance()
        self._request_id = 0

    @property
    def current_request_id(self) -> int:
        return self._request_id

    def request_window(self, start: datetime, end: datetime) -> int:
        self._request_id += 1
        request_id = self._request_id
        worker = _ScheduleWorker(request_id, self.base_dir, start, end)
        worker.signals.done.connect(self._deliver)
        worker.signals.failed.connect(self._deliver_failure)
        self.thread_pool.start(worker)
        return request_id

    def _deliver(self, request_id: int, snapshot: ScheduleSnapshot) -> None:
        if request_id == self._request_id:
            self.loaded.emit(request_id, snapshot)

    def _deliver_failure(self, request_id: int, message: str) -> None:
        if request_id == self._request_id:
            self.failed.emit(request_id, message)
