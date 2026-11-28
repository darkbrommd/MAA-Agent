"""Single executable entry point for the manager and background Agent."""

import os
import sys

from agent_runtime import PACKAGED_AGENT_FLAG

GUI_V2_FLAG = "--gui-v2"
GUI_V1_FLAG = "--gui-v1"


def main() -> None:
    if PACKAGED_AGENT_FLAG in sys.argv[1:]:
        from agent import main as run_agent

        run_agent()
    elif GUI_V1_FLAG in sys.argv[1:] or os.environ.get("MAA_AGENT_GUI", "").lower() == "v1":
        from maa_agent_gui import main as run_manager

        run_manager()
    else:
        from gui.main import main as run_manager

        run_manager()


if __name__ == "__main__":
    main()
