"""MAA Agent desktop manager.

The monitor remains a background Python process.  This Tk application is a
control and observability surface: status, lifecycle, schedules, timing policy,
configuration and logs. Normal minimize stays on the taskbar; the explicit
"hide to tray" action keeps it in the Windows notification area.
"""

from __future__ import annotations

import json
import locale
import os
import re
import shutil
import subprocess
import sys
import threading
import ctypes
import base64
import webbrowser
import hashlib
from datetime import date, datetime, timedelta
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import psutil

from agent_runtime import AgentMutex, agent_script_path, find_running_agents, is_script_for_installation, request_stop, validated_agent_for_dir
from schedule_utils import add_override, effective_occurrences, remove_override
from diagnostic_commands import enqueue as enqueue_diagnostic
from news_monitor import load_news_cache, refresh_news_cache
from gui_widgets import InlineCalendar, ScrollableFrame, ToggleSwitch
from resource_history import load_calendar_day as load_resource_calendar_day, memory_values, operational_date
from first_run_setup import configured_maa_labels, run_first_setup


APP_DIR = Path(__file__).resolve().parent
BASE_DIR = APP_DIR.parent
VERSION_PATH = BASE_DIR / "VERSION"
APP_VERSION = VERSION_PATH.read_text(encoding="utf-8").strip()
AUTHOR_NAME = "Taylor Yu"
AUTHOR_URL = "https://github.com/darkbrommd"
CONFIG_PATH = BASE_DIR / "config.json"
LOG_DIR = BASE_DIR / "logs"
STATE_DIR = BASE_DIR / "state"
ASSET_DIR = BASE_DIR / "assets"
ICON_PNG = ASSET_DIR / "app_icon.png"
ICON_ICO = ASSET_DIR / "app_icon.ico"
SHOW_REQUEST_FILE = STATE_DIR / "gui_show.request"
GUI_STATE_FILE = STATE_DIR / "gui_state.json"
SCHEDULE_RUN_STATE_FILE = STATE_DIR / "schedule_run_status.json"
SECRET_NAMES = {"bark_key", "deepseek_api_key"}
TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0)
NEW_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


CONFIG_HELP = {
    "process_missing_sustain_minutes": "MAA 连续消失达到这个时间才报警。Agent 自己重启不会让倒计时归零。",
    "startup_process_grace_minutes": "只在 Windows 刚开机时生效；不是每次重启 Agent 都宽限。",
    "expected_heavy_process_names": "帮助解释资源占用。Steam 启动器单独后台运行不会被当成正在游戏。",
    "news_maintenance_action": "supervision_only：只暂停该维护窗口内的漏跑报警，不直接改写 MAA 的实时配置。",
    "maa_gui_config_paths": "只读 MAA 的定时器配置，用来展示和核对计划；管理器不会在 MAA 运行时覆盖它。",
    "schedule_override_file": "本管理器的一次性监测例外。它改变报警预期，不会阻止 MAA 自己启动。",
    "quiet_startup": "开启后，Agent 正常启动时不发 Bark；事故通知不受影响。",
    "enable_proxy_healthcheck": "关闭后不读取系统代理，也不发起外网健康探测；适合临时停用 VPN/代理时使用。",
    "launch_paths": "GUI 一键启动及可选的随 Agent 启动路径。",
}


PALETTES = {
    "dark": {
        "bg": "#0b1118", "panel": "#17222e", "field": "#243342", "fg": "#f2f5f7",
        "muted": "#a8b7c6", "grid": "#3b4e60", "grid_soft": "#2b3d4d", "ram": "#ff6b81",
        "good": "#5bd38b", "bad": "#ff7b72", "accent": "#62a8ff", "tooltip": "#071019",
        "warning": "#ffb86b",
    },
    "light": {
        "bg": "#edf2f7", "panel": "#ffffff", "field": "#f7f9fb", "fg": "#17212b",
        "muted": "#5f6d7a", "grid": "#b8c6d3", "grid_soft": "#dce5ec", "ram": "#d7355f",
        "good": "#137333", "bad": "#b3261e", "accent": "#1769aa", "tooltip": "#ffffff",
        "warning": "#b45f06",
    },
}


def memory_text(total_mb: float, available_mb: float, percent: float) -> str:
    used_mb = max(0.0, float(total_mb) - float(available_mb))
    return f"{percent:.0f}%（已用 {used_mb / 1024:.1f} GB / 可用 {available_mb / 1024:.1f} GB，共 {total_mb / 1024:.1f} GB）"


def _mix_color(left: str, right: str, ratio: float) -> str:
    ratio = max(0.0, min(1.0, ratio))
    a = tuple(int(left[index:index + 2], 16) for index in (1, 3, 5))
    b = tuple(int(right[index:index + 2], 16) for index in (1, 3, 5))
    value = tuple(round(a[index] + (b[index] - a[index]) * ratio) for index in range(3))
    return "#%02x%02x%02x" % value


def time_of_day_color(when: datetime) -> str:
    hour = when.hour + when.minute / 60
    stops = ((0, "#17233f"), (6, "#d96c4f"), (10, "#f1c453"),
             (13, "#f4ead0"), (17, "#ed9a47"), (24, "#17233f"))
    for (start, start_color), (end, end_color) in zip(stops, stops[1:]):
        if start <= hour <= end:
            return _mix_color(start_color, end_color, (hour - start) / (end - start))
    return stops[-1][1]


def enable_hidpi_awareness() -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def set_windows_app_identity() -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MAA.Agent.Manager")
    except Exception:
        pass


def create_windows_shortcut(path: Path, *, startup: bool = False) -> None:
    """Create a .lnk through Windows' built-in WScript.Shell COM object."""
    path.parent.mkdir(parents=True, exist_ok=True)
    target = _pythonw()
    arguments = f'"{APP_DIR / "maa_agent_gui.py"}"' + (" --startup" if startup else "")
    def ps(value: str) -> str:
        return value.replace("'", "''")
    script = (
        "$w=New-Object -ComObject WScript.Shell;"
        f"$s=$w.CreateShortcut('{ps(str(path))}');"
        f"$s.TargetPath='{ps(str(target))}';"
        f"$s.Arguments='{ps(arguments)}';"
        f"$s.WorkingDirectory='{ps(str(BASE_DIR))}';"
        f"$s.IconLocation='{ps(str(ICON_ICO))},0';"
        "$s.Description='MAA Agent 管理器';$s.Save()"
    )
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        creationflags=NO_WINDOW,
        capture_output=True,
        timeout=20,
    )
    if completed.returncode:
        raise OSError(completed.stderr.decode(errors="replace") or "创建快捷方式失败")


def startup_marker_path() -> Path:
    # The existing elevated Scheduled Task points at a tiny compatibility VBS
    # under D:\MAA-Agent.  That forwarder checks this marker, so the GUI can
    # control autostart without requiring an administrator token.
    return STATE_DIR / "gui_autostart.enabled"


def startup_shortcut_path() -> Path:
    return Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "MAA Agent 管理器.lnk"


def filter_log_text(text: str, mode: str) -> str:
    if mode == "完整日志":
        return text
    keywords = (
        "error", "fail", "exception", "warning", "critical", "alert", "missing",
        "未响应", "缺失", "失败", "异常", "报警", "恢复", "维护公告", "diagnostic",
    )
    lines = [line for line in text.splitlines() if any(key in line.lower() for key in keywords)]
    return "\n".join(lines[-300:]) if lines else "（最近没有事故、异常或恢复记录）"


def load_gui_state() -> dict:
    try:
        value = json.loads(GUI_STATE_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def save_gui_state(value: dict) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        temp = GUI_STATE_FILE.with_suffix(".json.tmp")
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(GUI_STATE_FILE)
    except OSError:
        pass


def log_text_after(text: str, cutoff: datetime | None) -> str:
    if cutoff is None:
        return text
    kept = []
    for line in text.splitlines():
        match = re.match(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]", line)
        if not match:
            continue
        try:
            if datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S") > cutoff:
                kept.append(line)
        except ValueError:
            continue
    return "\n".join(kept)


def schedule_occurrence_key(label: str, when: datetime) -> str:
    return f"{label}|{when:%Y-%m-%d %H:%M}"


def load_schedule_run_records() -> dict[str, dict]:
    try:
        value=json.loads(SCHEDULE_RUN_STATE_FILE.read_text(encoding="utf-8"))
        runs=value.get("runs") if isinstance(value,dict) else None
        return runs if isinstance(runs,dict) else {}
    except Exception:
        return {}


def next_schedule_keys(rows: list[dict], now: datetime, run_records: dict[str, dict] | None = None) -> set[str]:
    run_records=run_records or {}
    candidates=[]
    for row in rows:
        when=row.get("datetime")
        if not isinstance(when,datetime) or when<=now or row.get("skipped"):
            continue
        key=schedule_occurrence_key(str(row.get("label","")),when)
        if (run_records.get(key) or {}).get("status") in {"running","completed","failed"}:
            continue
        candidates.append((when,key))
    if not candidates:
        return set()
    earliest=min(item[0] for item in candidates)
    return {key for when,key in candidates if when==earliest}


class MetricBar(tk.Canvas):
    """Small coloured progress bar with its value drawn inside the bar."""

    def __init__(self, master, *, palette: dict, fill: str, **kwargs):
        self.palette = palette
        self.fill = fill
        self.value = 0.0
        self.label = ""
        super().__init__(master, height=24, highlightthickness=0, **kwargs)
        self.bind("<Configure>", lambda _event: self.redraw())

    def set_palette(self, palette: dict):
        self.palette = palette
        self.redraw()

    def set_value(self, value: float, label: str):
        self.value = max(0.0, min(100.0, float(value)))
        self.label = label
        self.redraw()

    def redraw(self):
        width = max(20, self.winfo_width())
        height = max(18, self.winfo_height())
        self.delete("all")
        self.configure(background=self.palette["field"])
        self.create_rectangle(0, 0, width, height, fill=self.palette["field"], outline="")
        self.create_rectangle(0, 0, width * self.value / 100, height, fill=self.fill, outline="")
        text_color = "#ffffff" if self.palette["bg"] == PALETTES["dark"]["bg"] else self.palette["fg"]
        self.create_text(width / 2, height / 2, text=self.label, fill=text_color, font=("Microsoft YaHei UI", 9, "bold"))


class ResourceChart(tk.Canvas):
    def __init__(self, master, *, palette=None, on_open=None, **kwargs):
        self.palette = palette or PALETTES["dark"]
        super().__init__(master, background=self.palette["panel"], highlightthickness=0, **kwargs)
        self.rows: list[dict] = []
        self.day = date.today()
        self.threshold = 88.0
        self.on_open = on_open
        self.tooltip_bg = self.create_rectangle(0, 0, 0, 0, fill=self.palette["tooltip"], outline=self.palette["accent"], state="hidden")
        self.tooltip = self.create_text(0, 0, text="", fill=self.palette["fg"], anchor="nw", state="hidden", font=("Microsoft YaHei UI", 9))
        self.cursor_line = self.create_line(0, 0, 0, 0, fill=self.palette["accent"], dash=(3, 3), width=1, state="hidden")
        self.cursor_dot = self.create_oval(0, 0, 0, 0, fill=self.palette["accent"], outline=self.palette["fg"], width=1, state="hidden")
        self.plot_bounds = (48, 32, 382, 186)
        self.bind("<Configure>", lambda _e: self.redraw())
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda _e: self._hide_tooltip())
        self.bind("<Double-1>", self._open_nearest)

    def set_palette(self, palette: dict):
        self.palette = palette
        self.configure(background=palette["panel"])
        self.itemconfigure(self.tooltip_bg, fill=palette["tooltip"], outline=palette["accent"])
        self.itemconfigure(self.tooltip, fill=palette["fg"])
        self.itemconfigure(self.cursor_line, fill=palette["accent"])
        self.itemconfigure(self.cursor_dot, fill=palette["accent"], outline=palette["fg"])
        self.redraw()

    def set_data(self, rows: list[dict], day: date, threshold: float = 88):
        self.rows = rows
        self.day = day
        self.threshold = float(threshold)
        self.redraw()

    def _window(self):
        start = datetime.combine(self.day, datetime.min.time()).timestamp()
        return start, start + 24 * 3600

    def _hide_tooltip(self):
        self.itemconfigure(self.tooltip, state="hidden")
        self.itemconfigure(self.tooltip_bg, state="hidden")
        self.itemconfigure(self.cursor_line, state="hidden")
        self.itemconfigure(self.cursor_dot, state="hidden")

    def _nearest(self, x: float):
        if not self.rows:
            return None
        width = max(280, self.winfo_width()); left, right = 48, width - 18
        start, end = self._window()
        target = start + max(0, min(1, (x - left) / max(1, right - left))) * (end - start)
        return min(self.rows, key=lambda item: abs(float(item.get("epoch", 0)) - target))

    def redraw(self):
        self.delete("chart")
        width = max(280, self.winfo_width()); height = max(120, self.winfo_height())
        left, top, right, bottom = 48, 34, width - 18, height - 34
        self.plot_bounds = (left, top, right, bottom)
        for pct in range(0, 101, 25):
            y = bottom - (bottom - top) * pct / 100
            self.create_line(left, y, right, y, fill=self.palette["grid"], tags="chart")
            self.create_text(left - 8, y, text=str(pct), fill=self.palette["muted"], anchor="e", tags="chart", font=("Segoe UI", 8))
        for hour in range(0, 25, 4):
            x = left + (right - left) * hour / 24
            self.create_line(x, top, x, bottom, fill=self.palette["grid_soft"], tags="chart")
            self.create_text(x, bottom + 16, text=f"{hour:02d}:00", fill=self.palette["muted"], tags="chart", font=("Segoe UI", 8))
        threshold_y = bottom - (bottom - top) * max(0, min(100, self.threshold)) / 100
        self.create_line(left, threshold_y, right, threshold_y, fill=self.palette["warning"], dash=(5, 4), tags="chart")
        self.create_text(left + 5, threshold_y - 4, text=f"压力阈值 {self.threshold:.0f}%", fill=self.palette["warning"], anchor="sw", tags="chart", font=("Microsoft YaHei UI", 8))
        if not self.rows:
            self.create_text(width / 2, height / 2, text="这一天还没有采样数据", fill=self.palette["muted"], tags="chart", font=("Microsoft YaHei UI", 11))
            return
        start, end = self._window()
        def points(key):
            out=[]
            for row in self.rows:
                x=left+(right-left)*max(0,min(1,(float(row.get("epoch",start))-start)/(end-start)))
                y=bottom-(bottom-top)*max(0,min(100,float(row.get(key,0))))/100
                out.extend((x,y))
            return out
        ram=points("ram")
        if len(ram)>=4: self.create_line(*ram, fill=self.palette["ram"], width=2, smooth=True, tags="chart")
        abnormal_count = 0
        for row in self.rows:
            if not row.get("abnormal"):
                continue
            abnormal_count += 1
            x = left + (right-left) * max(0, min(1, (float(row.get("epoch", start))-start)/(end-start)))
            y = bottom - (bottom-top) * max(0, min(100, float(row.get("ram", 0)))) / 100
            self.create_oval(x-5, y-5, x+5, y+5, fill=self.palette["warning"], outline=self.palette["fg"], width=1, tags="chart")
        self.create_text(left, 12, text="内存", fill=self.palette["ram"], anchor="w", tags="chart")
        self.create_text(left + 62, 12, text=f"异常点 {abnormal_count}", fill=self.palette["warning"], anchor="w", tags="chart")
        self.tag_raise(self.cursor_line); self.tag_raise(self.cursor_dot)
        self.tag_raise(self.tooltip_bg); self.tag_raise(self.tooltip)

    def _motion(self, event):
        row = self._nearest(event.x)
        if not row:
            return
        width=max(280,self.winfo_width()); height=max(120,self.winfo_height())
        left, chart_top, right, chart_bottom = self.plot_bounds
        start, end = self._window()
        marker_x = left + (right-left) * max(0, min(1, (float(row.get("epoch", start))-start)/(end-start)))
        marker_y = chart_bottom - (chart_bottom-chart_top) * max(0, min(100, float(row.get("ram", 0)))) / 100
        self.coords(self.cursor_line, marker_x, chart_top, marker_x, chart_bottom)
        self.coords(self.cursor_dot, marker_x-5, marker_y-5, marker_x+5, marker_y+5)
        self.itemconfigure(self.cursor_line, state="normal")
        self.itemconfigure(self.cursor_dot, state="normal")
        dt=datetime.fromtimestamp(float(row.get("epoch",0)))
        used, available, _total = memory_values(row)
        top=row.get("top_apps") or row.get("top_processes") or []
        extra=("\n内存大户："+"、".join(f"{x.get('name')} {x.get('mb',0)/1024:.1f}GB" for x in top[:3])) if top else ""
        reasons = "；".join(row.get("abnormal_reasons") or [])
        reason_line = f"\n异常：{reasons}" if reasons else ""
        text = (f"{dt:%Y-%m-%d %H:%M:%S}\n"
                f"内存 {row.get('ram',0):.0f}% · 已用 {used/1024:.1f}GB · 可用 {available/1024:.1f}GB\n"
                f"Agent {row.get('agent_mb',0)/1024:.2f}GB · MAA {row.get('maa_mb',0)/1024:.1f}GB · MuMu {row.get('mumu_mb',0)/1024:.1f}GB{extra}{reason_line}")
        self.itemconfigure(self.tooltip,text=text,state="normal")
        self.coords(self.tooltip, event.x + 20, event.y + 16)
        bbox = self.bbox(self.tooltip)
        if bbox:
            x1, y1, x2, y2 = bbox
            dx = min(0, width - x2 - 10); dy = min(0, height - y2 - 10)
            self.move(self.tooltip, dx, dy); x1, y1, x2, y2 = self.bbox(self.tooltip)
            self.coords(self.tooltip_bg, x1-7, y1-5, x2+7, y2+5)
            self.itemconfigure(self.tooltip_bg,state="normal")
            self.tag_raise(self.cursor_line); self.tag_raise(self.cursor_dot)
            self.tag_raise(self.tooltip_bg); self.tag_raise(self.tooltip)

    def _open_nearest(self, event):
        row = self._nearest(event.x)
        if row and self.on_open:
            self.on_open(row)


