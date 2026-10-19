"""Interactive, no-command-line-secret deployment setup."""

import getpass
import sys

from .management import AUTH_PATH, read_config, set_panel_password, validate_settings, write_config


def visible_prompt(label: str) -> str:
    # The installer may be streamed through bash stdin; never consume script bytes.
    with open("/dev/tty", "r+", encoding="utf-8") as terminal:
        terminal.write(label)
        terminal.flush()
        return terminal.readline().strip()


def main() -> None:
    if len(sys.argv) == 2 and sys.argv[1] == "password":
        password = getpass.getpass("New panel password (16+ characters): ")
        if password != getpass.getpass("Repeat password: "):
            raise SystemExit("Passwords do not match")
        set_panel_password(password)
        print("Panel password updated; existing sessions invalidated.")
        return
    if len(sys.argv) != 1:
        raise SystemExit("usage: python -m damavand_bot.setup [password]")
    data = read_config()
    print("Configure the bot. Secrets are hidden and remain outside the Git checkout.")
    prompts = (
        ("BOT_TOKEN", "Telegram bot token", True),
        ("ADMIN_IDS", "Admin Telegram IDs (comma-separated)", False),
        ("CHANNEL_USERNAME", "Public channel @username (blank for private channel)", False),
        ("CHANNEL_ID", "Channel numeric ID (-100..., recommended)", False),
        ("CHANNEL_JOIN_URL", "Channel join URL (required for private channel)", False),
        ("SUPPORT_USERNAME", "Support @username", False),
        ("XUI_BASE_URL", "3x-ui API base URL", False),
        ("XUI_API_TOKEN", "3x-ui API token", True),
        ("XUI_SUBSCRIPTION_BASE", "Subscription base URL", False),
    )
    for key, label, secret in prompts:
        old = data.get(key, "")
        hint = " [keep current]" if old else ""
        value = (getpass.getpass if secret else visible_prompt)(f"{label}{hint}: ").strip()
        if value:
            data[key] = value
    validate_settings(data)
    write_config(data)
    if not AUTH_PATH.exists():
        print("Set a strong web-panel password.")
        password = getpass.getpass("Panel password (16+ characters): ")
        if password != getpass.getpass("Repeat password: "):
            raise SystemExit("Passwords do not match")
        set_panel_password(password)
    print("Configuration saved.")


if __name__ == "__main__":
    main()
