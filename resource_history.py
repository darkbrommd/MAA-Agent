"""Compact resource history used by the manager charts and detail views.

Files keep the legacy operational-day split for backward compatibility. The
GUI can assemble a calendar day (00:00-24:00) from the two relevant files.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import psutil


def operational_date(moment: datetime, start_hour: int = 4):
    shifted = moment - timedelta(hours=start_hour)
    return shifted.date()


def history_dir(base_dir: Path) -> Path:
    return base_dir / "state" / "resource_history"


def history_path(base_dir: Path, day, start_hour: int = 4) -> Path:
    if isinstance(day, datetime):
        day = operational_date(day, start_hour)
    return history_dir(base_dir) / f"{day.isoformat()}.jsonl"


def _normal_name(value: str) -> str:
    name = (value or "unknown").strip().lower()
    return name[:-4] if name.endswith(".exe") else name


def _category(name: str, cmdline: list[str], maa_names: set[str], mumu_names: set[str], heavy_names: set[str]) -> tuple[str, str]:
    lower = (name or "").lower()
    normal = _normal_name(lower)
    command = " ".join(str(x).lower() for x in (cmdline or []))
    if lower in maa_names or normal == "maa":
        return "maa", "MAA"
    if lower in mumu_names or normal.startswith(("mumu", "nemu")):
        return "mumu", "MuMu"
    if normal.startswith("python") and "agent.py" in command:
        return "agent", "MAA Agent"
    if lower in heavy_names or normal in {_normal_name(x) for x in heavy_names}:
        return "heavy_known", name or normal
    return "other", name or normal


def _process_snapshot(cfg: dict, *, process_limit: int = 20, app_limit: int = 12) -> tuple[list[dict], list[dict], float]:
    maa_names = {str(x).lower() for x in cfg.get("maa_process_names", ["MAA.exe"])}
    mumu_names = {str(x).lower() for x in cfg.get("mumu_process_names", [])}
    heavy_names = {str(x).lower() for x in cfg.get("expected_heavy_process_names", [])}
    processes: list[dict] = []
    groups: dict[tuple[str, str], dict] = defaultdict(lambda: {"mb": 0.0, "count": 0})
    agent_mb = 0.0
    for proc in psutil.process_iter(["pid", "name", "memory_info", "cmdline"]):
        try:
            memory = proc.info.get("memory_info")
            mb = (memory.rss if memory else 0) / 1048576
            if mb <= 0:
                continue
            name = proc.info.get("name") or f"PID {proc.pid}"
            category, app_name = _category(name, proc.info.get("cmdline") or [], maa_names, mumu_names, heavy_names)
            processes.append({"pid": proc.pid, "name": name, "mb": round(mb, 1), "category": category})
            key = (category, app_name)
            groups[key]["mb"] += mb
            groups[key]["count"] += 1
            if category == "agent":
                agent_mb += mb
        except (psutil.Error, AttributeError, TypeError):
            continue
    processes.sort(key=lambda row: row["mb"], reverse=True)
    apps = [
        {"name": name, "category": category, "mb": round(value["mb"], 1), "count": value["count"]}
        for (category, name), value in groups.items()
    ]
    apps.sort(key=lambda row: row["mb"], reverse=True)
    return processes[:process_limit], apps[:app_limit], round(agent_mb, 1)


def _process_totals(names: set[str]) -> tuple[float, int]:
    total = 0.0
    count = 0
    normal_names = {_normal_name(value) for value in names}
    is_mumu_family = any(value.startswith(("mumu", "nemu")) for value in normal_names)
    for proc in psutil.process_iter(["name", "memory_info"]):
        try:
            name = (proc.info.get("name") or "").lower()
            normal = _normal_name(name)
            if name in names or normal in normal_names or (is_mumu_family and normal.startswith(("mumu", "nemu"))):
                memory = proc.info.get("memory_info")
                total += (memory.rss if memory else 0) / 1048576
                count += 1
        except (psutil.Error, AttributeError):
            continue
    return round(total, 1), count


def _last_sample_time(path: Path) -> float:
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - 8192))
            lines = handle.read().decode("utf-8", errors="replace").splitlines()
        for line in reversed(lines):
            try:
                return float(json.loads(line).get("epoch", 0))
            except Exception:
                continue
    except OSError:
        pass
    return 0.0


def sample(base_dir: Path, cfg: dict, *, force: bool = False) -> dict | None:
    now = datetime.now()
    start_hour = int(cfg.get("operational_day_start_hour", 4))
    path = history_path(base_dir, now, start_hour)
    vm = psutil.virtual_memory()
    cpu = float(psutil.cpu_percent(interval=None))
    memory_abnormal = vm.percent >= float(cfg.get("memory_pressure_percent", 88))
    cpu_abnormal = cpu >= float(cfg.get("cpu_high_percent", 92))
    is_abnormal = memory_abnormal or cpu_abnormal
    interval_minutes = float(cfg.get("resource_sample_abnormal_minutes" if is_abnormal else "resource_sample_normal_minutes", 1 if is_abnormal else 5))
    if not force and time.time() - _last_sample_time(path) < max(10, interval_minutes * 60 - 5):
        return None

    maa_names = {str(x).lower() for x in cfg.get("maa_process_names", ["MAA.exe"])}
    mumu_names = {str(x).lower() for x in cfg.get("mumu_process_names", [])}
    maa_mb, maa_count = _process_totals(maa_names)
    mumu_mb, mumu_count = _process_totals(mumu_names)
    expected_maa = max(1, len(cfg.get("maa_instance_dirs", {})) or 2)
    missing_maa = maa_count < expected_maa
    abnormal = is_abnormal or missing_maa
    top_processes, top_apps, agent_mb = _process_snapshot(cfg)
    total_mb = vm.total / 1048576
    available_mb = vm.available / 1048576
    used_mb = max(0.0, total_mb - available_mb)
    reasons = []
    if memory_abnormal:
        reasons.append(f"内存 {vm.percent:.0f}% 达到阈值")
    if cpu_abnormal:
        reasons.append(f"CPU {cpu:.0f}% 达到阈值")
    if missing_maa:
        reasons.append(f"MAA 进程 {maa_count}/{expected_maa}")
    row = {
        "time": now.astimezone().isoformat(timespec="seconds"),
        "epoch": time.time(),
        "cpu": round(cpu, 1),
        "ram": round(float(vm.percent), 1),
        "total_mb": round(total_mb, 1),
        "used_mb": round(used_mb, 1),
        "available_mb": round(available_mb, 1),
        "maa_mb": maa_mb,
        "mumu_mb": mumu_mb,
        "agent_mb": agent_mb,
        "maa_count": maa_count,
        "mumu_count": mumu_count,
        "expected_maa_count": expected_maa,
        "abnormal": bool(abnormal),
        "abnormal_reasons": reasons,
        "top_apps": top_apps[:8],
    }
    if abnormal:
        row["top_processes"] = top_processes
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    prune(base_dir, int(cfg.get("resource_history_keep_days", 30)))
    return row


def prune(base_dir: Path, keep_days: int) -> None:
    cutoff = datetime.now().date() - timedelta(days=max(1, keep_days))
    directory = history_dir(base_dir)
    if not directory.exists():
        return
    for path in directory.glob("*.jsonl"):
        try:
            if datetime.strptime(path.stem, "%Y-%m-%d").date() < cutoff:
                path.unlink(missing_ok=True)
        except (ValueError, OSError):
            continue


def load_day(base_dir: Path, day, start_hour: int = 4) -> list[dict]:
    path = history_path(base_dir, day, start_hour)
    rows = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                value = json.loads(line)
                if isinstance(value, dict) and value.get("time"):
                    rows.append(value)
            except json.JSONDecodeError:
                continue
    except FileNotFoundError:
        pass
    return rows


def load_calendar_day(base_dir: Path, day: date, start_hour: int = 4) -> list[dict]:
    """Load one local calendar day while preserving legacy file boundaries."""
    start_dt = datetime.combine(day, datetime.min.time())
    start = start_dt.timestamp()
    end = (start_dt + timedelta(days=1)).timestamp()
    source_days = {
        operational_date(datetime.fromtimestamp(start), start_hour),
        operational_date(datetime.fromtimestamp(end - 1), start_hour),
    }
    rows: list[dict] = []
    for source_day in source_days:
        rows.extend(load_day(base_dir, source_day, start_hour))
    rows = [row for row in rows if start <= float(row.get("epoch", 0)) < end]
    rows.sort(key=lambda row: float(row.get("epoch", 0)))
    return rows


def memory_values(row: dict) -> tuple[float, float, float]:
    """Return used, available and total MB for both new and legacy rows."""
    available = float(row.get("available_mb") or 0)
    total = float(row.get("total_mb") or 0)
    used = float(row.get("used_mb") or 0)
    if not used and total:
        used = max(0.0, total - available)
    if not total and available and row.get("ram") is not None:
        percent = max(0.0, min(99.9, float(row.get("ram") or 0)))
        total = available / max(0.001, 1.0 - percent / 100.0)
        used = max(0.0, total - available)
    return used, available, total
