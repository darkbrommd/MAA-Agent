"""Compatibility helpers for resource-pressure whitelist entries."""

from __future__ import annotations

import os
import re
from pathlib import Path, PureWindowsPath


def normalize_resource_whitelist(config: dict) -> list[dict]:
    """Return stable whitelist records, migrating the legacy name-only list."""
    if "resource_whitelist" in config:
        raw_entries = config.get("resource_whitelist") or []
    else:
        raw_entries = config.get("expected_heavy_process_names") or []

    result: list[dict] = []
    seen: set[str] = set()
    for raw in raw_entries:
        if isinstance(raw, str):
            process_name = raw.strip()
            process_regex = ""
            path = ""
            name = Path(process_name).stem or process_name
            enabled = True
        elif isinstance(raw, dict):
            path = str(raw.get("path") or "").strip()
            process_name = str(raw.get("process_name") or (Path(path).name if path else "")).strip()
            process_regex = str(raw.get("process_regex") or "").strip()
            name = str(raw.get("name") or (Path(process_name).stem if process_name else Path(path).stem)).strip()
            enabled = bool(raw.get("enabled", True))
        else:
            continue
        if not process_name and not path and not process_regex:
            continue
        if not process_name:
            process_name = Path(path).name
        if not name:
            name = Path(process_name).stem or process_name
        identity = (
            f"regex:{process_regex.casefold()}"
            if process_regex
            else (os.path.normcase(os.path.abspath(path)) if path else process_name.casefold())
        )
        if identity in seen:
            continue
        seen.add(identity)
        entry = {
            "name": name,
            "process_name": process_name,
            "path": path,
            "enabled": enabled,
        }
        if process_regex:
            entry["process_regex"] = process_regex
        result.append(entry)
    return result


def enabled_process_names(config: dict) -> set[str]:
    return {
        str(entry.get("process_name") or "").casefold()
        for entry in normalize_resource_whitelist(config)
        if entry.get("enabled") and entry.get("process_name") and not entry.get("process_regex")
    }


def _compile_process_regex(pattern: str):
    try:
        return re.compile(str(pattern), re.IGNORECASE)
    except (re.error, OverflowError):
        return None


def valid_process_regex(pattern: str) -> bool:
    return _compile_process_regex(pattern) is not None


def enabled_process_patterns(config: dict) -> tuple:
    patterns = []
    for entry in normalize_resource_whitelist(config):
        if not entry.get("enabled") or not entry.get("process_regex"):
            continue
        compiled = _compile_process_regex(str(entry["process_regex"]))
        if compiled is not None:
            patterns.append(compiled)
    return tuple(patterns)


def _process_basename(value: str) -> str:
    return PureWindowsPath(str(value or "")).name


def process_matches(entry: dict, process_name: str, executable_path: str = "") -> bool:
    if not entry.get("enabled"):
        return False
    pattern_text = str(entry.get("process_regex") or "").strip()
    if pattern_text:
        pattern = _compile_process_regex(pattern_text)
        return bool(pattern and pattern.fullmatch(_process_basename(process_name)))
    configured_path = str(entry.get("path") or "").strip()
    if configured_path and executable_path:
        try:
            if os.path.normcase(os.path.abspath(configured_path)) == os.path.normcase(os.path.abspath(executable_path)):
                return True
        except OSError:
            pass
    configured_name = str(entry.get("process_name") or "").casefold()
    return bool(configured_name and configured_name == str(process_name or "").casefold())
