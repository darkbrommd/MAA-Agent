import ctypes
import hashlib
import json
import os
import re
import shutil
import statistics
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

from agent_runtime import (
    AgentMutex,
    agent_process_info,
    clear_runtime_files,
    find_running_agents,
    script_from_cmdline,
    write_runtime_record,
)
from schedule_utils import effective_occurrences, load_overrides, occurrence_key, read_maa_timer_schedule
from diagnostic_commands import consume as consume_diagnostic_commands, save_result as save_diagnostic_result
from news_monitor import poll as poll_news
from resource_history import sample as sample_resource_history

import psutil
import requests

try:
    import win32gui
    import win32process
except Exception:
    win32gui = None
    win32process = None

try:
    from openai import OpenAI
except Exception:
    OpenAI = None


BASE_DIR = Path(__file__).resolve().parents[1]
LOG_DIR = BASE_DIR / "logs"
ARCHIVE_DIR = LOG_DIR / "archive"
STATE_DIR = BASE_DIR / "state"
DISABLED_DIR = BASE_DIR / "disabled_alerts"

LOG_DIR.mkdir(exist_ok=True)
ARCHIVE_DIR.mkdir(exist_ok=True)
STATE_DIR.mkdir(exist_ok=True)
DISABLED_DIR.mkdir(exist_ok=True)

CONFIG_PATH = BASE_DIR / "config.json"
PID_FILE = STATE_DIR / "agent.pid"
STOP_FILE = STATE_DIR / "stop.request"
BARK_QUEUE_FILE = STATE_DIR / "bark_queue.jsonl"
DAILY_REPORT_SKIP_FILE = STATE_DIR / "daily_report_skip_date.txt"
DAILY_REPORT_SENT_FILE = STATE_DIR / "daily_report_sent_date.txt"
RESOURCE_PRESSURE_FILE = STATE_DIR / "resource_pressure.json"
PROCESS_HEALTH_FILE = STATE_DIR / "process_health.json"
DEBUG_OVERRIDE_FILE = STATE_DIR / "debug_overrides.json"
HEARTBEAT_STATE_FILE = STATE_DIR / "heartbeat_state.json"
NETWORK_HEALTH_FILE = STATE_DIR / "network_health.json"
HUNG_STATE_FILE = STATE_DIR / "hung_windows.json"
SCHEDULE_CACHE_FILE = STATE_DIR / "schedule_cache.json"
SCHEDULE_RUN_STATE_FILE = STATE_DIR / "schedule_run_status.json"
GENERIC_ISSUE_DIR = STATE_DIR / "issue_tracker"
GENERIC_ISSUE_DIR.mkdir(exist_ok=True)


CHAIN_NAME_MAP = {
    "StartUp": "启动",
    "Recruit": "公招",
    "Infrast": "基建",
    "Mall": "信用商店",
    "Fight": "自动清理智",
    "OperBox": "干员识别",
    "Depot": "仓库识别",
    "Award": "领取奖励",
    "CloseDown": "关闭游戏",
    "Copilot": "自动战斗作业",
    "Roguelike": "肉鸽",
    "Reclamation": "生息演算",
}


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_str():
    return datetime.now().strftime("%Y-%m-%d")


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


cfg = load_config()


