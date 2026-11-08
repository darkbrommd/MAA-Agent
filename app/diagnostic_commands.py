"""Small file-backed command queue shared by the GUI and Agent."""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path


def queue_path(base_dir: Path) -> Path:
    return base_dir / "state" / "diagnostic_commands.jsonl"


def result_path(base_dir: Path) -> Path:
    return base_dir / "state" / "diagnostic_last_result.json"


def enqueue(base_dir: Path, kind: str, target: str = "") -> str:
    path = queue_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    command_id = uuid.uuid4().hex
    row = {
        "id": command_id,
        "kind": str(kind),
        "target": str(target),
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "creator_pid": os.getpid(),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return command_id


def consume(base_dir: Path) -> list[dict]:
    path = queue_path(base_dir)
    if not path.exists():
        return []
    claimed = path.with_name(f"diagnostic_commands.{os.getpid()}.{int(time.time() * 1000)}.claimed")
    try:
        path.replace(claimed)
    except FileNotFoundError:
        return []
    rows: list[dict] = []
    try:
        for line in claimed.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                value = json.loads(line)
                if isinstance(value, dict) and value.get("id") and value.get("kind"):
                    rows.append(value)
            except json.JSONDecodeError:
                continue
    finally:
        claimed.unlink(missing_ok=True)
    return rows


def save_result(base_dir: Path, command: dict, ok: bool, message: str) -> None:
    path = result_path(base_dir)
    value = {
        "id": command.get("id"),
        "kind": command.get("kind"),
        "target": command.get("target"),
        "ok": bool(ok),
        "message": str(message),
        "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
