import json
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path


def _get_ci(d, *names, default=None):
    if not isinstance(d, dict):
        return default
    for name in names:
        if name in d:
            return d[name]
    lower = {str(k).lower(): v for k, v in d.items()}
    for name in names:
        if str(name).lower() in lower:
            return lower[str(name).lower()]
    return default


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _configured_gui_path(cfg, label):
    explicit = (cfg.get("maa_gui_config_paths") or {}).get(label)
    if explicit:
        return Path(explicit)
    instance_dir = (cfg.get("maa_instance_dirs") or {}).get(label)
    if instance_dir:
        return Path(instance_dir) / "config" / "gui.new.json"
    return None


def read_maa_timer_schedule(cfg):
    """Return (schedule_by_label, source_meta).

    Each schedule item is {time, timer_id, config, one_time, source}.
    MAA's nullable IsEnabled is intentional: current WPF treats null as enabled
    for one scheduled start, then turns it off after the start.
    """
    fallback = cfg.get("maa_schedule", {}) or {}
    labels = []
    for source in (cfg.get("maa_instance_dirs") or {}, cfg.get("maa_gui_config_paths") or {}, fallback):
        for label in source:
            if label not in labels:
                labels.append(label)

    schedule = {}
    meta = {}
    for label in labels:
        path = _configured_gui_path(cfg, label)
        parsed_ok = False
        items = []
        if path:
            parse_error = None
            candidates = [(path, "maa_gui"), (Path(str(path) + ".bak"), "maa_gui_bak")]
            for candidate, source_name in candidates:
                if not candidate.exists():
                    continue
                try:
                    root = _read_json(candidate)
                    timers = _get_ci(root, "Timers", "timers", default={})
                    timer_list = _get_ci(timers, "List", "list", default=[])
                    if not isinstance(timer_list, list):
                        raise ValueError("Timers.List is not a list")
                    parsed_ok = True
                    for timer in timer_list:
                        if not isinstance(timer, dict):
                            continue
                        enabled = _get_ci(timer, "IsEnabled", "isEnabled", default=False)
                        # MAA's own code skips only explicit false; null means one-time enabled.
                        if enabled is False:
                            continue
                        try:
                            hour = int(_get_ci(timer, "Hour", "hour"))
                            minute = int(_get_ci(timer, "Minute", "minute"))
                            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                                continue
                        except Exception:
                            continue
                        items.append({
                            "time": f"{hour:02d}:{minute:02d}",
                            "timer_id": _get_ci(timer, "Id", "id"),
                            "config": str(_get_ci(timer, "Config", "config", default="") or ""),
                            "one_time": enabled is None,
                            "source": source_name,
                        })
                    path = candidate
                    break
                except Exception as exc:
                    parse_error = str(exc)
                    items = []
            if not parsed_ok and parse_error:
                meta[label] = {"source": "fallback", "path": str(path), "error": parse_error}

        if parsed_ok:
            # MAA's own CheckTimers loop breaks on the first matching timer.
            # Two enabled slots with the same HH:MM therefore do not represent
            # two independent expected starts. Keep the first one by list order.
            dedup=[]; seen_times=set()
            for item in items:
                if item["time"] in seen_times:
                    continue
                seen_times.add(item["time"]); dedup.append(item)
            dedup.sort(key=lambda x: x["time"])
            schedule[label] = dedup
            source_name = dedup[0].get("source", "maa_gui") if dedup else ("maa_gui_bak" if str(path).endswith(".bak") else "maa_gui")
            meta[label] = {"source": source_name, "path": str(path), "count": len(dedup)}
        else:
            values = []
            for idx, hhmm in enumerate(fallback.get(label, []) or []):
                try:
                    h, m = [int(x) for x in str(hhmm).split(":", 1)]
                    if 0 <= h <= 23 and 0 <= m <= 59:
                        values.append({
                            "time": f"{h:02d}:{m:02d}",
                            "timer_id": idx,
                            "config": "",
                            "one_time": False,
                            "source": "fallback",
                        })
                except Exception:
                    continue
            values.sort(key=lambda x: x["time"])
            schedule[label] = values
            meta.setdefault(label, {"source": "fallback", "path": str(path) if path else None, "count": len(values)})
    return schedule, meta


def override_file_path(base_dir, cfg):
    configured = str(cfg.get("schedule_override_file", "state\\schedule_overrides.json"))
    p = Path(configured)
    if not p.is_absolute():
        p = Path(base_dir) / p
    return p


def load_overrides(base_dir, cfg, prune=True, now=None):
    path = override_file_path(base_dir, cfg)
    now = now or datetime.now()
    data = {"version": 1, "overrides": []}
    if path.exists():
        try:
            raw = _read_json(path)
            if isinstance(raw, dict):
                data.update(raw)
            elif isinstance(raw, list):
                data["overrides"] = raw
        except Exception:
            pass
    rows = [x for x in data.get("overrides", []) if isinstance(x, dict)]
    if prune:
        keep = []
        cutoff = now - timedelta(days=int(cfg.get("schedule_override_keep_days", 7)))
        for row in rows:
            try:
                day = datetime.strptime(str(row.get("date")), "%Y-%m-%d")
            except Exception:
                keep.append(row)
                continue
            if day >= cutoff.replace(hour=0, minute=0, second=0, microsecond=0):
                keep.append(row)
        if keep != rows:
            data["overrides"] = keep
            save_overrides(base_dir, cfg, data)
        rows = keep
    data["overrides"] = rows
    return data


