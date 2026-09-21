"""Official Arknights announcement monitor.

The official site exposes a small JSON endpoint.  We only fetch article bodies
for newly seen maintenance-related announcements, so normal polling is cheap
and does not involve an LLM.
"""

from __future__ import annotations

import html
import json
import re
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from schedule_utils import add_override, effective_occurrences


NEWS_API = "https://ak.hypergryph.com/api/news?category=ANNOUNCEMENT&page={page}"
ARTICLE_URL = "https://ak.hypergryph.com/news/{cid}"
NEWS_STATE_RELATIVE = ("state", "news_monitor.json")
DEFAULT_NEWS_CACHE_LIMIT = 50
DEFAULT_NEWS_INITIAL_FETCH_COUNT = 20
DEFAULT_NEWS_CONTENT_FETCH_LIMIT = 20
DISPLAY_TIME_TEXT_KEYS = ("displayTimeText", "display_time_text", "display_time")
TIME_RANGE_RE = re.compile(
    r"(?P<m1>\d{1,2})月(?P<d1>\d{1,2})日\s*"
    r"(?P<h1>\d{1,2})[:：](?P<n1>\d{2})\s*"
    r"(?:~|～|—|-|至)\s*"
    r"(?:(?P<m2>\d{1,2})月(?P<d2>\d{1,2})日\s*)?"
    r"(?P<h2>\d{1,2})[:：](?P<n2>\d{2})"
)


def _request_text(url: str, timeout: float = 15.0) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "MAA-Agent-NewsMonitor/2.0 (+local personal monitor)",
            "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def fetch_announcements(timeout: float = 15.0) -> list[dict]:
    rows = []
    # The official endpoint currently returns six rows per page.  Read enough
    # pages to seed the 50-item local cache while retaining a firm request cap.
    for page in range(1, 11):
        payload = json.loads(_request_text(NEWS_API.format(page=page), timeout))
        if payload.get("code") != 0 or not isinstance(payload.get("data"), dict):
            raise RuntimeError(f"官网新闻接口返回异常：{payload.get('msg') or payload.get('code')}")
        rows.extend(payload["data"].get("list", []))
        if payload["data"].get("end") or len(rows) >= DEFAULT_NEWS_CACHE_LIMIT:
            break
    unique = {str(row.get("cid")): row for row in rows if isinstance(row, dict) and row.get("cid")}
    return list(unique.values())


def article_plain_text(cid: str, timeout: float = 15.0) -> str:
    raw = _request_text(ARTICLE_URL.format(cid=cid), timeout)
    # Next.js serialises article HTML into the page stream.  Decode the small
    # set of escapes used there, then strip markup.  Repeated unescape handles
    # both JSON and HTML entities without executing page scripts.
    text = raw.replace("\\u003c", "<").replace("\\u003e", ">")
    text = text.replace("\\n", "\n").replace("\\\"", '"')
    text = html.unescape(html.unescape(text))
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_maintenance_window(text: str, display_timestamp: int | float | None = None):
    match = TIME_RANGE_RE.search(text)
    if not match:
        return None
    anchor = datetime.fromtimestamp(float(display_timestamp)) if display_timestamp else datetime.now()
    values = {key: int(value) if value is not None else None for key, value in match.groupdict().items()}
    start = datetime(anchor.year, values["m1"], values["d1"], values["h1"], values["n1"])
    end_month = values["m2"] or values["m1"]
    end_day = values["d2"] or values["d1"]
    end = datetime(anchor.year, end_month, end_day, values["h2"], values["n2"])
    if end <= start:
        end += timedelta(days=1)
    # Publication around New Year can refer to the adjacent year.
    if start - anchor > timedelta(days=180):
        start = start.replace(year=start.year - 1)
        end = end.replace(year=end.year - 1)
    elif anchor - start > timedelta(days=180):
        start = start.replace(year=start.year + 1)
        end = end.replace(year=end.year + 1)
    return start, end


def maintenance_related(row: dict, keywords: list[str]) -> bool:
    haystack = f"{row.get('title', '')}\n{row.get('brief', '')}"
    return any(str(keyword) in haystack for keyword in keywords if str(keyword).strip())


