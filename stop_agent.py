"""Gracefully stop the Agent in this directory.

The target is validated as a Python process executing this copy's exact
``agent.py`` before a stop request or forced termination is attempted.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from agent_runtime import request_stop


BASE_DIR = Path(__file__).resolve().parent


def parse_args():
    parser = argparse.ArgumentParser(description="Stop the local MAA Agent instance")
    parser.add_argument("--force", action="store_true", help="force stop after the graceful timeout")
    parser.add_argument("--quiet", action="store_true", help="suppress console output")
    parser.add_argument("--timeout", type=float, default=20, help="graceful wait in seconds")
    return parser.parse_args()


def main():
    args = parse_args()
    result = request_stop(BASE_DIR, timeout_seconds=max(0, args.timeout), force=args.force)
    if not args.quiet:
        print(result.message)
    return 0 if result.status in {"not_running", "stopped", "forced"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
