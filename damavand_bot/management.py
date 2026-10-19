"""Local deployment configuration and panel credential helpers.

Secrets are stored outside the Git checkout. Never log or render their values.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("DAMAVAND_CONFIG_DIR", "/etc/damavand-vpn-bot"))
CONFIG_PATH = CONFIG_DIR / "config.json"
AUTH_PATH = CONFIG_DIR / "panel-auth.json"
PANEL_DOMAIN_PATH = CONFIG_DIR / "panel-domain"

DEFAULTS = {
    "BOT_TOKEN": "",
    "ADMIN_IDS": "",
    "CHANNEL_USERNAME": "",
    "CHANNEL_ID": "",
    "CHANNEL_JOIN_URL": "",
    "SUPPORT_USERNAME": "",
    "DB_PATH": "/var/lib/damavand-vpn-bot/bot.db",
    "TERMS_FILE": "/etc/damavand-vpn-bot/terms-fa.txt",
    "TERMS_VERSION": "1",
    "XUI_VERIFY_SSL": "true",
    "XUI_BASE_URL": "",
    "XUI_API_TOKEN": "",
    "XUI_SUBSCRIPTION_BASE": "",
    "XUI_INBOUND_IDS": "",
    "DEMO_MODE": "false",
    "CARD_PAYMENT_ENABLED": "false",
    "CARD_PAYMENT_CARD_NUMBER": "",
    "CARD_PAYMENT_HOLDER": "",
    "CARD_PAYMENT_INSTRUCTIONS": "",
    "TRIAL_ENABLED": "true",
    "TRIAL_MB": "500",
    "TRIAL_DAYS": "1",
}
SETTINGS = (
    "BOT_TOKEN", "ADMIN_IDS", "CHANNEL_USERNAME", "CHANNEL_ID", "CHANNEL_JOIN_URL", "SUPPORT_USERNAME",
    "XUI_BASE_URL", "XUI_API_TOKEN", "XUI_SUBSCRIPTION_BASE",
    "CARD_PAYMENT_ENABLED", "CARD_PAYMENT_CARD_NUMBER",
    "CARD_PAYMENT_HOLDER", "CARD_PAYMENT_INSTRUCTIONS",
    "TRIAL_ENABLED", "TRIAL_MB", "TRIAL_DAYS",
)
SECRET_SETTINGS = frozenset({"BOT_TOKEN", "XUI_API_TOKEN"})
FIXED_SETTINGS = {"XUI_VERIFY_SSL": "true", "XUI_INBOUND_IDS": "", "DEMO_MODE": "false"}


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".new-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_config() -> dict[str, str]:
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8")) if CONFIG_PATH.exists() else {}
    if not isinstance(data, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in data.items()):
        raise ValueError("Invalid configuration file")
    return {**DEFAULTS, **data, **FIXED_SETTINGS}


def write_config(data: dict[str, str]) -> None:
    atomic_json(CONFIG_PATH, {key: value for key, value in data.items() if key not in FIXED_SETTINGS})


def validate_settings(data: dict[str, str]) -> None:
    from urllib.parse import urlsplit

    for key in SETTINGS:
        value = data.get(key, "")
        if not isinstance(value, str) or len(value) > 4096 or "\x00" in value or "\n" in value or "\r" in value:
            raise ValueError(f"Invalid {key}")
    if not re.fullmatch(r"\d{6,12}:[A-Za-z0-9_-]{30,}", data["BOT_TOKEN"]):
        raise ValueError("Enter a valid Telegram bot token")
    if not re.fullmatch(r"[1-9]\d*(,[1-9]\d*)*", data["ADMIN_IDS"]):
        raise ValueError("ADMIN_IDS must be comma-separated positive Telegram IDs")
    if data["CHANNEL_USERNAME"] and not re.fullmatch(r"@[A-Za-z0-9_]{5,32}", data["CHANNEL_USERNAME"]):
        raise ValueError("CHANNEL_USERNAME must be a public @channel username")
    if data["CHANNEL_ID"] and not re.fullmatch(r"-100\d{5,20}", data["CHANNEL_ID"]):
        raise ValueError("CHANNEL_ID must be a numeric -100... channel ID")
    if not (data["CHANNEL_ID"] or data["CHANNEL_USERNAME"]):
        raise ValueError("Set CHANNEL_ID or CHANNEL_USERNAME")
    if data["CHANNEL_ID"] and not data["CHANNEL_USERNAME"] and not data["CHANNEL_JOIN_URL"]:
        raise ValueError("A private channel ID also needs CHANNEL_JOIN_URL")
    if data["CHANNEL_JOIN_URL"]:
        join_url = urlsplit(data["CHANNEL_JOIN_URL"])
        if join_url.scheme != "https" or join_url.hostname not in {"t.me", "telegram.me"} or not join_url.path.strip("/") or join_url.username or join_url.password or join_url.fragment:
            raise ValueError("CHANNEL_JOIN_URL must be an HTTPS Telegram channel link")
    if not re.fullmatch(r"@[A-Za-z0-9_]{5,32}", data["SUPPORT_USERNAME"]):
        raise ValueError("SUPPORT_USERNAME must be a public @username")
    for key in ("XUI_BASE_URL", "XUI_SUBSCRIPTION_BASE"):
        url = urlsplit(data[key])
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.fragment:
            raise ValueError(f"Invalid {key}")
        if url.scheme == "http" and url.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError(f"{key} must use HTTPS off localhost")
    if not data["XUI_API_TOKEN"]:
        raise ValueError("XUI_API_TOKEN is required")
    if any(data[key] not in ("true", "false") for key in ("XUI_VERIFY_SSL", "DEMO_MODE", "CARD_PAYMENT_ENABLED", "TRIAL_ENABLED")):
        raise ValueError("Invalid boolean setting")
    if not data["TRIAL_MB"].isdigit() or not 1 <= int(data["TRIAL_MB"]) <= 102400:
        raise ValueError("TRIAL_MB must be 1–102400")
    if not data["TRIAL_DAYS"].isdigit() or not 1 <= int(data["TRIAL_DAYS"]) <= 365:
        raise ValueError("TRIAL_DAYS must be 1–365")
    if data["XUI_INBOUND_IDS"] and not re.fullmatch(r"[1-9]\d*(,[1-9]\d*)*", data["XUI_INBOUND_IDS"]):
        raise ValueError("XUI_INBOUND_IDS must be comma-separated positive IDs")
    if data["CARD_PAYMENT_ENABLED"] == "true":
        if not re.fullmatch(r"\d{16}", data["CARD_PAYMENT_CARD_NUMBER"]):
            raise ValueError("Card number must have 16 digits")
        if not data["CARD_PAYMENT_HOLDER"].strip():
            raise ValueError("Card holder is required")


def set_panel_password(password: str) -> None:
    if len(password) < 16 or len(password) > 256:
        raise ValueError("Panel password must be 16-256 characters")
    salt = secrets.token_bytes(32)
    password_hash = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    atomic_json(AUTH_PATH, {
        "salt": salt.hex(), "hash": password_hash.hex(),
    })


def verify_panel_password(password: str) -> bool:
    try:
        auth = json.loads(AUTH_PATH.read_text(encoding="utf-8"))
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(auth["salt"]), n=2**14, r=8, p=1)
        return hmac.compare_digest(actual, bytes.fromhex(auth["hash"]))
    except (OSError, ValueError, KeyError):
        return False
