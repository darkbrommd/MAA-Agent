"""Shared process lifecycle helpers for MAA Agent.

The original implementation treated *any* process whose command line happened
to contain an argument named ``agent.py`` as a running MAA Agent.  Editors and
diagnostic commands therefore produced false duplicate-process detections.

This module keeps lifecycle rules in one place for the agent, stop helper and
GUI.  A Windows named mutex closes the simultaneous-start race, while the
legacy-process fallback only accepts Python interpreters executing an exact
``agent.py`` script path.
"""

from __future__ import annotations

import ctypes
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import psutil


MUTEX_NAME = r"Local\MAA-Agent-Watchdog-7D3A2D45"
ERROR_ALREADY_EXISTS = 183
PYTHON_PROCESS_PREFIXES = ("python", "pythonw")
PACKAGED_EXE_NAME = "MAA-Agent.exe"
PACKAGED_AGENT_FLAG = "--maa-agent-background"


def agent_script_path(base_dir: Path | str) -> Path:
    """Return the current agent entry point, with legacy root-layout support."""
    base = Path(base_dir).resolve()
    nested = base / "app" / "agent.py"
    return nested if nested.is_file() else base / "agent.py"


def agent_script_candidates(base_dir: Path | str) -> tuple[Path, Path]:
    """Return current and pre-v1.1.2 entry points for one installation."""
    base = Path(base_dir).resolve()
    return base / "app" / "agent.py", base / "agent.py"


def agent_entry_candidates(base_dir: Path | str) -> tuple[Path, ...]:
    base = Path(base_dir).resolve()
    return (base / PACKAGED_EXE_NAME, *agent_script_candidates(base))


def agent_entry_path(base_dir: Path | str) -> Path:
    """Prefer the packaged executable and fall back to the Python source entry."""
    for candidate in agent_entry_candidates(base_dir):
        if candidate.is_file():
            return candidate
    return agent_script_path(base_dir)


def is_script_for_installation(script: Path | str, base_dir: Path | str) -> bool:
    """Whether an Agent script belongs to this installation, including legacy layout."""
    return any(_same_path(script, candidate) for candidate in agent_entry_candidates(base_dir))


def installation_dir_for_script(script: Path | str) -> Path:
    path = Path(script).resolve()
    if path.suffix.lower() == ".exe":
        return path.parent
    return path.parent.parent if path.parent.name.lower() == "app" else path.parent


def _same_path(left: Path | str, right: Path | str) -> bool:
    try:
        return os.path.normcase(str(Path(left).resolve())) == os.path.normcase(str(Path(right).resolve()))
    except Exception:
        return os.path.normcase(os.path.abspath(str(left))) == os.path.normcase(os.path.abspath(str(right)))


def _is_python_process_name(name: str | None) -> bool:
    stem = Path(name or "").stem.lower()
    return stem.startswith(PYTHON_PROCESS_PREFIXES)


def script_from_cmdline(
    cmdline: Iterable[str] | None,
    cwd: str | os.PathLike[str] | None = None,
    process_name: str | None = None,
) -> Path | None:
    """Return the executed agent.py only for an actual Python interpreter."""
    if process_name is not None and not _is_python_process_name(process_name):
        return None
    args = [str(x) for x in (cmdline or [])]
    if not args:
        return None
    if process_name is None and not _is_python_process_name(Path(args[0].strip('"')).name):
        return None
    # The first argument is the interpreter.  Only inspect subsequent arguments,
    # and stop at the first Python script argument so strings in -c code cannot
    # impersonate a process.
    for arg in args[1:]:
        clean = arg.strip('"')
        if clean.startswith("-"):
            continue
        candidate = Path(clean)
        if candidate.suffix.lower() != ".py":
            continue
        if candidate.name.lower() != "agent.py":
            return None
        if not candidate.is_absolute():
            if not cwd:
                return None
            candidate = Path(cwd) / candidate
        try:
            return candidate.resolve()
        except Exception:
            return candidate.absolute()
    return None