def _load_state(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _save_state(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def current_due_slot(now: datetime, check_times: list[str]) -> str | None:
    candidates: list[datetime] = []
    for offset in (0, -1):
        day = (now + timedelta(days=offset)).date()
        for value in check_times:
            try:
                hour, minute = [int(x) for x in str(value).split(":", 1)]
                slot = datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute)
                if slot <= now:
                    candidates.append(slot)
            except (ValueError, TypeError):
                continue
    return max(candidates).isoformat(timespec="minutes") if candidates else None


def apply_supervision_overrides(base_dir: Path, cfg: dict, start: datetime, end: datetime, cid: str) -> list[str]:
    if cfg.get("news_maintenance_action", "supervision_only") != "supervision_only":
        return []
    rows, _ = effective_occurrences(base_dir, cfg, start, end, include_skipped=True)
    changed: list[str] = []
    for row in rows:
        when = row.get("datetime")
        if not isinstance(when, datetime) or not (start <= when <= end) or row.get("skipped"):
            continue
        add_override(
            base_dir,
            cfg,
            row["label"],
            row["original_datetime"],
            "skip",
            note=f"官网维护公告 {cid}",
        )
        changed.append(f"{row['label']} {when:%m-%d %H:%M}：本次不做漏跑报警")
    return changed


def news_state_path(base_dir: Path | str) -> Path:
    """Absolute path of the state file shared by poll() and the news cache."""
    return Path(base_dir).joinpath(*NEWS_STATE_RELATIVE)


def _cfg_positive_int(value, default: int, minimum: int = 1) -> int:
    try:
        number = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default
    return number if number >= minimum else default


def _cache_limits(cfg: dict) -> tuple[int, int]:
    """Return the cache limit and the first-init limit from configuration."""
    cfg = cfg if isinstance(cfg, dict) else {}
    limit = _cfg_positive_int(cfg.get("news_cache_limit"), DEFAULT_NEWS_CACHE_LIMIT)
    initial = _cfg_positive_int(cfg.get("news_initial_fetch_count"), DEFAULT_NEWS_INITIAL_FETCH_COUNT)
    return limit, min(initial, limit)


def _row_display_time(row: dict) -> tuple[str, float]:
    """Return the readable display time and epoch seconds of a row or entry."""
    timestamp = 0.0
    for key in ("displayTime", "display_time_ts"):
        raw = row.get(key)
        if raw is None or raw == "":
            continue
        try:
            timestamp = float(str(raw).strip())
            break
        except (TypeError, ValueError):
            continue
    text = ""
    for key in DISPLAY_TIME_TEXT_KEYS:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            text = re.sub(r"\s+", " ", value).strip()
            break
    if not text and timestamp > 0:
        try:
            text = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")
        except (OverflowError, OSError, ValueError):
            text = ""
    return text, timestamp


def _make_cache_entry(row, existing=None) -> dict | None:
    """Normalise an API row or a cached entry; returns None when unusable."""
    if not isinstance(row, dict):
        return None
    cid = str(row.get("cid") or "").strip()
    if not cid:
        return None
    previous = existing if isinstance(existing, dict) else {}
    display_text, display_ts = _row_display_time(row)
    if not display_text:
        display_text, _ = _row_display_time(previous)
    if not display_ts and previous:
        try:
            display_ts = float(previous.get("display_time_ts") or 0)
        except (TypeError, ValueError):
            display_ts = 0.0

    def pick(key: str, fallback: str) -> str:
        for source in (row, previous):
            value = re.sub(r"\s+", " ", str(source.get(key) or "")).strip()
            if value:
                return value
        return fallback

    entry = {
        "cid": cid,
        "title": pick("title", "官网公告"),
        "brief": pick("brief", ""),
        "url": ARTICLE_URL.format(cid=cid),
        "display_time": display_text,
        "display_time_ts": float(display_ts or 0),
    }
    for source in (previous, row):
        content = source.get("content")
        if isinstance(content, str) and content.strip():
            entry["content"] = content.strip()
        if "content" not in entry:
            continue
        fetched_at = source.get("content_fetched_at")
        if isinstance(fetched_at, str) and fetched_at.strip():
            entry["content_fetched_at"] = fetched_at.strip()
    return entry


def _merge_news_items(existing_items, incoming_items, *, limit: int, initial_limit: int) -> tuple[list[dict], int, bool]:
    """Deduplicate by cid, refresh metadata and keep the newest rows."""
    merged: list[dict] = []
    index: dict[str, int] = {}
    cached = existing_items if isinstance(existing_items, list) else []
    for row in cached:
        entry = _make_cache_entry(row)
        if entry is None:
            continue
        position = index.get(entry["cid"])
        if position is None:
            index[entry["cid"]] = len(merged)
            merged.append(entry)
        else:
            merged[position] = _make_cache_entry(row, merged[position]) or merged[position]
    first_init = not merged
    added: set[str] = set()
    fetched = incoming_items if isinstance(incoming_items, list) else []
    for row in fetched:
        entry = _make_cache_entry(row)
        if entry is None:
            continue
        position = index.get(entry["cid"])
        if position is None:
            index[entry["cid"]] = len(merged)
            merged.append(entry)
            added.add(entry["cid"])
        else:
            merged[position] = _make_cache_entry(row, merged[position]) or merged[position]
    merged.sort(key=lambda entry: -float(entry.get("display_time_ts") or 0))
    capped = merged[: initial_limit if first_init else limit]
    # new_count reports only the announcements that actually stayed in the cache.
    new_count = len(added & {entry["cid"] for entry in capped})
    return capped, new_count, first_init


def _load_cache_items(state: dict) -> list:
    value = state.get("news_items") if isinstance(state, dict) else None
    return value if isinstance(value, list) else []


def _report_progress(progress, current: int, total: int, message: str) -> None:
    """Best effort progress reporting; a failing callback never aborts a refresh."""
    if progress is None:
        return
    try:
        progress(current=current, total=total, message=message)
        return
    except TypeError:
        pass
    except Exception:
        return
    try:
        progress(current, total, message)
    except Exception:
        pass


def _merge_cache_into_state(state: dict, cfg: dict, rows, now: datetime) -> None:
    """Refresh the cache part of the state without touching notification state.

    seen_ids, latest_events and last_slot stay exactly as the polling path left
    them, so populating the cache can never re-notify an older announcement.
    """
    try:
        limit, initial_limit = _cache_limits(cfg)
        items, _new_count, _first_init = _merge_news_items(
            _load_cache_items(state), rows, limit=limit, initial_limit=initial_limit
        )
    except Exception:
        return
    state["news_cache_version"] = 1
    state["news_items"] = items
    state["news_cache_total"] = len(items)
    state["news_cache_updated_at"] = now.isoformat(timespec="seconds")


def refresh_news_cache(base_dir: Path | str, cfg: dict | None = None, progress=None, *,
                       timeout: float | None = None, now: datetime | None = None) -> dict:
    """Force a cache refresh for the GUI news centre.

    Returns a structured result: status (baseline on first init, ok afterwards,
    error when the list cannot be fetched or stored), new_count, total, errors
    and the cached items.  The optional progress callback is invoked as
    progress(current=..., total=..., message=...) so a GUI can drive a bar.
    A failed article body fetch keeps the announcement metadata and only adds
    an entry to errors.
    """
    cfg = cfg if isinstance(cfg, dict) else {}
    limit, initial_limit = _cache_limits(cfg)
    state_path = news_state_path(base_dir)
    state = _load_state(state_path)
    cached = _load_cache_items(state)
    seconds = float(timeout if timeout is not None else cfg.get("news_timeout_seconds", 15) or 15)
    stamp = (now or datetime.now()).isoformat(timespec="seconds")
    errors: list[str] = []

    _report_progress(progress, 0, 1, "开始刷新官网公告")
    try:
        rows = fetch_announcements(seconds)
    except Exception as exc:
        errors.append(f"公告列表获取失败：{exc}")
        items, _new_count, _first_init = _merge_news_items(cached, [], limit=limit, initial_limit=initial_limit)
        _report_progress(progress, 1, 1, "公告列表获取失败，保留原有缓存")
        return {
            "status": "error",
            "new_count": 0,
            "total": len(items),
            "errors": errors,
            "items": items,
            "fetched_count": 0,
            "cache_file": str(state_path),
            "updated_at": state.get("news_cache_updated_at"),
        }

    rows = rows if isinstance(rows, list) else []
    items, new_count, first_init = _merge_news_items(cached, rows, limit=limit, initial_limit=initial_limit)
    content_limit = _cfg_positive_int(cfg.get("news_content_fetch_limit"), DEFAULT_NEWS_CONTENT_FETCH_LIMIT)
    pending = [entry for entry in items if not entry.get("content")][:content_limit]
    total_steps = 1 + len(pending)
    _report_progress(progress, 1, total_steps, f"官网公告列表已获取：{len(rows)} 条，新增 {new_count} 条")

    for index, entry in enumerate(pending, start=1):
        _report_progress(progress, index, total_steps,
                         f"抓取正文 {index}/{len(pending)}：{entry.get('title') or entry['cid']}")
        try:
            plain = article_plain_text(entry["cid"], seconds)
        except Exception as exc:
            errors.append(f"正文抓取失败 {entry['cid']}：{exc}")
            continue
        if plain and str(plain).strip():
            entry["content"] = re.sub(r"\s+", " ", str(plain)).strip()
            entry["content_fetched_at"] = stamp
        else:
            errors.append(f"正文为空 {entry['cid']}")

    state["news_cache_version"] = 1
    state["news_items"] = items
    state["news_cache_total"] = len(items)
    state["news_cache_updated_at"] = stamp
    state["news_cache_last_result"] = f"new={new_count}, total={len(items)}, errors={len(errors)}"
    status = "baseline" if first_init else "ok"
    try:
        _save_state(state_path, state)
    except Exception as exc:
        errors.append(f"状态写入失败：{exc}")
        status = "error"
    _report_progress(progress, total_steps, total_steps, f"官网公告缓存已更新：共 {len(items)} 条")
    return {
        "status": status,
        "new_count": new_count,
        "total": len(items),
        "errors": errors,
        "items": items,
        "fetched_count": len(rows),
        "cache_file": str(state_path),
        "updated_at": stamp,
    }


def load_news_cache(base_dir: Path | str, cfg: dict | None = None, *, limit: int | None = None) -> dict:
    """Read the local news centre cache without any network access."""
    cfg = cfg if isinstance(cfg, dict) else {}
    cache_limit, initial_limit = _cache_limits(cfg)
    if isinstance(limit, int) and limit > 0:
        cache_limit = min(cache_limit, limit)
        initial_limit = min(initial_limit, cache_limit)
    state = _load_state(news_state_path(base_dir))
    items, _new_count, _first_init = _merge_news_items(
        _load_cache_items(state), [], limit=cache_limit, initial_limit=initial_limit
    )
    return {
        "total": len(items),
        "items": items,
        "updated_at": state.get("news_cache_updated_at"),
        "last_result": state.get("news_cache_last_result"),
        "cache_file": str(news_state_path(base_dir)),
    }


def poll(base_dir: Path, cfg: dict, *, force: bool = False, now: datetime | None = None) -> dict:
    """Poll once and return a structured result for logging/notification."""
    now = now or datetime.now()
    state_path = base_dir / "state" / "news_monitor.json"
    state = _load_state(state_path)
    slot = current_due_slot(now, cfg.get("news_check_times", ["00:05", "12:05"]))
    if not force and (not slot or state.get("last_slot") == slot):
        return {"status": "not_due", "slot": slot, "events": []}

    rows = fetch_announcements(float(cfg.get("news_timeout_seconds", 15)))
    current_ids = [str(row["cid"]) for row in rows]
    seen = {str(value) for value in state.get("seen_ids", [])}
    if not state.get("initialized"):
        state.update({
            "initialized": True,
            "last_slot": slot or now.isoformat(timespec="minutes"),
            "last_checked_at": now.isoformat(timespec="seconds"),
            "seen_ids": current_ids[:100],
            "last_result": "baseline",
        })
        _merge_cache_into_state(state, cfg, rows, now)
        _save_state(state_path, state)
        return {"status": "baseline", "slot": slot, "events": [], "count": len(current_ids)}

    new_rows = [row for row in rows if str(row["cid"]) not in seen]
    new_rows.sort(key=lambda row: float(row.get("displayTime") or 0))
    keywords = [str(x) for x in cfg.get("news_keywords", ["闪断更新", "停机维护"])]
    events = []
    for row in new_rows:
        if not maintenance_related(row, keywords):
            continue
        cid = str(row["cid"])
        plain = article_plain_text(cid, float(cfg.get("news_timeout_seconds", 15)))
        window = parse_maintenance_window(plain or str(row.get("brief", "")), row.get("displayTime"))
        changes: list[str] = []
        if window and window[1] >= now:
            changes = apply_supervision_overrides(base_dir, cfg, window[0], window[1], cid)
        events.append({
            "cid": cid,
            "title": str(row.get("title", "官网维护公告")),
            "brief": re.sub(r"\s+", " ", str(row.get("brief", ""))).strip()[:360],
            "url": ARTICLE_URL.format(cid=cid),
            "start": window[0].isoformat(timespec="minutes") if window else None,
            "end": window[1].isoformat(timespec="minutes") if window else None,
            "changes": changes,
        })

    merged = list(dict.fromkeys(current_ids + list(seen)))[:100]
    state.update({
        "initialized": True,
        "last_slot": slot or now.isoformat(timespec="minutes"),
        "last_checked_at": now.isoformat(timespec="seconds"),
        "seen_ids": merged,
        "last_result": f"new={len(new_rows)}, relevant={len(events)}",
        "latest_events": events[-10:],
    })
    _merge_cache_into_state(state, cfg, rows, now)
    _save_state(state_path, state)
    return {"status": "ok", "slot": slot, "new_count": len(new_rows), "events": events}
