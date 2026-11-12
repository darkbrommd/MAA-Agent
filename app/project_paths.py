"""Shared source and packaged-installation paths."""

import sys
from pathlib import Path


def installation_root(source_file: Path | str | None = None) -> Path:
    """Return the folder containing the portable installation."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    source = Path(source_file or __file__).resolve()
    return source.parents[1]


def current_entry_path(source_file: Path | str) -> Path:
    """Return the executable in a frozen build, otherwise the Python entry file."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return Path(source_file).resolve()


def config_path(base_dir: Path) -> Path:
    """Prefer the tidy config directory, but keep existing root configs usable."""
    base_dir = Path(base_dir)
    preferred = base_dir / "config" / "config.json"
    legacy = base_dir / "config.json"
    if preferred.exists() or not legacy.exists():
        return preferred
    return legacy


def config_template_path(base_dir: Path) -> Path:
    return Path(base_dir) / "config" / "config.example.json"


def version_path(base_dir: Path) -> Path:
    return Path(base_dir) / "metadata" / "VERSION"
