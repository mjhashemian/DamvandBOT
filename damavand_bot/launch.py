"""Systemd entry point. Load private settings before importing the bot."""

import os
import sys

from .management import read_config


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in {"bot", "panel"}:
        raise SystemExit("usage: python -m damavand_bot.launch bot|panel")
    if sys.argv[1] == "bot":
        for key, value in read_config().items():
            os.environ[key] = value
        from .app import main as bot_main
        bot_main()
    else:
        from .panel import main as panel_main
        panel_main()


if __name__ == "__main__":
    main()
