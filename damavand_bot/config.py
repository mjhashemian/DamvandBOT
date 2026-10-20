from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .menu import MENU_SETTING_KEYS


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Config:
    bot_token: str
    admin_ids: frozenset[int]
    channel_username: str
    channel_id: str
    channel_join_url: str
    channel_join_required: bool
    support_username: str
    db_path: Path
    terms_file: Path
    terms_version: str
    xui_base_url: str
    xui_api_token: str
    xui_verify_ssl: bool
    xui_subscription_base: str
    xui_inbound_ids: tuple[int, ...]
    demo_mode: bool
    card_payment_enabled: bool
    card_payment_card_number: str
    card_payment_holder: str
    card_payment_instructions: str
    trial_enabled: bool
    trial_mb: int
    trial_days: int
    enabled_buttons: frozenset[str]

    @classmethod
    def from_env(cls) -> "Config":
        admin_ids = frozenset(
            int(value.strip())
            for value in _required("ADMIN_IDS").split(",")
            if value.strip()
        )
        if not admin_ids:
            raise RuntimeError("ADMIN_IDS must contain at least one administrator")
        inbound_ids: tuple[int, ...] = ()
        card_payment_enabled = _bool("CARD_PAYMENT_ENABLED", False)
        card_payment_card_number = os.getenv("CARD_PAYMENT_CARD_NUMBER", "").strip()
        card_payment_holder = os.getenv("CARD_PAYMENT_HOLDER", "").strip()
        if card_payment_enabled and (not card_payment_card_number or not card_payment_holder):
            raise RuntimeError(
                "CARD_PAYMENT_CARD_NUMBER and CARD_PAYMENT_HOLDER are required when card payments are enabled"
            )
        if card_payment_enabled and (not card_payment_card_number.isdigit() or len(card_payment_card_number) != 16):
            raise RuntimeError("CARD_PAYMENT_CARD_NUMBER must be 16 digits")
        return cls(
            bot_token=_required("BOT_TOKEN"),
            admin_ids=admin_ids,
            channel_username=os.getenv("CHANNEL_USERNAME", "").strip(),
            channel_id=os.getenv("CHANNEL_ID", "").strip(),
            channel_join_url=os.getenv("CHANNEL_JOIN_URL", "").strip(),
            channel_join_required=_bool("CHANNEL_JOIN_REQUIRED", True),
            support_username=os.getenv("SUPPORT_USERNAME", "").strip(),
            db_path=Path(os.getenv("DB_PATH", "/var/lib/damavand-vpn-bot/bot.db")),
            terms_file=Path(os.getenv("TERMS_FILE", "/etc/damavand-vpn-bot/terms-fa.txt")),
            terms_version=os.getenv("TERMS_VERSION", "1").strip(),
            xui_base_url=_required("XUI_BASE_URL").rstrip("/"),
            xui_api_token=_required("XUI_API_TOKEN"),
            xui_verify_ssl=True,
            xui_subscription_base=_required("XUI_SUBSCRIPTION_BASE").rstrip("/"),
            xui_inbound_ids=inbound_ids,
            demo_mode=False,
            card_payment_enabled=card_payment_enabled,
            card_payment_card_number=card_payment_card_number,
            card_payment_holder=card_payment_holder,
            card_payment_instructions=os.getenv("CARD_PAYMENT_INSTRUCTIONS", "").strip(),
            trial_enabled=_bool("TRIAL_ENABLED", True),
            trial_mb=int(os.getenv("TRIAL_MB", "500")),
            trial_days=int(os.getenv("TRIAL_DAYS", "1")),
            enabled_buttons=frozenset(name for name, key in MENU_SETTING_KEYS.items() if _bool(key, True)),
        )

    def button_enabled(self, name: str) -> bool:
        return name in self.enabled_buttons

    @property
    def channel_url(self) -> str:
        return self.channel_join_url or f"https://t.me/{self.channel_username.lstrip('@')}"

    @property
    def support_url(self) -> str:
        return f"https://t.me/{self.support_username.lstrip('@')}"

    def read_terms(self) -> str:
        try:
            return self.terms_file.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return ""
