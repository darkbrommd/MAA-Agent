from __future__ import annotations

import sys
from pathlib import Path


def resource_path(*parts: str) -> Path:
    """Resolve GUI assets in source, one-file extraction, or an unpacked release."""
    if getattr(sys, "frozen", False):
        roots = []
        bundle = getattr(sys, "_MEIPASS", "")
        if bundle:
            roots.append(Path(bundle))
        roots.append(Path(sys.executable).resolve().parent)
    else:
        roots = [Path(__file__).resolve().parents[2]]
    for root in roots:
        candidate = root.joinpath(*parts)
        if candidate.exists():
            return candidate
    return roots[0].joinpath(*parts)