TIMING_FIELDS = [
    ("检查间隔（秒）", "check_interval_seconds", "int"),
    ("开机宽限（分钟）", "startup_process_grace_minutes", "float"),
    ("进程缺失持续（分钟）", "process_missing_sustain_minutes", "float"),
    ("普通错误无进展（分钟）", "generic_error_stall_minutes", "float"),
    ("连接错误无进展（分钟）", "connection_error_stall_minutes", "float"),
    ("计划漏跑宽限（分钟）", "scheduled_run_grace_minutes", "float"),
    ("窗口未响应持续（分钟）", "hung_window_sustain_minutes", "float"),
    ("内存压力持续（分钟）", "memory_pressure_sustain_minutes", "float"),
    ("内存严重压力持续（分钟）", "memory_critical_sustain_minutes", "float"),
    ("代理探测间隔（分钟）", "proxy_healthcheck_interval_minutes", "float"),
    ("代理失败持续（分钟）", "proxy_failure_sustain_minutes", "float"),
    ("外部心跳间隔（分钟）", "heartbeat_interval_minutes", "float"),
]


def load_config() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("config.json 顶层必须是对象")
    return data


def atomic_save_config(data: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    backup = CONFIG_PATH.with_suffix(".json.bak")
    if CONFIG_PATH.exists():
        shutil.copy2(CONFIG_PATH, backup)
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # Parse our own output before replacing the live configuration.
    json.loads(tmp.read_text(encoding="utf-8"))
    tmp.replace(CONFIG_PATH)


def _same_path(left: str | Path, right: str | Path) -> bool:
    return os.path.normcase(str(Path(left).resolve())) == os.path.normcase(str(Path(right).resolve()))


def _pythonw() -> Path:
    bundled = BASE_DIR / ".venv" / "Scripts" / "pythonw.exe"
    if bundled.exists():
        return bundled
    return Path(sys.executable)


def start_agent_process() -> tuple[bool, str]:
    running = validated_agent_for_dir(BASE_DIR)
    if running:
        return False, f"Agent 已在运行（PID {running['pid']}）。"
    others = [row for row in find_running_agents() if not is_script_for_installation(row["script"], BASE_DIR)]
    if others:
        return False, f"检测到另一份 Agent：{others[0]['script']}（PID {others[0]['pid']}）。"
    try:
        subprocess.Popen(
            [str(_pythonw()), str(agent_script_path(BASE_DIR))],
            cwd=str(BASE_DIR),
            creationflags=NO_WINDOW | DETACHED | NEW_GROUP,
            close_fds=True,
        )
        return True, "启动请求已发送。"
    except OSError as exc:
        return False, f"启动失败：{exc}"


def tail_text(path: Path, max_lines: int = 300, max_bytes: int = 300_000) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            raw = handle.read()
        text = raw.decode("utf-8", errors="replace")
        return "\n".join(text.splitlines()[-max_lines:])
    except FileNotFoundError:
        return "（日志文件尚不存在）"
    except OSError as exc:
        return f"（读取失败：{exc}）"


class ManagerApp:
    def __init__(self):
        enable_hidpi_awareness()
        set_windows_app_identity()
        self.root = tk.Tk()
        self.root.title("MAA Agent 管理器")
        self.root.geometry("1220x820")
        self.root.minsize(1040, 700)
        self.cfg = load_config()
        self.gui_state = load_gui_state()
        self._dashboard_log_cleared_at = None
        self._apply_window_icon()
        self._apply_tk_scaling()
        self.config_dirty = False
        self.tray_icon = None
        self.tray_ready = False
        self.refresh_job = None
        self.schedule_rows: list[dict] = []
        self.schedule_day = datetime.now().date()
        self.schedule_range_start = self.schedule_day - timedelta(days=1)
        self.schedule_range_end = self.schedule_day + timedelta(days=1)
        self._schedule_signature = None
        self._dashboard_schedule_signature = None
        self._resource_signature = None
        self._schedule_rendering = False
        self._schedule_initial_position_pending = True
        self._schedule_extend_pending = False
        self._schedule_restore_day = None
        self._schedule_restoring = False
        self.schedule_calendar_dialog = None
        self.setting_switches: list[ToggleSwitch] = []
        self.proxy_check_var = tk.BooleanVar()
        self.resource_day = datetime.now().date()
        self.resource_rows: list[dict] = []
        self.timing_vars: dict[str, tk.StringVar] = {}
        self._build_style()
        self._build_ui()
        self.root.after(120, self._restore_dashboard_layout)
        self._apply_runtime_theme()
        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        self._setup_tray()
        self.refresh_all(full=True)
        if self.cfg.get("gui_start_agent_on_launch", True) and not validated_agent_for_dir(BASE_DIR):
            self.root.after(300, self.start_agent)
        if "--startup" in sys.argv and self.cfg.get("gui_start_minimized", True):
            self.root.after(900, self.hide_to_tray)

    def _apply_window_icon(self):
        try:
            if ICON_PNG.exists():
                self._icon_image = tk.PhotoImage(file=str(ICON_PNG))
                self.root.iconphoto(True, self._icon_image)
            if os.name == "nt" and ICON_ICO.exists():
                self.root.iconbitmap(default=str(ICON_ICO))
        except tk.TclError:
            pass

    def _apply_tk_scaling(self):
        try:
            self.root.update_idletasks()
            dpi = ctypes.windll.user32.GetDpiForWindow(self.root.winfo_id()) if os.name == "nt" else 96
            self.root.tk.call("tk", "scaling", max(1.0, float(dpi) / 72.0))
        except Exception:
            pass

    def _build_style(self):
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        self.style = style
        self.palette = PALETTES["dark" if self.cfg.get("gui_dark_mode", True) else "light"]
        palette = self.palette
        self.root.configure(background=palette["bg"])
        style.configure(".", background=palette["bg"], foreground=palette["fg"], fieldbackground=palette["field"])
        style.configure("TFrame", background=palette["bg"])
        style.configure("TLabelframe", background=palette["bg"], foreground=palette["fg"])
        style.configure("TLabelframe.Label", background=palette["bg"], foreground=palette["fg"])
        style.configure("TLabel", background=palette["bg"], foreground=palette["fg"])
        style.configure("TButton", background=palette["field"], foreground=palette["fg"], padding=(9, 5))
        style.map("TButton", background=[("active", palette["accent"])], foreground=[("active", "#ffffff")])
        style.configure(
            "TCombobox",
            fieldbackground=palette["field"],
            background=palette["field"],
            foreground=palette["fg"],
            arrowcolor=palette["fg"],
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", palette["field"])],
            foreground=[("readonly", palette["fg"])],
            selectbackground=[("readonly", palette["field"])],
            selectforeground=[("readonly", palette["fg"])],
        )
        style.configure("TCheckbutton", background=palette["bg"], foreground=palette["fg"])
        style.configure("TRadiobutton", background=palette["bg"], foreground=palette["fg"])
        style.configure("TNotebook", background=palette["bg"], borderwidth=0)
        style.configure("Activity.TLabelframe", background=palette["panel"])
        style.configure("Activity.TLabelframe.Label", background=palette["panel"], foreground=palette["fg"])
        style.configure("Activity.TFrame", background=palette["panel"])
        style.configure("Activity.TLabel", background=palette["panel"], foreground=palette["fg"])
        style.configure("Activity.TPanedwindow", background=palette["panel"])
        style.configure("TNotebook.Tab", background=palette["field"], foreground=palette["muted"], padding=(14, 8))
        style.map(
            "TNotebook.Tab",
            background=[("selected", palette["panel"])],
            foreground=[("selected", palette["fg"])],
            padding=[("selected", (14, 8)), ("!selected", (14, 8))],
            expand=[("selected", (0, 0, 0, 0))],
        )
        style.configure("Treeview", background=palette["panel"], fieldbackground=palette["panel"], foreground=palette["fg"], rowheight=28)
        style.configure("Treeview.Heading", background=palette["field"], foreground=palette["fg"])
        style.map("Treeview", background=[("selected", palette["accent"])], foreground=[("selected", "#ffffff")])
        style.configure("Horizontal.TProgressbar", troughcolor=palette["field"], background=palette["accent"])
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 18, "bold"))
        style.configure("Heading.TLabel", font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("DateTitle.TLabel", font=("Microsoft YaHei UI", 15, "bold"), foreground=palette["fg"])
        style.configure("Good.TLabel", foreground=palette["good"], font=("Microsoft YaHei UI", 12, "bold"))
        style.configure("Bad.TLabel", foreground=palette["bad"], font=("Microsoft YaHei UI", 12, "bold"))
        style.configure("Muted.TLabel", foreground=palette["muted"])
        style.configure("Link.TLabel", foreground=palette["accent"])

    def _build_ui(self):
        menubar = tk.Menu(self.root)
        app_menu = tk.Menu(menubar, tearoff=False)
        app_menu.add_command(label="启动 Agent", command=self.start_agent)
        app_menu.add_command(label="停止 Agent", command=self.stop_agent)
        app_menu.add_command(label="重启 Agent", command=self.restart_agent)
        app_menu.add_separator()
        app_menu.add_command(label="隐藏到托盘", command=self.hide_to_tray)
        app_menu.add_command(label="退出管理器（Agent 继续）", command=self.exit_gui)
        app_menu.add_command(label="退出 Agent 和管理器", command=self.exit_all)
        menubar.add_cascade(label="管理器", menu=app_menu)
        launch_menu = tk.Menu(menubar, tearoff=False)
        for label in (*configured_maa_labels(self.cfg), "MuMu"):
            launch_menu.add_command(label=f"启动 {label}", command=lambda x=label: self.launch_configured(x))
        menubar.add_cascade(label="启动程序", menu=launch_menu)
        drill_menu = tk.Menu(menubar, tearoff=False)
        for label in configured_maa_labels(self.cfg):
            drill_menu.add_command(label=f"{label} 未运行", command=lambda x=label: self.run_diagnostic("maa_missing", x))
        drill_menu.add_separator()
        for label,kind in (("Bark 通道","bark_transport"),("内存报警","memory"),("磁盘报警","disk"),("代理报警","proxy"),("窗口未响应","hung"),("计划漏跑","schedule"),("官网维护公告","news")):
            drill_menu.add_command(label=label, command=lambda x=kind: self.run_diagnostic(x))
        menubar.add_cascade(label="高级 → 通知测试", menu=drill_menu)
        self.root.configure(menu=menubar)

        self.header = tk.Canvas(self.root, height=70, highlightthickness=0)
        self.header.pack(fill="x", padx=14, pady=(12, 8))
        self.header.bind("<Configure>", lambda _event: self._draw_header_gradient())

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        self.dashboard = ttk.Frame(self.notebook, padding=14)
        self.schedule_tab = ttk.Frame(self.notebook, padding=14)
        self.timing_tab = ttk.Frame(self.notebook, padding=14)
        self.resource_tab = ttk.Frame(self.notebook, padding=14)
        self.news_tab = ttk.Frame(self.notebook, padding=14)
        self.settings_tab = ttk.Frame(self.notebook, padding=14)
        self.diagnostics_tab = ttk.Frame(self.notebook, padding=14)
        self.config_tab = ttk.Frame(self.notebook, padding=14)
        self.logs_tab = ttk.Frame(self.notebook, padding=14)
        self.notebook.add(self.dashboard, text=" 运行状态 ")
        self.notebook.add(self.schedule_tab, text=" 日程时间轴 ")
        self.notebook.add(self.resource_tab, text=" 资源历史 ")
        self.notebook.add(self.news_tab, text=" 官网公告 ")
        self.notebook.add(self.diagnostics_tab, text=" 通知测试 ")
        self.notebook.add(self.timing_tab, text=" 高级时间策略 ")
        self.notebook.add(self.config_tab, text=" 全部配置 ")
        self.notebook.add(self.logs_tab, text=" 日志 ")
        self.notebook.add(self.settings_tab, text=" 设置 ")
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        self._build_dashboard()
        self._build_schedule()
        self._build_resources()
        self._build_news()
        self._build_settings()
        self._build_diagnostics()
        self._build_timing()
        self._build_config()
        self._build_logs()

        self.footer_text = tk.StringVar(value="就绪")
        ttk.Label(self.root, textvariable=self.footer_text, style="Muted.TLabel", padding=(16, 0, 16, 8)).pack(fill="x")

    def _draw_header_gradient(self):
        if not hasattr(self, "header"):
            return
        width = max(1, self.header.winfo_width())
        height = max(1, self.header.winfo_height())
        hour = datetime.now().hour + datetime.now().minute / 60
        daylight = max(0.0, min(1.0, 1.0 - abs(hour - 12.0) / 8.0))
        if self.cfg.get("gui_dark_mode", True):
            left = (14 + int(68 * daylight), 25 + int(46 * daylight), 43 + int(20 * daylight))
            right = (22 + int(120 * daylight), 45 + int(84 * daylight), 70 + int(18 * daylight))
        else:
            left, right = (241, 153, 62), (248, 229, 176)
        self.header.delete("all")
        for index in range(64):
            ratio = index / 63
            color = tuple(round(left[i] + (right[i] - left[i]) * ratio) for i in range(3))
            self.header.create_rectangle(width * index / 64, 0, width * (index + 1) / 64 + 1, height, outline="", fill="#%02x%02x%02x" % color)
        self.header.create_text(18, height / 2, text="MAA Agent 管理器", anchor="w", fill="#ffffff", font=("Microsoft YaHei UI", 19, "bold"))
        self.header.create_text(width - 18, height / 2, text=str(BASE_DIR), anchor="e", fill="#f1f5f9", font=("Microsoft YaHei UI", 9))

    def _build_dashboard(self):
        top = ttk.Frame(self.dashboard)
        top.pack(fill="x")
        self.agent_status = ttk.Label(top, text="读取中…", style="Muted.TLabel")
        self.agent_status.pack(side="left")
        buttons = ttk.Frame(top)
        buttons.pack(side="right")
        ttk.Button(buttons, text="启动 Agent", command=self.start_agent).pack(side="left", padx=3)
        ttk.Button(buttons, text="停止 Agent", command=self.stop_agent).pack(side="left", padx=3)
        ttk.Button(buttons, text="重启 Agent", command=self.restart_agent).pack(side="left", padx=3)
        ttk.Button(buttons, text="立即刷新", command=self.refresh_all).pack(side="left", padx=3)

        cards = ttk.Frame(self.dashboard)
        cards.pack(fill="x", pady=(16, 10))
        for column in (0, 1, 2):
            cards.columnconfigure(column, weight=1)
        self.card_vars = {name: tk.StringVar(value="读取中…") for name in ("MAA", "MuMu", "资源")}
        for col, name in enumerate(("MAA", "MuMu", "资源")):
            frame = ttk.LabelFrame(cards, text=name, padding=12)
            frame.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 6, 0))
            ttk.Label(frame, textvariable=self.card_vars[name], justify="left").pack(anchor="w")
            if name == "资源":
                self.cpu_progress = MetricBar(frame, palette=self.palette, fill="#3182ce")
                self.cpu_progress.pack(fill="x", pady=(9, 2))
                self.memory_progress = MetricBar(frame, palette=self.palette, fill="#d84a68")
                self.memory_progress.pack(fill="x", pady=2)

        self.dashboard_info_panes = ttk.Panedwindow(self.dashboard, orient="horizontal")
        self.dashboard_info_panes.pack(fill="both", expand=True, pady=(6, 0))
        schedule_frame = ttk.LabelFrame(self.dashboard_info_panes, text="未来计划", padding=8)
        activity_frame = ttk.LabelFrame(self.dashboard_info_panes, text="最近事件与内存趋势", padding=8, style="Activity.TLabelframe")
        self.dashboard_info_panes.add(schedule_frame, weight=2)
        self.dashboard_info_panes.add(activity_frame, weight=1)
        self.next_schedule_cards = ScrollableFrame(schedule_frame, palette=self.palette)
        self.next_schedule_cards.pack(fill="both", expand=True)
        activity_bar = ttk.Frame(activity_frame, style="Activity.TFrame")
        activity_bar.pack(fill="x", pady=(0, 5))
        ttk.Label(activity_bar, text="显示", style="Activity.TLabel").pack(side="left")
        self.dashboard_log_mode = tk.StringVar(value="仅事故与恢复")
        ttk.Combobox(activity_bar, textvariable=self.dashboard_log_mode, values=("仅事故与恢复", "完整日志"), state="readonly", width=16).pack(side="left", padx=6)
        self.activity_expanded = True
        self.activity_toggle = ttk.Button(activity_bar, text="隐藏图表", width=9, command=self.toggle_dashboard_activity)
        self.activity_toggle.pack(side="right")
        ttk.Button(activity_bar, text="清空日志", width=9, command=self.clear_current_logs).pack(side="right", padx=(0, 6))
        self.dashboard_log_mode.trace_add("write", lambda *_: self.refresh_dashboard())
        self.activity_panes = ttk.Panedwindow(activity_frame, orient="vertical", style="Activity.TPanedwindow")
        self.activity_panes.pack(fill="both", expand=True)
        self.dashboard_chart_frame = ttk.Frame(self.activity_panes, style="Activity.TFrame")
        self.dashboard_log_frame = ttk.Frame(self.activity_panes, style="Activity.TFrame")
        self.dashboard_resource_chart = ResourceChart(self.dashboard_chart_frame, palette=self.palette, height=150, on_open=self.show_resource_detail)
        self.dashboard_resource_chart.pack(fill="both", expand=True)
        self.recent_log = tk.Text(self.dashboard_log_frame, height=8, wrap="word", state="disabled", font=("Consolas", 9))
        self.recent_log.pack(fill="both", expand=True)
        self.activity_panes.add(self.dashboard_chart_frame, weight=1)
        self.activity_panes.add(self.dashboard_log_frame, weight=2)
        self.dashboard_info_panes.bind("<ButtonRelease-1>", self._save_dashboard_layout, add="+")
        self.activity_panes.bind("<ButtonRelease-1>", self._save_dashboard_layout, add="+")

    def _build_schedule(self):
        nav = ttk.Frame(self.schedule_tab)
        nav.pack(fill="x", pady=(0, 8))
        nav.columnconfigure(0, weight=1, uniform="schedule-nav")
        nav.columnconfigure(1, weight=0)
        nav.columnconfigure(2, weight=1, uniform="schedule-nav")
        left_nav = ttk.Frame(nav)
        left_nav.grid(row=0, column=0, sticky="w")
        ttk.Button(left_nav, text="‹ 前一天", command=lambda: self.change_schedule_day(-1)).pack(side="left")
        ttk.Button(left_nav, text="今天", command=self.schedule_today).pack(side="left", padx=5)
        ttk.Button(left_nav, text="后一天 ›", command=lambda: self.change_schedule_day(1)).pack(side="left")
        self.schedule_day_var = tk.StringVar()
        self.schedule_day_title_var = tk.StringVar()
        date_picker = ttk.Frame(nav)
        date_picker.grid(row=0, column=1)
        self.schedule_day_title = ttk.Label(date_picker, textvariable=self.schedule_day_title_var, style="DateTitle.TLabel", cursor="hand2")
        self.schedule_day_title.pack(side="left", padx=(10, 8))
        self.schedule_day_title.bind("<Button-1>", lambda _event: self.open_schedule_calendar())
        self.calendar_toggle = ttk.Button(date_picker, text="▦", width=3, command=self.open_schedule_calendar)
        self.calendar_toggle.pack(side="left")
        self.schedule_window_var = tk.StringVar()
        ttk.Label(nav, textvariable=self.schedule_window_var, style="Muted.TLabel").grid(row=0, column=2, sticky="e")
        intro = "开关控制的是“本管理器是否监测这一次运行”，不会热改 MAA。04:00 是统计日边界，边界计划会特别标记。"
        self.schedule_intro = ttk.Label(self.schedule_tab, text=intro, style="Muted.TLabel")
        self.schedule_intro.pack(anchor="w", pady=(0, 8))
        self.schedule_cards = ScrollableFrame(
            self.schedule_tab,
            palette=self.palette,
            on_view_change=self._on_schedule_view_change,
            on_scroll_intent=self._on_schedule_scroll_intent,
        )
        self.schedule_cards.pack(fill="both", expand=True)
        actions = ttk.Frame(self.schedule_tab)
        actions.pack(fill="x", pady=(10, 0))
        ttk.Button(actions, text="当天全部开启", command=lambda: self.toggle_all_occurrences(True)).pack(side="left", padx=(0, 5))
        ttk.Button(actions, text="当天全部关闭", command=lambda: self.toggle_all_occurrences(False)).pack(side="left", padx=5)
        ttk.Button(actions, text="刷新", command=self.refresh_schedule).pack(side="right")

    def _build_resources(self):
        nav = ttk.Frame(self.resource_tab)
        nav.pack(fill="x", pady=(0, 8))
        ttk.Button(nav, text="‹ 前一天", command=lambda: self.change_resource_day(-1)).pack(side="left")
        ttk.Button(nav, text="今天", command=self.resource_today).pack(side="left", padx=5)
        ttk.Button(nav, text="后一天 ›", command=lambda: self.change_resource_day(1)).pack(side="left")
        self.resource_day_var = tk.StringVar()
        ttk.Label(nav, textvariable=self.resource_day_var, style="Heading.TLabel").pack(side="right")
        self.resource_summary_var = tk.StringVar(value="读取中…")
        ttk.Label(self.resource_tab, textvariable=self.resource_summary_var).pack(anchor="w", pady=(0, 6))
        self.resource_chart = ResourceChart(self.resource_tab, palette=self.palette, height=360, on_open=self.show_resource_detail)
        self.resource_chart.pack(fill="both", expand=True)
        frame = ttk.LabelFrame(self.resource_tab, text="异常采样（卡片仅列前三个内存大户；双击查看完整分析）", padding=6)
        frame.pack(fill="x", pady=(8, 0))
        self.snapshot_cards = ScrollableFrame(frame, palette=self.palette)
        self.snapshot_cards.canvas.configure(height=150)
        self.snapshot_cards.pack(fill="x")

    def _build_news(self):
        toolbar = ttk.Frame(self.news_tab)
        toolbar.pack(fill="x", pady=(0, 8))
        self.news_center_status = tk.StringVar(value="读取本地缓存…")
        ttk.Label(toolbar, textvariable=self.news_center_status, style="Muted.TLabel").pack(side="left")
        self.news_refresh_button = ttk.Button(toolbar, text="立即更新", command=self.refresh_news_center)
        self.news_refresh_button.pack(side="right")
        self.news_full_refresh_button = ttk.Button(
            toolbar, text="全部重拉", command=lambda: self.refresh_news_center(full=True)
        )
        self.news_full_refresh_button.pack(side="right", padx=(0, 6))
        self.news_progress = ttk.Progressbar(toolbar, maximum=1, mode="determinate", length=220)
        self.news_progress.pack(side="right", padx=8)

        panes = ttk.Panedwindow(self.news_tab, orient="horizontal")
        panes.pack(fill="both", expand=True)
        left = ttk.LabelFrame(panes, text="公告标题（本地最多 50 条）", padding=6)
        right = ttk.LabelFrame(panes, text="公告内容", padding=6)
        panes.add(left, weight=2)
        panes.add(right, weight=3)
        self.news_tree = ttk.Treeview(left, columns=("time", "title"), show="headings", selectmode="browse")
        self.news_tree.heading("time", text="时间")
        self.news_tree.heading("title", text="标题")
        self.news_tree.column("time", width=130, anchor="w")
        self.news_tree.column("title", width=360, anchor="w")
        self.news_tree.pack(fill="both", expand=True)
        self.news_tree.bind("<<TreeviewSelect>>", self.show_selected_news)

        self.news_detail = tk.Text(right, wrap="word", state="disabled", font=("Microsoft YaHei UI", 10), padx=10, pady=10)
        detail_scroll = ttk.Scrollbar(right, orient="vertical", command=self.news_detail.yview)
        self.news_detail.configure(yscrollcommand=detail_scroll.set)
        self.news_detail.pack(side="left", fill="both", expand=True)
        detail_scroll.pack(side="right", fill="y")
        self.news_items: list[dict] = []
        self.load_news_center()

    def _toggle_field(self, master, text: str, variable: tk.BooleanVar) -> ttk.Frame:
        """Create one consistent iOS-style boolean control bound to ``variable``."""
        field = ttk.Frame(master)
        label = ttk.Label(field, text=text, cursor="hand2")
        label.pack(side="left", fill="x", expand=True, padx=(0, 8))
        switch = ToggleSwitch(
            field,
            value=bool(variable.get()),
            palette=self.palette,
            width=48,
            height=26,
            command=lambda enabled: variable.set(bool(enabled)),
        )
        switch.pack(side="right")
        label.bind("<Button-1>", lambda _event: variable.set(not variable.get()))
        variable.trace_add("write", lambda *_args, control=switch, var=variable: control.set(bool(var.get())))
        self.setting_switches.append(switch)
        return field

    def _build_settings(self):
        self.settings_scroll = ScrollableFrame(self.settings_tab, palette=self.palette)
        self.settings_scroll.pack(fill="both", expand=True)
        settings_body = self.settings_scroll.body
        behavior = ttk.LabelFrame(settings_body, text="启动与后台行为", padding=12)
        behavior.pack(fill="x")
        self.start_with_windows_var=tk.BooleanVar()
        self.start_agent_with_gui_var=tk.BooleanVar()
        self.start_minimized_var=tk.BooleanVar()
        self.dark_mode_var=tk.BooleanVar()
        self.memory_alert_var=tk.BooleanVar()
        self._toggle_field(behavior,"登录 Windows 后启动管理器",self.start_with_windows_var).grid(row=0,column=0,sticky="ew",padx=(0,24),pady=4)
        self._toggle_field(behavior,"打开管理器时确保 Agent 已启动",self.start_agent_with_gui_var).grid(row=0,column=1,sticky="ew",pady=4)
        self._toggle_field(behavior,"开机启动时直接进入托盘",self.start_minimized_var).grid(row=1,column=0,sticky="ew",padx=(0,24),pady=4)
        self._toggle_field(behavior,"深色模式（含资源图表）",self.dark_mode_var).grid(row=1,column=1,sticky="ew",pady=4)
        self._toggle_field(behavior,"启用内存压力报警",self.memory_alert_var).grid(row=2,column=0,sticky="ew",padx=(0,24),pady=4)
        ttk.Label(behavior,text="关闭后仍记录资源历史，但不会发送内存压力通知。",style="Muted.TLabel").grid(row=2,column=1,sticky="w",pady=4)
        self._toggle_field(behavior,"启用系统代理健康检查",self.proxy_check_var).grid(row=3,column=0,sticky="ew",padx=(0,24),pady=4)
        ttk.Label(behavior,text="关闭后 Agent 不读取代理配置，也不发起外网探测；不影响其他监控。",style="Muted.TLabel").grid(row=3,column=1,sticky="w",pady=4)
        ttk.Label(behavior,text="任务栏固定由 Windows 管理：桌面和开始菜单快捷方式已创建，可右键它选择“固定到任务栏”。",style="Muted.TLabel").grid(row=4,column=0,columnspan=2,sticky="w",pady=(8,0))

        apps = ttk.LabelFrame(settings_body, text="实例别名、程序路径与随 Agent 启动", padding=12)
        apps.pack(fill="x", pady=(12,0))
        self.launch_path_vars={}; self.autostart_vars={}; self.instance_alias_vars={}
        ttk.Label(apps,text="实例",style="Muted.TLabel").grid(row=0,column=0,sticky="w")
        ttk.Label(apps,text="显示别名",style="Muted.TLabel").grid(row=0,column=1,sticky="w")
        ttk.Label(apps,text="可执行文件路径",style="Muted.TLabel").grid(row=0,column=2,sticky="w",padx=5)
        for row,label in enumerate((*configured_maa_labels(self.cfg),"MuMu"), start=1):
            path_var=tk.StringVar(); auto_var=tk.BooleanVar(); self.launch_path_vars[label]=path_var; self.autostart_vars[label]=auto_var
            ttk.Label(apps,text=label,width=8).grid(row=row,column=0,sticky="w",pady=5)
            if label.startswith("MAA"):
                alias_var=tk.StringVar(); self.instance_alias_vars[label]=alias_var
                ttk.Entry(apps,textvariable=alias_var,width=13).grid(row=row,column=1,sticky="w",padx=(0,5))
            else:
                ttk.Label(apps,text="—",style="Muted.TLabel",width=13).grid(row=row,column=1,sticky="w",padx=(0,5))
            ttk.Entry(apps,textvariable=path_var,width=58).grid(row=row,column=2,sticky="ew",padx=5)
            ttk.Button(apps,text="浏览…",command=lambda x=label:self.choose_launch_path(x)).grid(row=row,column=3,padx=4)
            ttk.Button(apps,text="立即启动",command=lambda x=label:self.launch_configured(x)).grid(row=row,column=4,padx=4)
            self._toggle_field(apps,"随 Agent 启动",auto_var).grid(row=row,column=5,sticky="ew",padx=(10,0))
        apps.columnconfigure(2,weight=1)

        news = ttk.LabelFrame(settings_body, text="明日方舟官网维护公告", padding=12)
        news.pack(fill="x", pady=(12,0))
        self.news_enabled_var=tk.BooleanVar(); self.news_times_var=tk.StringVar(); self.news_status_var=tk.StringVar()
        self._toggle_field(news,"启用官网公告监控",self.news_enabled_var).grid(row=0,column=0,sticky="ew")
        ttk.Label(news,text="每日检查（逗号分隔）").grid(row=0,column=1,sticky="e",padx=(20,5))
        ttk.Entry(news,textvariable=self.news_times_var,width=20).grid(row=0,column=2,sticky="w")
        ttk.Label(news,text="仅新增且标题/摘要命中维护关键词时读取正文；普通公告不调用 DeepSeek，也不发送通知。",style="Muted.TLabel").grid(row=1,column=0,columnspan=4,sticky="w",pady=(8,0))
        ttk.Label(news,textvariable=self.news_status_var,style="Muted.TLabel").grid(row=2,column=0,columnspan=4,sticky="w",pady=(5,0))
        bark = ttk.LabelFrame(settings_body, text="Bark 失败重试", padding=12)
        bark.pack(fill="x", pady=(12,0))
        self.bark_retry_attempts_var=tk.StringVar(); self.bark_retry_sleep_var=tk.StringVar(); self.bark_retry_infinite_var=tk.BooleanVar()
        ttk.Label(bark,text="每次发送尝试次数").grid(row=0,column=0,sticky="e",padx=(0,6))
        ttk.Entry(bark,textvariable=self.bark_retry_attempts_var,width=8).grid(row=0,column=1,sticky="w")
        ttk.Label(bark,text="每次间隔（秒）").grid(row=0,column=2,sticky="e",padx=(22,6))
        ttk.Entry(bark,textvariable=self.bark_retry_sleep_var,width=8).grid(row=0,column=3,sticky="w")
        self._toggle_field(bark,"无限保留失败消息并持续补发（不会阻塞监控主循环）",self.bark_retry_infinite_var).grid(row=0,column=4,sticky="ew",padx=(24,0))
        about = ttk.LabelFrame(settings_body, text="关于", padding=12)
        about.pack(fill="x", pady=(12,0))
        ttk.Label(about, text=f"MAA Agent v{APP_VERSION} · 作者：{AUTHOR_NAME}").pack(side="left")
        author_link = ttk.Label(about, text=AUTHOR_URL, style="Link.TLabel", cursor="hand2")
        author_link.pack(side="left", padx=(12,0))
        author_link.bind("<Button-1>", lambda _event: webbrowser.open(AUTHOR_URL))
        bar=ttk.Frame(settings_body); bar.pack(fill="x",pady=(14,0))
        ttk.Button(bar,text="修复桌面与开始菜单快捷方式",command=self.install_shortcuts).pack(side="left")
        ttk.Button(bar,text="保存设置",command=self.save_settings).pack(side="right")

    def _build_diagnostics(self):
        ttk.Label(self.diagnostics_tab,text="在这里发送测试提醒，确认手机通知和各项检查是否正常。测试不会关闭 MAA。",style="Muted.TLabel").pack(anchor="w",pady=(0,10))
        real=ttk.LabelFrame(self.diagnostics_tab,text="MAA 运行检查",padding=12); real.pack(fill="x")
        ttk.Label(real,text="测试会按实际检查流程运行，但不会停止或修改正在运行的 MAA。",wraplength=900).pack(anchor="w",pady=(0,8))
        for index,label in enumerate(configured_maa_labels(self.cfg)):
            ttk.Button(real,text=f"测试 {label} 未运行提醒",command=lambda x=label:self.run_diagnostic("maa_missing",x)).pack(side="left",padx=(0 if index == 0 else 8,8))
        previews=ttk.LabelFrame(self.diagnostics_tab,text="其他提醒测试",padding=12); previews.pack(fill="x",pady=(12,0))
        for idx,(label,kind) in enumerate((("Bark 通知","bark_transport"),("内存不足","memory"),("磁盘空间不足","disk"),("网络检查失败","proxy"),("窗口未响应","hung"),("任务未按时开始","schedule"),("Agent 出错","agent_error"),("官网维护","news"))):
            ttk.Button(previews,text=label,command=lambda x=kind:self.run_diagnostic(x),width=15).grid(row=idx//4,column=idx%4,padx=5,pady=5,sticky="ew")
        ttk.Button(previews,text="依次测试全部提醒",command=self.run_all_diagnostics).grid(row=2,column=0,columnspan=4,padx=5,pady=(12,5),sticky="ew")
        self.diagnostic_status_var=tk.StringVar(value="尚未执行测试。")
        ttk.Label(self.diagnostics_tab,textvariable=self.diagnostic_status_var,wraplength=980).pack(anchor="w",pady=(14,0))

    def _build_timing(self):
        report = ttk.LabelFrame(self.timing_tab, text="日报与通知", padding=12)
        report.pack(fill="x")
        self.enable_daily_var = tk.BooleanVar()
        self.daily_time_var = tk.StringVar()
        self.deepseek_daily_var = tk.BooleanVar()
        self._toggle_field(report, "启用每日报告", self.enable_daily_var).grid(row=0, column=0, sticky="ew", padx=(0, 16))
        ttk.Label(report, text="发送时间（HH:MM）").grid(row=0, column=1, sticky="e")
        ttk.Entry(report, textvariable=self.daily_time_var, width=9).grid(row=0, column=2, sticky="w", padx=(6, 18))
        self._toggle_field(report, "有价值时调用 DeepSeek", self.deepseek_daily_var).grid(row=0, column=3, sticky="ew")
        self._toggle_field(report, "启用系统代理健康检查", self.proxy_check_var).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))

        grid = ttk.LabelFrame(self.timing_tab, text="判断与采样时间", padding=12)
        grid.pack(fill="x", pady=(12, 0))
        for idx, (label, key, kind) in enumerate(TIMING_FIELDS):
            row, block = divmod(idx, 2)
            base_col = block * 3
            ttk.Label(grid, text=label).grid(row=row, column=base_col, sticky="e", padx=(0, 6), pady=5)
            var = tk.StringVar()
            self.timing_vars[key] = var
            ttk.Entry(grid, textvariable=var, width=10).grid(row=row, column=base_col + 1, sticky="w", padx=(0, 22), pady=5)
            ttk.Label(grid, text="秒" if key == "check_interval_seconds" else "分钟", style="Muted.TLabel").grid(row=row, column=base_col + 2, sticky="w")

        explain = (
            "时间设置保存后，正在运行的 Agent 会在下一轮自动重新读取 config.json。\n"
            "MAA 的长期执行时间仍在 MAA 自身“设置 → 定时执行”中修改；上一个标签页只处理单次例外。"
        )
        ttk.Label(self.timing_tab, text=explain, justify="left", style="Muted.TLabel").pack(anchor="w", pady=14)
        ttk.Button(self.timing_tab, text="保存时间策略", command=self.save_timing).pack(anchor="e")

    def _build_config(self):
        bar = ttk.Frame(self.config_tab)
        bar.pack(fill="x", pady=(0, 8))
        ttk.Label(bar, text="config.json 的完整键值；密钥仅显示为“已配置”。", style="Muted.TLabel").pack(side="left")
        ttk.Button(bar, text="重新读取", command=self.reload_config).pack(side="right", padx=3)
        ttk.Button(bar, text="保存 config.json", command=self.save_config).pack(side="right", padx=3)
        ttk.Button(bar, text="编辑选中项", command=self.edit_config_value).pack(side="right", padx=3)
        self.config_tree = ttk.Treeview(self.config_tab, columns=("key", "value", "type"), show="headings")
        self.config_tree.heading("key", text="配置项")
        self.config_tree.heading("value", text="当前值")
        self.config_tree.heading("type", text="类型")
        self.config_tree.column("key", width=300, anchor="w")
        self.config_tree.column("value", width=600, anchor="w")
        self.config_tree.column("type", width=90, anchor="center")
        self.config_tree.pack(fill="both", expand=True)
        self.config_tree.bind("<Double-1>", lambda _event: self.edit_config_value())
        self.config_tree.bind("<<TreeviewSelect>>", self.show_config_help)
        self.config_help_var=tk.StringVar(value="选择一项可查看用途说明。")
        ttk.Label(self.config_tab,textvariable=self.config_help_var,style="Muted.TLabel",wraplength=1000).pack(fill="x",pady=(7,0))

    def _build_logs(self):
        bar = ttk.Frame(self.logs_tab)
        bar.pack(fill="x", pady=(0, 8))
        self.log_choice = tk.StringVar(value="agent.log")
        ttk.Radiobutton(bar, text="运行日志", value="agent.log", variable=self.log_choice, command=self.refresh_logs).pack(side="left")
        ttk.Radiobutton(bar, text="决策日志", value="decision.log", variable=self.log_choice, command=self.refresh_logs).pack(side="left", padx=10)
        self.auto_log_var = tk.BooleanVar(value=True)
        self._toggle_field(bar, "自动刷新", self.auto_log_var).pack(side="right", padx=8)
        ttk.Button(bar, text="刷新", command=self.refresh_logs).pack(side="right")
        self.full_log_mode=tk.StringVar(value="仅事故与恢复")
        ttk.Combobox(bar,textvariable=self.full_log_mode,values=("仅事故与恢复","完整日志"),state="readonly",width=16).pack(side="right",padx=8)
        self.full_log_mode.trace_add("write",lambda *_:self.refresh_logs())
        content = ttk.Frame(self.logs_tab)
        content.pack(fill="both", expand=True)
        self.log_text = tk.Text(content, wrap="none", font=("Consolas", 9), state="disabled")
        ybar = ttk.Scrollbar(content, orient="vertical", command=self.log_text.yview)
        xbar = ttk.Scrollbar(content, orient="horizontal", command=self.log_text.xview)
        self.log_text.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        content.rowconfigure(0, weight=1)
        content.columnconfigure(0, weight=1)

    def _set_text(self, widget: tk.Text, value: str):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        widget.configure(state="disabled")

    def _set_footer(self, text: str):
        self.footer_text.set(f"{datetime.now():%H:%M:%S}  {text}")

    def toggle_dashboard_activity(self):
        self.activity_expanded = not self.activity_expanded
        if self.activity_expanded:
            try:
                self.activity_panes.insert(0, self.dashboard_chart_frame, weight=1)
            except tk.TclError:
                pass
            self.activity_toggle.configure(text="隐藏图表")
        else:
            try:
                self.activity_panes.forget(self.dashboard_chart_frame)
            except tk.TclError:
                pass
            self.activity_toggle.configure(text="显示图表")

    def clear_current_logs(self):
        self._dashboard_log_cleared_at = datetime.now()
        self._set_text(self.recent_log, "（主页最近事件已清空；完整日志仍保留在“日志”页。）")
        self._set_footer("已清空主页最近事件；实际日志未改动。")

    def _restore_dashboard_layout(self):
        positions = self.gui_state.get("dashboard_sashes", {})
        for key, panes in (("main", self.dashboard_info_panes), ("activity", self.activity_panes)):
            try:
                position = int(positions.get(key, 0))
                if position > 0 and len(panes.panes()) > 1:
                    panes.sashpos(0, position)
            except (TypeError, ValueError, tk.TclError):
                pass

    def _save_dashboard_layout(self, _event=None):
        positions = dict(self.gui_state.get("dashboard_sashes", {}))
        for key, panes in (("main", self.dashboard_info_panes), ("activity", self.activity_panes)):
            try:
                if len(panes.panes()) > 1:
                    positions[key] = int(panes.sashpos(0))
            except (TypeError, ValueError, tk.TclError):
                pass
        self.gui_state["dashboard_sashes"] = positions
        save_gui_state(self.gui_state)

    def confirm_close(self):
        self.hide_to_tray()

    def _on_tab_changed(self, _event=None):
        try:
            selected = self.notebook.nametowidget(self.notebook.select())
        except Exception:
            return
        if selected is self.resource_tab:
            self.resource_day = datetime.now().date()
            self.refresh_resources()
        elif selected is self.schedule_tab:
            self.refresh_schedule()
        elif selected is self.news_tab:
            self.load_news_center()

    def _apply_runtime_theme(self):
        self._build_style()
        palette = self.palette
        self.root.option_add("*Menu.background", palette["panel"])
        self.root.option_add("*Menu.foreground", palette["fg"])
        for widget in self.root.winfo_children():
            self._theme_widget_tree(widget, palette)
        if hasattr(self, "resource_chart"):
            self.resource_chart.set_palette(palette)
        if hasattr(self, "dashboard_resource_chart"):
            self.dashboard_resource_chart.set_palette(palette)
        if hasattr(self, "cpu_progress"):
            self.cpu_progress.set_palette(palette)
            self.memory_progress.set_palette(palette)
        for name in ("next_schedule_cards", "schedule_cards", "snapshot_cards", "settings_scroll"):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.set_palette(palette)
        if hasattr(self, "schedule_calendar"):
            self.schedule_calendar.set_palette(palette)
        for switch in getattr(self, "setting_switches", []):
            try:
                switch.set_palette(palette)
            except tk.TclError:
                pass
        if self.schedule_calendar_dialog is not None:
            try:
                self.schedule_calendar_dialog.configure(background=palette["bg"])
            except tk.TclError:
                pass
        self._draw_header_gradient()

    def _theme_widget_tree(self, widget, palette):
        try:
            if isinstance(widget, tk.Text):
                widget.configure(background=palette["panel"], foreground=palette["fg"], insertbackground=palette["fg"], selectbackground=palette["accent"])
        except tk.TclError:
            pass
        for child in widget.winfo_children():
            self._theme_widget_tree(child, palette)

    def _setup_tray(self):
        try:
            import pystray
            from PIL import Image, ImageDraw

            if ICON_PNG.exists():
                image=Image.open(ICON_PNG).convert("RGBA")
            else:
                image = Image.new("RGBA", (64, 64), (24, 95, 165, 255))
                draw = ImageDraw.Draw(image); draw.text((18, 16), "MA", fill="white")
            def launch_action(label):
                return lambda: self.root.after(0,lambda:self.launch_configured(label))
            def drill_action(label):
                return lambda: self.root.after(0,lambda:self.run_diagnostic("maa_missing",label))
            launch_submenu=pystray.Menu(*[
                pystray.MenuItem(f"启动 {label}",launch_action(label))
                for label in (*configured_maa_labels(self.cfg),"MuMu")
            ])
            drill_submenu=pystray.Menu(*[
                pystray.MenuItem(f"{label} 未运行提醒",drill_action(label))
                for label in configured_maa_labels(self.cfg)
            ], pystray.MenuItem("Bark 通知测试",lambda:self.root.after(0,lambda:self.run_diagnostic("bark_transport"))))
            menu = pystray.Menu(
                pystray.MenuItem("打开管理器", lambda: self.root.after(0, self.show_window), default=True),
                pystray.MenuItem("启动 Agent", lambda: self.root.after(0, self.start_agent)),
                pystray.MenuItem("停止 Agent", lambda: self.root.after(0, self.stop_agent)),
                pystray.MenuItem("重启 Agent", lambda: self.root.after(0, self.restart_agent)),
                pystray.MenuItem("启动程序",launch_submenu),
                pystray.MenuItem("通知测试",drill_submenu),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("退出管理器（Agent 继续运行）", lambda: self.root.after(0, self.exit_gui)),
                pystray.MenuItem("退出 Agent 和管理器", lambda: self.root.after(0, self.exit_all)),
            )
            self.tray_icon = pystray.Icon("maa_agent_manager", image, "MAA Agent 管理器", menu)
            threading.Thread(target=self.tray_icon.run, daemon=True, name="maa-agent-tray").start()
            self.tray_ready = True
        except Exception as exc:
            self.tray_ready = False
            self._set_footer(f"托盘不可用：{exc}")

    def hide_to_tray(self):
        if self.tray_ready:
            self.root.withdraw()
            self._set_footer("已最小化到托盘；Agent 继续运行。")
        else:
            self.root.iconify()

    def show_window(self):
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        self.root.focus_force()
        try:
            if self.notebook.nametowidget(self.notebook.select()) is self.resource_tab:
                self.resource_day = datetime.now().date()
        except Exception:
            pass
        self.refresh_all()

    def exit_gui(self):
        self._save_dashboard_layout()
        if self.refresh_job:
            self.root.after_cancel(self.refresh_job)
            self.refresh_job = None
        if self.tray_icon:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
        self.root.destroy()

    def exit_all(self):
        if not messagebox.askyesno("退出全部？","这会正常停止 Agent，然后退出管理器。MAA 和 MuMu 不会被关闭。",parent=self.root):
            return
        def worker():
            result=request_stop(BASE_DIR,timeout_seconds=20,force=False)
            self.root.after(0,lambda:(self._set_footer(result.message),self.exit_gui()))
        threading.Thread(target=worker,daemon=True,name="maa-agent-exit-all").start()

    def start_agent(self):
        ok, msg = start_agent_process()
        self._set_footer(msg)
        if not ok and "另一份" in msg:
            messagebox.showwarning("无法启动", msg, parent=self.root)
        self.root.after(1200, self.refresh_all)

    def stop_agent(self):
        self._set_footer("正在请求 Agent 正常停止…")

        def worker():
            result = request_stop(BASE_DIR, timeout_seconds=20, force=False)
            self.root.after(0, lambda: self._stop_finished(result))

        threading.Thread(target=worker, daemon=True, name="maa-agent-stop").start()

    def _stop_finished(self, result):
        self._set_footer(result.message)
        if result.status == "timeout":
            if messagebox.askyesno("Agent 尚未退出", result.message + "\n\n是否校验进程身份后强制停止？", parent=self.root):
                threading.Thread(target=self._force_stop_worker, daemon=True).start()
        elif result.status == "error":
            messagebox.showerror("停止失败", result.message, parent=self.root)
        self.refresh_all()

    def _force_stop_worker(self):
        result = request_stop(BASE_DIR, timeout_seconds=0, force=True)
        self.root.after(0, lambda: self._stop_finished(result))

    def restart_agent(self):
        self._set_footer("正在重启 Agent…")

        def worker():
            result = request_stop(BASE_DIR, timeout_seconds=20, force=False)
            if result.status in {"not_running", "stopped"}:
                ok, msg = start_agent_process()
                text = msg if ok else f"停止完成，但{msg}"
            else:
                text = result.message
            self.root.after(0, lambda: (self._set_footer(text), self.root.after(1200, self.refresh_all)))

        threading.Thread(target=worker, daemon=True, name="maa-agent-restart").start()

    def _display_instance(self, label: str) -> str:
        alias = str((self.cfg.get("maa_instance_aliases") or {}).get(label, "")).strip()
        return f"{alias}（{label}）" if alias else label

    def _maa_status(self) -> str:
        dirs = self.cfg.get("maa_instance_dirs", {})
        found: dict[str, list[int]] = {str(k): [] for k in dirs}
        unknown = []
        for proc in psutil.process_iter(["pid", "name", "exe"]):
            try:
                if (proc.info.get("name") or "").lower() != "maa.exe":
                    continue
                exe = proc.info.get("exe")
                matched = False
                if exe:
                    for label, path in dirs.items():
                        try:
                            if Path(exe).resolve().is_relative_to(Path(path).resolve()):
                                found[str(label)].append(proc.pid)
                                matched = True
                                break
                        except (OSError, ValueError):
                            continue
                if not matched:
                    unknown.append(proc.pid)
            except (psutil.Error, OSError):
                continue
        lines = []
        for label in sorted(found):
            lines.append(f"{self._display_instance(label)}: " + (f"运行中（PID {', '.join(map(str, found[label]))}）" if found[label] else "未检测到"))
        if unknown:
            lines.append(f"未映射 MAA: {len(unknown)} 个")
        try:
            health=json.loads((STATE_DIR/"process_health.json").read_text(encoding="utf-8"))
            sustain=float(self.cfg.get("process_missing_sustain_minutes",3))
            for key,rec in (health.get("missing") or {}).items():
                if not isinstance(rec,dict) or rec.get("alerted"):
                    continue
                elapsed=max(0,(datetime.now().timestamp()-float(rec.get("since",datetime.now().timestamp())))/60)
                label=key.split(":",1)[-1] if ":" in key else "MAA"
                lines.append(f"⏳ {self._display_instance(label)} 缺失确认中：约 {max(0,sustain-elapsed):.1f} 分钟后报警")
        except Exception:
            pass
        return "\n".join(lines) if lines else "未配置实例目录"

    def _mumu_status(self) -> str:
        names = self.cfg.get("mumu_process_names", [])
        if isinstance(names, str):
            names = [names]
        targets = {str(x).lower() for x in names}
        rows = []
        total_mb = 0.0
        for proc in psutil.process_iter(["pid", "name", "memory_info"]):
            try:
                if (proc.info.get("name") or "").lower() in targets:
                    rows.append(proc)
                    total_mb += proc.info["memory_info"].rss / 1024 / 1024
            except (psutil.Error, KeyError, TypeError):
                continue
        if not rows:
            return "未检测到 MuMu 相关进程"
        return f"相关进程：{len(rows)} 个\n总内存：{total_mb / 1024:.1f} GB"

    def _resource_status(self, cpu: float | None = None, mem=None) -> str:
        mem = mem or psutil.virtual_memory()
        cpu = psutil.cpu_percent(interval=None) if cpu is None else float(cpu)
        disks = []
        for item in self.cfg.get("monitor_disk_paths", ["C:\\", "D:\\"]):
            try:
                usage = psutil.disk_usage(item)
                disks.append(f"{item} 剩余 {usage.free / 1024**3:.1f} GB")
            except OSError:
                pass
        return f"CPU：{cpu:.0f}%\n内存：{memory_text(mem.total / 1048576, mem.available / 1048576, mem.percent)}\n" + "\n".join(disks)

    def _daily_state(self, day: date) -> dict:
        try:
            value = json.loads((STATE_DIR / f"daily_{day.isoformat()}.json").read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    def _schedule_card_state(self, row: dict, now: datetime, daily_cache: dict[date, dict],
                             run_cache: dict[str, dict] | None = None, *, is_next: bool = False) -> tuple[str, str, str]:
        when = row["datetime"]
        if row.get("skipped"):
            return "已关闭", "本次不纳入运行监测", "off"
        run_cache=run_cache or {}
        run=run_cache.get(schedule_occurrence_key(str(row["label"]),when)) or {}
        run_status=run.get("status")
        if run_status=="failed":
            error=run.get("error") or {}
            return "运行失败", str(error.get("title") or "任务没有正常完成"), "failed"
        if run_status=="running":
            try:
                updated=datetime.strptime(str(run.get("updated_at")),"%Y-%m-%d %H:%M:%S")
                fresh=timedelta(minutes=-5) <= now-updated <= timedelta(hours=6)
            except Exception:
                fresh=False
            if fresh:
                task=str(run.get("current_task") or "任务队列")
                return "正在进行", f"当前：{task}", "running"
            return "状态待确认", "运行记录长时间没有终态", "off"
        if run_status=="completed":
            completed=str(run.get("completed_at") or "")
            suffix=f" · {completed[11:16]} 完成" if len(completed)>=16 else ""
            return "已完成", f"任务队列已正常结束{suffix}", "done"
        daily = daily_cache.setdefault(when.date(), self._daily_state(when.date()))
        expected = when.strftime("%Y-%m-%d %H:%M")
        observed = (daily.get("observed_runs") or {}).get(row["label"], [])
        if any(str(item.get("occurrence")) == expected for item in observed if isinstance(item, dict)):
            return "已检测启动", "Agent 已收到本次启动证据", "done"
        digest = hashlib.sha1(f"{row['label']}|{expected}".encode("utf-8", errors="ignore")).hexdigest()
        if (STATE_DIR / f"scheduled_alert_{digest}.txt").exists():
            return "未检测到启动", "计划未触发、程序退出或被运行中任务阻断", "failed"
        if any(expected in str(item) and str(row["label"]) in str(item) for item in (daily.get("schedule") or {}).get("unverified", [])):
            return "无法核验", "当时 Agent 尚未覆盖该监测时段", "off"
        if when <= now:
            return "等待核验", "尚未收到本次启动证据", "off"
        if is_next:
            return "下一个计划", "即将执行", "next"
        return "等待运行", "已加入后续监测", "future"

    def _schedule_error_info(self, row: dict, run_cache: dict[str, dict]) -> dict:
        when=row["datetime"]
        run=run_cache.get(schedule_occurrence_key(str(row["label"]),when)) or {}
        if run.get("status")=="failed":
            return dict(run.get("error") or {})
        expected=when.strftime("%Y-%m-%d %H:%M")
        digest=hashlib.sha1(f"{row['label']}|{expected}".encode("utf-8",errors="ignore")).hexdigest()
        if (STATE_DIR/f"scheduled_alert_{digest}.txt").exists():
            return {
                "title":"未检测到计划启动",
                "summary":f"{self._display_instance(str(row['label']))} 在 {when:%Y-%m-%d %H:%M} 没有检测到启动证据。",
                "likely":"计划未触发、MAA/MuMu 已退出，或上一轮任务仍在运行。",
                "action":"检查 MAA 定时器、程序状态以及对应时段的 Agent 日志。",
                "task":"计划启动监测","time":expected,
            }
        return {}

    def show_schedule_error(self, row: dict, error: dict) -> None:
        when=row["datetime"]
        text=(f"实例：{self._display_instance(str(row['label']))}\n"
              f"计划时间：{when:%Y-%m-%d %H:%M}\n"
              f"失败任务：{error.get('task') or '当前任务'}\n"
              f"失败时间：{error.get('time') or '未知'}\n\n"
              f"发生了什么：{error.get('summary') or error.get('title') or '任务没有正常完成。'}\n\n"
              f"可能原因：{error.get('likely') or '没有更多可靠证据。'}\n\n"
              f"建议操作：{error.get('action') or '检查当前游戏画面与 MAA 日志。'}")
        messagebox.showerror(str(error.get("title") or "运行错误详情"),text,parent=self.root)

    def _card_background(self, state: str) -> str:
        dark = self.cfg.get("gui_dark_mode", True)
        if state == "failed":
            return "#3a2529" if dark else "#f9e0e3"
        if state == "running":
            return "#3b3420" if dark else "#fff1c2"
        if state == "next":
            return "#203a2a" if dark else "#dff3e5"
        if state in {"done", "off"}:
            return "#252d36" if dark else "#e3e8ed"
        return self.palette["panel"]

    def _bind_card_action(self, widgets: list[tk.Widget], callback) -> None:
        for widget in widgets:
            widget.bind("<Double-1>", callback)

    def _render_schedule_rows(self, container: ScrollableFrame, rows: list[dict], *, compact: bool = False) -> None:
        container.clear()
        container._schedule_day_widgets = []
        container._schedule_row_widgets = []
        now = datetime.now()
        daily_cache: dict[date, dict] = {}
        run_cache=load_schedule_run_records()
        next_keys=next_schedule_keys(rows,now,run_cache)
        previous_day = None
        if not rows:
            ttk.Label(container.body, text="这个时间范围内没有启用的计划。", style="Muted.TLabel", padding=14).pack(fill="x")
            return
        for index, row in enumerate(rows):
            when = row["datetime"]
            if when.date() != previous_day:
                day_label = ttk.Label(
                    container.body,
                    text=f"────────  {when:%Y 年 %m 月 %d 日}  ────────",
                    anchor="center",
                    style="Muted.TLabel",
                    padding=(4, 7),
                )
                day_label._schedule_day = when.date()
                day_label.pack(fill="x")
                container._schedule_day_widgets.append(day_label)
                previous_day = when.date()
            row_key=schedule_occurrence_key(str(row["label"]),when)
            status, detail, state = self._schedule_card_state(
                row,now,daily_cache,run_cache,is_next=row_key in next_keys
            )
            background = self._card_background(state)
            card = tk.Frame(container.body, background=background, bd=0, highlightthickness=1, highlightbackground=self.palette["grid"])
            card.pack(fill="x", padx=4, pady=4)
            if state == "failed":
                rail_color = self.palette["bad"]
            elif state == "running":
                rail_color = self.palette["warning"]
            elif state == "next":
                rail_color = self.palette["good"]
            elif when <= now or state == "off":
                rail_color = "#66717d" if self.cfg.get("gui_dark_mode", True) else "#1f2937"
            else:
                rail_color = time_of_day_color(when)
            tk.Frame(card, width=9, background=rail_color).pack(side="left", fill="y")
            content = tk.Frame(card, background=background, padx=12, pady=8)
            content.pack(side="left", fill="both", expand=True)
            switch_palette = dict(self.palette, bg=background)
            switch = ToggleSwitch(
                content,
                value=not row.get("skipped"),
                palette=switch_palette,
                command=lambda enabled, item=row: self.set_occurrence_enabled(item, enabled),
            )
            switch.grid(row=0, column=0, rowspan=2, padx=(0, 13), sticky="w")
            time_label = tk.Label(content, text=f"{when:%H:%M}", background=background, foreground=self.palette["fg"], font=("Segoe UI", 18, "bold"))
            time_label.grid(row=0, column=1, rowspan=2, sticky="w")
            name_label = tk.Label(content, text=self._display_instance(str(row["label"])), background=background, foreground=self.palette["fg"], font=("Microsoft YaHei UI", 11, "bold"))
            name_label.grid(row=0, column=2, sticky="w", padx=(16, 8))
            source = "MAA 当前配置" if str(row.get("source", "")).startswith("maa_gui") else "备用配置"
            note = str((row.get("override") or {}).get("note") or "").strip()
            source_text = f"{when:%m 月 %d 日} · {source}"
            if note:
                source_text += f"\n备注：{note}"
            source_label = tk.Label(
                content,
                text=source_text,
                background=background,
                foreground=self.palette["muted"],
                font=("Microsoft YaHei UI", 9),
                justify="left",
                anchor="w",
                wraplength=300 if compact else 460,
            )
            source_label.grid(row=1, column=2, sticky="w", padx=(16, 8))
            status_color = (self.palette["bad"] if state == "failed" else
                            self.palette["warning"] if state == "running" else
                            self.palette["good"] if state == "next" else self.palette["muted"])
            status_label = tk.Label(content, text=status, background=background, foreground=status_color, font=("Microsoft YaHei UI", 10, "bold"))
            status_label.grid(row=0, column=3, sticky="e", padx=(8, 0))
            detail_label = tk.Label(content, text=detail, background=background, foreground=self.palette["muted"], font=("Microsoft YaHei UI", 8), justify="right", wraplength=150 if compact else 220)
            detail_label.grid(row=1, column=3, sticky="e", padx=(8, 0))
            content.columnconfigure(2, weight=1, minsize=180 if compact else 260)
            content.columnconfigure(3, minsize=130 if compact else 180)
            if not compact:
                actions=tk.Frame(content,background=background)
                actions.grid(row=0,column=4,rowspan=2,padx=(12,0),sticky="e")
                error_info=self._schedule_error_info(row,run_cache) if state=="failed" else {}
                if error_info:
                    ttk.Button(actions,text="查看错误",command=lambda item=row,info=error_info:self.show_schedule_error(item,info)).pack(fill="x",pady=(0,4))
                ttk.Button(actions,text="修改时间",command=lambda item=row:self.move_occurrence(item)).pack(fill="x")
            self._bind_card_action(
                [card, content, time_label, name_label, source_label, status_label, detail_label],
                lambda _event, item=row: self.move_occurrence(item),
            )
            card._schedule_datetime = when
            container._schedule_row_widgets.append(card)
        container.refresh_scrollregion()

    def _schedule_rows_signature(self, rows: list[dict]) -> tuple:
        now = datetime.now()
        daily_cache: dict[date, dict] = {}
        run_cache=load_schedule_run_records()
        next_keys=next_schedule_keys(rows,now,run_cache)
        signature = []
        for row in rows:
            row_key=schedule_occurrence_key(str(row["label"]),row["datetime"])
            status, detail, state = self._schedule_card_state(
                row,now,daily_cache,run_cache,is_next=row_key in next_keys
            )
            override = row.get("override") or {}
            error=(run_cache.get(row_key) or {}).get("error") or {}
            signature.append((
                str(row.get("label", "")),
                row["datetime"].isoformat(timespec="minutes"),
                bool(row.get("skipped")),
                str(override.get("id", "")),
                str(override.get("note", "")),
                status,
                detail,
                state,
                str(error.get("title","")),
                str(error.get("summary","")),
            ))
        return tuple(signature)

    def set_occurrence_enabled(self, row: dict, enabled: bool) -> None:
        override = row.get("override") or {}
        if enabled and override.get("action") == "skip":
            remove_override(BASE_DIR, self.cfg, override_id=override.get("id"))
            self._set_footer("已开启本次运行监测。")
        elif not enabled and not row.get("skipped"):
            add_override(BASE_DIR, self.cfg, row["label"], row["original_datetime"], "skip", note="GUI 监测开关")
            self._set_footer("已关闭本次运行监测；MAA 自己的定时器未改动。")
        self.root.after_idle(lambda: (self.refresh_schedule(), self.refresh_dashboard()))

    def refresh_dashboard(self):
        row = validated_agent_for_dir(BASE_DIR)
        others = [x for x in find_running_agents() if not is_script_for_installation(x["script"], BASE_DIR)]
        if row:
            try:
                elapsed = datetime.now().timestamp() - row["create_time"]
                hours, remainder = divmod(int(elapsed), 3600)
                minutes = remainder // 60
                agent_mb = psutil.Process(int(row["pid"])).memory_info().rss / 1048576
                self.agent_status.configure(text=f"● Agent 正在运行   PID {row['pid']}   {hours}小时{minutes}分钟   内存 {agent_mb/1024:.2f} GB", style="Good.TLabel")
            except Exception:
                self.agent_status.configure(text=f"● Agent 正在运行   PID {row['pid']}", style="Good.TLabel")
        elif others:
            self.agent_status.configure(text=f"● 当前目录未运行；另一份 Agent 正在运行（PID {others[0]['pid']}）", style="Bad.TLabel")
        else:
            self.agent_status.configure(text="● Agent 已停止", style="Bad.TLabel")
        vm = psutil.virtual_memory(); cpu = psutil.cpu_percent(interval=None)
        self.card_vars["MAA"].set(self._maa_status())
        self.card_vars["MuMu"].set(self._mumu_status())
        self.card_vars["资源"].set(self._resource_status(cpu=cpu, mem=vm))
        self.cpu_progress.set_value(cpu, f"CPU {cpu:.0f}%")
        used_gb = (vm.total - vm.available) / 1024**3
        self.memory_progress.set_value(vm.percent, f"内存 {vm.percent:.0f}% · 已用 {used_gb:.1f} GB")
        recent=tail_text(LOG_DIR / "agent.log", max_lines=120, max_bytes=100_000)
        recent=log_text_after(recent, self._dashboard_log_cleared_at)
        self._set_text(self.recent_log, filter_log_text(recent,self.dashboard_log_mode.get()))
        today = datetime.now().date()
        mini_rows = load_resource_calendar_day(BASE_DIR, today, int(self.cfg.get("operational_day_start_hour", 4)))
        self.dashboard_resource_chart.set_data(mini_rows, today, float(self.cfg.get("memory_pressure_percent", 88)))

        now = datetime.now()
        rows, _meta = effective_occurrences(BASE_DIR, self.cfg, now, now + timedelta(hours=36), include_skipped=True)
        rows = rows[:16]
        signature = self._schedule_rows_signature(rows)
        if signature != self._dashboard_schedule_signature:
            self._render_schedule_rows(self.next_schedule_cards, rows, compact=True)
            self._dashboard_schedule_signature = signature

    def refresh_schedule(self, force: bool = False):
        start=datetime.combine(self.schedule_range_start,datetime.min.time())
        end=datetime.combine(self.schedule_range_end + timedelta(days=1),datetime.min.time())
        self.schedule_day_var.set(self.schedule_day.isoformat())
        self.schedule_day_title_var.set(f"{self.schedule_day:%Y 年 %m 月 %d 日}")
        self.schedule_window_var.set(f"已加载 {self.schedule_range_start:%m-%d} 至 {self.schedule_range_end:%m-%d}")
        self.schedule_rows, _meta = effective_occurrences(
            BASE_DIR, self.cfg, start, end, include_skipped=True
        )
        self.schedule_rows=[row for row in self.schedule_rows if start <= row["datetime"] < end]
        signature = (self.schedule_range_start, self.schedule_range_end, self._schedule_rows_signature(self.schedule_rows))
        if force or signature != self._schedule_signature:
            self._schedule_rendering = True
            self.schedule_cards.begin_update()
            try:
                self._render_schedule_rows(self.schedule_cards, self.schedule_rows)
                self._schedule_signature = signature
            finally:
                self.schedule_cards.end_update()
                self._schedule_rendering = False
            self.root.after_idle(self._finish_schedule_render)

    def change_schedule_day(self,delta:int):
        self.set_schedule_day(self.schedule_day + timedelta(days=delta))

    def schedule_today(self):
        self.set_schedule_day(datetime.now().date())

    def goto_schedule_day(self):
        try:
            self.set_schedule_day(datetime.strptime(self.schedule_day_var.get().strip(),"%Y-%m-%d").date())
        except ValueError:
            messagebox.showerror("日期格式错误","请输入 YYYY-MM-DD。",parent=self.root)

    def open_schedule_calendar(self):
        if self.schedule_calendar_dialog is None or not self.schedule_calendar_dialog.winfo_exists():
            dialog = tk.Toplevel(self.root)
            dialog.withdraw()
            dialog.title("选择日程日期")
            dialog.transient(self.root)
            dialog.resizable(False, False)
            dialog.configure(background=self.palette["bg"])
            dialog.protocol("WM_DELETE_WINDOW", self.close_schedule_calendar)
            dialog.bind("<Escape>", lambda _event: self.close_schedule_calendar())
            body = ttk.Frame(dialog, padding=20)
            body.pack(fill="both", expand=True)
            ttk.Label(body, text="选择要查看的日期", style="DateTitle.TLabel").pack(pady=(0, 12))
            self.schedule_calendar = InlineCalendar(body, self.schedule_day, self.set_schedule_day, self.palette)
            self.schedule_calendar.pack(fill="both", expand=True)
            footer = ttk.Frame(body)
            footer.pack(fill="x", pady=(14, 0))
            ttk.Button(footer, text="回到今天", command=self.schedule_today).pack(side="left")
            ttk.Button(footer, text="取消", command=self.close_schedule_calendar).pack(side="right")
            self.schedule_calendar_dialog = dialog
        try:
            selected = datetime.strptime(self.schedule_day_var.get().strip(), "%Y-%m-%d").date()
        except ValueError:
            selected = self.schedule_day
        self.schedule_calendar.set_date(selected)
        dialog = self.schedule_calendar_dialog
        dialog.update_idletasks()
        width, height = 440, 430
        x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - width) // 2)
        y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - height) // 2)
        dialog.geometry(f"{width}x{height}+{x}+{y}")
        dialog.deiconify()
        dialog.lift()
        dialog.grab_set()

    def close_schedule_calendar(self):
        if self.schedule_calendar_dialog is None:
            return
        try:
            self.schedule_calendar_dialog.grab_release()
            self.schedule_calendar_dialog.withdraw()
        except tk.TclError:
            pass

    def set_schedule_day(self, selected: date):
        self.schedule_day = selected
        self.schedule_range_start = selected - timedelta(days=1)
        self.schedule_range_end = selected + timedelta(days=1)
        self._schedule_signature = None
        self._schedule_initial_position_pending = True
        self._schedule_restore_day = None
        self.close_schedule_calendar()
        self.refresh_schedule(force=True)

    def _restore_schedule_position(self) -> None:
        """Put the selected/current time near the middle without rebuilding again."""
        canvas = self.schedule_cards.canvas
        canvas.update_idletasks()
        total = max(1, self.schedule_cards.body.winfo_reqheight())
        viewport = max(1, canvas.winfo_height())
        day_to_restore = self._schedule_restore_day
        self._schedule_restore_day = None
        self._schedule_restoring = True
        if day_to_restore is not None:
            for widget in getattr(self.schedule_cards, "_schedule_day_widgets", []):
                if getattr(widget, "_schedule_day", None) == day_to_restore:
                    canvas.yview_moveto(max(0.0, min(1.0, widget.winfo_y() / total)))
                    self._schedule_initial_position_pending = False
                    self.root.after_idle(lambda: setattr(self, "_schedule_restoring", False))
                    return

        target = datetime.now()
        if not (self.schedule_range_start <= target.date() <= self.schedule_range_end):
            target = datetime.combine(self.schedule_day, datetime.min.time()).replace(hour=12)
        cards = getattr(self.schedule_cards, "_schedule_row_widgets", [])
        if cards:
            card = min(cards, key=lambda item: abs((item._schedule_datetime - target).total_seconds()))
            y = card.winfo_y() - max(0, (viewport - card.winfo_height()) // 2)
            canvas.yview_moveto(max(0.0, min(1.0, y / total)))
        self._schedule_initial_position_pending = False
        self.root.after_idle(lambda: setattr(self, "_schedule_restoring", False))

    def _finish_schedule_render(self) -> None:
        """Swap the rebuilt timeline into view only after layout and positioning."""
        try:
            self._restore_schedule_position()
            self.schedule_cards.canvas.update_idletasks()
        except tk.TclError:
            pass

    def _on_schedule_view_change(self, first: float, last: float) -> None:
        if self._schedule_rendering or self._schedule_restoring:
            return
        try:
            top = self.schedule_cards.canvas.canvasy(0) + 28
            headers = getattr(self.schedule_cards, "_schedule_day_widgets", [])
            visible = None
            for widget in headers:
                if widget.winfo_y() <= top:
                    visible = widget._schedule_day
                elif visible is None:
                    visible = widget._schedule_day
                    break
            if visible is not None:
                self.schedule_day = visible
                self.schedule_day_var.set(visible.isoformat())
                self.schedule_day_title_var.set(f"{visible:%Y 年 %m 月 %d 日}")
        except (AttributeError, tk.TclError):
            return

    def _on_schedule_scroll_intent(self, steps: int, first: float, last: float) -> None:
        """Lazy-load only from a real wheel gesture, never from layout callbacks."""
        if self._schedule_rendering or self._schedule_restoring:
            return
        if self._schedule_initial_position_pending or self._schedule_extend_pending:
            return
        direction = -1 if steps < 0 and first <= 0.015 else (1 if steps > 0 and last >= 0.985 else 0)
        if not direction:
            return
        self._schedule_extend_pending = True
        self.root.after_idle(lambda step=direction: self._extend_schedule_window(step))

    def _extend_schedule_window(self, direction: int) -> None:
        self._schedule_extend_pending = False
        if self._schedule_rendering:
            return
        visible = self.schedule_day
        if direction < 0:
            self.schedule_range_start -= timedelta(days=1)
        else:
            self.schedule_range_end += timedelta(days=1)
        # Keep exactly the requested previous/current/next-day window alive.
        # Retiring the farthest day also keeps the number of native Tk child
        # widgets low, which materially improves Windows scroll painting.
        while (self.schedule_range_end - self.schedule_range_start).days + 1 > 3:
            if direction < 0:
                self.schedule_range_end -= timedelta(days=1)
            else:
                self.schedule_range_start += timedelta(days=1)
        self._schedule_restore_day = visible
        self._schedule_signature = None
        self.refresh_schedule(force=True)

    def toggle_occurrence(self, row: dict | None = None):
        row=row or self._selected_schedule()
        if not row: return
        if row.get("override") and row["override"].get("action")=="skip":
            remove_override(BASE_DIR,self.cfg,override_id=row["override"].get("id")); self._set_footer("已开启本次监测。")
        else:
            add_override(BASE_DIR,self.cfg,row["label"],row["original_datetime"],"skip",note="GUI 监测开关"); self._set_footer("已关闭本次监测；MAA 自己的定时器未改动。")
        self.refresh_schedule()

    def toggle_all_occurrences(self, enabled: bool):
        changed = 0
        for row in self.schedule_rows:
            if row["datetime"].date() != self.schedule_day:
                continue
            override = row.get("override") or {}
            if enabled and override.get("action") == "skip":
                changed += len(remove_override(BASE_DIR, self.cfg, override_id=override.get("id")))
            elif not enabled and not row.get("skipped"):
                add_override(BASE_DIR, self.cfg, row["label"], row["original_datetime"], "skip", note="GUI 当天批量开关")
                changed += 1
        state = "开启" if enabled else "关闭"
        self._set_footer(f"已将当天 {changed} 条计划的监测批量{state}。")
        self.refresh_schedule()

    def _selected_schedule(self) -> dict | None:
        messagebox.showinfo("请选择计划", "请直接使用计划卡片上的开关或“修改时间”按钮。", parent=self.root)
        return None

    def skip_occurrence(self):
        row = self._selected_schedule()
        if not row:
            return
        add_override(BASE_DIR, self.cfg, row["label"], row["original_datetime"], "skip", note="GUI")
        self._set_footer("已标记：本次不纳入 Agent 漏跑判断。")
        self.refresh_schedule()

    def move_occurrence(self, row: dict | None = None):
        row = row or self._selected_schedule()
        if not row:
            return
        default = row["datetime"].strftime("%Y-%m-%d %H:%M")
        value = simpledialog.askstring("修改本次预期时间", "输入 YYYY-MM-DD HH:MM（可跨天）：", initialvalue=default, parent=self.root)
        if value is None:
            return
        try:
            target = datetime.strptime(value.strip(), "%Y-%m-%d %H:%M")
        except ValueError:
            messagebox.showerror("格式错误", "请使用 YYYY-MM-DD HH:MM，例如 2026-09-20 23:30。", parent=self.root)
            return
        add_override(BASE_DIR, self.cfg, row["label"], row["original_datetime"], "move", new_dt=target, note="GUI")
        self._set_footer(f"已将本次预期调整到 {target:%Y-%m-%d %H:%M}。")
        self.refresh_schedule()

    def clear_override(self):
        row = self._selected_schedule()
        if not row:
            return
        ov = row.get("override")
        if not ov:
            self._set_footer("选中的计划没有一次性例外。")
            return
        remove_override(BASE_DIR, self.cfg, override_id=ov.get("id"))
        self._set_footer("已撤销一次性例外。")
        self.refresh_schedule()

    def _render_resource_cards(self, rows: list[dict]) -> None:
        self.snapshot_cards.clear()
        abnormal = [row for row in rows if row.get("abnormal")]
        if not abnormal:
            ttk.Label(self.snapshot_cards.body, text="这一天没有异常采样。", style="Muted.TLabel", padding=12).pack(fill="x")
            return
        for row in abnormal:
            used, available, _total = memory_values(row)
            when = datetime.fromtimestamp(float(row.get("epoch", 0)))
            reasons = "；".join(row.get("abnormal_reasons") or []) or f"内存 {row.get('ram', 0):.0f}%"
            apps = row.get("top_apps") or row.get("top_processes") or []
            top = "   ".join(f"{item.get('name')} {item.get('mb', 0)/1024:.1f} GB" for item in apps[:3]) or "没有进程明细"
            background = self.palette["panel"]
            card = tk.Frame(self.snapshot_cards.body, background=background, highlightthickness=1, highlightbackground=self.palette["grid"], padx=12, pady=8)
            card.pack(fill="x", padx=4, pady=4)
            time_label = tk.Label(card, text=f"{when:%H:%M}", background=background, foreground=self.palette["warning"], font=("Segoe UI", 15, "bold"))
            time_label.grid(row=0, column=0, rowspan=2, sticky="w", padx=(0, 16))
            reason_label = tk.Label(card, text=reasons, background=background, foreground=self.palette["fg"], font=("Microsoft YaHei UI", 10, "bold"))
            reason_label.grid(row=0, column=1, sticky="w")
            memory_label = tk.Label(card, text=f"已用 {used/1024:.1f} GB · 可用 {available/1024:.1f} GB", background=background, foreground=self.palette["muted"])
            memory_label.grid(row=1, column=1, sticky="w")
            top_label = tk.Label(card, text=top, background=background, foreground=self.palette["muted"], anchor="e")
            top_label.grid(row=0, column=2, rowspan=2, sticky="e", padx=(18, 0))
            card.columnconfigure(1, weight=1)
            callback = lambda _event, item=row: self.show_resource_detail(item)
            self._bind_card_action([card, time_label, reason_label, memory_label, top_label], callback)
        self.snapshot_cards.refresh_scrollregion()

    def refresh_resources(self):
        start_hour=int(self.cfg.get("operational_day_start_hour",4))
        rows=load_resource_calendar_day(BASE_DIR,self.resource_day,start_hour)
        self.resource_rows = rows
        start=datetime.combine(self.resource_day,datetime.min.time())
        self.resource_day_var.set(f"{start:%Y-%m-%d 00:00} → {(start+timedelta(days=1)):%m-%d 00:00}")
        self.resource_chart.set_data(rows,self.resource_day,float(self.cfg.get("memory_pressure_percent",88)))
        if rows:
            ram=[float(x.get("ram",0)) for x in rows]
            used_values=[memory_values(x)[0] for x in rows]
            self.resource_summary_var.set(f"采样 {len(rows)} 点 · 内存最低/最高 {min(ram):.0f}% / {max(ram):.0f}% · 峰值已用 {max(used_values)/1024:.1f} GB · 异常点 {sum(bool(x.get('abnormal')) for x in rows)}")
        else:
            self.resource_summary_var.set("这一天还没有采样；Agent 运行后正常状态每 5 分钟、异常状态每 1 分钟记录。")
        self._render_resource_cards(rows)

    def change_resource_day(self,delta:int):
        self.resource_day += timedelta(days=delta); self.refresh_resources()

    def resource_today(self):
        self.resource_day=datetime.now().date(); self.refresh_resources()

    def open_selected_snapshot(self, _event=None):
        return None

    def load_news_center(self, result: dict | None = None):
        data = result or load_news_cache(BASE_DIR, self.cfg)
        self.news_items = list(data.get("items") or [])
        for iid in self.news_tree.get_children():
            self.news_tree.delete(iid)
        for index, item in enumerate(self.news_items):
            self.news_tree.insert("", "end", iid=str(index), values=(item.get("display_time") or "—", item.get("title") or "官网公告"))
        updated = data.get("updated_at") or "尚未更新"
        self.news_center_status.set(f"本地 {len(self.news_items)} 条 · 更新：{updated}")
        if self.news_items:
            self.news_tree.selection_set("0")
            self.news_tree.focus("0")
            self.show_selected_news()
        else:
            self._set_text(self.news_detail, "还没有本地公告。点击“立即更新”会首次拉取 20 条，并逐条获取正文。")

    def show_selected_news(self, _event=None):
        selected = self.news_tree.selection()
        if not selected:
            return
        index = int(selected[0])
        if not 0 <= index < len(self.news_items):
            return
        item = self.news_items[index]
        parts = [
            str(item.get("title") or "官网公告"),
            str(item.get("display_time") or "时间未知"),
            str(item.get("url") or ""),
            "",
        ]
        brief = str(item.get("brief") or "").strip()
        content = str(item.get("content") or "").strip()
        if brief:
            parts.extend(("摘要", brief, ""))
        parts.extend(("正文", content or "（正文尚未成功缓存；再次点击更新可重试。）"))
        self._set_text(self.news_detail, "\n".join(parts))

    def _news_progress_update(self, current: int, total: int, message: str):
        def apply_update():
            self.news_progress.configure(maximum=max(1, total), value=min(current, max(1, total)))
            self.news_center_status.set(message)
        self.root.after(0, apply_update)

    def refresh_news_center(self, full=False):
        if full and not messagebox.askokcancel(
            "全部重新拉取",
            "将重新拉取公告列表和正文；耗时与请求量明显高于普通更新。继续吗？",
            parent=self.root,
        ):
            return
        if not self.news_items:
            proceed = messagebox.askokcancel(
                "首次更新官网公告",
                "首次更新将拉取约 20 条公告并逐条读取正文。若官网触发人机验证，更新可能中断；请稍后再次点击更新重试。",
                parent=self.root,
            )
            if not proceed:
                return
        self.news_refresh_button.configure(state="disabled")
        self.news_full_refresh_button.configure(state="disabled")
        self.news_progress.configure(maximum=1, value=0)

        def worker():
            result = refresh_news_cache(BASE_DIR, self.cfg, progress=self._news_progress_update, full=full)
            self.root.after(0, lambda: self._finish_news_refresh(result))

        threading.Thread(target=worker, name="news-cache-refresh", daemon=True).start()

    def _finish_news_refresh(self, result: dict):
        self.news_refresh_button.configure(state="normal")
        self.news_full_refresh_button.configure(state="normal")
        self.load_news_center(result)
        errors = list(result.get("errors") or [])
        if result.get("status") == "error":
            messagebox.showerror("公告更新失败", errors[0] if errors else "无法获取官网公告，已保留原有缓存。", parent=self.root)
        elif errors:
            messagebox.showwarning("公告更新完成", f"已缓存 {result.get('total', 0)} 条；其中 {len(errors)} 条正文未成功，稍后可再次更新。", parent=self.root)
        else:
            messagebox.showinfo("公告更新完成", f"本地缓存 {result.get('total', 0)} 条，新增 {result.get('new_count', 0)} 条。", parent=self.root)

    def show_resource_detail(self, row: dict):
        window = tk.Toplevel(self.root)
        window.title("资源采样详细分析")
        window.geometry("920x680")
        window.minsize(760, 560)
        window.configure(background=self.palette["bg"])
        try:
            window.iconbitmap(default=str(ICON_ICO))
        except tk.TclError:
            pass
        when=datetime.fromtimestamp(float(row.get("epoch",0)))
        used,available,total=memory_values(row)
        reasons="；".join(row.get("abnormal_reasons") or []) or "该点未达到异常阈值"
        detail = ScrollableFrame(window, palette=self.palette)
        detail.pack(fill="both", expand=True, padx=14, pady=14)
        heading = tk.Frame(detail.body, background=self.palette["panel"], padx=16, pady=14, highlightthickness=1, highlightbackground=self.palette["grid"])
        heading.pack(fill="x", pady=(0, 10))
        tk.Label(heading, text=f"{when:%Y 年 %m 月 %d 日  %H:%M:%S}", background=self.palette["panel"], foreground=self.palette["fg"], font=("Microsoft YaHei UI", 16, "bold")).pack(anchor="w")
        tk.Label(heading, text=reasons, background=self.palette["panel"], foreground=self.palette["bad"] if row.get("abnormal") else self.palette["muted"], font=("Microsoft YaHei UI", 10), justify="left", wraplength=820).pack(anchor="w", pady=(6, 0))

        metrics = ttk.Frame(detail.body)
        metrics.pack(fill="x", pady=(0, 10))
        for column in range(4):
            metrics.columnconfigure(column, weight=1)
        metric_rows = (
            ("系统内存", f"{row.get('ram',0):.0f}%", f"已用 {used/1024:.2f} / {total/1024:.2f} GB"),
            ("Agent", f"{row.get('agent_mb',0)/1024:.2f} GB", "管理器监控进程"),
            ("MAA", f"{row.get('maa_mb',0)/1024:.2f} GB", f"{row.get('maa_count',0)}/{row.get('expected_maa_count','?')} 个实例"),
            ("MuMu", f"{row.get('mumu_mb',0)/1024:.2f} GB", f"{row.get('mumu_count',0)} 个进程"),
        )
        for column, (title, value, caption) in enumerate(metric_rows):
            card = tk.Frame(metrics, background=self.palette["panel"], padx=12, pady=10, highlightthickness=1, highlightbackground=self.palette["grid"])
            card.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 5, 0))
            tk.Label(card, text=title, background=self.palette["panel"], foreground=self.palette["muted"], font=("Microsoft YaHei UI", 9)).pack(anchor="w")
            tk.Label(card, text=value, background=self.palette["panel"], foreground=self.palette["fg"], font=("Segoe UI", 15, "bold")).pack(anchor="w", pady=(3, 1))
            tk.Label(card, text=caption, background=self.palette["panel"], foreground=self.palette["muted"], font=("Microsoft YaHei UI", 8)).pack(anchor="w")

        def add_memory_card(parent, title: str, subtitle: str, memory_mb: float):
            card = tk.Frame(parent, background=self.palette["panel"], padx=13, pady=9, highlightthickness=1, highlightbackground=self.palette["grid"])
            card.pack(fill="x", pady=3)
            tk.Label(card, text=f"{memory_mb/1024:.2f} GB", background=self.palette["panel"], foreground=self.palette["ram"], font=("Segoe UI", 11, "bold")).pack(side="right", padx=(12, 0))
            text = tk.Frame(card, background=self.palette["panel"])
            text.pack(side="left", fill="x", expand=True)
            tk.Label(text, text=title, background=self.palette["panel"], foreground=self.palette["fg"], font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
            tk.Label(text, text=subtitle, background=self.palette["panel"], foreground=self.palette["muted"], font=("Microsoft YaHei UI", 8)).pack(anchor="w", pady=(2, 0))

        apps=row.get("top_apps") or []
        if apps:
            app_frame=ttk.LabelFrame(detail.body,text="按应用归并的内存大户",padding=8); app_frame.pack(fill="x",pady=(0,10))
            for item in apps:
                add_memory_card(app_frame, str(item.get("name") or "未知应用"), f"{item.get('category') or '未分类'} · {item.get('count',1)} 个进程", float(item.get("mb", 0)))
        processes=row.get("top_processes") or []
        proc_frame=ttk.LabelFrame(detail.body,text="异常发生时的单进程快照",padding=8); proc_frame.pack(fill="both",expand=True)
        if not processes:
            ttk.Label(proc_frame, text="这个采样点没有保存单进程明细。", style="Muted.TLabel", padding=10).pack(fill="x")
        for item in processes:
            add_memory_card(proc_frame, str(item.get("name") or "未知进程"), f"PID {item.get('pid','—')} · {item.get('category') or '未分类'}", float(item.get("mb", 0)))
        detail.refresh_scrollregion()

    def load_settings(self):
        self.start_with_windows_var.set(startup_shortcut_path().exists() or startup_marker_path().exists())
        self.start_agent_with_gui_var.set(bool(self.cfg.get("gui_start_agent_on_launch",True)))
        self.start_minimized_var.set(bool(self.cfg.get("gui_start_minimized",True)))
        self.dark_mode_var.set(bool(self.cfg.get("gui_dark_mode",True)))
        self.memory_alert_var.set(bool(self.cfg.get("enable_memory_alert",True)))
        self.proxy_check_var.set(bool(self.cfg.get("enable_proxy_healthcheck",True)))
        paths=self.cfg.get("launch_paths") or {}; autos=self.cfg.get("auto_start_apps_with_agent") or {}
        for label in self.launch_path_vars:
            self.launch_path_vars[label].set(str(paths.get(label,""))); self.autostart_vars[label].set(bool(autos.get(label,False)))
        aliases=self.cfg.get("maa_instance_aliases") or {}
        for label,var in self.instance_alias_vars.items():
            var.set(str(aliases.get(label,"")))
        self.news_enabled_var.set(bool(self.cfg.get("enable_news_monitor",True)))
        self.news_times_var.set(", ".join(str(x) for x in self.cfg.get("news_check_times",["00:05","12:05"])))
        try:
            state=json.loads((STATE_DIR/"news_monitor.json").read_text(encoding="utf-8"))
            self.news_status_var.set(f"上次检查：{state.get('last_checked_at','—')} · {state.get('last_result','—')}")
        except Exception:
            self.news_status_var.set("尚未建立公告基线；Agent 下一次检查时会初始化，不会补发旧公告。")
        self.bark_retry_attempts_var.set(str(self.cfg.get("bark_retry_attempts",3)))
        self.bark_retry_sleep_var.set(str(self.cfg.get("bark_retry_sleep_seconds",3)))
        self.bark_retry_infinite_var.set(bool(self.cfg.get("bark_retry_infinite",False)))

    def choose_launch_path(self,label):
        path=filedialog.askopenfilename(parent=self.root,title=f"选择 {label} 可执行文件",filetypes=(("可执行文件","*.exe"),("所有文件","*.*")))
        if path: self.launch_path_vars[label].set(path)

    def launch_configured(self,label):
        path=Path(str((self.cfg.get("launch_paths") or {}).get(label) or (self.launch_path_vars.get(label).get() if label in self.launch_path_vars else "")))
        if not path.is_file():
            messagebox.showwarning("路径未配置",f"请先在“设置”页选择 {label} 的 .exe 路径。",parent=self.root); return
        try:
            target=os.path.normcase(os.path.abspath(str(path)))
            for proc in psutil.process_iter(["exe"]):
                try:
                    if proc.info.get("exe") and os.path.normcase(os.path.abspath(proc.info["exe"]))==target:
                        self._set_footer(f"{label} 已在运行（PID {proc.pid}）。"); return
                except (psutil.Error,OSError): pass
            subprocess.Popen([str(path)],cwd=str(path.parent),creationflags=NO_WINDOW,close_fds=True)
            self._set_footer(f"已启动 {label}。")
        except OSError as exc:
            messagebox.showerror("启动失败",str(exc),parent=self.root)

    def save_settings(self):
        values=[x.strip() for x in self.news_times_var.get().replace("，",",").split(",") if x.strip()]
        if not values or any(not TIME_RE.match(x) for x in values):
            messagebox.showerror("时间格式错误","官网检查时间请使用 HH:MM，并用逗号分隔。",parent=self.root); return
        try:
            retry_attempts=int(self.bark_retry_attempts_var.get().strip())
            retry_sleep=float(self.bark_retry_sleep_var.get().strip())
            if retry_attempts < 1 or retry_sleep < 0:
                raise ValueError("重试次数至少为 1，间隔不能为负数。")
        except ValueError as exc:
            messagebox.showerror("Bark 重试设置错误",str(exc),parent=self.root); return
        old_dark=bool(self.cfg.get("gui_dark_mode",True))
        self.cfg["gui_start_agent_on_launch"]=self.start_agent_with_gui_var.get()
        self.cfg["gui_start_minimized"]=self.start_minimized_var.get()
        self.cfg["gui_dark_mode"]=self.dark_mode_var.get()
        self.cfg["enable_memory_alert"]=self.memory_alert_var.get()
        self.cfg["enable_proxy_healthcheck"]=self.proxy_check_var.get()
        self.cfg["launch_paths"]={k:v.get().strip() for k,v in self.launch_path_vars.items()}
        self.cfg["auto_start_apps_with_agent"]={k:v.get() for k,v in self.autostart_vars.items()}
        self.cfg["maa_instance_aliases"]={k:v.get().strip() for k,v in self.instance_alias_vars.items()}
        self.cfg["enable_news_monitor"]=self.news_enabled_var.get(); self.cfg["news_check_times"]=values
        self.cfg["bark_retry_attempts"]=retry_attempts; self.cfg["bark_retry_sleep_seconds"]=retry_sleep; self.cfg["bark_retry_infinite"]=self.bark_retry_infinite_var.get()
        try:
            atomic_save_config(self.cfg)
            marker=startup_marker_path()
            if self.start_with_windows_var.get():
                create_windows_shortcut(startup_shortcut_path(),startup=True)
                marker.unlink(missing_ok=True)
            else:
                startup_shortcut_path().unlink(missing_ok=True); marker.unlink(missing_ok=True)
        except Exception as exc:
            messagebox.showerror("保存失败",str(exc),parent=self.root); return
        if old_dark != self.cfg["gui_dark_mode"]:
            self._apply_runtime_theme()
            self._dashboard_schedule_signature = None
            self._schedule_signature = None
            self.refresh_dashboard(); self.refresh_schedule(force=True); self.refresh_resources()
        self.config_dirty=False; self.populate_config(); self._set_footer("设置已保存；Agent 下一轮自动读取。")

    def install_shortcuts(self):
        try:
            desktop=Path(os.environ.get("USERPROFILE",str(Path.home())))/"Desktop"/"MAA Agent 管理器.lnk"
            startmenu=Path(os.environ.get("APPDATA",""))/"Microsoft"/"Windows"/"Start Menu"/"Programs"/"MAA Agent 管理器.lnk"
            create_windows_shortcut(desktop); create_windows_shortcut(startmenu)
            self._set_footer("桌面与开始菜单快捷方式已修复。")
        except Exception as exc:
            messagebox.showerror("快捷方式创建失败",str(exc),parent=self.root)

    def run_diagnostic(self,kind,target=""):
        if not validated_agent_for_dir(BASE_DIR):
            messagebox.showwarning("Agent 未运行","通知测试需要正在运行的 Agent。请先启动 Agent。",parent=self.root); return
        command_id=enqueue_diagnostic(BASE_DIR,kind,target)
        text=f"测试请求已发送（{command_id[:8]}），Agent 会立即处理。"
        self.diagnostic_status_var.set(text); self._set_footer(text)

    def run_all_diagnostics(self):
        if messagebox.askyesno("确认连续推送？","这会向手机连续发送多条明确标注“测试”的通知，是否继续？",parent=self.root):
            self.run_diagnostic("all")

    def refresh_diagnostic_status(self):
        try:
            row=json.loads((STATE_DIR/"diagnostic_last_result.json").read_text(encoding="utf-8"))
            self.diagnostic_status_var.set(f"{row.get('finished_at')} · {'成功' if row.get('ok') else '失败'} · {row.get('message')}")
        except Exception:
            pass

    def load_timing(self):
        self.enable_daily_var.set(bool(self.cfg.get("enable_daily_report", True)))
        self.daily_time_var.set(str(self.cfg.get("daily_report_time", "23:50")))
        self.deepseek_daily_var.set(bool(self.cfg.get("deepseek_daily_report", True)))
        self.proxy_check_var.set(bool(self.cfg.get("enable_proxy_healthcheck", True)))
        for _label, key, _kind in TIMING_FIELDS:
            self.timing_vars[key].set(str(self.cfg.get(key, "")))

    def save_timing(self):
        daily_time = self.daily_time_var.get().strip()
        if not TIME_RE.match(daily_time):
            messagebox.showerror("时间格式错误", "日报时间必须是 HH:MM，例如 23:50。", parent=self.root)
            return
        updates = {
            "enable_daily_report": self.enable_daily_var.get(),
            "daily_report_time": daily_time,
            "deepseek_daily_report": self.deepseek_daily_var.get(),
            "enable_proxy_healthcheck": self.proxy_check_var.get(),
        }
        try:
            for _label, key, kind in TIMING_FIELDS:
                text = self.timing_vars[key].get().strip()
                value = int(text) if kind == "int" else float(text)
                if value < 0:
                    raise ValueError(f"{key} 不能为负数")
                updates[key] = value
        except ValueError as exc:
            messagebox.showerror("数值错误", str(exc), parent=self.root)
            return
        self.cfg.update(updates)
        try:
            atomic_save_config(self.cfg)
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc), parent=self.root)
            return
        self.config_dirty = False
        self.populate_config()
        self._set_footer("时间策略已保存；Agent 下一轮自动读取。")

    def _display_value(self, key: str, value) -> str:
        if key in SECRET_NAMES:
            return "••••••（已配置）" if value else "（未配置）"
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return str(value)

    def populate_config(self):
        for iid in self.config_tree.get_children():
            self.config_tree.delete(iid)
        for key in sorted(self.cfg):
            value = self.cfg[key]
            self.config_tree.insert("", "end", iid=key, values=(key, self._display_value(key, value), type(value).__name__))

    def show_config_help(self,_event=None):
        selected=self.config_tree.selection()
        if not selected: return
        key=selected[0]
        self.config_help_var.set(CONFIG_HELP.get(key,"高级配置项。双击可编辑；不确定含义时建议先保留当前值。"))

    def edit_config_value(self):
        selected = self.config_tree.selection()
        if not selected:
            messagebox.showinfo("请选择配置", "请先选中一个配置项。", parent=self.root)
            return
        key = selected[0]
        old = self.cfg[key]
        initial = "" if key in SECRET_NAMES else (json.dumps(old, ensure_ascii=False) if isinstance(old, (dict, list, bool)) else str(old))
        prompt = f"编辑 {key}\n复杂值请输入合法 JSON。"
        if key in SECRET_NAMES:
            prompt += "\n留空表示保持原密钥不变。"
        value = simpledialog.askstring("编辑配置", prompt, initialvalue=initial, parent=self.root, show="*" if key in SECRET_NAMES else None)
        if value is None or (key in SECRET_NAMES and value == ""):
            return
        try:
            if isinstance(old, bool):
                lowered = value.strip().lower()
                if lowered not in {"true", "false", "1", "0", "yes", "no"}:
                    raise ValueError("布尔值请输入 true 或 false")
                parsed = lowered in {"true", "1", "yes"}
            elif isinstance(old, int) and not isinstance(old, bool):
                parsed = int(value)
            elif isinstance(old, float):
                parsed = float(value)
            elif isinstance(old, (dict, list)):
                parsed = json.loads(value)
                if not isinstance(parsed, type(old)):
                    raise ValueError(f"值必须保持为 {type(old).__name__}")
            else:
                parsed = value
        except (ValueError, json.JSONDecodeError) as exc:
            messagebox.showerror("值无效", str(exc), parent=self.root)
            return
        self.cfg[key] = parsed
        self.config_dirty = True
        self.populate_config()
        self.config_tree.selection_set(key)
        self._set_footer("配置已在界面中修改；点击“保存 config.json”后生效。")

    def save_config(self):
        try:
            atomic_save_config(self.cfg)
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc), parent=self.root)
            return
        self.config_dirty = False
        self.load_timing()
        self._set_footer("config.json 已保存，并创建/更新 config.json.bak。")

    def reload_config(self):
        if self.config_dirty and not messagebox.askyesno("放弃未保存修改？", "界面里有未保存修改，确定重新读取磁盘文件？", parent=self.root):
            return
        try:
            self.cfg = load_config()
        except Exception as exc:
            messagebox.showerror("读取失败", str(exc), parent=self.root)
            return
        self.config_dirty = False
        self.populate_config()
        self.load_timing()
        self.load_settings()
        self._dashboard_schedule_signature = None
        self._schedule_signature = None
        self.refresh_dashboard()
        self.refresh_schedule(force=True)
        self._set_footer("已重新读取 config.json。")

    def refresh_logs(self):
        self._set_text(self.log_text, filter_log_text(tail_text(LOG_DIR / self.log_choice.get()),self.full_log_mode.get()))
        self.log_text.see("end")

    def refresh_all(self, full: bool = False):
        try:
            if not self.config_dirty:
                self.cfg = load_config()
            self.refresh_dashboard()
            try:
                selected_tab = self.notebook.nametowidget(self.notebook.select())
            except Exception:
                selected_tab = None
            if full or selected_tab is self.schedule_tab:
                self.refresh_schedule()
            if full or selected_tab is self.resource_tab:
                self.refresh_resources()
            if full:
                self.load_timing()
                self.load_settings()
                self.populate_config()
            self.refresh_diagnostic_status()
            if self.auto_log_var.get():
                self.refresh_logs()
            if SHOW_REQUEST_FILE.exists():
                SHOW_REQUEST_FILE.unlink(missing_ok=True); self.show_window()
            self._set_footer("状态已刷新。")
        except Exception as exc:
            self._set_footer(f"刷新失败：{exc}")
        finally:
            if self.refresh_job:
                self.root.after_cancel(self.refresh_job)
            self.refresh_job = self.root.after(5000, self.refresh_all)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    gui_mutex = AgentMutex(r"Local\MAA-Agent-Manager-8E79F134")
    if gui_mutex.acquire():
        try:
            if not CONFIG_PATH.exists() and not run_first_setup(BASE_DIR):
                raise SystemExit(0)
            ManagerApp().run()
        finally:
            gui_mutex.release()
    else:
        STATE_DIR.mkdir(parents=True,exist_ok=True)
        SHOW_REQUEST_FILE.write_text(datetime.now().isoformat(timespec="seconds"),encoding="utf-8")
