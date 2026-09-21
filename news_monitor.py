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
    for page in (1, 2):
        payload = json.loads(_request_text(NEWS_API.format(page=page), timeout))
        if payload.get("code") != 0 or not isinstance(payload.get("data"), dict):
            raise RuntimeError(f"官网新闻接口返回异常：{payload.get('msg') or payload.get('code')}")
        rows.extend(payload["data"].get("list", []))
        if payload["data"].get("end"):
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
        _save_state(state_path, {
            "initialized": True,
            "last_slot": slot or now.isoformat(timespec="minutes"),
            "last_checked_at": now.isoformat(timespec="seconds"),
            "seen_ids": current_ids[:100],
            "last_result": "baseline",
        })
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
    _save_state(state_path, {
        "initialized": True,
        "last_slot": slot or now.isoformat(timespec="minutes"),
        "last_checked_at": now.isoformat(timespec="seconds"),
        "seen_ids": merged,
        "last_result": f"new={len(new_rows)}, relevant={len(events)}",
        "latest_events": events[-10:],
    })
    return {"status": "ok", "slot": slot, "new_count": len(new_rows), "events": events}
