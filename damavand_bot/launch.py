"""Systemd entry point. Load private settings before importing the bot."""

import os
import sys
import time

from .management import CONFIG_PATH, read_config


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in {"bot", "panel"}:
        raise SystemExit("usage: python -m damavand_bot.launch bot|panel")
    if sys.argv[1] == "bot":
        while not CONFIG_PATH.exists():
            time.sleep(2)
        for key, value in read_config().items():
            os.environ[key] = value
        from .app import main as bot_main
        bot_main()
    else:
        from .panel import main as panel_main
        panel_main()


if __name__ == "__main__":
    main()