def save_overrides(base_dir, cfg, data):
    path = override_file_path(base_dir, cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def occurrence_key(label, dt):
    return f"{label}|{dt.strftime('%Y-%m-%d %H:%M')}"


def _parse_dt(text):
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(str(text), fmt)
        except Exception:
            pass
    raise ValueError(f"Invalid datetime: {text}")


def base_occurrences(base_dir, cfg, start_dt, end_dt):
    schedule, meta = read_maa_timer_schedule(cfg)
    rows = []
    now = datetime.now()
    for label, timers in schedule.items():
        for timer in timers:
            h, m = [int(x) for x in timer["time"].split(":")]
            if timer.get("one_time"):
                # MAA's tri-state timer means "run once at the next matching
                # clock time". Anchor it to the requested window, not blindly to
                # datetime.now(): the watchdog often asks for a window beginning
                # earlier today so it can still judge a one-time timer that was
                # due a few minutes ago. A future-only GUI window still naturally
                # resolves to tomorrow once today's time is outside that window.
                anchor = max(start_dt, now - timedelta(days=1))
                candidate = anchor.replace(hour=h, minute=m, second=0, microsecond=0)
                if candidate < anchor:
                    candidate += timedelta(days=1)
                candidates = [candidate]
            else:
                candidates = []
                day = start_dt.date()
                while day <= end_dt.date():
                    candidates.append(datetime(day.year, day.month, day.day, h, m))
                    day += timedelta(days=1)
            for dt in candidates:
                if start_dt <= dt <= end_dt:
                    rows.append({
                        "label": label,
                        "datetime": dt,
                        "original_datetime": dt,
                        "original_time": timer["time"],
                        "timer_id": timer.get("timer_id"),
                        "config": timer.get("config", ""),
                        "one_time": bool(timer.get("one_time")),
                        "source": timer.get("source", meta.get(label, {}).get("source", "fallback")),
                        "override": None,
                    })
    rows.sort(key=lambda x: (x["datetime"], x["label"]))
    return rows, meta

def effective_occurrences(base_dir, cfg, start_dt, end_dt, include_skipped=False):
    data = load_overrides(base_dir, cfg)
    # Include a normal one-day margin, then expand it for any move override whose
    # target falls inside the requested window. Otherwise an occurrence moved
    # several days could disappear once its original date is no longer nearby.
    raw_start = start_dt - timedelta(days=1)
    raw_end = end_dt + timedelta(days=1)
    for ov in data.get("overrides", []):
        if str(ov.get("action", "")).lower() != "move":
            continue
        try:
            original = _parse_dt(f"{ov.get('date')} {ov.get('original_time')}")
            target = _parse_dt(ov.get("new_datetime"))
        except Exception:
            continue
        if start_dt <= target <= end_dt:
            raw_start = min(raw_start, original - timedelta(minutes=1))
            raw_end = max(raw_end, original + timedelta(minutes=1))
    rows, meta = base_occurrences(base_dir, cfg, raw_start, raw_end)
    index = {}
    for ov in data.get("overrides", []):
        label = str(ov.get("label", ""))
        date = str(ov.get("date", ""))
        original_time = str(ov.get("original_time", ""))
        if label and date and original_time:
            index[(label, date, original_time)] = ov

    out = []
    for row in rows:
        original = row["original_datetime"]
        ov = index.get((row["label"], original.strftime("%Y-%m-%d"), row["original_time"]))
        if ov:
            action = str(ov.get("action", "")).lower()
            row = dict(row)
            row["override"] = ov
            if action == "skip":
                row["skipped"] = True
                if include_skipped and start_dt <= original <= end_dt:
                    out.append(row)
                continue
            if action == "move":
                try:
                    row["datetime"] = _parse_dt(ov.get("new_datetime"))
                except Exception:
                    pass
        row["skipped"] = False
        if start_dt <= row["datetime"] <= end_dt:
            out.append(row)
    out.sort(key=lambda x: (x["datetime"], x["label"]))
    return out, meta


def add_override(base_dir, cfg, label, original_dt, action, new_dt=None, note=""):
    action = str(action).lower()
    if action not in {"skip", "move"}:
        raise ValueError("action must be skip or move")
    if action == "move" and new_dt is None:
        raise ValueError("move requires new_dt")
    data = load_overrides(base_dir, cfg)
    date = original_dt.strftime("%Y-%m-%d")
    original_time = original_dt.strftime("%H:%M")
    # One override per base occurrence. Replacing it makes GUI/CLI idempotent.
    rows = [x for x in data.get("overrides", []) if not (
        str(x.get("label")) == label and str(x.get("date")) == date and str(x.get("original_time")) == original_time
    )]
    row = {
        "id": uuid.uuid4().hex[:12],
        "label": label,
        "date": date,
        "original_time": original_time,
        "action": action,
        "new_datetime": new_dt.strftime("%Y-%m-%d %H:%M") if new_dt else None,
        "note": note or "",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    rows.append(row)
    data["overrides"] = rows
    save_overrides(base_dir, cfg, data)
    return row


def remove_override(base_dir, cfg, override_id=None, label=None, original_dt=None):
    data = load_overrides(base_dir, cfg)
    old = data.get("overrides", [])
    kept = []
    removed = []
    for row in old:
        match = False
        if override_id and str(row.get("id")) == str(override_id):
            match = True
        elif label and original_dt:
            match = (str(row.get("label")) == label and
                     str(row.get("date")) == original_dt.strftime("%Y-%m-%d") and
                     str(row.get("original_time")) == original_dt.strftime("%H:%M"))
        if match:
            removed.append(row)
        else:
            kept.append(row)
    data["overrides"] = kept
    if removed:
        save_overrides(base_dir, cfg, data)
    return removed