def process_agent_info(proc: psutil.Process) -> dict | None:
    try:
        name = proc.name()
        cmdline = [str(value) for value in proc.cmdline()]
        if _is_python_process_name(name):
            try:
                cwd = proc.cwd()
            except psutil.Error:
                cwd = None
            script = script_from_cmdline(cmdline, cwd, name)
        else:
            if Path(name).name.lower() != PACKAGED_EXE_NAME.lower() or PACKAGED_AGENT_FLAG not in cmdline[1:]:
                return None
            try:
                script = Path(proc.exe()).resolve()
            except (psutil.Error, OSError):
                script = Path(cmdline[0]).resolve() if cmdline else None
        if script is None:
            return None
        try:
            parent_pid = proc.ppid()
        except (AttributeError, psutil.Error):
            parent_pid = None
        return {
            "pid": proc.pid,
            "ppid": parent_pid,
            "name": name,
            "script": str(script),
            "create_time": proc.create_time(),
        }
    except (psutil.Error, OSError, ValueError):
        return None


def agent_process_info(pid: int, expected_script: Path | str | None = None) -> dict | None:
    try:
        row = process_agent_info(psutil.Process(int(pid)))
    except (psutil.Error, OSError, ValueError):
        return None
    if row is None:
        return None
    if expected_script is not None and not _same_path(row["script"], expected_script):
        return None
    return row


def find_running_agents(
    exclude_pid: int | None = None,
    exclude_pids: Iterable[int] | None = None,
) -> list[dict]:
    excluded = {int(x) for x in (exclude_pids or [])}
    if exclude_pid is not None:
        excluded.add(int(exclude_pid))
    rows: list[dict] = []
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            if proc.pid in excluded:
                continue
            row = process_agent_info(proc)
            if row is not None and installation_dir_for_script(row["script"]).name.lower().startswith("maa-agent"):
                rows.append(row)
        except (psutil.Error, OSError, ValueError):
            continue
    # Python venv launchers on Windows can remain as a parent process with the
    # exact same command line. Keep the child that runs Python, otherwise a
    # legacy-layout force stop could terminate only the launcher shell.
    by_pid = {int(row["pid"]): row for row in rows}
    launcher_pids = {
        int(row["ppid"])
        for row in rows
        if row.get("ppid") in by_pid
        and _same_path(row["script"], by_pid[int(row["ppid"])]["script"])
    }
    rows = [row for row in rows if int(row["pid"]) not in launcher_pids]
    rows.sort(key=lambda x: (x.get("create_time", 0), x["pid"]))
    return rows


class AgentMutex:
    """A process-lifetime, cross-directory singleton lock on Windows."""

    def __init__(self, name: str = MUTEX_NAME):
        self.name = name
        self.handle = None
        self.acquired = False

    def acquire(self) -> bool:
        if os.name != "nt":
            # The deployed target is Windows. Tests and ad-hoc non-Windows use
            # still get the exact-process scan as a fallback.
            self.acquired = True
            return True
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        handle = kernel32.CreateMutexW(None, True, self.name)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        already_exists = ctypes.get_last_error() == ERROR_ALREADY_EXISTS
        if already_exists:
            kernel32.CloseHandle(handle)
            return False
        self.handle = handle
        self.acquired = True
        return True

    def release(self) -> None:
        if not self.acquired:
            return
        if os.name == "nt" and self.handle:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.ReleaseMutex(self.handle)
            kernel32.CloseHandle(self.handle)
        self.handle = None
        self.acquired = False


def runtime_file(base_dir: Path | str) -> Path:
    return Path(base_dir) / "state" / "agent_runtime.json"


def pid_file(base_dir: Path | str) -> Path:
    return Path(base_dir) / "state" / "agent.pid"


