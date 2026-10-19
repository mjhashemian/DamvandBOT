"""Set or reset the management panel password without exposing it in argv."""

import getpass
import secrets
import sys

from .management import set_panel_password


def main() -> None:
    if len(sys.argv) == 2 and sys.argv[1] == "generate":
        password = secrets.token_urlsafe(32)
        set_panel_password(password)
        print(password)
        return
    if len(sys.argv) > 2 or (len(sys.argv) == 2 and sys.argv[1] != "password"):
        raise SystemExit("usage: python -m damavand_bot.setup [password|generate]")
    password = getpass.getpass("Panel password (16+ characters): ")
    if password != getpass.getpass("Repeat password: "):
        raise SystemExit("Passwords do not match")
    set_panel_password(password)
    print("Panel password updated; existing sessions invalidated.")


if __name__ == "__main__":
    main()
