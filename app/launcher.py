"""Single executable entry point for the manager and background Agent."""

import sys

from agent_runtime import PACKAGED_AGENT_FLAG


def main() -> None:
    if PACKAGED_AGENT_FLAG in sys.argv[1:]:
        from agent import main as run_agent

        run_agent()
    else:
        from maa_agent_gui import main as run_manager

        run_manager()


if __name__ == "__main__":
    main()