def stop_file(base_dir: Path | str) -> Path:
    return Path(base_dir) / "state" / "stop.request"


def write_runtime_record(base_dir: Path | str, pid: int | None = None, entry_path: Path | str | None = None) -> dict:
    base = Path(base_dir).resolve()
    state = base / "state"
    state.mkdir(parents=True, exist_ok=True)
    pid = int(pid or os.getpid())
    proc = psutil.Process(pid)
    record = {
        "pid": pid,
        "create_time": proc.create_time(),
        "script": str(Path(entry_path).resolve() if entry_path else agent_entry_path(base)),
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    tmp = runtime_file(base).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(runtime_file(base))
    # Keep the legacy plain PID file for existing tools, but never trust it
    # without validating the process identity.
    pid_file(base).write_text(str(pid), encoding="utf-8")
    return record


def read_runtime_record(base_dir: Path | str) -> dict | None:
    path = runtime_file(base_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def validated_agent_for_dir(base_dir: Path | str) -> dict | None:
    base = Path(base_dir).resolve()
    record = read_runtime_record(base)
    if record:
        try:
            row = agent_process_info(int(record["pid"]))
            if (
                row
                and is_script_for_installation(row["script"], base)
                and abs(float(row["create_time"]) - float(record["create_time"])) < 1.0
            ):
                return row
        except (KeyError, TypeError, ValueError):
            pass
    # Backward-compatible fallback for the old plain PID file.
    try:
        legacy_pid = int(pid_file(base).read_text(encoding="utf-8").strip())
        row = agent_process_info(legacy_pid)
        if row and is_script_for_installation(row["script"], base):
            return row
    except Exception:
        pass
    # A PID file may have been lost, but the exact Python process is still a
    # valid target. Never select a sibling installation here.
    for row in find_running_agents():
        if is_script_for_installation(row["script"], base):
            return row
    return None


def clear_runtime_files(base_dir: Path | str, expected_pid: int | None = None) -> None:
    base = Path(base_dir)
    record = read_runtime_record(base)
    if expected_pid is not None and record:
        try:
            if int(record.get("pid", -1)) != int(expected_pid):
                return
        except (TypeError, ValueError):
            return
    for path in (runtime_file(base), pid_file(base)):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


@dataclass
class StopResult:
    status: str
    message: str
    pid: int | None = None


def request_stop(base_dir: Path | str, timeout_seconds: float = 20, force: bool = False) -> StopResult:
    base = Path(base_dir).resolve()
    row = validated_agent_for_dir(base)
    if row is None:
        clear_runtime_files(base)
        try:
            stop_file(base).unlink(missing_ok=True)
        except OSError:
            pass
        return StopResult("not_running", "Agent 没有运行。")

    pid = int(row["pid"])
    target_script = Path(row["script"])
    stop_file(base).parent.mkdir(parents=True, exist_ok=True)
    stop_file(base).write_text(
        json.dumps({"requested_at": time.time(), "target_pid": pid}),
        encoding="utf-8",
    )
    deadline = time.monotonic() + max(0, timeout_seconds)
    while time.monotonic() < deadline:
        if agent_process_info(pid, target_script) is None:
            clear_runtime_files(base, expected_pid=pid)
            return StopResult("stopped", "Agent 已正常停止。", pid)
        time.sleep(0.2)

    if force:
        # Revalidate immediately before termination so PID reuse cannot kill an
        # unrelated process.
        row = agent_process_info(pid, target_script)
        if row is not None:
            try:
                psutil.Process(pid).terminate()
                psutil.Process(pid).wait(timeout=5)
                clear_runtime_files(base, expected_pid=pid)
                return StopResult("forced", "Agent 未及时响应，已安全强制停止。", pid)
            except (psutil.Error, OSError) as exc:
                return StopResult("error", f"强制停止失败：{exc}", pid)
    return StopResult("timeout", "Agent 已收到停止请求，但 20 秒内仍未退出。", pid)