def log(msg):
    line = f"[{now_str()}] {msg}"
    print(line)
    with open(LOG_DIR / "agent.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def write_jsonl(path, obj):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def decision_log(obj):
    obj = dict(obj)
    obj.setdefault("time", now_str())
    write_jsonl(LOG_DIR / "decision.log", obj)


def archive_file(path, archive_name_prefix):
    if not path.exists():
        return False

    if path.stat().st_size <= 0:
        return False

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = ARCHIVE_DIR / f"{archive_name_prefix}_{ts}.log"

    try:
        shutil.move(str(path), str(target))
        return True
    except Exception as e:
        log(f"Archive failed for {path}: {e}")
        return False


def archive_own_logs_if_needed(force=False):
    state_path = STATE_DIR / "log_archive_date.txt"
    current = today_str()

    last = None
    if state_path.exists():
        try:
            last = state_path.read_text(encoding="utf-8").strip()
        except Exception:
            last = None

    if not last:
        state_path.write_text(current, encoding="utf-8")
        return

    size_limit_mb = float(cfg.get("log_archive_max_mb", 10))
    agent_log = LOG_DIR / "agent.log"
    decision = LOG_DIR / "decision.log"

    size_too_large = False
    for p in [agent_log, decision]:
        try:
            if p.exists() and p.stat().st_size > size_limit_mb * 1024 * 1024:
                size_too_large = True
        except Exception:
            pass

    if force or last != current or size_too_large:
        archived = []
        if archive_file(agent_log, f"agent_{last}"):
            archived.append("agent.log")
        if archive_file(decision, f"decision_{last}"):
            archived.append("decision.log")

        state_path.write_text(current, encoding="utf-8")

        if archived:
            log(f"Archived logs: {', '.join(archived)}")


def alert_disabled(name):
    return (DISABLED_DIR / f"{name}.off").exists()


def recently_alerted(name):
    cooldown = int(cfg.get("alert_cooldown_minutes", 60))
    path = STATE_DIR / f"last_alert_{name}.txt"

    if not path.exists():
        return False

    try:
        last = float(path.read_text(encoding="utf-8").strip())
        return time.time() - last < cooldown * 60
    except Exception:
        return False


def mark_alerted(name):
    path = STATE_DIR / f"last_alert_{name}.txt"
    path.write_text(str(time.time()), encoding="utf-8")


def trim_utf8_bytes(text, max_bytes):
    if text is None:
        return ""

    text = str(text)

    if len(text.encode("utf-8")) <= max_bytes:
        return text

    result = []
    used = 0

    for ch in text:
        b = ch.encode("utf-8")
        if used + len(b) > max_bytes:
            break
        result.append(ch)
        used += len(b)

    return "".join(result)


def bark_payload(title, body, level="active"):
    title = trim_utf8_bytes(title, int(cfg.get("bark_title_max_bytes", 120)))

    return {
        "device_key": cfg["bark_key"],
        "title": title,
        "body": body or "",
        "group": "MAA-Agent",
        "level": level
    }


def payload_size_bytes(payload):
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def is_payload_too_large_error(error_text):
    if not error_text:
        return False

    lower = str(error_text).lower()
    return "payloadtoolarge" in lower or "payload too large" in lower or "too large" in lower


def split_body_for_bark(title, body, level):
    max_payload_bytes = int(cfg.get("bark_max_payload_bytes", 3400))
    max_chunks = int(cfg.get("bark_max_chunks", 8))

    body = body or ""
    title = trim_utf8_bytes(title, int(cfg.get("bark_title_max_bytes", 120)))

    test_payload = bark_payload(title, body, level)
    if payload_size_bytes(test_payload) <= max_payload_bytes:
        return [body]

    tentative_title = trim_utf8_bytes(f"{title} (999/999)", int(cfg.get("bark_title_max_bytes", 120)))
    tentative_prefix = "【第 999/999 段】\n"

    def fits(text):
        p = bark_payload(tentative_title, tentative_prefix + text, level)
        return payload_size_bytes(p) <= max_payload_bytes

    chunks = []
    current = ""

    lines = body.splitlines(keepends=True)
    if not lines:
        lines = [body]

    for line in lines:
        candidate = current + line

        if fits(candidate):
            current = candidate
            continue

        if current:
            chunks.append(current)
            current = ""

        if fits(line):
            current = line
            continue

        piece = ""
        for ch in line:
            candidate_piece = piece + ch
            if fits(candidate_piece):
                piece = candidate_piece
            else:
                if piece:
                    chunks.append(piece)
                piece = ch

        current = piece

    if current:
        chunks.append(current)

    if len(chunks) > max_chunks:
        original_count = len(chunks)
        chunks = chunks[:max_chunks - 1]
        chunks.append(
            f"……\n内容过长，已截断。\n原始共 {original_count} 段，当前只发送前 {max_chunks} 段。\n完整信息请看 {LOG_DIR}。"
        )

    return chunks


def split_payload_if_needed(payload):
    title = payload.get("title", "")
    body = payload.get("body", "")
    level = payload.get("level", "active")

    chunks = split_body_for_bark(title, body, level)

    if len(chunks) == 1:
        p = dict(payload)
        p["title"] = trim_utf8_bytes(p.get("title", ""), int(cfg.get("bark_title_max_bytes", 120)))
        p["body"] = chunks[0]
        return [p]

    result = []
    total = len(chunks)

    for i, chunk in enumerate(chunks, start=1):
        part_title = trim_utf8_bytes(f"{title} ({i}/{total})", int(cfg.get("bark_title_max_bytes", 120)))
        part_body = f"【第 {i}/{total} 段】\n{chunk}"

        p = dict(payload)
        p["title"] = part_title
        p["body"] = part_body

        result.append(p)

    return result


def post_bark_once(payload, attempts):
    url = cfg["bark_server"].rstrip("/") + "/push"
    sleep_seconds = float(cfg.get("bark_retry_sleep_seconds", 3))
    last_error = None

    for i in range(max(1, attempts)):
        try:
            r = requests.post(url, json=payload, timeout=12)

            if 200 <= r.status_code < 300:
                return True, False, f"HTTP {r.status_code}"

            last_error = f"HTTP {r.status_code}: {r.text[:500]}"

            if is_payload_too_large_error(last_error):
                return False, True, last_error

            if 400 <= r.status_code < 500:
                return False, True, last_error

        except Exception as e:
            last_error = str(e)

        if i < attempts - 1:
            time.sleep(sleep_seconds)

    return False, False, last_error


def send_bark_payload(payload, attempts=None):
    if not cfg.get("enable_bark", True):
        return True

    if attempts is None:
        attempts = int(cfg.get("bark_retry_attempts", 3))

    payloads = split_payload_if_needed(payload)
    all_ok = True

    for part in payloads:
        ok, permanent, err = post_bark_once(part, attempts)

        if ok:
            log(f"Bark sent: {payload_size_bytes(part)} bytes {part.get('title', '')}")
            update_daily_stat("bark_sent", 1)
            continue

        if permanent:
            log(f"Bark permanent failed, not queued: {err} | title={part.get('title', '')} | bytes={payload_size_bytes(part)}")
            update_daily_stat("bark_failed", 1)
            add_daily_event(
                "bark",
                f"Bark 永久失败，已丢弃，不入队。title={part.get('title', '')} err={err}",
                severity="warning"
            )

            if is_payload_too_large_error(err):
                fallback = bark_payload(
                    "Bark消息过大未发送",
                    f"一条 Agent 通知因内容过长未能发送。\n标题：{part.get('title', '')}\n时间：{now_str()}\n完整内容请看 {LOG_DIR}。",
                    "active"
                )
                post_bark_once(fallback, 1)

            continue

        log(f"Bark send failed after {attempts} attempt(s): {err}")
        update_daily_stat("bark_failed", 1)
        all_ok = False

    return all_ok


def queue_bark_payload(payload):
    try:
        payloads = split_payload_if_needed(payload)
        queued = 0

        with open(BARK_QUEUE_FILE, "a", encoding="utf-8") as f:
            for p in payloads:
                size = payload_size_bytes(p)
                max_payload_bytes = int(cfg.get("bark_max_payload_bytes", 3400))

                if size > max_payload_bytes:
                    log(f"Bark payload still too large after split, dropped: {size} bytes {p.get('title', '')}")
                    add_daily_event(
                        "bark",
                        f"Bark 消息拆分后仍过大，已丢弃：{p.get('title', '')} {size} bytes",
                        severity="warning"
                    )
                    continue

                item = {
                    "queued_at": now_str(),
                    "payload": p
                }
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
                queued += 1

        if queued:
            log(f"Bark queued {queued} item(s): {payload.get('title', '')}")
            update_daily_stat("bark_queued", queued)

    except Exception as e:
        log(f"Failed to queue Bark payload: {e}")


def parse_time_str(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def process_bark_queue():
    if not BARK_QUEUE_FILE.exists():
        return

    try:
        lines = BARK_QUEUE_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception as e:
        log(f"Failed to read Bark queue: {e}")
        return

    if not lines:
        try:
            BARK_QUEUE_FILE.unlink()
        except Exception:
            pass
        return

    max_items = int(cfg.get("bark_queue_max_items", 100))
    ttl_hours = float(cfg.get("bark_queue_ttl_hours", 6))
    lines = lines[-max_items:]

    remaining = []
    sent_count = 0
    dropped_count = 0
    retry_per_loop = max(1, int(cfg.get("bark_queue_retry_per_loop", 3)))
    attempt_lines = lines[:retry_per_loop]
    # Keep the rest untouched so a Bark outage cannot monopolize the watchdog
    # main loop for many minutes.
    untouched_lines = lines[retry_per_loop:]

    for line in attempt_lines:
        try:
            item = json.loads(line)
            payload = item.get("payload")
            queued_at = item.get("queued_at")

            if not payload:
                continue

            queued_time = parse_time_str(queued_at) if queued_at else None
            if queued_time and not cfg.get("bark_retry_infinite", False) and datetime.now() - queued_time > timedelta(hours=ttl_hours):
                dropped_count += 1
                log(f"Dropped stale Bark queue item: queued_at={queued_at} title={payload.get('title', '')}")
                continue

            ok = send_bark_payload(payload, attempts=1)
            if ok:
                sent_count += 1
            else:
                remaining.append(line)

        except Exception:
            remaining.append(line)

    remaining.extend(untouched_lines)

    try:
        if remaining:
            BARK_QUEUE_FILE.write_text("\n".join(remaining) + "\n", encoding="utf-8")
        else:
            BARK_QUEUE_FILE.unlink()
    except Exception as e:
        log(f"Failed to update Bark queue: {e}")

    if sent_count:
        log(f"Bark queue resent {sent_count} item(s).")
        add_daily_event("bark", f"Bark 队列补发成功 {sent_count} 条。", severity="info")

    if dropped_count:
        log(f"Bark queue dropped {dropped_count} stale item(s).")
        add_daily_event("bark", f"Bark 队列丢弃过期消息 {dropped_count} 条。", severity="info")

    if len(remaining) >= 5:
        # Do not Bark about a Bark outage: that only creates another queued
        # message when the channel is already unavailable. Keep it local/daily.
        log(f"Bark queue backlog: {len(remaining)} item(s) remain.")
        add_special_notice(f"Bark 通知仍有 {len(remaining)} 条等待补发。")


def bark_force(title, body, level="active"):
    payload = bark_payload(title, body, level)
    ok = send_bark_payload(payload)
    if not ok:
        queue_bark_payload(payload)


def bark(title, body, level="active", alert_name="general"):
    if not cfg.get("enable_bark", True):
        return

    if alert_disabled(alert_name):
        log(f"Alert disabled: {alert_name}")
        return

    if recently_alerted(alert_name):
        log(f"Alert cooldown skipped: {alert_name}")
        return

    payload = bark_payload(title, body, level)
    ok = send_bark_payload(payload)

    if not ok:
        queue_bark_payload(payload)

    mark_alerted(alert_name)


def daily_stats_path():
    return STATE_DIR / f"daily_{today_str()}.json"


def default_daily_stats():
    return {
        "date": today_str(), "started_at": now_str(),
        "bark_sent": 0, "bark_failed": 0, "bark_queued": 0, "deepseek_calls": 0,
        "alerts": [], "events": [], "special_notices": [], "hard_errors": [],
        "incidents": [], "recoverable_issues": [], "taskchains": {},
        "observed_runs": {"MAA1": [], "MAA2": []},
        "schedule": {"unverified": [], "sources": {}, "skipped_overrides": [], "expected_occurrences": []},
        "memory": {
            "system_total_mb": None, "system_used_min_mb": None, "system_used_max_mb": None,
            "system_used_current_mb": None, "system_available_min_mb": None,
            "system_available_current_mb": None, "system_percent_min": None,
            "system_percent_max": None, "system_percent_current": None,
            "percent_samples": [],
            "pressure_minutes": 0.0, "critical_minutes": 0.0,
            "expected_external_pressure_minutes": 0.0,
            "maa_min_mb": None, "maa_max_mb": None, "maa_current_mb": None,
            "mumu_min_mb": None, "mumu_max_mb": None, "mumu_current_mb": None
        },
        "cpu": {"current_percent": None, "min_percent": None, "max_percent": None,
                "sum_percent": 0.0, "samples": 0, "percent_samples": [], "high_minutes": 0.0},
        "disks": {},
        "resource_context": {"expected_heavy_processes": [], "top_memory_processes": []},
        "process": {"maa_count_current": 0, "mumu_count_current": 0, "maa_instances": {}},
        "hung_windows": []
    }

def load_daily_stats():
    path = daily_stats_path()
    if not path.exists():
        data = default_daily_stats(); save_daily_stats(data); return data
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        base = default_daily_stats(); base.update(data)
        defaults = default_daily_stats()
        for key in ["memory", "cpu", "resource_context", "process", "schedule"]:
            merged = dict(defaults.get(key, {})); merged.update(base.get(key, {}) or {}); base[key] = merged
        base.setdefault("disks", {}); base.setdefault("incidents", []); base.setdefault("recoverable_issues", [])
        return base
    except Exception:
        data = default_daily_stats(); save_daily_stats(data); return data

def save_daily_stats(data):
    daily_stats_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def update_daily_stat(key, amount):
    data = load_daily_stats()
    data[key] = int(data.get(key, 0)) + amount
    save_daily_stats(data)


def add_daily_event(kind, message, severity="info", label=None):
    data = load_daily_stats()
    item = {
        "time": now_str(),
        "kind": kind,
        "severity": severity,
        "message": message
    }
    if label:
        item["label"] = label

    data.setdefault("events", []).append(item)
    data["events"] = data["events"][-200:]
    save_daily_stats(data)


def add_daily_alert(message, severity="warning", label=None):
    data = load_daily_stats()
    item = {
        "time": now_str(),
        "severity": severity,
        "message": message
    }
    if label:
        item["label"] = label

    data.setdefault("alerts", []).append(item)
    data["alerts"] = data["alerts"][-100:]
    save_daily_stats(data)


def add_special_notice(message, label=None):
    data = load_daily_stats()
    item = {
        "time": now_str(),
        "message": message
    }
    if label:
        item["label"] = label

    notices = data.setdefault("special_notices", [])
    if not any(n.get("message") == message and n.get("label") == label for n in notices[-20:]):
        notices.append(item)
        data["special_notices"] = notices[-100:]
        save_daily_stats(data)


def incident_key(category, label=None, task=None, key=None):
    return key or f"{category}|{label or ''}|{task or ''}"


def add_incident(category, summary, label=None, task=None, likely=None, action=None, key=None):
    data = load_daily_stats()
    ikey = incident_key(category, label, task, key)
    incidents = data.setdefault("incidents", [])
    current = next((x for x in reversed(incidents) if x.get("key") == ikey and x.get("status", "open") == "open"), None)
    if current:
        current.update({"last_seen": now_str(), "summary": summary, "likely": likely, "action": action})
        current["occurrences"] = int(current.get("occurrences", 1)) + 1
    else:
        item = {
            "key": ikey, "time": now_str(), "opened_at": now_str(), "last_seen": now_str(),
            "status": "open", "occurrences": 1, "category": category, "summary": summary,
            "task": task, "likely": likely, "action": action
        }
        if label:
            item["label"] = label
        incidents.append(item)
    data["incidents"] = incidents[-120:]
    save_daily_stats(data)
    return ikey


def resolve_incident(category=None, label=None, task=None, key=None, resolution="已恢复"):
    data = load_daily_stats()
    ikey = incident_key(category, label, task, key) if key or category else None
    changed = False
    for item in reversed(data.setdefault("incidents", [])):
        if item.get("status", "open") != "open":
            continue
        if ikey is not None and item.get("key") != ikey:
            continue
        item["status"] = "resolved"
        item["resolved_at"] = now_str()
        item["resolution"] = resolution
        changed = True
        if ikey is not None:
            break
    if changed:
        save_daily_stats(data)
    return changed


def open_incidents(data=None):
    data = data or load_daily_stats()
    return [x for x in data.get("incidents", []) if x.get("status", "open") == "open"]


def incident_is_open(key,data=None):
    return any(x.get("key")==key for x in open_incidents(data))

def add_recoverable_issue(category, summary, label=None, task=None):
    data = load_daily_stats(); rows=data.setdefault("recoverable_issues", [])
    key=f"{category}|{label or ''}|{task or ''}"
    current=next((x for x in reversed(rows) if x.get("key")==key),None)
    if current:
        current["occurrences"]=int(current.get("occurrences",1))+1
        current["last_seen"]=now_str(); current["summary"]=summary
    else:
        item={"key":key,"time":now_str(),"last_seen":now_str(),"occurrences":1,"category":category,"summary":summary,"task":task}
        if label: item["label"]=label
        rows.append(item)
    data["recoverable_issues"]=rows[-200:]; save_daily_stats(data)

def recoverable_issue_total(data=None):
    data=data or load_daily_stats()
    return sum(max(1,int(x.get("occurrences",1) or 1)) for x in data.get("recoverable_issues",[]))

def update_taskchain_count(label, chain, event_name):
    if not chain:
        return

    data = load_daily_stats()
    taskchains = data.setdefault("taskchains", {})
    label_data = taskchains.setdefault(label, {})
    chain_data = label_data.setdefault(chain, {})
    chain_data[event_name] = int(chain_data.get(event_name, 0)) + 1
    save_daily_stats(data)


def process_memory_snapshot(limit=6):
    rows = []
    for proc in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            info = proc.info; mi = info.get("memory_info"); rss = mi.rss / 1024 / 1024 if mi else 0
            if rss > 0: rows.append((rss, info.get("name") or f"PID {info.get('pid')}"))
        except (psutil.Error, AttributeError): pass
    rows.sort(reverse=True)
    return [{"name": name, "mb": round(rss, 1)} for rss, name in rows[:limit]]


def expected_heavy_processes_running():
    configured = {x.lower() for x in cfg.get("expected_heavy_process_names", [])}; found = []
    for proc in psutil.process_iter(["name"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if name in configured and name not in found: found.append(name)
        except psutil.Error: pass
    return found


def update_memory_stats(maa_mem, mumu_mem, maa_count, mumu_count):
    data = load_daily_stats(); mem = data.setdefault("memory", {})
    interval_minutes = float(cfg.get("check_interval_seconds", 60)) / 60.0
    def upd_mb(prefix, value):
        value = round(float(value), 1); mem[f"{prefix}_current_mb"] = value
        if mem.get(f"{prefix}_min_mb") is None or value < mem[f"{prefix}_min_mb"]: mem[f"{prefix}_min_mb"] = value
        if mem.get(f"{prefix}_max_mb") is None or value > mem[f"{prefix}_max_mb"]: mem[f"{prefix}_max_mb"] = value
    vm = psutil.virtual_memory(); total_mb = vm.total/1048576; available_mb = vm.available/1048576; used_mb = total_mb-available_mb; percent=float(vm.percent)
    mem["system_total_mb"] = round(total_mb,1); mem["system_used_current_mb"] = round(used_mb,1); mem["system_available_current_mb"] = round(available_mb,1); mem["system_percent_current"] = round(percent,1)
    mem_samples = mem.setdefault("percent_samples", []); mem_samples.append(round(percent,1)); mem["percent_samples"] = mem_samples[-1440:]
    for k,v in [("system_used_min_mb",used_mb),("system_available_min_mb",available_mb),("system_percent_min",percent)]:
        if mem.get(k) is None or v < mem[k]: mem[k] = round(v,1)
    for k,v in [("system_used_max_mb",used_mb),("system_percent_max",percent)]:
        if mem.get(k) is None or v > mem[k]: mem[k] = round(v,1)
    if percent >= float(cfg.get("memory_pressure_percent",88)): mem["pressure_minutes"] = round(float(mem.get("pressure_minutes",0))+interval_minutes,1)
    if percent >= float(cfg.get("memory_critical_percent",95)): mem["critical_minutes"] = round(float(mem.get("critical_minutes",0))+interval_minutes,1)
    upd_mb("maa",maa_mem); upd_mb("mumu",mumu_mem)
    cpu=data.setdefault("cpu",{}); cpu_now=float(psutil.cpu_percent(interval=None)); cpu["current_percent"]=round(cpu_now,1)
    if cpu.get("min_percent") is None or cpu_now < cpu["min_percent"]: cpu["min_percent"]=round(cpu_now,1)
    if cpu.get("max_percent") is None or cpu_now > cpu["max_percent"]: cpu["max_percent"]=round(cpu_now,1)
    cpu["sum_percent"]=round(float(cpu.get("sum_percent",0))+cpu_now,2); cpu["samples"]=int(cpu.get("samples",0))+1
    cpu_samples=cpu.setdefault("percent_samples",[]); cpu_samples.append(round(cpu_now,1)); cpu["percent_samples"]=cpu_samples[-1440:]
    if cpu_now >= float(cfg.get("cpu_high_percent",92)): cpu["high_minutes"]=round(float(cpu.get("high_minutes",0))+interval_minutes,1)
    disks=data.setdefault("disks",{})
    for drive in cfg.get("monitor_disk_paths", ["C:\\", "D:\\"]):
        try:
            du=psutil.disk_usage(drive); free_gb=du.free/1073741824; rec=disks.setdefault(drive,{})
            rec["free_current_gb"]=round(free_gb,1); rec["total_gb"]=round(du.total/1073741824,1); rec["used_percent_current"]=round(float(du.percent),1)
            if rec.get("free_min_gb") is None or free_gb < rec["free_min_gb"]: rec["free_min_gb"]=round(free_gb,1)
        except Exception: pass
    context=data.setdefault("resource_context",{}); context["expected_heavy_processes"]=expected_heavy_processes_running(); context["top_memory_processes"]=process_memory_snapshot(6)
    proc_data=data.setdefault("process",{}); proc_data["maa_count_current"]=maa_count; proc_data["mumu_count_current"]=mumu_count; save_daily_stats(data)

def _observed_occurrence_text(item):
    if isinstance(item, dict):
        return item.get("occurrence")
    if isinstance(item, str):
        # Legacy form was HH:MM@detected. It cannot prove the date reliably.
        return None
    return None


def is_observed_occurrence(label, scheduled_dt, data=None):
    data = data or load_daily_stats()
    target = scheduled_dt.strftime("%Y-%m-%d %H:%M")
    candidates = [data]
    target_date = scheduled_dt.strftime("%Y-%m-%d")
    if str(data.get("date", "")) != target_date:
        try:
            historical_path = STATE_DIR / f"daily_{target_date}.json"
            if historical_path.exists():
                candidates.append(json.loads(historical_path.read_text(encoding="utf-8")))
        except Exception:
            pass
    for candidate in candidates:
        for item in candidate.get("observed_runs", {}).get(label, []):
            if _observed_occurrence_text(item) == target:
                return True
    return False


def mark_observed_run(label, scheduled_dt, actual_dt=None, chain=None, source="callback"):
    actual_dt = actual_dt or datetime.now()
    data = load_daily_stats()
    observed = data.setdefault("observed_runs", {}).setdefault(label, [])
    occurrence = scheduled_dt.strftime("%Y-%m-%d %H:%M")
    if any(_observed_occurrence_text(x) == occurrence for x in observed):
        return False
    item = {
        "occurrence": occurrence,
        "actual_time": actual_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "detected_at": now_str(),
        "delay_minutes": round((actual_dt - scheduled_dt).total_seconds()/60, 1),
        "chain": chain,
        "source": source,
    }
    observed.append(item)
    data["observed_runs"][label] = observed[-40:]
    # A positive TaskChainStart supersedes an earlier "Agent did not cover this
    # time" bookkeeping entry. Keep the daily summary internally consistent.
    unverified=data.setdefault("schedule",{}).setdefault("unverified",[])
    data["schedule"]["unverified"]=[x for x in unverified if str(x) not in {f"{label} {occurrence}", f"{label}|{occurrence}"}]
    save_daily_stats(data)
    resolve_incident(key=f"schedule|{label}|{occurrence}", resolution="后续检测到计划启动")
    return True


def refresh_schedule_cache():
    try:
        schedule,meta=read_maa_timer_schedule(cfg)
        SCHEDULE_CACHE_FILE.write_text(json.dumps({"saved_at":now_str(),"schedule":schedule,"meta":meta},ensure_ascii=False,indent=2),encoding="utf-8")
    except Exception as e:
        log(f"Schedule cache refresh failed: {e}")


def load_schedule_cache():
    try:
        if SCHEDULE_CACHE_FILE.exists():
            data=json.loads(SCHEDULE_CACHE_FILE.read_text(encoding="utf-8"))
            saved=datetime.strptime(data.get("saved_at"),"%Y-%m-%d %H:%M:%S")
            if datetime.now()-saved <= timedelta(minutes=float(cfg.get("schedule_cache_max_age_minutes",180))):
                return data
    except Exception:
        pass
    return None


def match_schedule_start(label, actual_dt, chain=None, source="callback"):
    before = float(cfg.get("scheduled_match_before_minutes", 2))
    # Any start that happens before the watchdog's miss-grace expires should be
    # eligible to satisfy the occurrence; otherwise +15 min can be "on time
    # enough not to alert yet" but simultaneously impossible to match.
    after = max(float(cfg.get("scheduled_match_after_minutes", 10)),
                float(cfg.get("scheduled_run_grace_minutes", 20)))
    start = actual_dt - timedelta(minutes=after)
    end = actual_dt + timedelta(minutes=before)
    all_rows,_=effective_occurrences(BASE_DIR,cfg,start,end,include_skipped=True)
    # An explicit one-off skip means "do not judge this occurrence". If MAA
    # nevertheless runs because the user did not disable its own timer, do not
    # turn that into a monitored expected run.
    skipped_near=[x for x in all_rows if x.get("label")==label and x.get("skipped") and abs((actual_dt-x["original_datetime"]).total_seconds()) <= max(before,after)*60]
    if skipped_near:
        return None
    candidates = [x for x in all_rows if x.get("label") == label and not x.get("skipped")]
    if not candidates:
        # One-time MAA timers may switch IsEnabled null -> false immediately
        # after starting, disappearing from the live config before Agent reads
        # the log. Fall back to the recent pre-run timer snapshot.
        cache=load_schedule_cache() or {}; timers=(cache.get("schedule") or {}).get(label,[])
        cached=[]
        for timer in timers:
            try:
                h,m=[int(x) for x in str(timer.get("time")).split(":")]
            except Exception:
                continue
            for day_delta in (-1,0,1):
                base=(actual_dt+timedelta(days=day_delta)).replace(hour=h,minute=m,second=0,microsecond=0)
                delta=(actual_dt-base).total_seconds()/60
                if -before <= delta <= after:
                    cached.append({"label":label,"datetime":base,"source":"schedule_cache"})
        candidates=cached
    if not candidates:
        return None
    candidates.sort(key=lambda x: abs((actual_dt - x["datetime"]).total_seconds()))
    row = candidates[0]
    delta = (actual_dt - row["datetime"]).total_seconds()/60
    if -before <= delta <= after:
        mark_observed_run(label, row["datetime"], actual_dt=actual_dt, chain=chain, source=source if row.get("source")!="schedule_cache" else "schedule_cache+callback")
        return row
    return None


def schedule_run_key(label, scheduled_dt):
    occurrence = scheduled_dt if isinstance(scheduled_dt, str) else scheduled_dt.strftime("%Y-%m-%d %H:%M")
    return f"{label}|{occurrence}"


def load_schedule_run_state():
    try:
        if SCHEDULE_RUN_STATE_FILE.exists():
            data=json.loads(SCHEDULE_RUN_STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(data,dict) and isinstance(data.get("runs"),dict):
                return data
    except Exception:
        pass
    return {"runs":{}}


def save_schedule_run_state(data):
    runs=data.setdefault("runs",{})
    cutoff=datetime.now()-timedelta(days=45)
    for key,rec in list(runs.items()):
        try:
            occurrence=datetime.strptime(str(rec.get("occurrence")),"%Y-%m-%d %H:%M")
        except Exception:
            occurrence=None
        if occurrence is None or occurrence < cutoff:
            runs.pop(key,None)
    if len(runs)>240:
        keep=sorted(runs,key=lambda key:str(runs[key].get("occurrence","")))[-240:]
        data["runs"]={key:runs[key] for key in keep}
    temp=SCHEDULE_RUN_STATE_FILE.with_suffix(".json.tmp")
    temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    temp.replace(SCHEDULE_RUN_STATE_FILE)


def _active_schedule_run(data,label):
    rows=[rec for rec in data.get("runs",{}).values()
          if rec.get("label")==label and rec.get("status")=="running"]
    return max(rows,key=lambda rec:str(rec.get("occurrence",""))) if rows else None


def mark_schedule_run_started(label,row,event_dt,chain=None,payload=None):
    scheduled_dt=row.get("datetime")
    if not isinstance(scheduled_dt,datetime):
        return None
    data=load_schedule_run_state(); runs=data.setdefault("runs",{}); key=schedule_run_key(label,scheduled_dt)
    rec=runs.get(key)
    # Later task chains and the separate CloseDown queue can still match the
    # same clock occurrence. Never reopen a run whose queue already ended.
    if rec and rec.get("status") in {"completed","failed"}:
        return rec
    payload=payload if isinstance(payload,dict) else {}
    rec=rec or {
        "key":key,"label":label,"occurrence":scheduled_dt.strftime("%Y-%m-%d %H:%M"),
        "scheduled_at":scheduled_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "started_at":event_dt.strftime("%Y-%m-%d %H:%M:%S"),"status":"running",
        "taskids":[],
    }
    rec["status"]="running"; rec["updated_at"]=event_dt.strftime("%Y-%m-%d %H:%M:%S")
    rec["current_chain"]=chain; rec["current_task"]=readable_chain_name(chain) if chain else "当前任务"
    taskid=payload.get("taskid")
    if taskid is not None and taskid not in rec.setdefault("taskids",[]):
        rec["taskids"].append(taskid)
    if payload.get("uuid"): rec["uuid"]=payload.get("uuid")
    runs[key]=rec; save_schedule_run_state(data); return rec


def update_schedule_run_progress(label,event_dt,callback,chain=None,payload=None):
    data=load_schedule_run_state(); rec=_active_schedule_run(data,label)
    if not rec:
        return None
    payload=payload if isinstance(payload,dict) else {}
    rec["updated_at"]=event_dt.strftime("%Y-%m-%d %H:%M:%S")
    rec["last_callback"]=callback
    if chain:
        rec["current_chain"]=chain; rec["current_task"]=readable_chain_name(chain)
    taskid=payload.get("taskid")
    if taskid is not None and taskid not in rec.setdefault("taskids",[]):
        rec["taskids"].append(taskid)
    if callback=="AllTasksCompleted":
        rec["status"]="completed"; rec["completed_at"]=event_dt.strftime("%Y-%m-%d %H:%M:%S")
        rec["current_chain"]=None; rec["current_task"]=None
    save_schedule_run_state(data); return rec


def mark_schedule_run_failed(label,event_dt,callback,chain,diagnosis,payload=None):
    data=load_schedule_run_state(); rec=_active_schedule_run(data,label)
    if not rec:
        return None
    diagnosis=diagnosis or {}
    rec["status"]="failed"; rec["updated_at"]=event_dt.strftime("%Y-%m-%d %H:%M:%S")
    rec["failed_at"]=event_dt.strftime("%Y-%m-%d %H:%M:%S")
    rec["current_chain"]=chain; rec["current_task"]=readable_chain_name(chain) if chain else "当前任务"
    rec["last_callback"]=callback
    rec["error"]={
        "title":diagnosis.get("title") or "任务执行失败",
        "summary":diagnosis.get("happened") or f"{rec['current_task']}没有正常完成。",
        "likely":diagnosis.get("likely") or "界面识别、连接或游戏状态异常。",
        "action":diagnosis.get("action") or "远程检查当前游戏画面和 MAA 状态。",
        "task":diagnosis.get("task") or rec["current_task"],
        "chain":chain,"callback":callback,"time":rec["failed_at"],
    }
    save_schedule_run_state(data); return rec

def _agent_script_from_cmdline(cmdline, cwd=None):
    return script_from_cmdline(cmdline, cwd)


def pid_is_our_agent(pid):
    return agent_process_info(pid, Path(__file__).resolve()) is not None


def find_other_maa_agent_process():
    """Find another MAA-Agent copy even when it lives in a backup directory.

    This is intentionally conservative: only Python command lines whose script is
    named agent.py and whose parent directory name starts with MAA-Agent count.
    """
    # Python 3.14 virtual environments may keep a python/pythonw launcher
    # parent alive with the same ``agent.py`` command line. It is part of this
    # process, not a second Agent, so exclude the full ancestor chain.
    excluded = {os.getpid()}
    try:
        excluded.update(parent.pid for parent in psutil.Process().parents())
    except psutil.Error:
        pass
    rows = find_running_agents(exclude_pids=excluded)
    return rows[0] if rows else None


def another_agent_is_running():
    # First honor this directory's PID file.
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text(encoding="utf-8").strip())
            if pid != os.getpid() and pid_is_our_agent(pid):
                return True
        except Exception:
            pass

    # Then check sibling/backup copies such as D:\MAA-Agent and
    # D:\MAA-Agent_0920. This prevents the new copy from being started while
    # an older copy is still active.
    return find_other_maa_agent_process() is not None


def write_pid():
    write_runtime_record(BASE_DIR)


def remove_pid():
    clear_runtime_files(BASE_DIR, expected_pid=os.getpid())


def clear_stop_request():
    try:
        if STOP_FILE.exists():
            STOP_FILE.unlink()
    except Exception:
        pass


def stop_requested():
    return STOP_FILE.exists()


def sleep_with_stop(seconds):
    end = time.time() + seconds
    while time.time() < end:
        if stop_requested():
            return
        # Alarm-drill commands are interactive. Poll their tiny file-backed
        # queue during the normal sleep so a 60-second monitor interval never
        # turns a button click into a delayed notification.
        if (STATE_DIR / "diagnostic_commands.jsonl").exists():
            execute_diagnostic_commands()
        time.sleep(min(0.2, max(0.01, end - time.time())))


def process_matches(name, target_names):
    name = (name or "").lower()
    return name in {x.lower() for x in target_names}


def find_processes(names):
    result = []
    for p in psutil.process_iter(["pid", "name", "exe", "memory_info"]):
        try:
            if process_matches(p.info.get("name"), names):
                result.append(p)
        except psutil.Error:
            pass
    return result


def total_memory_mb(procs):
    total = 0
    for p in procs:
        try:
            total += p.memory_info().rss / 1024 / 1024
        except psutil.Error:
            pass
    return total


def start_maa_by_bat():
    bat = cfg.get("start_maa_bat", "")
    if not bat or not Path(bat).exists():
        msg = f"配置里的 start_maa_bat 找不到：{bat}"
        log(msg)
        add_daily_alert(msg, severity="warning")
        bark(
            "MAA 自动启动脚本不可用",
            "MAA 的自动启动脚本没有找到。\n你要做什么：检查 Agent 配置中的 MAA 启动脚本路径。",
            level="timeSensitive",
            alert_name="start_bat_missing",
        )
        return

    try:
        subprocess.Popen(
            ["cmd.exe", "/d", "/s", "/c", bat],
            cwd=str(Path(bat).parent),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log(f"Started MAA by bat: {bat}")
        add_daily_event("process", f"已尝试通过 BAT 启动 MAA：{bat}")
    except Exception as e:
        msg = str(e)
        log(f"MAA BAT 启动失败：{msg}")
        add_daily_alert(f"MAA BAT 启动失败：{msg}", severity="warning")
        bark(
            "MAA 自动启动失败",
            f"MAA 的自动启动脚本执行失败。\n你要做什么：远程确认启动脚本和 MAA 状态。\n详细错误已记录到 {LOG_DIR / 'agent.log'}。",
            level="timeSensitive",
            alert_name="start_bat_failed",
        )


def reboot_windows(reason):
    bark_force(
        "MAA Agent 准备重启电脑",
        f"原因：{reason}\n时间：{now_str()}\n30 秒后重启。",
        level="timeSensitive"
    )
    log(f"Rebooting Windows: {reason}")
    subprocess.Popen(
        ["shutdown.exe", "/r", "/t", "30"],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def startup_process_grace_active():
    grace = float(cfg.get("startup_process_grace_minutes", 10)); uptime = windows_uptime_hours()
    return uptime is not None and uptime < grace / 60.0


def load_process_health_state():
    try:
        if PROCESS_HEALTH_FILE.exists():
            data=json.loads(PROCESS_HEALTH_FILE.read_text(encoding="utf-8"))
            if isinstance(data,dict): return data
    except Exception:
        pass
    return {"missing": {}}


def save_process_health_state(data):
    tmp=PROCESS_HEALTH_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    tmp.replace(PROCESS_HEALTH_FILE)


def debug_forced_missing_labels():
    try:
        data=json.loads(DEBUG_OVERRIDE_FILE.read_text(encoding="utf-8"))
        until=float(data.get("until",0))
        if until <= time.time():
            DEBUG_OVERRIDE_FILE.unlink(missing_ok=True)
            return set()
        return {str(x) for x in data.get("forced_missing",[]) if str(x)}
    except Exception:
        return set()


def detect_maa_instance_processes():
    dirs={k:Path(v) for k,v in (cfg.get("maa_instance_dirs") or {}).items() if v}
    found={label:[] for label in dirs}
    unknown=[]
    for proc in find_processes(cfg.get("maa_process_names",["MAA.exe"])):
        try:
            exe=proc.exe() or ""
        except psutil.Error:
            exe=""
        exe_lower=os.path.normcase(os.path.abspath(exe)) if exe else ""
        matched=False
        for label,root in dirs.items():
            try:
                root_lower=os.path.normcase(os.path.abspath(str(root)))
                if exe_lower and (exe_lower == os.path.join(root_lower,"MAA.exe") or exe_lower.startswith(root_lower+os.sep)):
                    found[label].append(proc); matched=True; break
            except Exception:
                pass
        if not matched:
            unknown.append(proc)
    return found,unknown


def _process_missing_elapsed(state,key,missing):
    now_ts=time.time(); rows=state.setdefault("missing",{})
    if missing:
        rec=rows.setdefault(key,{})
        rec["since"]=rec.get("since") or now_ts
        return (now_ts-float(rec["since"]))/60.0,rec
    rec=rows.pop(key,None)
    return 0.0,rec


def check_processes():
    if not cfg.get("enable_process_alert", True):
        return
    if startup_process_grace_active():
        log("Startup grace: process alerts suppressed.")
        return

    all_maa=find_processes(cfg.get("maa_process_names",["MAA.exe"]))
    found,unknown=detect_maa_instance_processes()
    expected=list((cfg.get("maa_instance_dirs") or {}).keys())
    sustain=float(cfg.get("process_missing_sustain_minutes",3))
    state=load_process_health_state(); alerts=[]
    forced_missing=debug_forced_missing_labels()

    # Prefer exact exe-path mapping when it is available. If none of the running
    # processes can be mapped, fall back to count rather than guessing MAA1/MAA2.
    mapped_count=sum(len(v) for v in found.values())
    # If even one MAA process cannot be mapped to an instance directory, do
    # not infer which named instance is missing. Fall back to total-count logic.
    exact_mapping_usable=bool(expected) and not unknown
    instance_status={}
    if exact_mapping_usable:
        for label in expected:
            diagnostic=label in forced_missing
            missing=len(found.get(label,[]))==0 or diagnostic
            elapsed,rec=_process_missing_elapsed(state,f"instance:{label}",missing)
            if diagnostic:
                rec["diagnostic"]=True
            instance_status[label]="missing" if missing else "running"
            if not missing:
                recovered=resolve_incident(key=f"process|{label}",resolution="MAA 进程已恢复")
                if recovered: add_recoverable_issue("process_recovered",f"{label} 进程已恢复",label=label)
                continue
            if elapsed < sustain:
                continue
            if not rec.get("alerted"):
                rec["alerted"]=True
                alerts.append((label,elapsed,bool(rec.get("diagnostic"))))
    else:
        # Generic count fallback avoids falsely naming the missing instance when
        # Windows denies exe-path access or MAA was launched in an unusual way.
        missing_count=max(0,max(2,len(expected) or 2)-len(all_maa))
        elapsed,rec=_process_missing_elapsed(state,"generic",missing_count>0)
        if missing_count==0:
            recovered=resolve_incident(key="process|generic",resolution="MAA 进程数量已恢复")
            if recovered: add_recoverable_issue("process_recovered","MAA 进程数量已恢复")
        elif elapsed>=sustain and not rec.get("alerted"):
            rec["alerted"]=True
            alerts.append((None,elapsed,False))

    save_process_health_state(state)
    data=load_daily_stats(); proc_data=data.setdefault("process",{}); proc_data["maa_count_current"]=len(all_maa); proc_data["maa_instances"]=instance_status; save_daily_stats(data)
    if not alerts:
        return

    if alerts[0][0] is not None:
        labels=[x[0] for x in alerts]
        is_drill=all(x[2] for x in alerts)
        title=(f"{labels[0]} 已退出" if len(labels)==1 else "多个 MAA 已退出")
        if is_drill: title="【演习】"+title
        body=(("这是由管理器触发的报警演习，下面仍使用真实的缺失检测和 Bark 通道。\n" if is_drill else "")+
              f"发生了什么：当前没有检测到 {'、'.join(labels)} 的 MAA 进程。\n"
              "大概率原因：对应 MAA 被关掉、崩溃，或没有被启动脚本拉起。\n"
              "你要做什么：如果不是你主动关闭的，现在值得远程看一眼。")
        if not is_drill:
            for label,_,_ in alerts:
                add_incident("process_missing",f"{label} MAA 进程缺失",label=label,likely="MAA 退出/崩溃或未启动",action="远程检查",key=f"process|{label}")
            add_daily_alert(f"MAA 进程缺失：{'、'.join(labels)}",severity="critical")
        else:
            add_daily_event("diagnostic",f"真实检测链路演习：{'、'.join(labels)} 缺失",severity="info")
        if is_drill:
            bark_force(title, body, level="timeSensitive")
        else:
            bark(title,body,level="timeSensitive",alert_name="maa_instance_missing_"+"_".join(labels))
    else:
        missing=max(0,2-len(all_maa))
        body=(f"发生了什么：应有 2 个 MAA，目前只检测到 {len(all_maa)} 个。\n"
              "无法可靠判断具体是 MAA1 还是 MAA2，因此不点名实例。\n"
              "你要做什么：如果不是手动关闭，远程检查两个 MAA。")
        add_incident("process_missing",f"MAA 进程数量不足（少 {missing} 个）",likely="MAA 退出/崩溃或路径无法识别",action="远程检查",key="process|generic")
        add_daily_alert("MAA 进程数量不足",severity="critical")
        bark("有 MAA 进程掉了",body,level="timeSensitive",alert_name="maa_process_count_missing")

    if cfg.get("auto_restart_maa_when_missing",False):
        start_maa_by_bat()

def enum_windows_for_pids(target_pids):
    windows = []

    if win32gui is None or win32process is None:
        return windows

    def callback(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return

        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid in target_pids:
                title = win32gui.GetWindowText(hwnd)
                if title:
                    windows.append((hwnd, pid, title))
        except Exception:
            pass

    win32gui.EnumWindows(callback, None)
    return windows


def is_hung_window(hwnd):
    try:
        user32 = ctypes.windll.user32
        return bool(user32.IsHungAppWindow(hwnd))
    except Exception:
        return False


def load_hung_state():
    try:
        if HUNG_STATE_FILE.exists():
            data=json.loads(HUNG_STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(data,dict): return data
    except Exception: pass
    return {"active":{}}


def save_hung_state(data):
    HUNG_STATE_FILE.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")


def check_hung_windows():
    if not cfg.get("enable_hung_window_alert", True):
        return
    target_procs=find_processes(cfg.get("maa_process_names",[]))+find_processes(cfg.get("mumu_process_names",[]))
    pids=set()
    for proc in target_procs:
        try: pids.add(proc.pid)
        except psutil.Error: pass
    windows=enum_windows_for_pids(pids) if pids else []
    hung={(str(pid),title) for hwnd,pid,title in windows if is_hung_window(hwnd)}
    state=load_hung_state(); active=state.setdefault("active",{}); now_ts=time.time(); sustain=float(cfg.get("hung_window_sustain_minutes",2)); alerts=[]
    current_keys=set()
    for pid,title in hung:
        key=f"{pid}|{title}"; current_keys.add(key); rec=active.setdefault(key,{"since":now_ts,"pid":int(pid),"title":title,"alerted":False})
        age=(now_ts-float(rec.get("since",now_ts)))/60
        if age>=sustain and not rec.get("alerted"):
            rec["alerted"]=True; alerts.append((key,rec,age))
    for key,rec in list(active.items()):
        if key in current_keys: continue
        if rec.get("alerted"):
            if resolve_incident(key=f"hung|{rec.get('pid')}",resolution="窗口已恢复响应"):
                add_recoverable_issue("hung_recovered",f"窗口已恢复：{rec.get('title')}")
        active.pop(key,None)
    save_hung_state(state)
    for key,rec,age in alerts:
        pid=rec.get("pid"); title=rec.get("title")
        data=load_daily_stats(); data.setdefault("hung_windows",[]).append({"time":now_str(),"pid":pid,"title":title,"sustain_minutes":round(age,1)}); data["hung_windows"]=data["hung_windows"][-50:]; save_daily_stats(data)
        body=(f"发生了什么：窗口“{title}”长时间没有恢复响应。\n"
              "大概率原因：MAA/MuMu 界面或进程可能卡住。\n"
              "你要做什么：如果不是正在进行明显的重负载操作，远程确认对应程序。")
        add_incident("hung_window",f"窗口持续未响应：{title}",likely="MAA/MuMu UI 卡死",action="远程确认",key=f"hung|{pid}")
        add_daily_alert(f"窗口持续未响应：{title}",severity="critical")
        bark("MAA/MuMu 持续未响应",body,level="critical",alert_name=f"hung_window_{pid}")
        if cfg.get("auto_reboot_when_hung",False): reboot_windows(f"窗口持续未响应：{title}")

def load_resource_pressure_state():
    try:
        if RESOURCE_PRESSURE_FILE.exists(): return json.loads(RESOURCE_PRESSURE_FILE.read_text(encoding="utf-8"))
    except Exception: pass
    return {"high_since": None, "critical_since": None, "last_alert": None}


def save_resource_pressure_state(state):
    RESOURCE_PRESSURE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def format_top_processes(rows, limit=4):
    if not rows: return "未知"
    return "、".join(f"{x.get('name')} {x.get('mb',0)/1024:.1f}GB" for x in rows[:limit])


def check_disk_health():
    data=load_daily_stats(); threshold=float(cfg.get("disk_critical_free_gb",10))
    for drive,rec in data.get("disks",{}).items():
        free=rec.get("free_current_gb")
        if not isinstance(free,(int,float)):
            continue
        ikey=f"disk|{drive}"
        if free < threshold:
            already=incident_is_open(ikey,data)
            add_incident("disk_low",f"{drive} 空闲空间仅 {free:.1f}GB",likely="日志、游戏更新、缓存或其他文件持续增长",action="清理磁盘空间",key=ikey)
            if not already:
                add_daily_alert(f"{drive} 磁盘空间很低：{free:.1f}GB",severity="warning")
                bark("备用机磁盘空间很低",f"发生了什么：{drive} 当前只剩 {free:.1f}GB。\n你要做什么：建议在下一次方便时清理，避免 MAA/模拟器/游戏更新因磁盘满失败。",level="timeSensitive",alert_name="disk_low_"+hashlib.sha1(drive.encode()).hexdigest()[:8])
        else:
            if resolve_incident(key=ikey,resolution="磁盘空闲空间已恢复"):
                add_recoverable_issue("disk_recovered",f"{drive} 空闲空间已恢复到 {free:.1f}GB")


def check_memory():
    if not cfg.get("enable_memory_alert", True): return
    maa_procs=find_processes(cfg["maa_process_names"]); mumu_procs=find_processes(cfg["mumu_process_names"])
    maa_mem=total_memory_mb(maa_procs); mumu_mem=total_memory_mb(mumu_procs); update_memory_stats(maa_mem,mumu_mem,len(maa_procs),len(mumu_procs)); check_disk_health()
    vm=psutil.virtual_memory(); percent=float(vm.percent); available_mb=vm.available/1048576; used_mb=(vm.total-vm.available)/1048576
    high_pct=float(cfg.get("memory_pressure_percent",88)); critical_pct=float(cfg.get("memory_critical_percent",95))
    high_need=float(cfg.get("memory_pressure_sustain_minutes",30)); critical_need=float(cfg.get("memory_critical_sustain_minutes",10)); critical_avail=float(cfg.get("memory_critical_available_mb",1536))
    maa_bad=maa_mem>float(cfg.get("max_total_maa_memory_mb",3000)); mumu_bad=mumu_mem>float(cfg.get("max_total_mumu_memory_mb",9000))
    state=load_resource_pressure_state(); now_ts=time.time()
    if percent >= high_pct: state["high_since"] = state.get("high_since") or now_ts
    else:
        state["high_since"]=None; state["critical_since"]=None; state["last_alert"]=None; save_resource_pressure_state(state)
        if resolve_incident(key="memory|pressure",resolution="系统内存压力已恢复"):
            add_recoverable_issue("memory_recovered","持续内存压力已恢复")
        return
    if percent >= critical_pct: state["critical_since"] = state.get("critical_since") or now_ts
    else: state["critical_since"]=None
    high_for=(now_ts-float(state.get("high_since") or now_ts))/60; critical_for=(now_ts-float(state.get("critical_since") or now_ts))/60 if state.get("critical_since") else 0
    data=load_daily_stats(); context=data.get("resource_context",{}); heavy=context.get("expected_heavy_processes",[]); top=context.get("top_memory_processes",[])
    other_mb=max(0.0,(vm.total-vm.available)/1048576-maa_mem-mumu_mem); heavy_lower={x.lower() for x in heavy}; background_only={"steam.exe","rockstargameslauncher.exe","redprelauncher.exe","gtavlauncher.exe","playgtav.exe"}; specific_heavy=any(x not in background_only for x in heavy_lower); large_external=any((row.get("name") or "").lower() not in {"maa.exe","mumuplayer.exe","nemuplayer.exe","mumuvmmheadless.exe"} and float(row.get("mb",0)) >= float(cfg.get("expected_large_external_process_mb",3000)) for row in top); steam_with_large=("steam.exe" in heavy_lower and large_external); expected_external=other_mb>=float(cfg.get("expected_external_memory_mb",7000)) and not maa_bad and not mumu_bad and (specific_heavy or steam_with_large)
    if expected_external:
        mem=data.setdefault("memory",{}); mem["expected_external_pressure_minutes"]=round(float(mem.get("expected_external_pressure_minutes",0))+float(cfg.get("check_interval_seconds",60))/60,1); save_daily_stats(data)
    critical_now=percent>=critical_pct and available_mb<=critical_avail and critical_for>=critical_need
    sustained=high_for>=high_need; should_alert=critical_now or (sustained and (maa_bad or mumu_bad or not expected_external))
    if not should_alert: save_resource_pressure_state(state); return
    reasons=[]
    if maa_bad: reasons.append(f"MAA 合计占用偏高（{maa_mem/1024:.1f}GB）")
    if mumu_bad: reasons.append(f"MuMu 合计占用偏高（{mumu_mem/1024:.1f}GB）")
    if critical_now: reasons.append("可用内存已经很低")
    if not reasons: reasons.append("没有发现能解释压力的已知重负载程序")
    body=(f"发生了什么：系统内存持续吃紧，当前 {percent:.0f}%，已用 {used_mb/1024:.1f}GB，可用 {available_mb/1024:.1f}GB。\n"
          f"可能原因：{'；'.join(reasons)}。\n当前内存大户：{format_top_processes(top)}。\n"
          "你要做什么：如果这不是你主动运行游戏等重负载造成的，检查是否有程序卡死或异常占用。")
    already=bool(state.get("last_alert")) or incident_is_open("memory|pressure")
    add_incident("memory_pressure",f"系统内存持续 {percent:.0f}%",likely="；".join(reasons),action="检查高占用进程",key="memory|pressure")
    if not already:
        add_daily_alert(f"持续内存压力：{percent:.0f}%",severity="warning")
        bark("备用机内存持续吃紧",body,level="timeSensitive",alert_name="system_memory_pressure")
        state["last_alert"]=now_ts
    save_resource_pressure_state(state)

def reset_transient_session_state():
    """Reset sensor timers only after a real Windows reboot.

    An Agent restart is not evidence that MAA recovered.  Keeping the original
    missing timestamp fixes the gap where repeatedly restarting the watchdog
    could postpone a process alert forever.
    """
    current_boot=int(psutil.boot_time())
    state=load_process_health_state()
    previous_boot=state.get("windows_boot_time")
    same_boot=previous_boot in (None,current_boot)
    state["windows_boot_time"]=current_boot
    if same_boot:
        save_process_health_state(state)
        log("Sensor state preserved across Agent restart (same Windows boot).")
        return

    state["missing"]={}
    save_process_health_state(state)
    save_resource_pressure_state({"high_since":None,"critical_since":None,"last_alert":None})
    try: HUNG_STATE_FILE.write_text(json.dumps({"active":{}},ensure_ascii=False),encoding="utf-8")
    except Exception: pass
    try:
        if NETWORK_HEALTH_FILE.exists():
            net=json.loads(NETWORK_HEALTH_FILE.read_text(encoding="utf-8")); net["failure_since"]=None; net["alerted"]=False; NETWORK_HEALTH_FILE.write_text(json.dumps(net,ensure_ascii=False,indent=2),encoding="utf-8")
    except Exception: pass
    for path in GENERIC_ISSUE_DIR.glob("pending_*.json"):
        try: path.unlink()
        except Exception: pass
    log("Sensor state reset because Windows boot time changed.")


def existing_logs():
    result = []
    for p in cfg.get("maa_log_paths", []):
        path = Path(p)
        if path.exists():
            result.append(path)
    return result


def label_for_log_path(path):
    s = str(path).lower()

    if "\\maa1\\" in s or "/maa1/" in s:
        return "MAA1"

    if "\\maa2\\" in s or "/maa2/" in s:
        return "MAA2"

    return path.parent.parent.name or path.parent.name


def log_offset_state_path(path):
    key = hashlib.sha1(str(path).encode("utf-8", errors="ignore")).hexdigest()
    return STATE_DIR / f"log_offset_{key}.json"


def initialize_log_offsets_on_start():
    if not cfg.get("skip_old_logs_on_start", True):
        return

    count = 0; preserved = 0
    for path in existing_logs():
        try:
            state_path = log_offset_state_path(path)
            # Existing offsets belong to this exact log path and must survive
            # Agent restarts. Overwriting them here would silently discard any
            # MAA output written while the monitor was stopped.
            if state_path.exists():
                preserved += 1
                continue
            state = {
                "path": str(path),
                "offset": path.stat().st_size,
                "size": path.stat().st_size,
                "updated_at": now_str()
            }
            state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            count += 1
        except Exception as e:
            log(f"Failed to prime offset for {path}: {e}")

    log(f"Log offsets ready: primed {count}, preserved {preserved}.")


def read_new_log_text(path):
    state_path = log_offset_state_path(path)
    size = path.stat().st_size

    old_offset = 0
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            old_offset = int(state.get("offset", 0))
        except Exception:
            old_offset = 0

    if old_offset > size:
        old_offset = 0

    if old_offset == size:
        return ""

    max_read = int(cfg.get("log_increment_max_bytes", 1024 * 1024))
    if size - old_offset > max_read:
        old_offset = max(0, size - max_read)

    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        f.seek(old_offset)
        text = f.read()

    state = {
        "path": str(path),
        "offset": size,
        "size": size,
        "updated_at": now_str()
    }
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    return text


def resolve_agent_owned_path(value, default_name):
    raw = str(value or "").strip()
    path = Path(raw) if raw else (STATE_DIR / default_name)
    if not path.is_absolute():
        path = BASE_DIR / path
    return path


def copilot_notify_once_path():
    return resolve_agent_owned_path(cfg.get("copilot_notify_once_file"), "copilot_notify.once")


def copilot_notify_always_path():
    return resolve_agent_owned_path(cfg.get("copilot_notify_always_file"), "copilot_notify.always")


def copilot_notify_enabled():
    if cfg.get("copilot_notify_default", False):
        return True
    if copilot_notify_once_path().exists():
        return True
    if copilot_notify_always_path().exists():
        return True
    return False


def consume_copilot_notify_once():
    try:
        path = copilot_notify_once_path()
        if path.exists():
            path.unlink()
            log("Consumed copilot_notify.once")
    except Exception as e:
        log(f"Failed to consume copilot notify once file: {e}")


SPECIAL_NOTICE_PATTERNS = [
    "正在获取更新",
    "正在检查资源完整性",
    "GameStartUpdateOCR",
    "GameStartCheckResourceOCR",
    "PrtsErrorConfirm",
    "NotDownloadVoice",
    "TodaysSupplies",
    "There is no available start button",
    "ExceededLimit",
    "exec times exceeded the limit"
]

SPECIAL_NOTICE_HUMAN = {
    "正在获取更新": "检测到游戏更新流程",
    "GameStartUpdateOCR": "检测到游戏更新流程",
    "正在检查资源完整性": "检测到资源完整性检查",
    "GameStartCheckResourceOCR": "检测到资源完整性检查",
    "PrtsErrorConfirm": "检测到游戏错误确认界面",
    "OfflineConfirm": "检测到掉线/离线确认界面",
    "There is no available start button": "出现“没有可用开始按钮”状态",
    "ExceededLimit": "有任务达到执行次数上限",
    "exec times exceeded the limit": "有任务达到执行次数上限",
}


def find_patterns(text, patterns):
    lower = text.lower()
    hits = []
    for p in patterns:
        if p.lower() in lower:
            hits.append(p)
    return hits


MAA_LOG_TS_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d{1,6})?)\]")
MAA_CALLBACK_RE = re.compile(r"Assistant::append_callback\s*\|\s*([A-Za-z0-9_]+)\s*(\{.*\})?\s*$")


def parse_maa_timestamp(line):
    m=MAA_LOG_TS_RE.match(line)
    if not m:
        return None
    value=m.group(1)
    for fmt in ("%Y-%m-%d %H:%M:%S.%f","%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value,fmt)
        except Exception:
            pass
    return None


def parse_maa_records(text):
    rows=[]
    for line in text.splitlines():
        ts=parse_maa_timestamp(line)
        m=MAA_CALLBACK_RE.search(line)
        if not m:
            rows.append({"time":ts,"callback":None,"payload":{},"chain":None,"raw":line})
            continue
        callback=m.group(1); payload={}
        raw_json=m.group(2)
        if raw_json:
            try:
                payload=json.loads(raw_json)
            except Exception:
                payload={}
        chain=payload.get("taskchain") if isinstance(payload,dict) else None
        rows.append({"time":ts,"callback":callback,"payload":payload,"chain":chain,"raw":line})
    return rows


def has_confirmed_subtask(records, task_name):
    """Return true only when MAA actually entered a named task node.

    Raw analyzer lines list every candidate in ``to_be_recognized``.  A name
    appearing there is not a recognition result; only callback details.task is.
    """
    wanted=str(task_name).lower()
    for record in records:
        if record.get("callback") not in {"SubTaskStart", "SubTaskCompleted"}:
            continue
        payload=record.get("payload") or {}
        details=payload.get("details") if isinstance(payload,dict) else None
        actual=details.get("task") if isinstance(details,dict) else None
        if actual and str(actual).lower().split("@")[-1]==wanted:
            return True
    return False


def pending_issue_path(label):
    safe=re.sub(r"[^A-Za-z0-9_.-]+","_",label)
    return GENERIC_ISSUE_DIR/f"pending_{safe}.json"


def load_pending_issues(label):
    path=pending_issue_path(label)
    try:
        if path.exists():
            data=json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data,dict): return data
    except Exception:
        pass
    return {"issues":{}}


def save_pending_issues(label,data):
    pending_issue_path(label).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")


def diagnose_maa_event(context, callback=None, chain=None):
    lower=context.lower(); task=readable_chain_name(chain) if chain else "当前任务"
    terminal=callback in {"TaskChainError","TaskChainStopped"}
    def anyp(words): return any(w.lower() in lower for w in words)
    if anyp(["其他设备登录","在其他设备","账号已在","登录已失效","重复登录","another device","logged in elsewhere","login expired","kicked out"]):
        return {"category":"login_conflict","title":"游戏账号疑似被顶/登录失效","task":task,"happened":f"{task}执行时登录状态异常。","likely":"账号在另一台设备登录、登录态过期，或游戏把当前会话踢下线。","action":"如果你刚在别处登录过可忽略；否则远程看游戏登录界面。","mode":"immediate"}
    if anyp(["客户端版本过低","强制更新","version update","maintenance","维护中","服务器维护"]):
        return {"category":"game_update","title":"版本更新/维护阻断任务","task":task,"happened":f"{task}遇到版本更新或维护信号。","likely":"官方更新、强制资源下载或维护。","action":"完成更新或等待维护结束；观察下一轮。","mode":"immediate"}
    if callback in {"InternalError","InitFailed"}:
        return {"category":"maa_internal","title":"MAA 初始化异常","task":task,"happened":f"{task}出现 MAA 初始化异常。","likely":"MAA 自身、连接初始化或运行环境异常。","action":"现在值得远程检查 MAA 与 MuMu。","mode":"immediate"}
    if anyp(["fatal exception","崩溃","crash"]):
        return {"category":"crash","title":"MAA / 游戏疑似崩溃","task":task,"happened":f"{task}附近出现崩溃信号。","likely":"MAA、MuMu 或游戏异常退出。","action":"远程确认对应进程状态。","mode":"immediate"}
    if anyp(["device offline","device not found","controller is unavailable","connect failed","connection refused","adb连接失败","adb 连接失败","无法连接","touchmodenotavailable","screencapfailed"]):
        return {"category":"task_failed" if terminal else "emulator_connection","title":"任务因 MuMu / ADB 连接异常而失败" if terminal else "MuMu / ADB 连接持续异常","task":task,"happened":f"{task}出现 MuMu/ADB 控制连接失败。","likely":"模拟器连接异常或 MuMu/ADB 卡住。","action":"远程检查 MuMu/ADB 与游戏状态。","mode":"pending" if terminal else "pending_connection"}
    if anyp(["network error","network timeout","timed out","connection reset","网络错误","网络异常","请求超时","连接超时"]):
        return {"category":"task_failed" if terminal else "game_network","title":"任务因游戏/网络异常而失败" if terminal else "游戏/网络持续异常","task":task,"happened":f"{task}出现网络连接失败。","likely":"代理、网络或游戏服务器异常。","action":"远程检查网络、代理与游戏状态。" if terminal else "只有持续不恢复才需要检查网络/代理。","mode":"pending" if terminal else "pending_connection"}
    # OfflineConfirm occurs in MAA's candidate lists during normal operation.
    # It is failure evidence only when a callback says that node was entered,
    # or when StartUp emits its explicit login-failure warning.
    confirmed_offline=has_confirmed_subtask(parse_maa_records(context),"OfflineConfirm")
    if callback in {"TaskChainError","TaskChainStopped"} and (confirmed_offline or anyp(["login failed, entering game-restart loop"])):
        return {"category":"task_failed","title":"游戏离线导致任务失败","task":task,"happened":f"{task}遇到游戏离线/登录失败并被中止。","likely":"游戏掉线、登录态失效、账号在其他设备登录，或登录界面发生变化。","action":"远程检查游戏登录界面；恢复登录后重新执行任务。","mode":"pending"}
    if chain=="CloseDown" and callback in {"TaskChainError","TaskChainStopped","SubTaskError","SubTaskStopped"}:
        return {"category":"cleanup_glitch","title":"关闭游戏收尾小异常","task":task,"happened":"关闭游戏收尾步骤返回异常。","likely":"进程关闭竞态。","action":"无需处理。","mode":"suppress"}
    # A stopped task chain is terminal. It may sometimes be deliberate, but a
    # monitoring agent must prefer a false positive over silently accepting a
    # scheduled run that never completed.
    if callback=="TaskChainStopped":
        return {"category":"task_failed","title":"任务链被中止","task":task,"happened":f"{task}在完成前被中止。","likely":"登录、识别、连接失败，或有人手动停止了任务。","action":"确认是否为人工停止；否则远程检查游戏和 MAA。","mode":"pending"}
    if callback=="SubTaskStopped":
        return {"category":"subtask_stopped","title":"子任务被停止","task":task,"happened":f"{task}的子任务被停止。","likely":"手动停止或 MAA 内部流程控制。","action":"若不是人工操作，检查当前画面。","mode":"suppress"}
    if callback=="SubTaskError":
        # This callback is retry-level telemetry: successful Infrast/Mall runs
        # routinely emit it.  The enclosing chain's terminal callback decides
        # success or failure and is handled separately below/above.
        return {"category":"subtask_retry","title":"子任务重试","task":task,"happened":f"{task}出现一次可恢复的子任务错误。","likely":"MAA 正在重试识别或跳过非关键步骤。","action":"等待所属任务链的最终结果。","mode":"suppress"}
    if callback=="TaskChainError":
        return {"category":"task_failed","title":"任务执行失败","task":task,"happened":f"{task}没有正常完成。","likely":"界面识别、游戏状态或连接出现异常。","action":"远程检查当前游戏画面和 MAA 状态。","mode":"pending"}
    return None


def pending_signature(d,chain):
    return f"{d.get('category')}|{chain or 'unknown'}"


def queue_pending_issue(label,d,chain,event_dt,payload=None):
    data=load_pending_issues(label); issues=data.setdefault("issues",{}); sig=pending_signature(d,chain)
    ts=(event_dt or datetime.now()).timestamp(); old=issues.get(sig)
    taskid=payload.get("taskid") if isinstance(payload,dict) else None
    uuid=payload.get("uuid") if isinstance(payload,dict) else None
    occurrence_id=(f"{uuid or ''}|{taskid}" if taskid is not None else None)
    rec=old or {
        "signature":sig,"category":d.get("category"),"chain":chain,"title":d.get("title"),"task":d.get("task"),
        "happened":d.get("happened"),"likely":d.get("likely"),"action":d.get("action"),"first_error":ts,
        "last_error":ts,"count":0,"alerted":False
    }
    # A later task id is a new execution, not a duplicate callback from the
    # old one. Re-arm the alert and its age clock so recurring failures cannot
    # be silenced forever by an earlier alerted record.
    if old and occurrence_id and occurrence_id!=old.get("occurrence_id"):
        rec["first_error"]=ts; rec["alerted"]=False; rec.pop("alerted_at",None)
    if occurrence_id:
        rec["occurrence_id"]=occurrence_id; rec["taskid"]=taskid
        if uuid: rec["uuid"]=uuid
    rec["last_error"]=max(float(rec.get("last_error",ts)),ts); rec["count"]=int(rec.get("count",0))+1
    issues[sig]=rec; save_pending_issues(label,data); return rec


def resolve_pending_on_progress(label,event_dt,callback,chain=None):
    if callback not in {"TaskChainStart","SubTaskStart","SubTaskCompleted","TaskChainCompleted","AllTasksCompleted"}:
        return
    data=load_pending_issues(label); issues=data.setdefault("issues",{}); ts=(event_dt or datetime.now()).timestamp(); changed=False
    for sig,rec in list(issues.items()):
        last=float(rec.get("last_error",0) or 0)
        if ts < last:
            continue
        # A TaskChainError is a terminal failure for that task chain. Starting
        # another chain (and even AllTasksCompleted) only proves that the queue
        # moved on; it does not prove that the failed chain recovered. Only an
        # explicit completion of the same chain may clear this failure.
        if rec.get("category")=="task_failed":
            recovered=(callback=="TaskChainCompleted" and bool(chain) and chain==rec.get("chain"))
        else:
            # Generic connection/network errors may legitimately self-recover
            # once MAA produces any later normal progress.
            recovered=True
        if recovered:
            if rec.get("alerted"):
                resolve_incident(key=f"maa_issue|{label}|{sig}",resolution="同一任务链随后明确完成")
            add_recoverable_issue(rec.get("category","recovered"),f"{rec.get('task') or '任务'} 异常后已恢复",label=label,task=rec.get("task"))
            issues.pop(sig,None); changed=True
    if changed: save_pending_issues(label,data)
    # Immediate high-confidence incidents should not poison the nightly report
    # after the machine has demonstrably resumed normal MAA activity.
    if callback in {"TaskChainStart","TaskChainCompleted","AllTasksCompleted"}:
        daily=load_daily_stats(); daily_changed=False; recovered=[]
        for item in daily.get("incidents",[]):
            if item.get("status","open")!="open" or item.get("label")!=label:
                continue
            if item.get("category") not in {"login_conflict","game_update","maa_internal","crash"}:
                continue
            item["status"]="resolved"; item["resolved_at"]=now_str(); item["resolution"]="后续 MAA 活动恢复正常"; daily_changed=True
            recovered.append((item.get("category","recovered"),item.get("task")))
        if daily_changed:
            save_daily_stats(daily)
            for category,task in recovered: add_recoverable_issue(category,f"{label} 后续活动恢复正常",label=label,task=task)


def evaluate_pending_issues(label):
    data=load_pending_issues(label); issues=data.setdefault("issues",{}); now_ts=time.time(); changed=False
    for sig,rec in list(issues.items()):
        # Repeated failures are evidence of a continuous stall, not a reason to
        # restart the clock. Progress removes the pending record elsewhere, so
        # elapsed time must be measured from the first unresolved error.
        # Log timestamps can be a fraction of a second ahead of the monitor's
        # wall clock. Clamp to zero so explicit failures are still immediate.
        age=max(0.0,(now_ts-float(rec.get("first_error",rec.get("last_error",now_ts))))/60.0)
        if age>720:
            issues.pop(sig,None); changed=True; continue
        category=rec.get("category","task_failed")
        # TaskChainError is MAA's explicit task-chain execution/recognition
        # failure signal, so it must alert immediately. The grace period is
        # retained only for lower-confidence connection/network symptoms.
        threshold=(0.0 if category=="task_failed" else
                   float(cfg.get("connection_error_stall_minutes",5) if category in {"emulator_connection","game_network"} else cfg.get("generic_error_stall_minutes",6)))
        if age<threshold or rec.get("alerted"):
            continue
        rec["alerted"]=True; rec["alerted_at"]=now_str(); changed=True
        chain=rec.get("chain"); task=rec.get("task") or readable_chain_name(chain or "当前任务")
        status_text=("MAA 已明确报告该任务失败；后续其他任务继续或队列结束都不代表它成功。" if category=="task_failed" else "之后一直没有恢复正常进展。")
        body=(f"发生了什么：{rec.get('happened')}，{status_text}\n"
              f"大概率原因：{rec.get('likely')}\n你要做什么：{rec.get('action')}")
        add_incident(category,rec.get("happened"),label=label,task=task,likely=rec.get("likely"),action=rec.get("action"),key=f"maa_issue|{label}|{sig}")
        add_daily_alert(f"{label}：{rec.get('title')}（{task}）",severity="warning",label=label)
        bark(f"{label}：{rec.get('title')}",body,level="timeSensitive",alert_name=f"maa_issue_{label}_{category}")
        decision_log({"label":label,"decision":"alert_task_failed" if category=="task_failed" else "alert_pending_stall","reason":category,"taskchain":chain,"age_minutes":round(age,1),"count":rec.get("count"),"deepseek":False})
    if changed: save_pending_issues(label,data)


def readable_chain_name(chain):
    return CHAIN_NAME_MAP.get(chain, chain)


def copilot_notify_state_path(label):
    return STATE_DIR/f"copilot_last_notify_{label}.json"


def notify_copilot_record_if_needed(label,path,record):
    if record.get("chain")!="Copilot":
        return False
    cb=record.get("callback")
    # AllTasksCompleted means the queue ended, not that every task succeeded.
    status="success" if cb=="TaskChainCompleted" else "failed" if cb in {"TaskChainError","TaskChainStopped"} else None
    if not status:
        # Let the generic monitor inspect retries and other callbacks. The
        # optional Copilot notification feature must never become an error-
        # suppression bypass.
        return False
    update_taskchain_count(label,"Copilot",status)
    if not copilot_notify_enabled():
        return status=="success"
    state_path=copilot_notify_state_path(label); now_ts=time.time()
    try: old=json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    except Exception: old={}
    if old.get("status")==status and now_ts-float(old.get("time",0) or 0)<60:
        return True
    state_path.write_text(json.dumps({"status":status,"time":now_ts}),encoding="utf-8")
    if status=="success":
        bark_force(f"{label} 自动战斗完成",f"{label} 的 Copilot 自动战斗已完成。\n时间：{now_str()}",level="active")
    else:
        bark_force(f"{label} 自动战斗失败",f"{label} 的 Copilot 自动战斗没有正常完成。\n你要做什么：远程检查当前游戏画面和 MAA 状态。\n时间：{now_str()}",level="timeSensitive")
    consume_copilot_notify_once(); return True


def _context_for_record(lines,index,radius=5):
    lo=max(0,index-radius); hi=min(len(lines),index+radius+1)
    return "\n".join(x.get("raw","") for x in lines[lo:hi])


def process_log_text(label,path,text,backfill_only=False):
    records=parse_maa_records(text)
    special_hits=find_patterns(text,SPECIAL_NOTICE_PATTERNS)
    if has_confirmed_subtask(records,"OfflineConfirm"):
        special_hits.append("OfflineConfirm")
    if not backfill_only:
        for hit in special_hits:
            human=SPECIAL_NOTICE_HUMAN.get(hit)
            if human: add_special_notice(human,label=label)
    for i,record in enumerate(records):
        cb=record.get("callback"); chain=record.get("chain"); event_dt=record.get("time") or datetime.now()
        if cb=="TaskChainStart":
            matched=match_schedule_start(label,event_dt,chain=chain,source="callback")
            if matched and not backfill_only:
                mark_schedule_run_started(label,matched,event_dt,chain,record.get("payload"))
        if backfill_only or not cb:
            continue
        if chain: update_taskchain_count(label,chain,cb)
        if cb in {"TaskChainCompleted","AllTasksCompleted"}:
            update_schedule_run_progress(label,event_dt,cb,chain,record.get("payload"))
        context=None; d=None
        if cb in {"InternalError","InitFailed","TaskChainError","TaskChainStopped","SubTaskError","SubTaskStopped"}:
            context=_context_for_record(records,i)
            d=diagnose_maa_event(context,cb,chain)
            if cb in {"InternalError","InitFailed","TaskChainError","TaskChainStopped"} and d and d.get("mode")!="suppress":
                mark_schedule_run_failed(label,event_dt,cb,chain,d,record.get("payload"))
        if notify_copilot_record_if_needed(label,path,record):
            continue
        resolve_pending_on_progress(label,event_dt,cb,chain)
        if cb not in {"InternalError","InitFailed","TaskChainError","TaskChainStopped","SubTaskError","SubTaskStopped"}:
            continue
        if not d: continue
        mode=d.get("mode")
        if mode=="suppress":
            add_recoverable_issue(d["category"],d["happened"],label=label,task=d.get("task"))
            decision_log({"label":label,"path":str(path),"decision":"suppress","reason":d["category"],"taskchain":chain,"callback":cb,"deepseek":False})
            continue
        if mode=="immediate":
            body=f"发生了什么：{d['happened']}\n大概率原因：{d['likely']}\n你要做什么：{d['action']}\n时间：{event_dt:%Y-%m-%d %H:%M:%S}"
            key=f"maa_issue|{label}|{d['category']}|{chain or 'unknown'}"
            add_incident(d["category"],d["happened"],label=label,task=d.get("task"),likely=d.get("likely"),action=d.get("action"),key=key)
            add_daily_alert(f"{label}：{d['title']}（{d.get('task')}）",severity="warning",label=label)
            bark(f"{label}：{d['title']}",body,level="timeSensitive",alert_name=f"maa_issue_{label}_{d['category']}")
            decision_log({"label":label,"path":str(path),"decision":"alert_immediate","reason":d["category"],"taskchain":chain,"callback":cb,"deepseek":False})
            continue
        rec=queue_pending_issue(label,d,chain,event_dt,record.get("payload"))
        decision_log({"label":label,"path":str(path),"decision":"defer","reason":d["category"],"taskchain":chain,"callback":cb,"repeat_count":rec.get("count"),"deepseek":False})
        # Do not wait until the end of the current read batch: a later success
        # from another task must never erase this explicit failure before Bark
        # has had a chance to send/queue it.
        if d.get("category")=="task_failed":
            evaluate_pending_issues(label)
    if not backfill_only:
        # Also notice low-level connection/network failures that may not carry a
        # callback. They are always deferred; a later normal callback resolves them.
        callbacks={r.get("callback") for r in records if r.get("callback")}
        if not callbacks.intersection({"InternalError","InitFailed","TaskChainError","TaskChainStopped","SubTaskError"}):
            progress_cbs={"TaskChainStart","SubTaskStart","SubTaskCompleted","TaskChainCompleted","AllTasksCompleted"}
            last_progress=max((i for i,r in enumerate(records) if r.get("callback") in progress_cbs),default=-1)
            low_level_patterns=["device offline","device not found","controller is unavailable","connect failed","connection refused","adb连接失败","adb 连接失败","无法连接","network error","network timeout","timed out","connection reset","网络错误","网络异常","请求超时","连接超时"]
            last_low_error=max((i for i,r in enumerate(records) if any(x in r.get("raw","").lower() for x in [p.lower() for p in low_level_patterns])),default=-1)
            if last_low_error>last_progress:
                d=diagnose_maa_event(records[last_low_error].get("raw",""),None,None)
                if d and d.get("mode") in {"pending","pending_connection"}:
                    queue_pending_issue(label,d,None,records[last_low_error].get("time") or datetime.now())
        evaluate_pending_issues(label)


def backfill_recent_schedule_starts():
    hours=float(cfg.get("schedule_backfill_hours",12)); max_bytes=int(cfg.get("schedule_backfill_max_bytes",2*1024*1024)); cutoff=datetime.now()-timedelta(hours=hours)
    total=0
    for path in existing_logs():
        try:
            size=path.stat().st_size; start=max(0,size-max_bytes)
            with open(path,"r",encoding="utf-8",errors="ignore") as f:
                f.seek(start); text=f.read()
            if start>0:
                nl=text.find("\n"); text=text[nl+1:] if nl>=0 else text
            rows=parse_maa_records(text)
            recent=[]
            for r in rows:
                if r.get("callback")=="TaskChainStart" and r.get("time") and r["time"]>=cutoff:
                    recent.append(r.get("raw",""))
            if recent:
                process_log_text(label_for_log_path(path),path,"\n".join(recent),backfill_only=True); total+=len(recent)
        except Exception as e:
            log(f"Schedule backfill failed for {path}: {e}")
    log(f"Backfilled recent scheduled-start evidence from {total} TaskChainStart record(s).")


def check_logs():
    if not cfg.get("enable_log_alert",True):
        return
    labels_seen=set()
    for path in existing_logs():
        label=label_for_log_path(path); labels_seen.add(label)
        try: text=read_new_log_text(path)
        except Exception as e:
            decision_log({"label":label,"path":str(path),"decision":"read_failed","error":str(e)}); continue
        if text.strip():
            process_log_text(label,path,text,backfill_only=False)
        else:
            evaluate_pending_issues(label)
    # Pending state still needs to age even if a log file disappears entirely.
    for state_file in GENERIC_ISSUE_DIR.glob("pending_*.json"):
        label=state_file.stem[len("pending_"):]
        if label not in labels_seen:
            evaluate_pending_issues(label)

def agent_started_datetime():
    try:
        ts=float(agent_start_time_path().read_text(encoding="utf-8").strip())
        return datetime.fromtimestamp(ts)
    except Exception:
        return datetime.now()


def schedule_monitoring_coverage_start():
    agent_start=agent_started_datetime()
    try:
        boot_ready=datetime.fromtimestamp(psutil.boot_time())+timedelta(minutes=float(cfg.get("startup_process_grace_minutes",10)))
    except Exception:
        boot_ready=agent_start
    return max(agent_start,boot_ready)


def scheduled_alert_state_path(label, scheduled_dt):
    key_src=occurrence_key(label,scheduled_dt)
    key=hashlib.sha1(key_src.encode("utf-8",errors="ignore")).hexdigest()
    return STATE_DIR/f"scheduled_alert_{key}.txt"


def scheduled_alert_already_sent(label,scheduled_dt):
    return scheduled_alert_state_path(label,scheduled_dt).exists()


def mark_scheduled_alert_sent(label,scheduled_dt):
    scheduled_alert_state_path(label,scheduled_dt).write_text(str(time.time()),encoding="utf-8")


def _record_schedule_metadata(meta, skipped=None, unverified=None, expected_rows=None, valid_rows=None, now_dt=None):
    data=load_daily_stats(); sch=data.setdefault("schedule",{}); sch["sources"]=meta or {}
    if skipped is not None:
        sch["skipped_overrides"]=skipped[-50:]
    if unverified is not None:
        existing=sch.setdefault("unverified",[])
        for item in unverified:
            if item not in existing: existing.append(item)
        sch["unverified"]=existing[-50:]
    if expected_rows is not None:
        ledger=sch.setdefault("expected_occurrences",[])
        if valid_rows is not None:
            now_dt=now_dt or datetime.now(); today=now_dt.strftime("%Y-%m-%d")
            valid={f"{row['label']}|{row['datetime']:%Y-%m-%d %H:%M}" for row in valid_rows if not row.get("skipped")}
            observed=set()
            for label, values in data.get("observed_runs",{}).items():
                for item in values:
                    occurrence=_observed_occurrence_text(item)
                    if occurrence:
                        observed.add(f"{label}|{occurrence}")
            reconciled=[]
            for item in ledger:
                text=str(item)
                try:
                    label, occurrence=text.split("|",1)
                    scheduled_dt=datetime.strptime(occurrence,"%Y-%m-%d %H:%M")
                except Exception:
                    continue
                if scheduled_dt.strftime("%Y-%m-%d") != today:
                    continue
                if text in valid or text in observed or scheduled_alert_already_sent(label,scheduled_dt) or incident_is_open(f"schedule|{label}|{occurrence}"):
                    reconciled.append(text)
            ledger=reconciled
        for row in expected_rows:
            text=f"{row['label']}|{row['datetime']:%Y-%m-%d %H:%M}"
            if text not in ledger: ledger.append(text)
        sch["expected_occurrences"]=ledger[-80:]
    save_daily_stats(data)


def check_scheduled_runs():
    if not cfg.get("enable_scheduled_run_check",False):
        return
    refresh_schedule_cache()
    now_dt=datetime.now(); grace=float(cfg.get("scheduled_run_grace_minutes",20))
    day_start=now_dt.replace(hour=0,minute=0,second=0,microsecond=0)
    # A live timer edit must apply from today forward, never retroactively to
    # yesterday. The former two-hour lookback caused a new 22:15 timer to be
    # invented for the previous day and reported as missed.
    start=day_start; end=day_start+timedelta(days=1)-timedelta(microseconds=1)
    occurrences,meta=effective_occurrences(BASE_DIR,cfg,start,end)
    with_skipped,_=effective_occurrences(BASE_DIR,cfg,start,end,include_skipped=True)
    skipped=[]
    for row in with_skipped:
        if row.get("skipped"):
            skipped.append(f"{row['label']} {row['original_datetime']:%Y-%m-%d %H:%M}")
    # Resolve any already-open miss for an occurrence the user subsequently
    # marked as skipped/moved. Read the override ledger directly because a moved
    # target may lie outside the current monitoring window and therefore not
    # appear in effective_occurrences() yet.
    for ov in load_overrides(BASE_DIR,cfg).get("overrides",[]):
        action=str(ov.get("action","")).lower()
        if action not in {"skip","move"}:
            continue
        try:
            original=datetime.strptime(f"{ov.get('date')} {ov.get('original_time')}","%Y-%m-%d %H:%M")
        except Exception:
            continue
        label=str(ov.get("label", ""))
        if not label or original>now_dt:
            continue
        if resolve_incident(key=f"schedule|{label}|{original:%Y-%m-%d %H:%M}",
                            resolution="用户已为本次计划设置一次性例外"):
            add_recoverable_issue("schedule_override",f"{label} {original:%H:%M} 的旧漏跑状态已因一次性例外结案",label=label)
    coverage=schedule_monitoring_coverage_start(); data=load_daily_stats(); abnormal=[]; unverified=[]
    for row in occurrences:
        label=row["label"]; scheduled_dt=row["datetime"]
        if scheduled_dt>now_dt or (now_dt-scheduled_dt).total_seconds()/60 < grace:
            continue
        if is_observed_occurrence(label,scheduled_dt,data=data):
            continue
        occurrence_text=f"{label} {scheduled_dt:%Y-%m-%d %H:%M}"
        # If Agent was not alive/ready at that time, absence of evidence is not
        # evidence of a missed MAA run. Backfill still records a positive start.
        if scheduled_dt < coverage:
            unverified.append(occurrence_text)
            continue
        if scheduled_alert_already_sent(label,scheduled_dt):
            continue
        abnormal.append(row)
    expected_rows=[row for row in occurrences if row["datetime"]<=now_dt+timedelta(minutes=1)]
    _record_schedule_metadata(meta,skipped=skipped,unverified=unverified,expected_rows=expected_rows,
                              valid_rows=with_skipped,now_dt=now_dt)
    if not abnormal:
        return
    details=[]
    for row in abnormal:
        label=row["label"]; scheduled_dt=row["datetime"]
        details.append(f"{label} {scheduled_dt:%H:%M}")
    body=("发生了什么：以下计划到现在仍没有观察到实际启动："+"；".join(details)+"。\n"
          "大概率原因：计划没有触发、MAA/MuMu 已退出，或 MAA 当时正忙。\n"
          "你要做什么：如果不是你有意跳过，现在值得远程看一眼。")
    labels=[]
    for row in abnormal:
        label=row["label"]; scheduled_dt=row["datetime"]; labels.append(label)
        key=f"schedule|{label}|{scheduled_dt:%Y-%m-%d %H:%M}"
        add_incident("scheduled_run_missing",f"{label} {scheduled_dt:%H:%M} 未观测到实际启动",label=label,likely="计划未触发、MAA/MuMu退出或定时被运行中任务阻断",action="远程确认",key=key)
        mark_scheduled_alert_sent(label,scheduled_dt)
    labels=list(dict.fromkeys(labels)); add_daily_alert("计划任务疑似漏跑："+"、".join(labels),severity="critical")
    bark("计划任务可能没跑｜"+"、".join(labels),body,level="timeSensitive",alert_name="scheduled_run_check_"+"_".join(labels))

def windows_uptime_hours():
    try:
        return (time.time() - psutil.boot_time()) / 3600
    except Exception:
        return None


def agent_start_time_path():
    return STATE_DIR / "agent_start_time.txt"


def reset_agent_start_time():
    agent_start_time_path().write_text(str(time.time()), encoding="utf-8")

    reminder_path = STATE_DIR / "last_uptime_reminder.txt"
    try:
        if reminder_path.exists():
            reminder_path.unlink()
    except Exception:
        pass


def get_agent_start_time():
    path = agent_start_time_path()

    if not path.exists():
        reset_agent_start_time()
        return time.time()

    try:
        return float(path.read_text(encoding="utf-8").strip())
    except Exception:
        reset_agent_start_time()
        return time.time()


def agent_uptime_hours():
    return (time.time() - get_agent_start_time()) / 3600


def check_uptime_reminder():
    if not cfg.get("enable_uptime_reminder", False): return
    hours=agent_uptime_hours(); threshold=float(cfg.get("uptime_reminder_hours",168))
    if hours >= threshold: add_daily_event("uptime",f"Agent 已连续运行 {hours:.1f} 小时。",severity="info")

def deepseek_usage_path():
    return STATE_DIR / f"deepseek_usage_{today_str()}.txt"


def get_deepseek_usage_count():
    path = deepseek_usage_path()
    if not path.exists():
        return 0

    try:
        return int(path.read_text(encoding="utf-8").strip())
    except Exception:
        return 0


def increment_deepseek_usage_count():
    count = get_deepseek_usage_count() + 1
    deepseek_usage_path().write_text(str(count), encoding="utf-8")

    data = load_daily_stats()
    data["deepseek_calls"] = count
    save_daily_stats(data)


def abnormal_schedule_summary():
    alerts = load_daily_stats().get("alerts", [])
    abnormal = []
    for a in alerts:
        msg = a.get("message", "")
        if "计划运行疑似异常" in msg or "计划任务疑似漏跑" in msg or "未按计划" in msg or "卡住" in msg:
            abnormal.append(a)

    if not abnormal:
        return "计划运行：无异常"

    lines = ["计划运行异常："]
    for a in abnormal[-5:]:
        lines.append(f"- {a.get('time')} {a.get('message')}")
    return "\n".join(lines)


def daily_report_is_skipped_today():
    if not DAILY_REPORT_SKIP_FILE.exists(): return False
    try: return DAILY_REPORT_SKIP_FILE.read_text(encoding="utf-8").strip() == today_str()
    except Exception: return False


def daily_report_already_sent_today():
    try:
        return DAILY_REPORT_SENT_FILE.exists() and DAILY_REPORT_SENT_FILE.read_text(encoding="utf-8").strip() == today_str()
    except Exception:
        return False


def mark_daily_report_sent_today():
    DAILY_REPORT_SENT_FILE.write_text(today_str(), encoding="utf-8")


def daily_report_due(now_dt=None):
    if not cfg.get("enable_daily_report", True) or daily_report_is_skipped_today() or daily_report_already_sent_today():
        return False
    now_dt = now_dt or datetime.now()
    try:
        hour, minute = [int(x) for x in str(cfg.get("daily_report_time", "23:50")).split(":", 1)]
        target = now_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
    except Exception:
        return False
    # Use >= rather than exact HH:MM equality: a slow loop or restart at 23:51
    # must not silently lose the report for the whole day.
    return now_dt >= target


def cpu_average(cpu):
    n=int(cpu.get("samples",0) or 0); return round(float(cpu.get("sum_percent",0))/n,1) if n else None


def due_schedule_occurrences_today(now_dt=None):
    now_dt=now_dt or datetime.now(); start=now_dt.replace(hour=0,minute=0,second=0,microsecond=0)
    rows,_=effective_occurrences(BASE_DIR,cfg,start,now_dt)
    return [x for x in rows if x["datetime"]<=now_dt]


def expected_schedule_count_today(now_dt=None):
    now_dt=now_dt or datetime.now(); prefix=now_dt.strftime("%Y-%m-%d "); keys=set()
    for row in due_schedule_occurrences_today(now_dt): keys.add(f"{row['label']}|{row['datetime']:%Y-%m-%d %H:%M}")
    data=load_daily_stats()
    for item in data.get("schedule",{}).get("expected_occurrences",[]):
        if "|"+prefix in str(item):
            try:
                dt=datetime.strptime(str(item).split("|",1)[1],"%Y-%m-%d %H:%M")
                if dt<=now_dt: keys.add(str(item))
            except Exception: pass
    # If a scheduled start was positively matched (including from a short-lived
    # one-time timer that disappeared from MAA config), it is expected by
    # definition. This prevents nonsensical daily summaries such as 1/0.
    for label,values in data.get("observed_runs",{}).items():
        for observed in values:
            text=_observed_occurrence_text(observed)
            if not text or not text.startswith(prefix):
                continue
            try:
                dt=datetime.strptime(text,"%Y-%m-%d %H:%M")
                if dt<=now_dt: keys.add(f"{label}|{text}")
            except Exception:
                pass
    return len(keys)


def observed_schedule_count_today(data=None,now_dt=None):
    data=data or load_daily_stats(); now_dt=now_dt or datetime.now(); date_prefix=now_dt.strftime("%Y-%m-%d "); count=0
    for values in data.get("observed_runs",{}).values():
        for item in values:
            text=_observed_occurrence_text(item)
            if text and text.startswith(date_prefix): count+=1
    return count


def unverified_schedule_count_today(data=None):
    data=data or load_daily_stats(); prefix=today_str(); return sum(1 for x in data.get("schedule",{}).get("unverified",[]) if prefix in str(x))


def percentile(values,p=95):
    vals=[float(x) for x in values if isinstance(x,(int,float))]
    if not vals: return None
    vals.sort()
    if len(vals)==1: return round(vals[0],1)
    pos=(len(vals)-1)*(p/100.0); lo=int(pos); hi=min(lo+1,len(vals)-1); frac=pos-lo
    return round(vals[lo]*(1-frac)+vals[hi]*frac,1)


def recent_baseline_summary(days=7):
    vals={"ram":[],"maa":[],"mumu":[]}; used=0
    for path in sorted(STATE_DIR.glob("daily_*.json"),reverse=True):
        if path == daily_stats_path(): continue
        try:
            mem=json.loads(path.read_text(encoding="utf-8")).get("memory",{})
            for key,out in [("system_percent_max","ram"),("maa_max_mb","maa"),("mumu_max_mb","mumu")]:
                if isinstance(mem.get(key),(int,float)): vals[out].append(float(mem[key]))
            used += 1
            if used>=days: break
        except Exception: pass
    if not used: return "近7日基线：暂无足够历史"
    parts=[]
    if vals["ram"]: parts.append(f"内存峰值中位数 {statistics.median(vals['ram']):.1f}%")
    if vals["maa"]: parts.append(f"MAA峰值中位数 {statistics.median(vals['maa'])/1024:.1f}GB")
    if vals["mumu"]: parts.append(f"MuMu峰值中位数 {statistics.median(vals['mumu'])/1024:.1f}GB")
    return f"近{used}日基线："+"；".join(parts)


def local_daily_report_text():
    data=load_daily_stats(); mem=data.get("memory",{}); cpu=data.get("cpu",{}); incidents=open_incidents(data); recoverable=data.get("recoverable_issues",[]); disks=data.get("disks",{}); context=data.get("resource_context",{}); schedule=data.get("schedule",{}); notices=data.get("special_notices",[])
    expected=expected_schedule_count_today(); observed=observed_schedule_count_today(data); unverified=unverified_schedule_count_today(data)
    status="无需处理" if not incidents else "有需要处理/确认的异常"
    ram_p95=percentile(mem.get("percent_samples",[]),95); cpu_p95=percentile(cpu.get("percent_samples",[]),95); avg=cpu_average(cpu)
    lines=[f"总体：{status}",f"日期：{today_str()}",f"计划任务：已确认启动 {observed}/{expected}；无法核验 {unverified} 个；{abnormal_schedule_summary()}"]
    if schedule.get("skipped_overrides"): lines.append("本次例外："+"；".join(schedule.get("skipped_overrides",[])[-6:]))
    source_bits=[]
    for label,meta in sorted((schedule.get("sources") or {}).items()): source_bits.append(f"{label}={'MAA当前配置' if str(meta.get('source','')).startswith('maa_gui') else '备用时间表'}")
    if source_bits: lines.append("计划来源："+"；".join(source_bits))
    lines.append(f"系统内存：当前 {mem.get('system_percent_current')}%（已用 {(mem.get('system_used_current_mb') or 0)/1024:.1f}GB / 可用 {(mem.get('system_available_current_mb') or 0)/1024:.1f}GB），P95 {ram_p95 if ram_p95 is not None else '未知'}%，峰值 {mem.get('system_percent_max')}%（峰值已用 {(mem.get('system_used_max_mb') or 0)/1024:.1f}GB），最低可用 {(mem.get('system_available_min_mb') or 0)/1024:.1f}GB；高占用累计 {mem.get('pressure_minutes',0)} 分钟，其中已知重负载可解释 {mem.get('expected_external_pressure_minutes',0)} 分钟")
    lines.append(f"CPU：均值 {avg if avg is not None else '未知'}%，P95 {cpu_p95 if cpu_p95 is not None else '未知'}%，峰值 {cpu.get('max_percent')}%，高负载 {cpu.get('high_minutes',0)} 分钟")
    lines.append(f"MAA：当前 {(mem.get('maa_current_mb') or 0)/1024:.1f}GB，峰值 {(mem.get('maa_max_mb') or 0)/1024:.1f}GB；MuMu：当前 {(mem.get('mumu_current_mb') or 0)/1024:.1f}GB，峰值 {(mem.get('mumu_max_mb') or 0)/1024:.1f}GB")
    if disks: lines.append("磁盘："+"；".join(f"{k} 空闲 {v.get('free_current_gb')}GB（今日最低 {v.get('free_min_gb')}GB）" for k,v in disks.items()))
    if context.get("expected_heavy_processes"): lines.append("已知重负载程序："+"、".join(context["expected_heavy_processes"]))
    lines.append("当前内存大户："+format_top_processes(context.get("top_memory_processes",[]),5)); lines.append(recent_baseline_summary()); lines.append(f"自动恢复/低价值异常：{len(recoverable)} 类，共 {recoverable_issue_total(data)} 次（聚合计数，不逐条建议）")
    if notices:
        lines.append("额外观察："+"；".join(f"{x.get('label','')} {x.get('message','')}".strip() for x in notices[-6:]))
    if incidents:
        lines.append("仍未解决：")
        for x in incidents[-8:]: lines.append(f"- {x.get('label','')} {x.get('summary')}")
    else: lines.append("仍未解决：无")
    lines.append(f"Bark：成功 {data.get('bark_sent',0)}，失败 {data.get('bark_failed',0)}，入队 {data.get('bark_queued',0)}")
    return "\n".join(lines)


def compact_daily_report_for_bark(local_report=None, ai_report=None):
    data=load_daily_stats(); mem=data.get("memory",{}); cpu=data.get("cpu",{}); incidents=open_incidents(data); recoverable=data.get("recoverable_issues",[]); notices=data.get("special_notices",[]); avg=cpu_average(cpu); ram_p95=percentile(mem.get("percent_samples",[]),95); cpu_p95=percentile(cpu.get("percent_samples",[]),95)
    expected=expected_schedule_count_today(); observed=observed_schedule_count_today(data); unverified=unverified_schedule_count_today(data)
    lines=[]
    if ai_report: lines += [ai_report.strip(),""]
    schedule_text=f"计划：确认启动 {observed}/{expected}"
    if unverified: schedule_text+=f"｜{unverified} 个时段无法核验"
    lines += ["关键数据",schedule_text,f"内存：P95 {ram_p95 if ram_p95 is not None else '未知'}%｜峰值 {mem.get('system_percent_max')}%（已用 {(mem.get('system_used_max_mb') or 0)/1024:.1f}GB）｜最低可用 {(mem.get('system_available_min_mb') or 0)/1024:.1f}GB",f"CPU：均值 {avg if avg is not None else '未知'}%｜P95 {cpu_p95 if cpu_p95 is not None else '未知'}%｜峰值 {cpu.get('max_percent')}%",f"MAA峰值 {(mem.get('maa_max_mb') or 0)/1024:.1f}GB｜MuMu峰值 {(mem.get('mumu_max_mb') or 0)/1024:.1f}GB",f"自动恢复/低价值异常 {len(recoverable)} 类/{recoverable_issue_total(data)} 次｜未解决 {len(incidents)} 条"]
    if notices:
        lines.append("额外观察："+"；".join(f"{x.get('label','')} {x.get('message','')}".strip() for x in notices[-4:]))
    low=[]; threshold=float(cfg.get("disk_low_free_gb",25))
    for drive,rec in data.get("disks",{}).items():
        if (rec.get("free_current_gb") or 999999)<threshold: low.append(f"{drive} 仅余 {rec.get('free_current_gb')}GB")
    if low: lines.append("磁盘："+"；".join(low))
    if incidents:
        lines += ["","需要处理/确认"]
        for x in incidents[-4:]: lines.append(f"- {x.get('label','')} {x.get('summary')}")
    elif not ai_report: lines += ["","结论：今天没有需要你处理的异常。"]
    return "\n".join(lines)


def deepseek_daily_worth_calling(data=None):
    data=data or load_daily_stats()
    if open_incidents(data): return True
    mem=data.get("memory",{}); cpu=data.get("cpu",{})
    if float(mem.get("pressure_minutes",0) or 0)>=float(cfg.get("deepseek_resource_pressure_minutes",30)): return True
    if float(cpu.get("high_minutes",0) or 0)>=float(cfg.get("deepseek_cpu_high_minutes",60)): return True
    return False


def deepseek_daily_report(local_report):
    if not cfg.get("use_deepseek",False) or not cfg.get("deepseek_daily_report",True) or daily_report_is_skipped_today(): return None
    if cfg.get("deepseek_daily_when_useful_only",True) and not deepseek_daily_worth_calling():
        add_daily_event("deepseek","今天没有需要 AI 二次判断的异常，未调用 DeepSeek。",severity="info")
        return None
    if get_deepseek_usage_count() >= int(cfg.get("deepseek_max_calls_per_day",1)): return None
    api_key=cfg.get("deepseek_api_key","").strip()
    if not api_key or api_key=="这里填你的DeepSeek API Key" or OpenAI is None: return None
    try:
        client=OpenAI(api_key=api_key,base_url="https://api.deepseek.com"); report=trim_utf8_bytes(local_report,int(cfg.get("deepseek_daily_input_max_bytes",9000)))
        prompt=f"""你负责一台 Windows 挂机备用机的每日运维摘要。只在已有本地规则发现值得判断的异常时提供二次分析。\n\n只输出4行中文纯文本，每行以【结论】【资源趋势】【运行质量】【建议】开头，总计不超过420汉字。不要复述一次性 SubTaskError、已恢复错误、内部回调名或日志路径。计划任务的‘无法核验’表示当时缺少可靠监控证据，不能判为漏跑。证据不足不要编原因；没有动作就写无需处理。\n\n本地统计：\n{report}"""
        kwargs=dict(model=cfg.get("deepseek_model","deepseek-v4-flash"),messages=[{"role":"system","content":"只分析真正需要人决策的挂机机异常，不制造建议。"},{"role":"user","content":prompt}],max_tokens=420,temperature=0)
        try: resp=client.chat.completions.create(**kwargs,extra_body={"thinking":{"type":"disabled"}})
        except Exception: resp=client.chat.completions.create(**kwargs)
        increment_deepseek_usage_count(); return trim_utf8_bytes(resp.choices[0].message.content.strip(),int(cfg.get("deepseek_daily_output_max_bytes",1800)))
    except Exception as e:
        log(f"DeepSeek daily report failed: {e}"); add_daily_event("deepseek",f"DeepSeek 日报失败：{e}",severity="warning"); return None


def daily_report():
    if not cfg.get("enable_daily_report",True): return False
    if daily_report_is_skipped_today(): log("Daily report skipped for today by user switch."); return False
    if daily_report_already_sent_today(): return False
    # Force one schedule evaluation just before the report so the schedule-source
    # metadata and any due misses are current.
    check_scheduled_runs()
    local_report=local_daily_report_text(); ai_report=deepseek_daily_report(local_report); body=compact_daily_report_for_bark(local_report,ai_report)
    data=load_daily_stats(); incidents=open_incidents(data)
    title="MAA 日报｜需要看一下" if incidents else "MAA 日报｜今天正常"
    bark_force(title,body,level="passive")
    # bark_force queues transient failures, so one report attempt per calendar day
    # is enough; otherwise a restart could duplicate both Bark and DeepSeek calls.
    mark_daily_report_sent_today()
    return True

def time_is(target):
    return datetime.now().strftime("%H:%M") == target


def windows_system_http_proxy():
    forced=str(cfg.get("proxy_healthcheck_proxy_url","") or "").strip()
    if forced:
        return forced
    if os.name!="nt" or str(cfg.get("proxy_healthcheck_mode","auto")).lower()=="off":
        return None
    try:
        import winreg
        key=winreg.OpenKey(winreg.HKEY_CURRENT_USER,r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
        enabled=int(winreg.QueryValueEx(key,"ProxyEnable")[0])
        if not enabled:
            return None
        raw=str(winreg.QueryValueEx(key,"ProxyServer")[0] or "").strip()
        if not raw:
            return None
        # WinINET accepts either host:port or protocol-specific entries such as
        # http=127.0.0.1:7890;https=127.0.0.1:7890.
        selected_kind=None
        if ";" in raw or "=" in raw:
            parts={}
            for piece in raw.split(";"):
                if "=" in piece:
                    k,v=piece.split("=",1); parts[k.strip().lower()]=v.strip()
            for kind in ("https","http","socks"):
                if parts.get(kind):
                    selected_kind=kind; raw=parts[kind]; break
            else:
                raw=""
        if not raw:
            return None
        if selected_kind=="socks" or raw.lower().startswith("socks"):
            # requests needs PySocks for SOCKS. Silently skip rather than creating
            # a false outage alarm on a configuration we cannot test reliably.
            return None
        if "://" not in raw:
            raw="http://"+raw
        return raw
    except Exception:
        return None


def check_proxy_health():
    if not cfg.get("enable_proxy_healthcheck",True):
        return
    proxy=windows_system_http_proxy()
    if not proxy:
        return
    state={}
    try:
        if NETWORK_HEALTH_FILE.exists(): state=json.loads(NETWORK_HEALTH_FILE.read_text(encoding="utf-8"))
    except Exception: state={}
    interval=max(1.0,float(cfg.get("proxy_healthcheck_interval_minutes",2))); now_ts=time.time()
    if now_ts-float(state.get("last_check",0) or 0)<interval*60:
        return
    state["last_check"]=now_ts; url=str(cfg.get("proxy_healthcheck_url","https://www.google.com/generate_204"))
    ok=False; error=None; status=None
    try:
        r=requests.get(url,proxies={"http":proxy,"https":proxy},timeout=float(cfg.get("proxy_healthcheck_timeout_seconds",12)),allow_redirects=False)
        status=int(r.status_code)
        # A probe that is blocked/rejected (4xx) is not healthy for this use case;
        # 2xx/3xx proves the proxy can reach the configured external endpoint.
        ok=200 <= status < 400
    except Exception as e:
        error=str(e)
    if ok:
        was_alerted=bool(state.get("alerted")); state.update({"failure_since":None,"alerted":False,"last_ok":now_ts,"last_status":status,"last_error":None,"proxy":proxy})
        NETWORK_HEALTH_FILE.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding="utf-8")
        if was_alerted and resolve_incident(key="network|proxy",resolution="系统代理外网探针已恢复"):
            add_recoverable_issue("proxy_recovered","系统代理外网探针已恢复")
        return
    state["failure_since"]=state.get("failure_since") or now_ts; state["last_error"]=(error or f"HTTP {status}")[:400]; state["proxy"]=proxy
    failed_for=(now_ts-float(state["failure_since"]))/60
    sustain=float(cfg.get("proxy_failure_sustain_minutes",10))
    if failed_for>=sustain and not state.get("alerted"):
        state["alerted"]=True
        body=("发生了什么：系统代理已经无法正常访问外网。\n"
              "大概率原因：代理客户端退出、节点失效、本地代理端口失联，或外网出口异常。\n"
              "你要做什么：如果这台机本来就应该走代理，现在值得远程检查代理客户端。")
        add_incident("proxy_health","系统代理外网出口异常",likely="代理客户端/节点/出口异常",action="检查代理客户端",key="network|proxy")
        add_daily_alert("系统代理外网探针持续失败",severity="warning")
        bark("备用机代理/外网出口异常",body,level="timeSensitive",alert_name="proxy_health_failure")
    NETWORK_HEALTH_FILE.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding="utf-8")


def send_external_heartbeat_if_due():
    url=str(cfg.get("heartbeat_url","") or "").strip()
    if not url:
        return
    interval=max(1.0,float(cfg.get("heartbeat_interval_minutes",5)))
    state={}
    try:
        if HEARTBEAT_STATE_FILE.exists(): state=json.loads(HEARTBEAT_STATE_FILE.read_text(encoding="utf-8"))
    except Exception: state={}
    now_ts=time.time(); last=float(state.get("last_attempt",0) or 0)
    if now_ts-last<interval*60:
        return
    state["last_attempt"]=now_ts
    try:
        r=requests.get(url,timeout=float(cfg.get("heartbeat_timeout_seconds",10)))
        status=int(r.status_code); state["last_status"]=status
        if 200 <= status < 400:
            state["last_ok"]=now_ts; state["last_error"]=None
        else:
            state["last_error"]=f"HTTP {status}"
    except Exception as e:
        # Deliberately no Bark here: if the entire host/network is dying, local
        # notification is the wrong detector. The external heartbeat service is
        # expected to alert on missing pings.
        state["last_error"]=str(e)[:300]
        log(f"External heartbeat ping failed: {e}")
    HEARTBEAT_STATE_FILE.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding="utf-8")


def execute_diagnostic_commands():
    templates={
        "bark_transport": ("【演习】Bark 通道测试","这是一条由管理器触发的通道测试。收到它说明 Bark 网络、密钥和手机通知链路可用。","active"),
        "memory": ("【演习】系统内存持续吃紧","模拟信号：系统内存达到报警条件。真实运行时这里会附带占用率、已用/可用内存与内存大户。","timeSensitive"),
        "disk": ("【演习】磁盘空间很低","模拟信号：监控磁盘达到剩余空间阈值。真实运行时这里会给出盘符和剩余容量。","timeSensitive"),
        "proxy": ("【演习】代理/外网出口异常","模拟信号：外网探针持续失败。真实运行时会包含代理模式、持续时间和错误摘要。","timeSensitive"),
        "hung": ("【演习】MAA/MuMu 持续未响应","模拟信号：窗口持续未响应。真实运行时会包含窗口标题、PID 与持续时间。","critical"),
        "schedule": ("【演习】计划任务可能没跑","模拟信号：计划在宽限期后仍无启动证据。真实运行时会包含实例、计划时间与证据来源。","timeSensitive"),
        "agent_error": ("【演习】MAA Agent 自身异常","模拟信号：Agent 主循环发生异常但仍会继续监控。","critical"),
        "news": ("【演习】官网维护公告","模拟信号：检测到一条新的闪断更新/停机维护公告；真实运行时会附带窗口、原文链接和日程操作。","timeSensitive"),
    }
    for command in consume_diagnostic_commands(BASE_DIR):
        try:
            kind=str(command.get("kind"))
            target=str(command.get("target") or "MAA1")
            if kind == "maa_missing":
                expected=set((cfg.get("maa_instance_dirs") or {}).keys())
                if target not in expected:
                    raise ValueError(f"未知实例：{target}")
                sustain=float(cfg.get("process_missing_sustain_minutes",3))
                state=load_process_health_state()
                rec=state.setdefault("missing",{}).setdefault(f"instance:{target}",{})
                rec.update({"since":time.time()-sustain*60-2,"alerted":False,"diagnostic":True})
                save_process_health_state(state)
                DEBUG_OVERRIDE_FILE.write_text(json.dumps({"forced_missing":[target],"until":time.time()+max(90,float(cfg.get("check_interval_seconds",60))*2+10)},ensure_ascii=False),encoding="utf-8")
                check_processes()
                message=f"已把 {target} 演习信号送入真实进程检测器并立即执行报警链路。"
            elif kind == "all":
                for key,(title,body,level) in templates.items():
                    if key != "bark_transport":
                        bark_force(title,body,level=level)
                message="已依次发送所有非破坏性通知演习。"
            elif kind in templates:
                title,body,level=templates[kind]
                bark_force(title,body,level=level)
                add_daily_event("diagnostic",f"通知演习：{kind}",severity="info")
                message=f"已通过正式 Bark 发送路径执行 {kind} 演习。"
            else:
                raise ValueError(f"未知演习类型：{kind}")
            save_diagnostic_result(BASE_DIR,command,True,message)
            log(f"Diagnostic completed: {kind} {target} - {message}")
        except Exception as exc:
            save_diagnostic_result(BASE_DIR,command,False,str(exc))
            log(f"Diagnostic failed: {command.get('kind')} - {exc}")


def check_official_news():
    if not cfg.get("enable_news_monitor",True):
        return
    try:
        result=poll_news(BASE_DIR,cfg)
        status=result.get("status")
        if status == "baseline":
            log(f"Official news baseline established with {result.get('count',0)} item(s).")
        elif status == "ok":
            add_daily_event("news",f"官网公告检查：新增 {result.get('new_count',0)}，维护相关 {len(result.get('events',[]))}",severity="info")
            for event in result.get("events",[]):
                window="未能可靠解析维护时间"
                if event.get("start") and event.get("end"):
                    start=datetime.fromisoformat(event["start"]); end=datetime.fromisoformat(event["end"])
                    window=f"{start:%m-%d %H:%M} 至 {end:%m-%d %H:%M}"
                changes=event.get("changes") or []
                action="；".join(changes) if changes else "没有命中该时段内的 MAA 计划，因此未修改监督日程"
                body=(f"公告：{event.get('title')}\n维护窗口：{window}\n"
                      f"管理器操作：{action}\n原文：{event.get('url')}\n"
                      f"摘要：{event.get('brief') or '无'}")
                bark("明日方舟官网维护公告",body,level="timeSensitive",alert_name=f"official_news_{event.get('cid')}")
    except Exception as exc:
        log(f"Official news check failed: {exc}")
        add_daily_event("news",f"官网公告检查失败：{exc}",severity="warning")


def app_is_running(path_text):
    try:
        target=os.path.normcase(os.path.abspath(str(path_text)))
        for proc in psutil.process_iter(["exe"]):
            try:
                if proc.info.get("exe") and os.path.normcase(os.path.abspath(proc.info["exe"])) == target:
                    return True
            except (psutil.Error,OSError):
                continue
    except Exception:
        pass
    return False


def auto_start_configured_apps():
    enabled=cfg.get("auto_start_apps_with_agent") or {}
    paths=cfg.get("launch_paths") or {}
    for label,wanted in enabled.items():
        path=Path(str(paths.get(label) or ""))
        if not wanted or not path.is_file() or app_is_running(path):
            continue
        try:
            subprocess.Popen([str(path)],cwd=str(path.parent),creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0),close_fds=True)
            log(f"Auto-started configured app: {label} ({path})")
            add_daily_event("process",f"启动 Agent 时自动启动 {label}",severity="info")
        except Exception as exc:
            log(f"Auto-start {label} failed: {exc}")
            add_daily_event("process",f"自动启动 {label} 失败：{exc}",severity="warning")


def startup_status_text():
    """Build a short user-facing snapshot; monitoring policy stays out of Bark."""
    try:
        maa = find_processes(cfg.get("maa_process_names", ["MAA.exe"]))
        found, unknown = detect_maa_instance_processes()
        expected = list((cfg.get("maa_instance_dirs") or {}).keys())
        if expected and not unknown:
            bits = [f"{label}{'运行' if found.get(label) else '未检测到'}" for label in expected]
            maa_text = " / ".join(bits)
        else:
            expected_count = max(2, len(expected) or 2)
            maa_text = f"{len(maa)}/{expected_count} 个进程"
    except Exception:
        maa_text = "状态读取失败"

    try:
        mumu_count = len(find_processes(cfg.get("mumu_process_names", [])))
        mumu_text = "已运行" if mumu_count else "未检测到"
    except Exception:
        mumu_text = "状态读取失败"

    try:
        mem = psutil.virtual_memory()
        mem_text = f"{mem.percent:.0f}%（已用 {(mem.total-mem.available)/1024/1024/1024:.1f} GB / 可用 {mem.available/1024/1024/1024:.1f} GB）"
    except Exception:
        mem_text = "状态读取失败"

    if not cfg.get("enable_proxy_healthcheck", True):
        proxy_text = "健康检查已关闭"
    else:
        try:
            proxy_text = "已检测到系统代理" if windows_system_http_proxy() else "未检测到系统 HTTP 代理"
        except Exception:
            proxy_text = "状态读取失败"

    return (f"时间：{now_str()}\n"
            "初始状态：\n"
            f"MAA：{maa_text}\n"
            f"MuMu：{mumu_text}\n"
            f"内存：{mem_text}\n"
            f"系统代理：{proxy_text}")


def main():
    global cfg

    singleton = AgentMutex()
    if not singleton.acquire():
        log("Start ignored: the MAA Agent mutex is already held.")
        return

    other = find_other_maa_agent_process()
    if other is not None:
        log(f"Start ignored: verified legacy Agent PID={other['pid']} script={other['script']}")
        singleton.release()
        return

    stopped_by_request = False
    initialized = False

    try:
        clear_stop_request()
        write_pid()
        initialized = True
        reset_agent_start_time()
        archive_own_logs_if_needed()
        log("MAA Agent started.")
        reset_transient_session_state()
        auto_start_configured_apps()
        backfill_recent_schedule_starts()
        initialize_log_offsets_on_start()

        if not cfg.get("quiet_startup", True):
            bark_force("MAA Agent 已启动", startup_status_text(), level="active")

        last_daily_reboot_date = ""

        while True:
            if stop_requested():
                stopped_by_request = True
                log("Stop requested. Exiting main loop.")
                break

            try:
                cfg.update(load_config())

                archive_own_logs_if_needed()
                process_bark_queue()
                execute_diagnostic_commands()
                send_external_heartbeat_if_due()
                check_proxy_health()
                check_official_news()

                check_processes()
                check_hung_windows()
                sample_resource_history(BASE_DIR,cfg)
                check_memory()
                check_logs()
                check_scheduled_runs()
                check_uptime_reminder()

                today = today_str()

                if daily_report_due():
                    daily_report()

                if cfg.get("enable_daily_reboot", False):
                    if time_is(cfg.get("daily_reboot_time", "04:30")) and last_daily_reboot_date != today:
                        last_daily_reboot_date = today
                        reboot_windows("每日计划重启")

                if resolve_incident(key="agent|self",resolution="后续主循环运行正常"):
                    add_recoverable_issue("agent_recovered","MAA Agent 自身异常后已恢复")

            except Exception as e:
                log(f"Agent main loop error: {e}")
                add_incident("agent_error","Agent 主循环出现异常",likely="代码/配置/文件或系统调用异常",action="检查 agent.log",key="agent|self")
                add_daily_alert(f"Agent 自身异常：{e}", severity="critical")
                bark(
                    "MAA Agent 自身异常",
                    f"Agent 自身遇到异常，但仍会继续尝试下一轮监控。\n你要做什么：如果后续仍反复出现，检查 {LOG_DIR / 'agent.log'}。",
                    level="critical",
                    alert_name="agent_error",
                )

            sleep_with_stop(int(cfg.get("check_interval_seconds", 60)))

    finally:
        if initialized:
            remove_pid()
            clear_stop_request()
            log("MAA Agent stopped.")

            if stopped_by_request and cfg.get("notify_on_stop", True):
                bark_force("MAA Agent 已停止", f"时间：{now_str()}\n这是你手动请求停止后的正常退出。", level="active")
        singleton.release()


if __name__ == "__main__":
    main()
